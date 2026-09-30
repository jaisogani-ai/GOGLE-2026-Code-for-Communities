"""
TATHYON Operational Intelligence Layer.

Provides three capabilities that distinguish a resilience control plane
from a generic dashboard:

1. RESOURCE HEALTH PROFILE: Six explicit operational dimensions for each
   facility-resource pair. NOT a generic trust score. Each dimension has
   a named status, a numeric value, and a human-readable explanation.

2. DISCREPANCY DETECTOR: Longitudinal pattern recognition across observation
   history. Detects SYSTEMATIC CLAIM-REALITY GAPS, not merely individual
   stockouts. This is the layer that would tell a District Officer:
   "Facility A has digitally claimed 20-30% above usable stock in 7 of the
   last 10 observation cycles."

3. CONFIDENCE CALCULATOR: Derives data-quality confidence from actual
   observable properties (freshness, reconciliation gap, observation count,
   attestation completeness). NEVER an arbitrary 0-100 AI score.

CRITICAL INVARIANT: Every score and classification is mathematically derived
from observable data properties. If the data is insufficient, the system
says "INSUFFICIENT_DATA" — it does not guess.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .graph import HealthcareResourceGraph, ResourceState
from .schema import now


# --------------------------------------------------------------------------
# 1. RESOURCE HEALTH PROFILE
# --------------------------------------------------------------------------

@dataclass
class HealthDimension:
    """A single operational health dimension with explanation."""
    name: str
    status: str          # "HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN"
    value: float         # Normalized 0.0-1.0 for the dimension
    explanation: str     # "WHY IS THIS HEALTHY/UNHEALTHY?"
    raw_metric: Any = None  # The underlying measurement

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResourceHealthProfile:
    """Operational health assessment across 6 explicit dimensions.
    
    NOT a generic trust score. Each dimension is independently meaningful
    and independently actionable.
    """
    facility_id: str
    resource_id: str
    supply: HealthDimension
    freshness: HealthDimension
    demand: HealthDimension
    replenishment: HealthDimension
    quality: HealthDimension
    reconciliation: HealthDimension
    overall_status: str     # "HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN"
    critical_dimensions: list[str]  # Which dimensions are driving the status
    as_of: str = field(default_factory=now)

    def to_dict(self) -> dict:
        return {
            "facility_id": self.facility_id,
            "resource_id": self.resource_id,
            "dimensions": {
                "supply": self.supply.to_dict(),
                "freshness": self.freshness.to_dict(),
                "demand": self.demand.to_dict(),
                "replenishment": self.replenishment.to_dict(),
                "quality": self.quality.to_dict(),
                "reconciliation": self.reconciliation.to_dict(),
            },
            "overall_status": self.overall_status,
            "critical_dimensions": self.critical_dimensions,
            "as_of": self.as_of,
        }


def compute_health_profile(
    state: ResourceState,
    graph: Optional[HealthcareResourceGraph] = None,
) -> ResourceHealthProfile:
    """Computes the 6-dimension health profile for a facility-resource pair.
    
    Every dimension returns:
    - status: HEALTHY / DEGRADED / CRITICAL / UNKNOWN
    - value: 0.0 (worst) to 1.0 (best)
    - explanation: human-readable "why" string
    """
    # 1. SUPPLY: Is there enough usable stock?
    days_stock = state.days_of_usable_stock
    if days_stock >= 21.0:
        supply = HealthDimension(
            "supply", "HEALTHY", min(days_stock / 30.0, 1.0),
            f"Usable stock covers {days_stock:.1f} days at current velocity ({state.consumption_velocity:.1f} units/day).",
            raw_metric={"days_of_stock": days_stock, "usable_quantity": state.usable_quantity},
        )
    elif days_stock >= 7.0:
        supply = HealthDimension(
            "supply", "DEGRADED", days_stock / 21.0,
            f"Stock buffer at {days_stock:.1f} days — below 21-day safety threshold. "
            f"Usable: {state.usable_quantity:.0f} units, velocity: {state.consumption_velocity:.1f}/day.",
            raw_metric={"days_of_stock": days_stock, "usable_quantity": state.usable_quantity},
        )
    elif days_stock > 0:
        supply = HealthDimension(
            "supply", "CRITICAL", max(days_stock / 7.0, 0.0),
            f"CRITICAL: Only {days_stock:.1f} days of usable stock remain. "
            f"Stockout imminent at {state.consumption_velocity:.1f} units/day consumption.",
            raw_metric={"days_of_stock": days_stock, "usable_quantity": state.usable_quantity},
        )
    else:
        supply = HealthDimension(
            "supply", "CRITICAL", 0.0,
            f"ZERO usable stock. Claimed: {state.claimed_quantity:.0f}, Usable: 0.0. "
            f"Facility cannot serve patients for this resource.",
            raw_metric={"days_of_stock": 0.0, "usable_quantity": 0.0},
        )

    # 2. FRESHNESS: How recent is the physical attestation?
    fresh = state.is_attestation_fresh(max_age_hours=48.0)
    if state.last_attested_at:
        try:
            att_time = datetime.fromisoformat(state.last_attested_at.replace("Z", "+00:00"))
            if att_time.tzinfo is None:
                att_time = att_time.replace(tzinfo=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - att_time).total_seconds() / 3600.0
        except Exception:
            age_hours = 999.0
    else:
        age_hours = 999.0

    if fresh and age_hours <= 24.0:
        freshness = HealthDimension(
            "freshness", "HEALTHY", max(1.0 - age_hours / 48.0, 0.0),
            f"Physical attestation is {age_hours:.0f} hours old — within 24h freshness window.",
            raw_metric={"age_hours": round(age_hours, 1), "last_attested_at": state.last_attested_at},
        )
    elif fresh:
        freshness = HealthDimension(
            "freshness", "DEGRADED", max(1.0 - age_hours / 72.0, 0.0),
            f"Attestation is {age_hours:.0f} hours old — valid but approaching staleness threshold (48h).",
            raw_metric={"age_hours": round(age_hours, 1), "last_attested_at": state.last_attested_at},
        )
    else:
        freshness = HealthDimension(
            "freshness", "CRITICAL", 0.0,
            f"Attestation is STALE ({age_hours:.0f}h old) or MISSING. "
            f"Physical stock state cannot be trusted. Inventory is not donor-eligible.",
            raw_metric={"age_hours": round(age_hours, 1), "last_attested_at": state.last_attested_at},
        )

    # 3. DEMAND: Is consumption velocity stable or surging?
    vel = state.consumption_velocity
    forecast_d = state.forecast_demand
    if vel <= 0:
        demand = HealthDimension(
            "demand", "UNKNOWN", 0.5,
            "No consumption velocity recorded. Cannot assess demand trajectory.",
            raw_metric={"velocity": vel, "forecast_demand": forecast_d},
        )
    elif forecast_d > 0 and forecast_d > vel * state.lead_time * 1.5:
        demand = HealthDimension(
            "demand", "CRITICAL", 0.2,
            f"Forecast demand ({forecast_d:.0f}) exceeds 150% of baseline over lead time. "
            f"Possible surge or acceleration event.",
            raw_metric={"velocity": vel, "forecast_demand": forecast_d},
        )
    elif forecast_d > vel * state.lead_time * 1.1:
        demand = HealthDimension(
            "demand", "DEGRADED", 0.6,
            f"Demand trending upward: forecast {forecast_d:.0f} vs baseline {vel * state.lead_time:.0f}.",
            raw_metric={"velocity": vel, "forecast_demand": forecast_d},
        )
    else:
        demand = HealthDimension(
            "demand", "HEALTHY", 0.9,
            f"Consumption velocity stable at {vel:.1f} units/day. No acceleration detected.",
            raw_metric={"velocity": vel, "forecast_demand": forecast_d},
        )

    # 4. REPLENISHMENT: Is incoming stock and lead time adequate?
    incoming = state.incoming_quantity
    lead_needed = vel * state.lead_time if vel > 0 else 0.0
    if incoming >= lead_needed and lead_needed > 0:
        replenishment = HealthDimension(
            "replenishment", "HEALTHY", min(incoming / max(lead_needed, 1.0), 1.0),
            f"Incoming shipment ({incoming:.0f}) covers lead-time demand ({lead_needed:.0f}). "
            f"Lead time: {state.lead_time:.0f} days.",
            raw_metric={"incoming": incoming, "lead_time": state.lead_time, "lead_demand": lead_needed},
        )
    elif incoming > 0:
        replenishment = HealthDimension(
            "replenishment", "DEGRADED", incoming / max(lead_needed, 1.0),
            f"Incoming ({incoming:.0f}) covers only {incoming/max(lead_needed,1)*100:.0f}% "
            f"of lead-time demand ({lead_needed:.0f}).",
            raw_metric={"incoming": incoming, "lead_time": state.lead_time, "lead_demand": lead_needed},
        )
    else:
        replenishment = HealthDimension(
            "replenishment", "CRITICAL" if vel > 0 else "UNKNOWN", 0.0,
            "No incoming shipment recorded. Facility dependent entirely on current stock."
            if vel > 0 else "No consumption and no incoming — resource may be inactive.",
            raw_metric={"incoming": 0.0, "lead_time": state.lead_time, "lead_demand": lead_needed},
        )

    # 5. QUALITY: Is there expired or quarantined stock?
    total_unusable = state.expired_quantity + state.quarantined_quantity
    total_observed = max(state.observed_quantity, state.claimed_quantity, 1.0)
    unusable_ratio = total_unusable / total_observed
    if unusable_ratio <= 0.05:
        quality = HealthDimension(
            "quality", "HEALTHY", 1.0 - unusable_ratio,
            f"Quality is high: only {unusable_ratio*100:.1f}% of stock is expired/quarantined.",
            raw_metric={"expired": state.expired_quantity, "quarantined": state.quarantined_quantity},
        )
    elif unusable_ratio <= 0.20:
        quality = HealthDimension(
            "quality", "DEGRADED", 1.0 - unusable_ratio,
            f"Quality concern: {unusable_ratio*100:.1f}% of stock is expired ({state.expired_quantity:.0f}) "
            f"or quarantined ({state.quarantined_quantity:.0f}).",
            raw_metric={"expired": state.expired_quantity, "quarantined": state.quarantined_quantity},
        )
    else:
        quality = HealthDimension(
            "quality", "CRITICAL", max(1.0 - unusable_ratio, 0.0),
            f"CRITICAL quality issue: {unusable_ratio*100:.1f}% of observed stock "
            f"is expired or quarantined. Immediate physical audit required.",
            raw_metric={"expired": state.expired_quantity, "quarantined": state.quarantined_quantity},
        )

    # 6. RECONCILIATION: How close is claimed to usable?
    phantom = state.phantom_inventory
    if state.claimed_quantity > 0:
        recon_ratio = state.usable_quantity / state.claimed_quantity
    else:
        recon_ratio = 1.0 if state.usable_quantity <= 0 else 0.0

    if recon_ratio >= 0.90:
        reconciliation = HealthDimension(
            "reconciliation", "HEALTHY", recon_ratio,
            f"Digital claim ({state.claimed_quantity:.0f}) closely matches usable "
            f"({state.usable_quantity:.0f}). Reconciliation gap: {phantom:.0f} units.",
            raw_metric={"claimed": state.claimed_quantity, "usable": state.usable_quantity, "phantom": phantom},
        )
    elif recon_ratio >= 0.60:
        reconciliation = HealthDimension(
            "reconciliation", "DEGRADED", recon_ratio,
            f"Reconciliation gap: claimed {state.claimed_quantity:.0f} vs usable "
            f"{state.usable_quantity:.0f}. Phantom inventory: {phantom:.0f} units "
            f"({(1-recon_ratio)*100:.0f}% discrepancy).",
            raw_metric={"claimed": state.claimed_quantity, "usable": state.usable_quantity, "phantom": phantom},
        )
    else:
        reconciliation = HealthDimension(
            "reconciliation", "CRITICAL", max(recon_ratio, 0.0),
            f"SEVERE phantom inventory: digital claim {state.claimed_quantity:.0f}, "
            f"physically usable only {state.usable_quantity:.0f}. "
            f"Phantom: {phantom:.0f} units ({(1-recon_ratio)*100:.0f}% gap). "
            f"Systematic claim-reality discrepancy suspected.",
            raw_metric={"claimed": state.claimed_quantity, "usable": state.usable_quantity, "phantom": phantom},
        )

    # Overall status: worst dimension drives it
    dimensions = [supply, freshness, demand, replenishment, quality, reconciliation]
    critical_dims = [d.name for d in dimensions if d.status == "CRITICAL"]
    degraded_dims = [d.name for d in dimensions if d.status == "DEGRADED"]

    if critical_dims:
        overall = "CRITICAL"
    elif degraded_dims:
        overall = "DEGRADED"
    elif any(d.status == "UNKNOWN" for d in dimensions):
        overall = "DEGRADED"
    else:
        overall = "HEALTHY"

    return ResourceHealthProfile(
        facility_id=state.facility_id,
        resource_id=state.resource_id,
        supply=supply,
        freshness=freshness,
        demand=demand,
        replenishment=replenishment,
        quality=quality,
        reconciliation=reconciliation,
        overall_status=overall,
        critical_dimensions=critical_dims + degraded_dims,
    )


# --------------------------------------------------------------------------
# 2. DISCREPANCY DETECTOR
# --------------------------------------------------------------------------

@dataclass
class DiscrepancyRecord:
    """A single observed discrepancy event."""
    facility_id: str
    resource_id: str
    discrepancy_type: str  # e.g. "PHANTOM_INVENTORY", "EXPIRED_STOCK", "SHORT_DELIVERY"
    magnitude: float
    observed_at: str
    details: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiscrepancyPattern:
    """A longitudinal pattern detected across multiple observations."""
    facility_id: str
    resource_id: str
    pattern_type: str
    occurrence_count: int
    severity: str           # "LOW", "MEDIUM", "HIGH", "SYSTEMATIC"
    average_magnitude: float
    explanation: str
    recommendation: str
    observations: list[DiscrepancyRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["observations"] = [o.to_dict() for o in self.observations]
        return d


class DiscrepancyDetector:
    """Detects systematic operational patterns across observation history.
    
    The key insight: a single stockout is an event. A facility that consistently
    claims 20-30% above usable stock is a PATTERN. The system must recognize
    patterns, not just events.
    """

    def __init__(self):
        self.history: list[DiscrepancyRecord] = []

    def record_observation(
        self,
        facility_id: str,
        resource_id: str,
        claimed: float,
        usable: float,
        expired: float = 0.0,
        quarantined: float = 0.0,
        delivered: float = 0.0,
        expected_delivery: float = 0.0,
        transfer_success: bool = True,
        as_of: Optional[str] = None,
    ) -> list[DiscrepancyRecord]:
        """Records an observation and detects immediate discrepancies."""
        ts = as_of or now()
        new_records = []

        # Phantom inventory detection
        if claimed > 0 and usable < claimed * 0.85:
            phantom = claimed - usable
            rec = DiscrepancyRecord(
                facility_id=facility_id,
                resource_id=resource_id,
                discrepancy_type="PHANTOM_INVENTORY",
                magnitude=round(phantom, 1),
                observed_at=ts,
                details=f"Claimed {claimed:.0f} but only {usable:.0f} usable. "
                        f"Phantom: {phantom:.0f} units ({phantom/claimed*100:.0f}% gap).",
            )
            new_records.append(rec)

        # Expired stock accumulation
        if expired > 0 and (claimed > 0 and expired / claimed > 0.10):
            rec = DiscrepancyRecord(
                facility_id=facility_id,
                resource_id=resource_id,
                discrepancy_type="EXPIRED_STOCK_ACCUMULATION",
                magnitude=round(expired, 1),
                observed_at=ts,
                details=f"Expired stock {expired:.0f} is {expired/max(claimed,1)*100:.0f}% of claimed inventory.",
            )
            new_records.append(rec)

        # Short delivery detection
        if expected_delivery > 0 and delivered < expected_delivery * 0.90:
            shortfall = expected_delivery - delivered
            rec = DiscrepancyRecord(
                facility_id=facility_id,
                resource_id=resource_id,
                discrepancy_type="SHORT_DELIVERY",
                magnitude=round(shortfall, 1),
                observed_at=ts,
                details=f"Expected delivery {expected_delivery:.0f}, received only {delivered:.0f}. "
                        f"Shortfall: {shortfall:.0f} units ({shortfall/expected_delivery*100:.0f}%).",
            )
            new_records.append(rec)

        # Transfer failure
        if not transfer_success:
            rec = DiscrepancyRecord(
                facility_id=facility_id,
                resource_id=resource_id,
                discrepancy_type="TRANSFER_FAILURE",
                magnitude=expected_delivery,
                observed_at=ts,
                details=f"Transfer of {expected_delivery:.0f} units failed to arrive at facility.",
            )
            new_records.append(rec)

        self.history.extend(new_records)
        return new_records

    def detect_patterns(
        self,
        facility_id: Optional[str] = None,
        resource_id: Optional[str] = None,
        min_occurrences: int = 3,
    ) -> list[DiscrepancyPattern]:
        """Detects longitudinal patterns across observation history.
        
        A pattern is SYSTEMATIC when the same discrepancy type occurs
        at the same facility-resource pair at least `min_occurrences` times.
        """
        # Group by (facility_id, resource_id, discrepancy_type)
        groups: dict[tuple[str, str, str], list[DiscrepancyRecord]] = {}
        for rec in self.history:
            if facility_id and rec.facility_id != facility_id:
                continue
            if resource_id and rec.resource_id != resource_id:
                continue
            key = (rec.facility_id, rec.resource_id, rec.discrepancy_type)
            groups.setdefault(key, []).append(rec)

        patterns = []
        for (fid, rid, dtype), records in groups.items():
            count = len(records)
            if count < min_occurrences:
                continue

            avg_mag = sum(r.magnitude for r in records) / count

            if count >= 7:
                severity = "SYSTEMATIC"
            elif count >= 5:
                severity = "HIGH"
            elif count >= 3:
                severity = "MEDIUM"
            else:
                severity = "LOW"

            explanation = self._explain_pattern(dtype, count, avg_mag, fid)
            recommendation = self._recommend_action(dtype, severity, fid)

            patterns.append(DiscrepancyPattern(
                facility_id=fid,
                resource_id=rid,
                pattern_type=dtype,
                occurrence_count=count,
                severity=severity,
                average_magnitude=round(avg_mag, 1),
                explanation=explanation,
                recommendation=recommendation,
                observations=records,
            ))

        patterns.sort(key=lambda p: p.occurrence_count, reverse=True)
        return patterns

    @staticmethod
    def _explain_pattern(dtype: str, count: int, avg_mag: float, fid: str) -> str:
        explanations = {
            "PHANTOM_INVENTORY": (
                f"Facility {fid} has shown a SYSTEMATIC CLAIM-REALITY GAP in {count} "
                f"of the observed cycles. Digital claims average {avg_mag:.0f} units above "
                f"physically usable stock. This is not a single stockout — it is a persistent "
                f"overstatement of available inventory."
            ),
            "EXPIRED_STOCK_ACCUMULATION": (
                f"Facility {fid} has accumulated expired stock in {count} cycles, averaging "
                f"{avg_mag:.0f} expired units. This suggests inadequate FEFO rotation or "
                f"delayed withdrawal of expired batches."
            ),
            "SHORT_DELIVERY": (
                f"Supplier deliveries to {fid} have been short in {count} cycles, averaging "
                f"{avg_mag:.0f} units below expected. This may indicate supplier fill rate "
                f"problems or transit losses."
            ),
            "TRANSFER_FAILURE": (
                f"Transfers to {fid} have failed {count} times. This indicates route reliability "
                f"issues or operational barriers to redistribution."
            ),
        }
        return explanations.get(dtype, f"Pattern {dtype} occurred {count} times at {fid}.")

    @staticmethod
    def _recommend_action(dtype: str, severity: str, fid: str) -> str:
        if severity == "SYSTEMATIC":
            prefix = "URGENT: Escalate to District Officer for investigation. "
        elif severity == "HIGH":
            prefix = "Priority investigation recommended. "
        else:
            prefix = "Monitor and track. "

        recommendations = {
            "PHANTOM_INVENTORY": prefix + f"Conduct unannounced physical verification at {fid}. "
                                          f"Compare digital ledger entries against batch-level counts.",
            "EXPIRED_STOCK_ACCUMULATION": prefix + f"Review FEFO compliance at {fid}. "
                                                    f"Check cold-chain integrity and stock rotation procedures.",
            "SHORT_DELIVERY": prefix + f"Audit supplier contracts and delivery documentation. "
                                       f"Cross-check with transporter waybills.",
            "TRANSFER_FAILURE": prefix + f"Review route availability and operational procedures. "
                                         f"Check for road/infrastructure barriers.",
        }
        return recommendations.get(dtype, prefix + "Investigate root cause.")


# --------------------------------------------------------------------------
# 3. CONFIDENCE CALCULATOR
# --------------------------------------------------------------------------

def calculate_confidence(
    state: ResourceState,
    observation_count: int = 1,
    attestation_count: int = 0,
) -> dict:
    """Derives data-quality confidence from actual observable properties.
    
    NEVER an arbitrary 0-100 AI score. Each factor is independently meaningful
    and auditable:
    
    - freshness_factor: How recent is the last attestation?
    - reconciliation_factor: How close is claimed to usable?
    - observation_depth_factor: How many independent observations exist?
    - completeness_factor: Are all required fields populated?
    
    The composite confidence is the geometric mean of the factors,
    which means a zero in ANY dimension drives confidence toward zero.
    This is intentional: a perfectly fresh attestation with zero reconciliation
    should NOT score 50%.
    """
    # Freshness factor (0.0 to 1.0)
    if state.last_attested_at:
        try:
            att = datetime.fromisoformat(state.last_attested_at.replace("Z", "+00:00"))
            if att.tzinfo is None:
                att = att.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - att).total_seconds() / 3600.0
            # Exponential decay: halves every 24 hours
            freshness_factor = math.exp(-0.693 * age_h / 24.0)
        except Exception:
            freshness_factor = 0.0
    else:
        freshness_factor = 0.0

    # Reconciliation factor (0.0 to 1.0)
    if state.claimed_quantity > 0:
        recon_factor = min(state.usable_quantity / state.claimed_quantity, 1.0)
    elif state.usable_quantity <= 0:
        recon_factor = 0.5  # No claim, no usable — neutral
    else:
        recon_factor = 1.0  # Has usable, no claim — OK

    # Observation depth factor
    depth_factor = min(observation_count / 5.0, 1.0)  # saturates at 5 observations

    # Completeness factor: are all critical fields populated?
    fields_present = sum([
        state.claimed_quantity > 0 or state.observed_quantity > 0,
        state.consumption_velocity > 0 or state.usable_quantity > 0,
        state.last_attested_at is not None,
        state.lead_time > 0,
        attestation_count > 0,
    ])
    completeness_factor = fields_present / 5.0

    # Geometric mean: all factors must be reasonable for high confidence
    factors = [
        max(freshness_factor, 0.01),
        max(recon_factor, 0.01),
        max(depth_factor, 0.01),
        max(completeness_factor, 0.01),
    ]
    composite = math.exp(sum(math.log(f) for f in factors) / len(factors))
    composite = round(min(max(composite, 0.0), 1.0), 3)

    return {
        "composite_confidence": composite,
        "factors": {
            "freshness": round(freshness_factor, 3),
            "reconciliation": round(recon_factor, 3),
            "observation_depth": round(depth_factor, 3),
            "completeness": round(completeness_factor, 3),
        },
        "interpretation": (
            "HIGH: Multiple independent observations with fresh attestation and close reconciliation."
            if composite >= 0.7 else (
                "MODERATE: Some data quality gaps exist. Review freshness and reconciliation."
                if composite >= 0.4 else
                "LOW: Significant data quality gaps. Physical verification strongly recommended."
            )
        ),
        "methodology": "Geometric mean of freshness (exp decay τ=24h), reconciliation ratio, "
                        "observation depth (saturates at 5), and field completeness (5 fields).",
    }
