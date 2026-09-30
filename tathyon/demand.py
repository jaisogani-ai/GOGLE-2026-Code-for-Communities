"""
TATHYON Demand Signal Fusion & Emergency Signal Engine.

Two interconnected capabilities:

1. DEMAND SIGNAL FUSION: Combines multiple structured demand components into
   a single forecasted demand with causal explanation. The model explains
   "Demand increased because..." rather than merely outputting a prediction.

   Signal components:
   - historical_consumption: baseline from event store
   - patient_footfall: facility OPD/IPD attendance
   - seasonality: periodic patterns (monsoon, winter, etc.)
   - outbreak_shock: multiplier from active emergency signals
   - emergency_event: declared emergency impact
   - facility_catchment: population pressure
   - recent_acceleration: short-term velocity change
   - shipment_delay: demand redistribution from delayed replenishment

2. EMERGENCY SIGNAL ENGINE: Typed emergency events with structured impact.
   Each event defines: affected_geography, affected_resources, demand_multiplier,
   duration, and uncertainty. No random *3 or *1.25 multipliers — every shock
   is configurable, justified, and documented.

CRITICAL RULE: Multipliers are CONFIGURABLE defaults, not magic numbers.
Every default is justified by public health literature or WHO norms.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .schema import now, ResourceType


# --------------------------------------------------------------------------
# Emergency Signal Types
# --------------------------------------------------------------------------

class EmergencyType(str, Enum):
    """Typed emergency events with operational meaning."""
    DENGUE_SURGE = "DENGUE_SURGE"
    FLOOD = "FLOOD"
    HEATWAVE = "HEATWAVE"
    DOG_BITE_SURGE = "DOG_BITE_SURGE"
    OUTBREAK = "OUTBREAK"
    WAREHOUSE_FAILURE = "WAREHOUSE_FAILURE"
    ROAD_DISRUPTION = "ROAD_DISRUPTION"
    COLD_CHAIN_FAILURE = "COLD_CHAIN_FAILURE"
    MASS_CASUALTY = "MASS_CASUALTY"
    SEASONAL_MONSOON = "SEASONAL_MONSOON"


@dataclass
class EmergencySignal:
    """A structured emergency event with operationally meaningful impact.
    
    Every field is independently auditable:
    - affected_geography: which districts/facilities are hit
    - affected_resources: which resource types see elevated demand
    - demand_multiplier: configurable, not arbitrary
    - duration_days: how long the shock persists
    - uncertainty: how confident we are in the multiplier estimate
    """
    signal_id: str
    emergency_type: EmergencyType
    name: str
    description: str
    affected_geography: list[str]       # district or facility IDs
    affected_resources: list[str]       # resource IDs or resource type names
    demand_multiplier: float            # 1.0 = no change, 2.0 = double demand
    duration_days: float                # expected duration of the shock
    uncertainty: float                  # 0.0 = certain, 1.0 = highly uncertain
    source: str = "OPERATOR_DECLARED"   # who/what declared this
    is_active: bool = True
    declared_at: str = field(default_factory=now)
    expires_at: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["emergency_type"] = self.emergency_type.value
        return d


# Default emergency signal templates with justified multipliers
EMERGENCY_TEMPLATES: dict[EmergencyType, dict] = {
    EmergencyType.DENGUE_SURGE: {
        "name": "Dengue Fever Surge",
        "description": "Acute dengue outbreak with elevated case load requiring anti-pyretics, "
                       "IV fluids, platelet monitoring, and ORS.",
        "affected_resources": ["MED_PARACETAMOL", "MED_ORS", "MED_IV_FLUID", "BED_ICU_OXYGEN"],
        "demand_multiplier": 2.5,
        "duration_days": 21,
        "uncertainty": 0.3,
        "justification": "WHO dengue surge planning: 2-3x baseline OPD during outbreak peak. "
                         "India NVBDCP historical data shows 2-4x case multiplier in endemic districts.",
    },
    EmergencyType.FLOOD: {
        "name": "Flood Emergency",
        "description": "Flooding disrupts road access, contaminates water supply, increases "
                       "diarrhoeal disease and waterborne infections.",
        "affected_resources": ["MED_ORS", "MED_ANTIBIOTICS", "MED_IV_FLUID", "STAFF_NURSE_TRAINED"],
        "demand_multiplier": 1.8,
        "duration_days": 14,
        "uncertainty": 0.5,
        "justification": "Flood-affected populations show 1.5-2.5x increase in acute diarrhoeal disease "
                         "(WHO WASH guidance). Road disruption doubles effective lead time.",
    },
    EmergencyType.HEATWAVE: {
        "name": "Heatwave Emergency",
        "description": "Extreme heat event increasing heatstroke, dehydration, and cold-chain risk.",
        "affected_resources": ["MED_ORS", "MED_IV_FLUID", "BED_ICU_OXYGEN"],
        "demand_multiplier": 1.5,
        "duration_days": 7,
        "uncertainty": 0.2,
        "justification": "IMD heatwave data: 1.3-1.8x emergency admissions. Cold-chain stress may "
                         "increase vaccine/oxytocin expiry rates.",
    },
    EmergencyType.DOG_BITE_SURGE: {
        "name": "Dog Bite / Rabies Exposure Surge",
        "description": "Cluster of animal bite cases requiring post-exposure prophylaxis (PEP). "
                       "Anti-rabies vaccine (ARV) is life-critical and time-sensitive.",
        "affected_resources": ["MED_ANTI_RABIES_VACCINE"],
        "demand_multiplier": 3.0,
        "duration_days": 28,
        "uncertainty": 0.4,
        "justification": "India reports ~17.4M animal bites/year (APCRI). Regional surges of 2-4x "
                         "baseline are documented in tribal and rural districts during monsoon.",
    },
    EmergencyType.OUTBREAK: {
        "name": "Generic Outbreak",
        "description": "Notified disease outbreak with generalized demand increase across essential medicines.",
        "affected_resources": ["MED_ANTIBIOTICS", "MED_IV_FLUID", "MED_ORS", "MED_PARACETAMOL"],
        "demand_multiplier": 2.0,
        "duration_days": 21,
        "uncertainty": 0.5,
        "justification": "Generic outbreak multiplier based on IDSP weekly reports. Specific diseases "
                         "should use typed signals (DENGUE_SURGE, etc.) for precision.",
    },
    EmergencyType.WAREHOUSE_FAILURE: {
        "name": "Warehouse / Depot Failure",
        "description": "District or state warehouse becomes unavailable (fire, flood, contamination, "
                       "cold-chain collapse). All downstream facilities lose their supply source.",
        "affected_resources": [],  # Affects ALL resources from that warehouse
        "demand_multiplier": 1.0,  # Demand doesn't change, SUPPLY does
        "duration_days": 14,
        "uncertainty": 0.3,
        "justification": "Supply-side shock: demand stays constant but replenishment drops to zero "
                         "for dependent facilities. Lead time effectively becomes infinite.",
    },
    EmergencyType.ROAD_DISRUPTION: {
        "name": "Road / Route Disruption",
        "description": "Major road disruption (landslide, bridge collapse, flood) blocks primary "
                       "supply route. Alternative routes may exist but with longer transit time.",
        "affected_resources": [],  # Affects transit, not demand directly
        "demand_multiplier": 1.0,
        "duration_days": 7,
        "uncertainty": 0.4,
        "justification": "Does not change demand but doubles or triples effective transit time, "
                         "making replenishment lead time the critical variable.",
    },
    EmergencyType.COLD_CHAIN_FAILURE: {
        "name": "Cold Chain Failure",
        "description": "Cold chain equipment failure at facility. All cold-chain-dependent stock "
                       "(vaccines, oxytocin) becomes quarantined pending assessment.",
        "affected_resources": ["MED_ANTI_RABIES_VACCINE", "MED_OXYTOCIN_INJ"],
        "demand_multiplier": 1.0,  # Demand constant, usable stock drops
        "duration_days": 3,
        "uncertainty": 0.2,
        "justification": "WHO cold chain guidelines: stock exposed to temperature excursion must "
                         "be quarantined until VVM/shake test assessment. Effective stockout for affected SKUs.",
    },
    EmergencyType.MASS_CASUALTY: {
        "name": "Mass Casualty Event",
        "description": "Industrial accident, transportation disaster, or stampede generating sudden "
                       "surge in trauma/emergency demand.",
        "affected_resources": ["MED_IV_FLUID", "MED_ANTIBIOTICS", "BED_ICU_OXYGEN", "EQ_VENT_ICU",
                               "STAFF_NURSE_TRAINED"],
        "demand_multiplier": 4.0,
        "duration_days": 3,
        "uncertainty": 0.6,
        "justification": "Mass casualty surge: 3-5x emergency capacity demand in first 72 hours. "
                         "WHO mass casualty management guidelines.",
    },
    EmergencyType.SEASONAL_MONSOON: {
        "name": "Monsoon Season",
        "description": "Predictable seasonal increase in waterborne disease, malaria, dengue, and "
                       "road disruption during Indian monsoon (June-September).",
        "affected_resources": ["MED_ORS", "MED_ANTIBIOTICS", "MED_PARACETAMOL"],
        "demand_multiplier": 1.4,
        "duration_days": 90,
        "uncertainty": 0.2,
        "justification": "NVBDCP/IDSP data: 1.3-1.6x increase in OPD for acute gastroenteritis, "
                         "malaria, and vector-borne diseases during monsoon months.",
    },
}


# --------------------------------------------------------------------------
# Demand Signal Fusion
# --------------------------------------------------------------------------

@dataclass
class DemandSignalComponent:
    """A single structured demand signal with causal attribution."""
    signal_type: str
    value: float           # Contribution in units/day
    weight: float          # Weight in the fusion (0.0 to 1.0)
    explanation: str       # "Demand increased because..."
    source: str = "DERIVED"
    confidence: float = 0.8

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FusedDemandForecast:
    """The output of demand signal fusion: a single forecasted demand rate
    with full causal decomposition.
    
    Every component explains WHY demand is at its current level.
    The fused rate is NOT a black box.
    """
    facility_id: str
    resource_id: str
    fused_daily_rate: float             # Final units/day
    baseline_daily_rate: float          # Historical consumption baseline
    total_multiplier: float             # Effective combined multiplier
    components: list[DemandSignalComponent]
    active_emergencies: list[str]       # Emergency signal IDs affecting this forecast
    explanation: str                    # "Demand increased because..."
    horizon_days: int = 14
    total_demand_over_horizon: float = 0.0
    as_of: str = field(default_factory=now)
    provenance: str = "SIMULATION"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["components"] = [c.to_dict() for c in self.components]
        return d


class DemandFusionEngine:
    """Fuses multiple demand signals into an explainable forecast.
    
    The engine does NOT rely only on historical consumption. It combines:
    1. Historical consumption (baseline)
    2. Patient footfall (if available)
    3. Seasonality adjustment
    4. Active emergency signals
    5. Recent velocity acceleration
    6. Shipment delay pressure
    
    And explains: "Demand is X because of Y, Z, and W."
    """

    def __init__(self):
        self.active_emergencies: list[EmergencySignal] = []

    def declare_emergency(self, signal: EmergencySignal) -> None:
        """Declare an active emergency signal."""
        self.active_emergencies.append(signal)

    def clear_emergency(self, signal_id: str) -> None:
        """Deactivate an emergency signal."""
        self.active_emergencies = [
            s for s in self.active_emergencies if s.signal_id != signal_id
        ]

    def fuse_demand(
        self,
        facility_id: str,
        resource_id: str,
        baseline_velocity: float,
        district: str = "",
        patient_footfall_ratio: float = 1.0,
        seasonality_factor: float = 1.0,
        recent_velocity: Optional[float] = None,
        shipment_delayed: bool = False,
        shipment_delay_days: float = 0.0,
        horizon_days: int = 14,
    ) -> FusedDemandForecast:
        """Fuses demand signals and produces explainable forecast."""
        components: list[DemandSignalComponent] = []
        reasons: list[str] = []
        active_ids: list[str] = []

        # 1. Baseline historical consumption
        components.append(DemandSignalComponent(
            signal_type="historical_consumption",
            value=baseline_velocity,
            weight=1.0,
            explanation=f"Historical consumption baseline: {baseline_velocity:.1f} units/day.",
            source="EVENT_STORE",
        ))

        effective_multiplier = 1.0

        # 2. Patient footfall adjustment
        if patient_footfall_ratio != 1.0:
            foot_adj = patient_footfall_ratio
            effective_multiplier *= foot_adj
            components.append(DemandSignalComponent(
                signal_type="patient_footfall",
                value=baseline_velocity * (foot_adj - 1.0),
                weight=0.8,
                explanation=f"Patient footfall is {foot_adj:.1f}x normal, "
                            f"{'increasing' if foot_adj > 1 else 'decreasing'} expected demand.",
                source="FACILITY_MIS",
                confidence=0.7,
            ))
            if foot_adj > 1.1:
                reasons.append(f"patient footfall is {foot_adj:.0%} of normal")

        # 3. Seasonality
        if seasonality_factor != 1.0:
            effective_multiplier *= seasonality_factor
            components.append(DemandSignalComponent(
                signal_type="seasonality",
                value=baseline_velocity * (seasonality_factor - 1.0),
                weight=0.7,
                explanation=f"Seasonal adjustment factor {seasonality_factor:.2f}x "
                            f"({'elevated' if seasonality_factor > 1 else 'reduced'} demand period).",
                source="HISTORICAL_PATTERN",
                confidence=0.85,
            ))
            if seasonality_factor > 1.1:
                reasons.append(f"seasonal demand is elevated ({seasonality_factor:.0%}x)")

        # 4. Active emergency signals
        for signal in self.active_emergencies:
            if not signal.is_active:
                continue
            # Check if this emergency affects this resource
            resource_match = (
                not signal.affected_resources
                or resource_id in signal.affected_resources
                or any(r in resource_id for r in signal.affected_resources)
            )
            # Check geography
            geo_match = (
                not signal.affected_geography
                or district in signal.affected_geography
                or facility_id in signal.affected_geography
            )
            if resource_match and geo_match:
                effective_multiplier *= signal.demand_multiplier
                active_ids.append(signal.signal_id)
                components.append(DemandSignalComponent(
                    signal_type="emergency_event",
                    value=baseline_velocity * (signal.demand_multiplier - 1.0),
                    weight=1.0 - signal.uncertainty,
                    explanation=f"EMERGENCY: {signal.name} — {signal.description} "
                                f"Demand multiplier {signal.demand_multiplier:.1f}x "
                                f"(uncertainty: {signal.uncertainty:.0%}).",
                    source=signal.source,
                    confidence=1.0 - signal.uncertainty,
                ))
                reasons.append(f"active {signal.emergency_type.value} emergency "
                               f"(multiplier {signal.demand_multiplier:.1f}x)")

        # 5. Recent velocity acceleration
        if recent_velocity is not None and recent_velocity > baseline_velocity * 1.1:
            accel_ratio = recent_velocity / max(baseline_velocity, 0.1)
            # Blend recent velocity into the multiplier
            accel_weight = min((accel_ratio - 1.0) * 0.5, 0.5)
            effective_multiplier *= (1.0 + accel_weight)
            components.append(DemandSignalComponent(
                signal_type="recent_acceleration",
                value=baseline_velocity * accel_weight,
                weight=0.6,
                explanation=f"Recent consumption ({recent_velocity:.1f}/day) is "
                            f"{accel_ratio:.1f}x baseline ({baseline_velocity:.1f}/day). "
                            f"Short-term acceleration detected.",
                source="RECENT_OBSERVATIONS",
                confidence=0.65,
            ))
            reasons.append(f"recent consumption accelerated to {recent_velocity:.1f}/day")

        # 6. Shipment delay pressure
        if shipment_delayed and shipment_delay_days > 0:
            # Delayed shipment means the facility must stretch existing stock longer
            components.append(DemandSignalComponent(
                signal_type="shipment_delay",
                value=0.0,  # Doesn't change demand, but changes effective lead time
                weight=0.5,
                explanation=f"Shipment delayed by {shipment_delay_days:.0f} days. "
                            f"Effective lead time extended. Existing stock must cover "
                            f"additional {shipment_delay_days:.0f} days.",
                source="SHIPMENT_TRACKING",
                confidence=0.9,
            ))
            reasons.append(f"shipment delayed by {shipment_delay_days:.0f} days")

        fused_rate = round(baseline_velocity * effective_multiplier, 2)
        total_demand = round(fused_rate * horizon_days, 1)

        if reasons:
            explanation = f"Demand increased to {fused_rate:.1f} units/day because: " + "; ".join(reasons) + "."
        else:
            explanation = (
                f"Demand stable at {fused_rate:.1f} units/day based on historical "
                f"consumption. No active modifiers detected."
            )

        return FusedDemandForecast(
            facility_id=facility_id,
            resource_id=resource_id,
            fused_daily_rate=fused_rate,
            baseline_daily_rate=baseline_velocity,
            total_multiplier=round(effective_multiplier, 3),
            components=components,
            active_emergencies=active_ids,
            explanation=explanation,
            horizon_days=horizon_days,
            total_demand_over_horizon=total_demand,
        )
