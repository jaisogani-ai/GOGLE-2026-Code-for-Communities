"""
TATHYON Scenario Builder, Failure Injector & Final Demo.

Provides three capabilities:

1. FAILURE INJECTOR: Named failure modes that run through SAME demonstration
   code paths. Not separate demo code — actual demonstration logic handling
   actual operational failures.

2. SCENARIO BUILDER: Operator constructs resource + location + shock +
   duration + multiplier + warehouse state + route state, then runs the
   twin engine. Custom scenarios without code changes.

3. FINAL DEMO SCENARIO: The complete 16-step district outbreak demonstrating
   the entire causal loop from emergency signal through outcome feedback.

CRITICAL RULE: Every scenario runs through demonstration code. There is NO
separate demo pathway. If the demo breaks, demonstration is broken too.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .graph import HealthcareResourceGraph, ResourceState
from .planner import PlanExplorer, ResponsePlanner
from .schema import EventType, FacilityType, ResourceType, new_id, now
from .store import EventStore
from .twin import EmergencyResilienceScenarioEngine, ShockType


# --------------------------------------------------------------------------
# 1. FAILURE INJECTOR
# --------------------------------------------------------------------------

class FailureMode(str, Enum):
    """Named failure modes that exercise demonstration code paths."""
    LEDGER_WRONG = "LEDGER_WRONG"               # Digital claim diverges from physical
    STOCK_EXPIRED = "STOCK_EXPIRED"             # Expired stock not withdrawn
    ATTESTATION_STALE = "ATTESTATION_STALE"     # Observation too old to trust
    WAREHOUSE_DOWN = "WAREHOUSE_DOWN"           # Supply hub offline
    SHIPMENT_DELAYED = "SHIPMENT_DELAYED"       # Replenishment arrives late
    DEMAND_SURGE = "DEMAND_SURGE"               # Consumption velocity spikes
    ROUTE_BLOCKED = "ROUTE_BLOCKED"             # Road/route unavailable
    DONOR_BELOW_FLOOR = "DONOR_BELOW_FLOOR"     # All donors below safety floor
    NO_FEASIBLE_DONOR = "NO_FEASIBLE_DONOR"     # No donor can help
    COLD_CHAIN_BREAK = "COLD_CHAIN_BREAK"       # Cold chain failure quarantines stock


FAILURE_DESCRIPTIONS = {
    FailureMode.LEDGER_WRONG: (
        "Digital claim says 500 units. Physical count reveals 180. "
        "The 320-unit phantom inventory CANNOT be moved, sold, or consumed."
    ),
    FailureMode.STOCK_EXPIRED: (
        "Expired batches remain on shelf. Claimed quantity includes them. "
        "Usable quantity is lower than what the ledger shows."
    ),
    FailureMode.ATTESTATION_STALE: (
        "Last physical attestation is >72 hours old. Stock state cannot be trusted. "
        "Facility is NOT eligible as a donor until re-attested."
    ),
    FailureMode.WAREHOUSE_DOWN: (
        "District warehouse is offline (flood, fire, contamination). "
        "Zero supply dispatch to all dependent PHCs."
    ),
    FailureMode.SHIPMENT_DELAYED: (
        "Expected shipment is delayed by 7+ days. Facility must survive "
        "on current stock longer than planned."
    ),
    FailureMode.DEMAND_SURGE: (
        "Consumption velocity has doubled. Stock runway halved. "
        "Stockout imminent if no action taken."
    ),
    FailureMode.ROUTE_BLOCKED: (
        "Primary supply route is blocked (landslide, flood, construction). "
        "Alternative routes exist but with 2-3x transit time."
    ),
    FailureMode.DONOR_BELOW_FLOOR: (
        "Nearby facilities have stock but ALL are below safety floor. "
        "No facility can donate without endangering its own patients."
    ),
    FailureMode.NO_FEASIBLE_DONOR: (
        "No facility within reachable distance has verified usable surplus. "
        "Inter-district escalation required; supply from outside the district is a state decision."
    ),
    FailureMode.COLD_CHAIN_BREAK: (
        "Cold chain equipment failed. All cold-chain-dependent stock "
        "(vaccines, oxytocin) is quarantined pending assessment."
    ),
}


class FailureInjector:
    """Injects named failure modes into the demonstration graph.
    
    CRITICAL: These mutations run through the SAME code paths as demonstration.
    If the injector breaks something, it means demonstration can break the same way.
    """

    def __init__(self, graph: HealthcareResourceGraph, store: EventStore):
        self.graph = graph
        self.store = store

    def inject(
        self,
        failure: FailureMode | str,
        facility_id: str,
        resource_id: str,
        magnitude: float = 1.0,
    ) -> dict:
        """Injects a failure into the graph and returns the before/after state."""
        if isinstance(failure, str):
            failure = FailureMode(failure)

        state = self.graph.get_resource_state(facility_id, resource_id)
        if not state:
            return {
                "status": "FACILITY_NOT_FOUND",
                "failure": failure.value,
                "facility_id": facility_id,
                "resource_id": resource_id,
            }

        before = {
            "claimed": state.claimed_quantity,
            "usable": state.usable_quantity,
            "velocity": state.consumption_velocity,
            "lead_time": state.lead_time,
            "expired": state.expired_quantity,
            "quarantined": state.quarantined_quantity,
            "risk": state.risk,
        }

        # Apply failure mutation
        if failure == FailureMode.LEDGER_WRONG:
            # Digital claim stays high, usable drops
            state.usable_quantity = max(state.claimed_quantity * 0.35 * magnitude, 0.0)
            state.observed_quantity = state.usable_quantity

        elif failure == FailureMode.STOCK_EXPIRED:
            expired_qty = state.claimed_quantity * 0.30 * magnitude
            state.expired_quantity = round(expired_qty, 1)
            state.usable_quantity = max(state.claimed_quantity - expired_qty, 0.0)

        elif failure == FailureMode.ATTESTATION_STALE:
            # Make attestation 96 hours old
            state.last_attested_at = None  # Force stale

        elif failure == FailureMode.WAREHOUSE_DOWN:
            state.usable_quantity = 0.0
            state.incoming_quantity = 0.0
            state.observed_quantity = 0.0

        elif failure == FailureMode.SHIPMENT_DELAYED:
            state.lead_time = state.lead_time * (2.0 + magnitude)
            state.incoming_quantity = 0.0

        elif failure == FailureMode.DEMAND_SURGE:
            state.consumption_velocity *= (2.0 * magnitude)

        elif failure == FailureMode.ROUTE_BLOCKED:
            state.lead_time *= 3.0

        elif failure == FailureMode.DONOR_BELOW_FLOOR:
            # Set usable to just below safety floor
            safety = state.consumption_velocity * 14.0  # 14 day floor
            state.usable_quantity = max(safety * 0.8, 0.0)

        elif failure == FailureMode.NO_FEASIBLE_DONOR:
            state.usable_quantity = 0.0

        elif failure == FailureMode.COLD_CHAIN_BREAK:
            quarantine_qty = state.usable_quantity * 0.60 * magnitude
            state.quarantined_quantity = round(quarantine_qty, 1)
            state.usable_quantity = max(state.usable_quantity - quarantine_qty, 0.0)

        # Recalculate risk
        days = state.days_of_usable_stock
        state.risk = (
            "CRITICAL" if days <= 1.0 else
            "HIGH" if days <= 3.0 else
            "MEDIUM" if days <= 7.0 else "LOW"
        )

        after = {
            "claimed": state.claimed_quantity,
            "usable": state.usable_quantity,
            "velocity": state.consumption_velocity,
            "lead_time": state.lead_time,
            "expired": state.expired_quantity,
            "quarantined": state.quarantined_quantity,
            "risk": state.risk,
        }

        # Record the injection event
        self.store.append(
            event_type=EventType.OBSERVATION,
            facility_id=facility_id,
            resource_type=ResourceType.MEDICINE,
            resource_key=resource_id,
            payload={
                "event": "FAILURE_INJECTION",
                "failure_mode": failure.value,
                "description": FAILURE_DESCRIPTIONS[failure],
                "before": before,
                "after": after,
            },
            actor="FailureInjector",
        )

        return {
            "status": "INJECTED",
            "failure": failure.value,
            "description": FAILURE_DESCRIPTIONS[failure],
            "facility_id": facility_id,
            "resource_id": resource_id,
            "before": before,
            "after": after,
            "provenance": "SIMULATION",
        }


# --------------------------------------------------------------------------
# 2. SCENARIO BUILDER
# --------------------------------------------------------------------------

@dataclass
class ScenarioSpec:
    """An operator-defined scenario specification."""
    name: str
    description: str
    resource_id: str
    target_district: str
    shock_type: str                     # ShockType value or custom
    demand_multiplier: float = 1.0
    lead_time_multiplier: float = 1.0
    warehouse_available: bool = True
    route_available: bool = True
    horizon_days: int = 14
    facility_failures: list[dict] = field(default_factory=list)
    # Each failure: {"facility_id": str, "failure_mode": str, "magnitude": float}

    def to_dict(self) -> dict:
        return asdict(self)


class ScenarioBuilder:
    """Builds and runs custom scenarios through the demonstration twin engine."""

    def __init__(self, graph: HealthcareResourceGraph, store: EventStore):
        self.graph = graph
        self.store = store

    def build_and_run(self, spec: ScenarioSpec) -> dict:
        """Builds a custom scenario and runs it through the twin engine."""
        # Deep copy the graph for scenario simulation
        sim_graph = copy.deepcopy(self.graph)
        sim_store = self.store  # Share store for event recording

        injector = FailureInjector(sim_graph, sim_store)
        injection_results = []

        # Apply facility-specific failures
        for failure_spec in spec.facility_failures:
            fid = failure_spec.get("facility_id", "")
            mode = failure_spec.get("failure_mode", "")
            mag = failure_spec.get("magnitude", 1.0)
            result = injector.inject(mode, fid, spec.resource_id, mag)
            injection_results.append(result)

        # Apply global scenario mutations
        for (fid, rid), state in sim_graph.states.items():
            if rid != spec.resource_id:
                continue
            fac = sim_graph.facilities.get(fid)
            if not fac:
                continue

            # Demand multiplier
            if spec.demand_multiplier != 1.0:
                if not spec.target_district or fac.district == spec.target_district:
                    state.consumption_velocity *= spec.demand_multiplier

            # Lead time multiplier
            if spec.lead_time_multiplier != 1.0:
                state.lead_time *= spec.lead_time_multiplier

            # Warehouse availability
            if not spec.warehouse_available:
                if fac.facility_type in (FacilityType.DISTRICT_WAREHOUSE, FacilityType.STATE_WAREHOUSE):
                    state.usable_quantity = 0.0
                    state.incoming_quantity = 0.0

            # Route availability
            if not spec.route_available:
                state.lead_time *= 3.0  # Triple transit time

        # Run twin on the mutated graph
        twin = EmergencyResilienceScenarioEngine(sim_graph, sim_store)

        # Map custom shock to ShockType if possible
        try:
            shock = ShockType(spec.shock_type)
        except ValueError:
            shock = ShockType.DEMAND_PLUS_50  # fallback

        twin_result = twin.run_simulation(
            shock=shock,
            resource_id=spec.resource_id,
            horizon_days=spec.horizon_days,
            target_district=spec.target_district,
        )

        # Run plan explorer
        explorer = PlanExplorer(sim_graph, sim_store)
        exploration = explorer.explore_plans(
            resource_id=spec.resource_id,
            min_safety_days=float(spec.horizon_days),
        )

        return {
            "scenario_name": spec.name,
            "description": spec.description,
            "specification": spec.to_dict(),
            "injection_results": injection_results,
            "twin_result": twin_result.to_dict(),
            "plan_exploration": exploration,
            "provenance": "SIMULATION",
            "as_of": now(),
        }


# --------------------------------------------------------------------------
# 3. FINAL DEMO SCENARIO: 16-Step District Outbreak
# --------------------------------------------------------------------------

def run_final_demo(graph: HealthcareResourceGraph, store: EventStore) -> dict:
    """The complete 16-step district outbreak demonstrating the full causal loop.
    
    1. OBSERVE: Facility reports stock levels
    2. RECONCILE: Compare claimed vs usable
    3. DETECT PHANTOM: Identify claim-reality gap
    4. FORECAST: Project demand forward
    5. SIGNAL: Declare dengue outbreak emergency
    6. FUSE DEMAND: Combine signals into elevated forecast
    7. PREDICT STOCKOUT: Calculate when stockout occurs
    8. ASSESS RISK: Grade facility risk
    9. IDENTIFY DONORS: Find verified surplus facilities
    10. TWIN DO-NOTHING: What happens without intervention
    11. TWIN WITH PLAN: What happens with redistribution
    12. GENERATE PLANS: Create multiple candidate plans
    13. PRESENT TRADEOFFS: Show officer the comparison
    14. HUMAN APPROVAL: Officer selects and approves
    15. EXECUTE: Generate SOR payload for dispatch
    16. RECORD OUTCOME: Close the loop with delivery data
    
    This runs through demonstration CODE. If it breaks, demonstration is broken.
    """
    from .demand import DemandFusionEngine, EmergencySignal, EmergencyType
    from .intelligence import DiscrepancyDetector, compute_health_profile

    steps: list[dict] = []

    # Setup: pick first available facility-resource pair with real data
    target_resource = None
    target_facility = None
    for (fid, rid), state in graph.states.items():
        if state.consumption_velocity > 0 and state.usable_quantity > 0:
            target_resource = rid
            target_facility = fid
            break

    if not target_resource or not target_facility:
        return {"error": "No active facility-resource pair found in graph."}

    fac = graph.facilities.get(target_facility)
    state = graph.get_resource_state(target_facility, target_resource)

    # STEP 1: OBSERVE
    steps.append({
        "step": 1, "name": "OBSERVE",
        "description": f"Facility {fac.name if fac else target_facility} reports current stock.",
        "data": {
            "facility_id": target_facility,
            "resource_id": target_resource,
            "claimed": state.claimed_quantity,
            "usable": state.usable_quantity,
            "velocity": state.consumption_velocity,
        }
    })

    # STEP 2: RECONCILE
    phantom = state.phantom_inventory
    steps.append({
        "step": 2, "name": "RECONCILE",
        "description": "Compare claimed vs usable inventory.",
        "data": {
            "claimed": state.claimed_quantity,
            "usable": state.usable_quantity,
            "phantom": phantom,
            "reconciliation_status": "GAP_DETECTED" if phantom > 0 else "RECONCILED",
        }
    })

    # STEP 3: DETECT PATTERNS
    detector = DiscrepancyDetector()
    records = detector.record_observation(
        target_facility, target_resource,
        claimed=state.claimed_quantity,
        usable=state.usable_quantity,
        expired=state.expired_quantity,
    )
    steps.append({
        "step": 3, "name": "DETECT_PATTERNS",
        "description": "Run discrepancy detection on observation.",
        "data": {
            "immediate_discrepancies": len(records),
            "details": [r.to_dict() for r in records],
        }
    })

    # STEP 4: FORECAST
    days_stock = state.days_of_usable_stock
    steps.append({
        "step": 4, "name": "FORECAST",
        "description": f"Project demand forward. Current runway: {days_stock:.1f} days.",
        "data": {
            "days_of_stock": days_stock,
            "velocity": state.consumption_velocity,
            "forecast_demand_14d": round(state.consumption_velocity * 14, 1),
        }
    })

    # STEP 5: DECLARE EMERGENCY
    district = fac.district if fac else "Central"
    steps.append({
        "step": 5, "name": "SIGNAL_EMERGENCY",
        "description": f"Dengue outbreak declared in {district}.",
        "data": {
            "emergency_type": "DENGUE_SURGE",
            "district": district,
            "demand_multiplier": 2.5,
        }
    })

    # STEP 6: FUSE DEMAND
    engine = DemandFusionEngine()
    engine.declare_emergency(EmergencySignal(
        signal_id="DEMO-DENGUE-001",
        emergency_type=EmergencyType.DENGUE_SURGE,
        name="Demo Dengue Outbreak",
        description="Simulated dengue surge for demo",
        affected_geography=[district],
        affected_resources=[target_resource],
        demand_multiplier=2.5,
        duration_days=21,
        uncertainty=0.3,
    ))
    fused = engine.fuse_demand(
        target_facility, target_resource,
        baseline_velocity=state.consumption_velocity,
        district=district,
    )
    steps.append({
        "step": 6, "name": "FUSE_DEMAND",
        "description": "Combine signals into elevated forecast.",
        "data": {
            "baseline_rate": fused.baseline_daily_rate,
            "fused_rate": fused.fused_daily_rate,
            "multiplier": fused.total_multiplier,
            "explanation": fused.explanation,
        }
    })

    # STEP 7: PREDICT STOCKOUT
    elevated_days = state.usable_quantity / max(fused.fused_daily_rate, 0.1)
    steps.append({
        "step": 7, "name": "PREDICT_STOCKOUT",
        "description": f"At elevated demand, stockout in {elevated_days:.1f} days.",
        "data": {
            "days_to_stockout_baseline": days_stock,
            "days_to_stockout_elevated": round(elevated_days, 1),
            "acceleration_factor": fused.total_multiplier,
        }
    })

    # STEP 8: ASSESS RISK
    health = compute_health_profile(state)
    steps.append({
        "step": 8, "name": "ASSESS_RISK",
        "description": f"Facility health: {health.overall_status}.",
        "data": health.to_dict(),
    })

    # STEP 9: IDENTIFY DONORS
    donors = graph.get_donor_inventory(target_resource, min_safety_days=14.0)
    steps.append({
        "step": 9, "name": "IDENTIFY_DONORS",
        "description": f"Found {len(donors)} verified donor facilities.",
        "data": {
            "donors_count": len(donors),
            "donors": [{"id": d["facility_id"], "name": d["facility_name"],
                        "surplus": d["surplus_transferable"]} for d in donors[:5]],
        }
    })

    # STEP 10: TWIN DO-NOTHING
    twin = EmergencyResilienceScenarioEngine(graph, store)
    twin_result = twin.run_simulation(
        shock=ShockType.DEMAND_PLUS_100,
        resource_id=target_resource,
        target_district=district,
    )
    steps.append({
        "step": 10, "name": "TWIN_DO_NOTHING",
        "description": "Simulate: what happens without intervention?",
        "data": {
            "stockouts": twin_result.baseline.stockouts,
            "shortage": twin_result.baseline.shortage_quantity,
            "coverage": twin_result.baseline.service_coverage,
        }
    })

    # STEP 11: TWIN WITH PLAN
    steps.append({
        "step": 11, "name": "TWIN_WITH_PLAN",
        "description": "Simulate: what happens with redistribution?",
        "data": {
            "stockouts": twin_result.tathyon_response.stockouts,
            "shortage": twin_result.tathyon_response.shortage_quantity,
            "coverage": twin_result.tathyon_response.service_coverage,
            "delta": twin_result.delta,
        }
    })

    # STEP 12: GENERATE PLANS
    explorer = PlanExplorer(graph, store)
    exploration = explorer.explore_plans(target_resource, min_safety_days=14.0)
    steps.append({
        "step": 12, "name": "GENERATE_PLANS",
        "description": f"Generated {len(exploration.get('plans', []))} candidate plans.",
        "data": {
            "plans_count": len(exploration.get("plans", [])),
            "tradeoff_matrix": exploration.get("tradeoff_matrix", []),
        }
    })

    # STEP 13: PRESENT TRADEOFFS
    steps.append({
        "step": 13, "name": "PRESENT_TRADEOFFS",
        "description": "Officer reviews plan comparison.",
        "data": exploration.get("tradeoff_matrix", []),
    })

    # STEP 14: HUMAN APPROVAL
    planner = ResponsePlanner(graph, store)
    plan = planner.plan_redistribution(target_resource)
    if plan.status == "PROPOSED" and plan.quantity > 0:
        approved = planner.approve_plan(plan, "DEMO-CMO-001", "Chief Medical Officer")
        steps.append({
            "step": 14, "name": "HUMAN_APPROVAL",
            "description": "Chief Medical Officer approves redistribution plan.",
            "data": {
                "plan_id": approved.plan_id,
                "status": approved.status,
                "approved_by": approved.approved_by,
                "quantity": approved.quantity,
            }
        })

        # STEP 15: EXECUTE
        steps.append({
            "step": 15, "name": "EXECUTE_SOR_PAYLOAD",
            "description": "Generate System of Record payload for e-Aushadhi/DVDMS.",
            "data": {
                "sor_status": approved.system_of_record_payload.get("status", "UNKNOWN") if approved.system_of_record_payload else "NONE",
                "voucher_id": approved.system_of_record_payload.get("voucher_id", "NONE") if approved.system_of_record_payload else "NONE",
            }
        })

        # STEP 16: RECORD OUTCOME
        outcome = planner.record_outcome(
            plan_id=approved.plan_id,
            actual_delivered_qty=approved.quantity * 0.95,  # 95% delivery
            response_time_hours=8.5,
            outcome_status="SUCCESSFUL",
            stockout_prevented=True,
            plan=approved,
        )
        steps.append({
            "step": 16, "name": "RECORD_OUTCOME",
            "description": "Close the loop: delivery confirmed, outcome recorded.",
            "data": outcome,
        })
    else:
        steps.append({
            "step": 14, "name": "HUMAN_APPROVAL",
            "description": f"Plan status: {plan.status}. Reason: {plan.reason}",
            "data": {"status": plan.status, "reason": plan.reason},
        })

    return {
        "demo_name": "DISTRICT_OUTBREAK_16_STEP",
        "description": "Complete 16-step causal loop: OBSERVE → RECONCILE → DETECT → "
                       "FORECAST → SIGNAL → FUSE → PREDICT → ASSESS → DONORS → "
                       "TWIN_NOTHING → TWIN_PLAN → PLANS → TRADEOFFS → APPROVE → "
                       "EXECUTE → OUTCOME",
        "steps": steps,
        "total_steps": len(steps),
        "uses_engine_code_paths": True,
        "provenance": "SIMULATION",
        "as_of": now(),
    }
