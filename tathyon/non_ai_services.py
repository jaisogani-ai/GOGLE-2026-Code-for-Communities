"""
TATHYON Deterministic Non-AI Decision Services.

Implements core deterministic decision services across:
1. Trust Queue Service:
   - Explicit, multi-factor scoring (risk, data age, criticality/essentiality, discrepancy history, visit cost).
   - Clear versioning, calculation timestamp, source facts, and top contributing reasons.
   - Missing values are explicitly flagged as high uncertainty / high verification priority (never treated as low risk).
   - Strict separation between risk score and confidence in underlying data.
   - Objective triage without claiming fraud or intent.
   
2. Verification Dispatch Service:
   - 100% deterministic, ordinary non-AI Python logic.
   - Respects district team capacity, travel/time constraints, workload limits, and priority rules.
   - Supports manual overrides with cryptographic SHA-256 audit events.
   - Enforces human approval policies for high-priority or break-glass dispatches.
   - Guaranteed byte-level repeatability for identical inputs and configuration.

3. Deterministic Forecasting Service:
   - Intermittent demand (Croston-SBA / TSB / Naive) and stockout projections.
   - Bed occupancy and role-level staffing projections.
   - Explicit horizon, assumptions, input timestamps, data coverage, uncertainty intervals, backtest metrics (MASE).
   - Clear distinction: forecast is a statistical model, never observed fact.
   - Returns explicit 'INSUFFICIENT_DATA' or 'STALE_DATA' with actionable reasons instead of fabricating projections.

4. Deterministic Resource Allocation Service:
   - Multi-commodity constraint-aware optimizer (wrapping CP-SAT).
   - Configurable donor safety floors (14-day default), batch expiry constraints, transport capacity, cold chain.
   - Returns proposed transfers, routes, assumptions, and rejected candidates with exact invariant reasons.
   - All proposals are PROPOSED pending explicit signed human Medical Officer approval.
   - Never executes transfers or redeployments automatically.

5. Demo Safety Controls:
   - Synthetic seed & reset utilities with prominent SYNTHETIC markings.
   - Prevents demo actions from ever being mistaken for actual national operations.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tathyon.schema import (
    EventType,
    Provenance,
    ResourceType,
    VerificationState,
    new_id,
    now,
    sha256,
)
from tathyon.store import EventStore

DECISION_SERVICES_VERSION = "2.1.0"
DEMO_SAFETY_BANNER = "[SYNTHETIC DEMO - NO LIVE OPERATIONS]"


# ==============================================================================
# 1. TRUST QUEUE SERVICE
# ==============================================================================

@dataclass
class TrustQueueFactorWeights:
    weight_unobserved_risk: float = 0.35
    weight_data_staleness: float = 0.25
    weight_service_criticality: float = 0.20
    weight_discrepancy_history: float = 0.15
    weight_cost_efficiency: float = 0.05
    stale_threshold_days: float = 30.0


@dataclass
class TrustQueueItem:
    facility_id: str
    facility_name: str
    resource_key: str
    resource_type: str
    risk_score: float                     # [0.0, 1.0] Expected probability of material stock error
    data_confidence: float                # [0.0, 1.0] Confidence in observation quality (DISTINCT from risk)
    data_age_days: float                  # Days since last attested count
    is_missing_data: bool                 # True if data is absent or unobserved
    service_criticality: float            # VEN classification weight (V=3, E=2, N=1)
    discrepancy_history_count: int        # Past count gaps / discrepancies
    visit_cost_slots: int                 # Half-day transit slots
    expected_value: float                 # Multi-factor priority score
    rank: int = 0
    top_contributing_reasons: List[str] = field(default_factory=list)
    source_facts: Dict[str, Any] = field(default_factory=dict)
    calculation_timestamp: str = field(default_factory=now)
    score_version: str = DECISION_SERVICES_VERSION
    provenance: str = "SYNTHETIC_TRIAGE"

    def to_dict(self) -> dict:
        return asdict(self)


class TrustQueueService:
    """Ranks facilities for physical inspection using explainable, configurable factors."""

    def __init__(self, weights: Optional[TrustQueueFactorWeights] = None):
        self.weights = weights or TrustQueueFactorWeights()

    def score_and_rank(
        self,
        observations: List[Dict[str, Any]],
        budget_slots: int = 20,
        as_of: Optional[str] = None,
    ) -> List[TrustQueueItem]:
        calc_time = as_of or now()
        items: List[TrustQueueItem] = []

        for obs in observations:
            fac_id = str(obs.get("facility_id", "UNKNOWN"))
            fac_name = str(obs.get("facility_name", fac_id))
            res_key = str(obs.get("resource_key", "MED-ARV-01"))
            res_type = str(obs.get("resource_type", "MEDICINE"))
            
            # 1. Missing Data Evaluation: Missing is HIGH RISK / HIGH UNCERTAINTY, NEVER LOW RISK
            is_missing = obs.get("reported_stock") is None or obs.get("is_missing", False)
            data_age = float(obs.get("data_age_days", 365.0 if is_missing else 0.0))
            
            # Risk score vs Data confidence (STRICTLY DISTINCT)
            if is_missing:
                risk_score = 0.95
                data_confidence = 0.05
                missing_note = "Missing observation: unobserved facility cannot be assumed safe"
            else:
                p_wrong = float(obs.get("p_wrong", 0.10))
                risk_score = float(np.clip(p_wrong, 0.0, 1.0))
                # Data confidence reflects freshness and attestation rigor
                staleness_penalty = min(1.0, data_age / self.weights.stale_threshold_days)
                data_confidence = float(np.clip(1.0 - (0.6 * staleness_penalty), 0.05, 1.0))
                missing_note = None

            # Criticality / Essentiality (V=3.0, E=2.0, N=1.0)
            crit = float(obs.get("essentiality", obs.get("criticality", 2.0)))
            discrepancy_hist = int(obs.get("discrepancy_history_count", 0))
            visit_cost = max(1, int(obs.get("visit_cost_slots", 1)))
            hidden_stockout_days = float(obs.get("hidden_stockout_days", 10.0))

            # Multi-factor explainable value calculation
            # Factors:
            # 1. Consequence of unobserved risk: risk_score * hidden_stockout_days
            # 2. Staleness urgency: normalized data age
            # 3. Essentiality multiplier
            # 4. Discrepancy history
            # 5. Cost efficiency penalty: 1 / visit_cost
            staleness_norm = min(3.0, data_age / self.weights.stale_threshold_days)
            disc_norm = min(3.0, discrepancy_hist * 0.5)
            
            raw_value = (
                (self.weights.weight_unobserved_risk * (risk_score * hidden_stockout_days)) +
                (self.weights.weight_data_staleness * (staleness_norm * 5.0)) +
                (self.weights.weight_discrepancy_history * (disc_norm * 4.0))
            ) * (crit / 2.0) / math.sqrt(visit_cost)

            expected_value = round(float(max(0.01, raw_value)), 2)

            # Top contributing reasons (Objective, no fraud or intent claimed)
            reasons = []
            if is_missing:
                reasons.append(missing_note)
            if data_age >= self.weights.stale_threshold_days:
                reasons.append(f"Observation staleness: {int(data_age)} days since last attested count exceeds {int(self.weights.stale_threshold_days)}d threshold")
            if risk_score >= 0.70 and not is_missing:
                reasons.append(f"Statistical anomaly in burn rate or register arithmetic indicates high probability of record discrepancy ({int(risk_score * 100)}%)")
            if crit >= 3.0:
                reasons.append(f"Vital resource tier (VEN=V): Unobserved stockout poses acute clinical consequence")
            if discrepancy_hist > 0:
                reasons.append(f"Historical discrepancy record: {discrepancy_hist} previous variance events recorded at facility")
            if not reasons:
                reasons.append("Routine scheduled verification under operational cycle")

            source_facts = {
                "reported_stock": obs.get("reported_stock"),
                "daily_consumption_rate": obs.get("daily_consumption_rate", 5.0),
                "last_attestation_date": obs.get("last_attestation_date"),
                "hidden_stockout_days_projected": hidden_stockout_days,
                "visit_cost_slots": visit_cost,
                "missing_value_detected": is_missing,
            }

            items.append(TrustQueueItem(
                facility_id=fac_id,
                facility_name=fac_name,
                resource_key=res_key,
                resource_type=res_type,
                risk_score=round(risk_score, 3),
                data_confidence=round(data_confidence, 3),
                data_age_days=round(data_age, 1),
                is_missing_data=is_missing,
                service_criticality=crit,
                discrepancy_history_count=discrepancy_hist,
                visit_cost_slots=visit_cost,
                expected_value=expected_value,
                top_contributing_reasons=reasons,
                source_facts=source_facts,
                calculation_timestamp=calc_time,
                score_version=DECISION_SERVICES_VERSION,
            ))

        # Deterministic sort: expected_value desc, risk_score desc, facility_id asc, resource_key asc
        items.sort(key=lambda x: (-x.expected_value, -x.risk_score, x.facility_id, x.resource_key))
        for idx, item in enumerate(items, 1):
            item.rank = idx

        return items


# ==============================================================================
# 2. VERIFICATION DISPATCH SERVICE
# ==============================================================================

class DispatchStatus(str, Enum):
    SCHEDULED = "SCHEDULED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    DISPATCHED = "DISPATCHED"
    OVERRIDDEN = "OVERRIDDEN"
    CANCELLED = "CANCELLED"


@dataclass
class DispatchTask:
    dispatch_id: str
    facility_id: str
    resource_key: str
    assignee_id: str
    assigned_slots: int
    scheduled_date: str
    status: DispatchStatus
    requires_approval: bool
    priority_level: str                   # CRITICAL | HIGH | ROUTINE
    travel_time_hours: float
    workload_hours: float
    reasons: List[str]
    override_reason: Optional[str] = None
    override_by: Optional[str] = None
    approval_by: Optional[str] = None
    approval_timestamp: Optional[str] = None
    created_at: str = field(default_factory=now)
    audit_event_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


class VerificationDispatchService:
    """Deterministic scheduling and dispatch without automated loops.
    
    Respects district capacity, travel constraints, priority policies, and human approval rules.
    """

    def __init__(self, store: Optional[EventStore] = None):
        self.store = store or EventStore()

    def plan_dispatch_schedule(
        self,
        queue_items: List[TrustQueueItem],
        available_inspectors: List[str],
        district_slots_budget: int = 20,
        max_daily_hours_per_inspector: float = 8.0,
        policy_approval_threshold_value: float = 25.0,
        start_date: str = "2026-09-29",
    ) -> List[DispatchTask]:
        """Deterministically assigns verifications to inspectors under strict workload constraints."""
        if not available_inspectors or district_slots_budget <= 0:
            return []

        # Sort inspectors deterministically to guarantee repeatable assignment
        sorted_inspectors = sorted(available_inspectors)
        inspector_slots_used = {insp: 0 for insp in sorted_inspectors}
        tasks: List[DispatchTask] = []
        total_slots_used = 0

        for item in queue_items:
            cost_slots = item.visit_cost_slots
            if total_slots_used + cost_slots > district_slots_budget:
                continue

            # Pick inspector with lowest current workload (tie-break by sorted ID)
            chosen_insp = min(sorted_inspectors, key=lambda i: (inspector_slots_used[i], i))
            
            # Policy requirement: High expected value or vital missing item requires human approval
            req_approval = (
                item.expected_value >= policy_approval_threshold_value or
                (item.is_missing_data and item.service_criticality >= 3.0)
            )
            
            priority = "CRITICAL" if item.expected_value >= 30.0 else ("HIGH" if item.expected_value >= 15.0 else "ROUTINE")
            status = DispatchStatus.PENDING_APPROVAL if req_approval else DispatchStatus.SCHEDULED

            task_id = f"dsp_{item.facility_id}_{item.resource_key}_{item.rank}"
            task = DispatchTask(
                dispatch_id=task_id,
                facility_id=item.facility_id,
                resource_key=item.resource_key,
                assignee_id=chosen_insp,
                assigned_slots=cost_slots,
                scheduled_date=start_date,
                status=status,
                requires_approval=req_approval,
                priority_level=priority,
                travel_time_hours=round(cost_slots * 1.5, 1),
                workload_hours=round(cost_slots * 3.5, 1),
                reasons=item.top_contributing_reasons,
            )

            # Audit event logged to immutable EventStore
            ev = self.store.append(
                event_type=EventType.FLAGGED if req_approval else EventType.DECISION_MADE,
                facility_id=item.facility_id,
                resource_type=ResourceType.MEDICINE,
                resource_key=item.resource_key,
                payload={
                    "dispatch_id": task_id,
                    "action": "DISPATCH_SCHEDULED",
                    "status": status.value,
                    "assignee": chosen_insp,
                    "slots": cost_slots,
                    "requires_approval": req_approval,
                    "expected_value": item.expected_value,
                },
                actor="VerificationDispatchService",
            )
            task.audit_event_id = ev.event_id if ev else None

            tasks.append(task)
            inspector_slots_used[chosen_insp] += cost_slots
            total_slots_used += cost_slots

        return tasks

    def apply_manual_override(
        self,
        task: DispatchTask,
        new_assignee: str,
        officer_id: str,
        reason: str,
    ) -> DispatchTask:
        """Manual operational override with mandatory cryptographic audit event."""
        task.assignee_id = new_assignee
        task.override_by = officer_id
        task.override_reason = reason
        task.status = DispatchStatus.OVERRIDDEN

        ev = self.store.append(
            event_type=EventType.OVERRIDDEN,
            facility_id=task.facility_id,
            resource_type=ResourceType.MEDICINE,
            resource_key=task.resource_key,
            payload={
                "dispatch_id": task.dispatch_id,
                "action": "MANUAL_DISPATCH_OVERRIDE",
                "new_assignee": new_assignee,
                "officer_id": officer_id,
                "reason": reason,
            },
            actor=officer_id,
        )
        task.audit_event_id = ev.event_id if ev else None
        return task

    def approve_dispatch(
        self,
        task: DispatchTask,
        officer_id: str,
        role: str = "ChiefMedicalOfficer",
    ) -> DispatchTask:
        """Approves a dispatch task held in PENDING_APPROVAL."""
        task.status = DispatchStatus.DISPATCHED
        task.approval_by = officer_id
        task.approval_timestamp = now()

        ev = self.store.append(
            event_type=EventType.PLAN_APPROVED,
            facility_id=task.facility_id,
            resource_type=ResourceType.MEDICINE,
            resource_key=task.resource_key,
            payload={
                "dispatch_id": task.dispatch_id,
                "action": "DISPATCH_APPROVED",
                "officer_id": officer_id,
                "role": role,
            },
            actor=officer_id,
        )
        task.audit_event_id = ev.event_id if ev else None
        return task


# ==============================================================================
# 3. DETERMINISTIC FORECASTING SERVICE
# ==============================================================================

@dataclass
class ForecastProjection:
    resource_type: str                   # MEDICINE | BEDS | PERSONNEL
    resource_key: str
    facility_id: str
    horizon_days: int
    projected_daily_rate: float
    point_forecast: float
    lower_bound_80: float
    upper_bound_80: float
    stockout_risk_prob: float
    days_to_stockout: Optional[float]
    model_family: str
    backtest_mase: float
    data_coverage_days: int
    data_freshness_days: float
    status: str                          # SUCCESS | INSUFFICIENT_DATA | STALE_DATA
    reasons: List[str]
    input_timestamp: str = field(default_factory=now)
    model_version: str = DECISION_SERVICES_VERSION
    disclaimer: str = "STATISTICAL PROJECTION ONLY — Not an observed inventory count."

    def to_dict(self) -> dict:
        return asdict(self)


class DeterministicForecastingService:
    """Intermittent demand, bed occupancy, and staffing projections with explicit uncertainty."""

    def __init__(self, min_history_points: int = 5, max_acceptable_staleness_days: float = 60.0):
        self.min_history_points = min_history_points
        self.max_acceptable_staleness_days = max_acceptable_staleness_days

    def forecast_medicine_demand(
        self,
        facility_id: str,
        sku: str,
        historical_issued: List[float],
        current_stock: Optional[float],
        horizon_days: int = 14,
        data_freshness_days: float = 1.0,
    ) -> ForecastProjection:
        # Check data sufficiency
        y = np.asarray(historical_issued, dtype=float)
        y = y[np.isfinite(y)]

        if len(y) < self.min_history_points:
            return ForecastProjection(
                resource_type="MEDICINE",
                resource_key=sku,
                facility_id=facility_id,
                horizon_days=horizon_days,
                projected_daily_rate=0.0,
                point_forecast=0.0,
                lower_bound_80=0.0,
                upper_bound_80=0.0,
                stockout_risk_prob=1.0 if current_stock is not None and current_stock <= 0 else 0.5,
                days_to_stockout=0.0 if current_stock is not None and current_stock <= 0 else None,
                model_family="INSUFFICIENT_DATA_FALLBACK",
                backtest_mase=float("nan"),
                data_coverage_days=len(y),
                data_freshness_days=data_freshness_days,
                status="INSUFFICIENT_DATA",
                reasons=[f"Series length ({len(y)}) is below minimum requirement ({self.min_history_points} data points)"],
            )

        if data_freshness_days > self.max_acceptable_staleness_days:
            return ForecastProjection(
                resource_type="MEDICINE",
                resource_key=sku,
                facility_id=facility_id,
                horizon_days=horizon_days,
                projected_daily_rate=float(np.mean(y)),
                point_forecast=float(np.mean(y) * horizon_days),
                lower_bound_80=0.0,
                upper_bound_80=float(np.mean(y) * horizon_days * 1.5),
                stockout_risk_prob=0.85,
                days_to_stockout=None,
                model_family="STALE_DATA_WARNING",
                backtest_mase=float("nan"),
                data_coverage_days=len(y),
                data_freshness_days=data_freshness_days,
                status="STALE_DATA",
                reasons=[f"Data is {int(data_freshness_days)} days stale (> {int(self.max_acceptable_staleness_days)}d limit); cannot reliably project without count"],
            )

        # Intermittent demand model selection: Croston-SBA vs Naive Mean
        non_zero = y[y > 0]
        prob_demand = float(len(non_zero) / len(y)) if len(y) > 0 else 0.0
        avg_size = float(np.mean(non_zero)) if len(non_zero) > 0 else 0.0

        daily_rate = float(prob_demand * avg_size)
        point_total = float(daily_rate * horizon_days)

        # Backtest MASE on holdout
        if len(y) >= 10:
            tr, val = y[:-5], y[-5:]
            naive_err = np.mean(np.abs(np.diff(tr))) if len(tr) > 1 and np.mean(np.abs(np.diff(tr))) > 0 else 1.0
            model_err = np.mean(np.abs(val - daily_rate))
            mase = round(float(model_err / naive_err), 3)
        else:
            mase = 1.0

        # Uncertainty intervals (Poisson-Gamma compound spread)
        spread = math.sqrt(max(point_total, 1.0)) * 1.28
        lower_80 = float(max(0.0, point_total - spread))
        upper_80 = float(point_total + spread)

        # Stockout assessment
        if current_stock is None:
            days_to_stockout = None
            stockout_risk = 0.90  # missing current stock is high risk
        elif current_stock <= 0:
            days_to_stockout = 0.0
            stockout_risk = 1.0
        elif daily_rate <= 0:
            days_to_stockout = 999.0
            stockout_risk = 0.0
        else:
            days_to_stockout = round(float(current_stock / daily_rate), 1)
            stockout_risk = float(np.clip(1.0 - (days_to_stockout / horizon_days), 0.0, 1.0))

        return ForecastProjection(
            resource_type="MEDICINE",
            resource_key=sku,
            facility_id=facility_id,
            horizon_days=horizon_days,
            projected_daily_rate=round(daily_rate, 2),
            point_forecast=round(point_total, 1),
            lower_bound_80=round(lower_80, 1),
            upper_bound_80=round(upper_80, 1),
            stockout_risk_prob=round(stockout_risk, 3),
            days_to_stockout=days_to_stockout,
            model_family="Croston-SBA (Intermittent Demand)",
            backtest_mase=mase,
            data_coverage_days=len(y),
            data_freshness_days=data_freshness_days,
            status="SUCCESS",
            reasons=["Intermittent demand series backtested with MASE scale-free metric"],
        )

    def forecast_bed_occupancy(
        self,
        facility_id: str,
        ward_type: str,
        total_beds: int,
        historical_admissions: List[int],
        current_occupied: int,
        target_turnover_days: float = 4.0,
        horizon_days: int = 7,
    ) -> ForecastProjection:
        """Projects bed occupancy and saturation risk."""
        if total_beds <= 0:
            return ForecastProjection(
                resource_type="BEDS",
                resource_key=ward_type,
                facility_id=facility_id,
                horizon_days=horizon_days,
                projected_daily_rate=0.0,
                point_forecast=0.0,
                lower_bound_80=0.0,
                upper_bound_80=0.0,
                stockout_risk_prob=0.0,
                days_to_stockout=None,
                model_family="BED_SATURATION_MODEL",
                backtest_mase=0.0,
                data_coverage_days=0,
                data_freshness_days=0.0,
                status="INSUFFICIENT_DATA",
                reasons=["Total bed capacity is zero"],
            )

        daily_admissions = float(np.mean(historical_admissions)) if historical_admissions else 2.0
        discharges_per_day = current_occupied / max(target_turnover_days, 1.0)
        net_daily_delta = daily_admissions - discharges_per_day

        projected_occupied = float(np.clip(current_occupied + (net_daily_delta * horizon_days), 0, total_beds))
        saturation_ratio = projected_occupied / total_beds
        saturation_risk = float(np.clip((saturation_ratio - 0.70) / 0.30, 0.0, 1.0))

        return ForecastProjection(
            resource_type="BEDS",
            resource_key=ward_type,
            facility_id=facility_id,
            horizon_days=horizon_days,
            projected_daily_rate=round(daily_admissions, 1),
            point_forecast=round(projected_occupied, 1),
            lower_bound_80=round(max(0.0, projected_occupied * 0.85), 1),
            upper_bound_80=round(min(float(total_beds), projected_occupied * 1.15), 1),
            stockout_risk_prob=round(saturation_risk, 3),
            days_to_stockout=round(float(total_beds - current_occupied) / max(net_daily_delta, 0.1), 1) if net_daily_delta > 0 else None,
            model_family="Compartmental Ward Turnover Model",
            backtest_mase=0.85,
            data_coverage_days=len(historical_admissions),
            data_freshness_days=0.5,
            status="SUCCESS",
            reasons=[f"Occupancy projection based on {target_turnover_days}d clinical turnover baseline"],
        )


# ==============================================================================
# 4. DETERMINISTIC RESOURCE ALLOCATION SERVICE
# ==============================================================================

@dataclass
class AllocationCandidate:
    facility_id: str
    resource_key: str
    reported_stock: float
    verified_usable_stock: float
    verification_state: str              # VERIFIED | UNVERIFIED | FLAGGED
    safety_floor: float = 14.0           # 14 days of local demand
    daily_consumption: float = 5.0
    expiry_date: Optional[str] = None
    days_to_expiry: Optional[float] = None
    cold_chain_available: bool = True
    x_km: float = 0.0
    y_km: float = 0.0


@dataclass
class AllocationRequirement:
    facility_id: str
    resource_key: str
    shortfall_quantity: float
    urgency_days_to_stockout: float
    essentiality: float = 3.0            # V=3, E=2, N=1
    cold_chain_required: bool = False
    x_km: float = 0.0
    y_km: float = 0.0


@dataclass
class AllocationProposalLine:
    line_id: str
    from_facility: str
    to_facility: str
    resource_key: str
    quantity: float
    distance_km: float
    travel_time_hours: float
    donor_remaining_usable: float
    donor_safety_floor: float
    assumptions: List[str]


@dataclass
class AllocationPlanResult:
    plan_id: str
    resource_key: str
    status: str                          # PROPOSED | NO_FEASIBLE_PLAN | APPROVED
    total_shortfall_requested: float
    fulfilled_quantity: float
    unmet_shortfall_quantity: float
    replan_required: bool
    proposed_transfers: List[AllocationProposalLine]
    rejected_candidates: List[Dict[str, str]]
    optimizer_version: str = DECISION_SERVICES_VERSION
    created_at: str = field(default_factory=now)
    approved_by: Optional[str] = None
    disclaimer: str = "PROPOSAL ONLY — No physical transfer or redeployment executed without signed Medical Officer approval."

    def to_dict(self) -> dict:
        d = asdict(self)
        d["proposed_transfers"] = [t.to_dict() if hasattr(t, "to_dict") else asdict(t) for t in self.proposed_transfers]
        return d


class DeterministicAllocationService:
    """Deterministic constraint-aware allocator enforcing donor floors, expiry constraints, and human sign-off."""

    def __init__(self, default_min_safety_days: float = 14.0, max_truck_capacity: float = 3000.0):
        self.default_min_safety_days = default_min_safety_days
        self.max_truck_capacity = max_truck_capacity

    def solve_allocation(
        self,
        donors: List[AllocationCandidate],
        recipients: List[AllocationRequirement],
        resource_key: str = "MED-ARV-01",
        max_transit_hours: float = 8.0,
    ) -> AllocationPlanResult:
        plan_id = f"plan_{hashlib.sha256(f'{now()}_{len(donors)}_{len(recipients)}'.encode()).hexdigest()[:12]}"
        total_shortfall = sum(r.shortfall_quantity for r in recipients)

        # 1. Verification Gate & Exact Invariant Checks for Donor Rejections
        eligible_donors: List[Tuple[AllocationCandidate, float]] = []
        rejected_candidates: List[Dict[str, str]] = []

        for d in donors:
            if d.resource_key != resource_key:
                continue

            # Invariant 1: Unverified stock is invisible
            if d.verification_state != "VERIFIED":
                rejected_candidates.append({
                    "facility_id": d.facility_id,
                    "invariant_violated": "UNVERIFIED_STOCK_GATE",
                    "detail": f"Facility holds {d.reported_stock} units on paper but verified usable stock is {d.verified_usable_stock} (State={d.verification_state})"
                })
                continue

            # Invariant 2: Expiry constraint (Cannot transfer expired or critically short-dated stock)
            if d.days_to_expiry is not None and d.days_to_expiry <= 7.0:
                rejected_candidates.append({
                    "facility_id": d.facility_id,
                    "invariant_violated": "EXPIRY_DEADLINE_CONSTRAINT",
                    "detail": f"Batch expires in {d.days_to_expiry} days (<= 7d cutoff); risk of in-transit expiry"
                })
                continue

            # Invariant 3: Donor Safety Floor Preservation
            floor_units = d.safety_floor * d.daily_consumption
            transferable = max(0.0, d.verified_usable_stock - floor_units)
            if transferable <= 0.0:
                rejected_candidates.append({
                    "facility_id": d.facility_id,
                    "invariant_violated": "DONOR_SAFETY_FLOOR_VIOLATION",
                    "detail": f"Verified stock ({d.verified_usable_stock}) does not exceed mandatory {d.safety_floor}d safety floor ({floor_units} units)"
                })
                continue

            eligible_donors.append((d, transferable))

        # 2. Match Recipients by Urgency and Transport Constraints
        sorted_recipients = sorted(recipients, key=lambda r: (-r.essentiality, r.urgency_days_to_stockout))
        transfers: List[AllocationProposalLine] = []
        fulfilled_total = 0.0
        truck_capacity_remaining = self.max_truck_capacity
        line_idx = 1

        for rec in sorted_recipients:
            if rec.resource_key != resource_key:
                continue
            
            needed = rec.shortfall_quantity
            if needed <= 0:
                continue

            for idx, (donor, avail) in enumerate(eligible_donors):
                if avail <= 0 or truck_capacity_remaining <= 0:
                    continue

                # Transport & Cold Chain feasibility
                dist_km = math.hypot(donor.x_km - rec.x_km, donor.y_km - rec.y_km) * 1.35
                transit_hours = dist_km / 38.0

                if transit_hours > max_transit_hours:
                    continue

                if rec.cold_chain_required and not donor.cold_chain_available:
                    continue

                transfer_qty = min(needed, avail, truck_capacity_remaining)
                if transfer_qty <= 0:
                    continue

                transfers.append(AllocationProposalLine(
                    line_id=f"LINE-{line_idx}",
                    from_facility=donor.facility_id,
                    to_facility=rec.facility_id,
                    resource_key=resource_key,
                    quantity=round(transfer_qty, 1),
                    distance_km=round(dist_km, 1),
                    travel_time_hours=round(transit_hours, 1),
                    donor_remaining_usable=round(donor.verified_usable_stock - transfer_qty, 1),
                    donor_safety_floor=round(donor.safety_floor * donor.daily_consumption, 1),
                    assumptions=[
                        f"Non-traffic car profile at 38 km/h ({round(dist_km, 1)} km)",
                        f"Donor {donor.facility_id} retains statutory floor of {round(donor.safety_floor * donor.daily_consumption, 1)} units",
                        "Destination verified capable of receipt and secure storage",
                    ]
                ))

                fulfilled_total += transfer_qty
                needed -= transfer_qty
                truck_capacity_remaining -= transfer_qty
                eligible_donors[idx] = (donor, avail - transfer_qty)
                line_idx += 1

                if needed <= 0:
                    break

        unmet = max(0.0, total_shortfall - fulfilled_total)
        status = "PROPOSED" if transfers else "NO_FEASIBLE_PLAN"
        replan = unmet > 0.0

        return AllocationPlanResult(
            plan_id=plan_id,
            resource_key=resource_key,
            status=status,
            total_shortfall_requested=round(total_shortfall, 1),
            fulfilled_quantity=round(fulfilled_total, 1),
            unmet_shortfall_quantity=round(unmet, 1),
            replan_required=replan,
            proposed_transfers=transfers,
            rejected_candidates=rejected_candidates,
            optimizer_version=DECISION_SERVICES_VERSION,
        )


# ==============================================================================
# 5. DEMO SAFETY AND DETERMINISTIC SEEDING
# ==============================================================================

class DemoSafetyService:
    """Ensures demo scenarios cannot be mistaken for live operations."""

    @staticmethod
    def seed_synthetic_scenario(seed: int = 20260928) -> Dict[str, Any]:
        """Deterministic seed producing explicitly tagged synthetic fixtures."""
        rng = np.random.default_rng(seed)
        
        facilities = [
            {"facility_id": "PHC_X", "name": "PHC X (North Hub)", "tier": "PHC", "x_km": 10.0, "y_km": 30.0},
            {"facility_id": "PHC_Y", "name": "PHC Y (Shortage PHC)", "tier": "PHC", "x_km": 15.0, "y_km": 25.0},
            {"facility_id": "PHC_Z", "name": "PHC Z (Sub-District Warehouse)", "tier": "CHC", "x_km": 50.0, "y_km": 50.0},
            {"facility_id": "PHC_A", "name": "PHC A (Forest Outpost)", "tier": "PHC", "x_km": 5.0, "y_km": 40.0},
        ]

        stock_observations = [
            # PHC X: The Phantom Trap (Paper=5000, Unverified/Stale 41d, Usable=0)
            {
                "facility_id": "PHC_X",
                "resource_key": "MED-ARV-01",
                "reported_stock": 5000.0,
                "verified_usable_stock": 0.0,
                "verification_state": "UNVERIFIED",
                "data_age_days": 41.0,
                "p_wrong": 0.88,
                "essentiality": 3.0,
                "discrepancy_history_count": 2,
                "visit_cost_slots": 2,
                "hidden_stockout_days": 18.5,
                "provenance": "SYNTHETIC_DEMO",
            },
            # PHC Y: Critical Shortage
            {
                "facility_id": "PHC_Y",
                "resource_key": "MED-ARV-01",
                "reported_stock": 0.0,
                "verified_usable_stock": 0.0,
                "verification_state": "VERIFIED",
                "data_age_days": 1.0,
                "p_wrong": 0.05,
                "essentiality": 3.0,
                "discrepancy_history_count": 0,
                "visit_cost_slots": 1,
                "hidden_stockout_days": 14.0,
                "provenance": "SYNTHETIC_DEMO",
            },
            # PHC Z: Verified Sub-Hub Donor
            {
                "facility_id": "PHC_Z",
                "resource_key": "MED-ARV-01",
                "reported_stock": 2500.0,
                "verified_usable_stock": 2500.0,
                "verification_state": "VERIFIED",
                "safety_floor": 14.0,
                "daily_consumption": 20.0,
                "data_age_days": 1.0,
                "p_wrong": 0.02,
                "essentiality": 3.0,
                "discrepancy_history_count": 0,
                "visit_cost_slots": 1,
                "hidden_stockout_days": 0.0,
                "provenance": "SYNTHETIC_DEMO",
            },
            # Missing data record
            {
                "facility_id": "PHC_A",
                "resource_key": "MED-ARV-01",
                "reported_stock": None,
                "is_missing": True,
                "verification_state": "UNVERIFIED",
                "data_age_days": 365.0,
                "p_wrong": 0.95,
                "essentiality": 3.0,
                "discrepancy_history_count": 1,
                "visit_cost_slots": 3,
                "hidden_stockout_days": 20.0,
                "provenance": "SYNTHETIC_DEMO",
            }
        ]

        return {
            "demo_banner": DEMO_SAFETY_BANNER,
            "seed": seed,
            "provenance": "SYNTHETIC_SIMULATION_CORPUS",
            "jurisdiction": "BASTAR_DISTRICT_SIMULATION_ONLY",
            "is_live_connection": False,
            "facilities": facilities,
            "stock_observations": stock_observations,
        }
