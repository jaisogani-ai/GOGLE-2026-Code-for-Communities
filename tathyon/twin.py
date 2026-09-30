"""
TATHYON Emergency Resilience Resilience Scenario Engine — Deterministic Network Simulation.
BUILD PHASE 3: EMERGENCY RESILIENCE SCENARIO ENGINE

Deterministic simulation engine running over the Healthcare Resource Graph.
Evaluates the impact of acute macro shocks on public health resilience:
  1. Demand +20%
  2. Demand +50%
  3. Demand +100%
  4. District warehouse unavailable
  5. Supplier lead time x2
  6. One-district outbreak uplift

For every scenario, deterministically computes:
  - BASELINE (No redistribution; network absorbs failure alone)
  - TATHYON RESPONSE (Constrained redistribution over verified donor inventory)

Across standardized metrics:
  - stockouts
  - shortage quantity
  - critical facilities affected
  - distance (km)
  - cost (INR)
  - expiry waste
  - service coverage (%)
  - response time (hours)

CRITICAL INVARIANT:
Every output explicitly declares provenance as "SIMULATION".
Never present synthetic simulation as real-world impact.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from .graph import Facility, HealthcareResourceGraph, ResourceState, haversine_distance_km
from .planner import ResponsePlan, ResponsePlanner
import json
from .schema import EventType, FacilityType, Provenance, ResourceType, new_id, now, sha256
from .store import EventStore


class ShockType(str, Enum):
    DEMAND_PLUS_20 = "DEMAND_PLUS_20"
    DEMAND_PLUS_50 = "DEMAND_PLUS_50"
    DEMAND_PLUS_100 = "DEMAND_PLUS_100"
    DISTRICT_WAREHOUSE_UNAVAILABLE = "DISTRICT_WAREHOUSE_UNAVAILABLE"
    LEAD_TIME_X2 = "LEAD_TIME_X2"
    ONE_DISTRICT_OUTBREAK_UPLIFT = "ONE_DISTRICT_OUTBREAK_UPLIFT"


SHOCK_DESCRIPTIONS = {
    ShockType.DEMAND_PLUS_20: "Acute +20% demand surge across all network delivery points.",
    ShockType.DEMAND_PLUS_50: "Severe +50% epidemic demand spike across primary health centres.",
    ShockType.DEMAND_PLUS_100: "Catastrophic +100% outbreak surge doubling consumption rate.",
    ShockType.DISTRICT_WAREHOUSE_UNAVAILABLE: "Sudden closure/quarantine of District Warehouse hub (zero supply dispatch).",
    ShockType.LEAD_TIME_X2: "Upstream supplier supply-chain collapse doubling replenishment lead times (x2).",
    ShockType.ONE_DISTRICT_OUTBREAK_UPLIFT: "Localized cluster outbreak causing +150% demand uplift in epicenter district.",
}


@dataclass
class SimulationMetrics:
    """Standardized resilience metrics comparing Baseline vs Tathyon Response."""
    stockouts: int                            # Number of facility stockouts
    shortage_quantity: float                  # Cumulative unfulfilled patient demand units
    critical_facilities_affected: int         # Count of PHCs, CHCs, and Hospitals facing stockouts
    distance: float                           # Total redistribution transport kilometers
    cost: float                               # Estimated logistics cost in INR (₹25/km + ₹200 handling)
    expiry_waste: float                       # Expired unusable units
    service_coverage: float                   # Percentage of patient demand successfully met (0-100%)
    response_time: float                      # Average response and delivery time in hours

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TwinSimulationResult:
    """The complete deterministic output of a Resilience Scenario Engine stress test."""
    simulation_id: str
    shock: str
    shock_description: str
    resource_id: str
    horizon_days: int
    baseline: SimulationMetrics
    tathyon_response: SimulationMetrics
    delta: dict[str, Any]
    response_plan: Optional[dict] = None
    seed: int = 42
    version: str = "1.0.0"
    initial_state_hash: str = ""
    provenance: str = "SIMULATION"
    disclaimer: str = (
        "SIMULATION ONLY — Deterministic stress test over synthetic network topology. "
        "Never present synthetic simulation as real-world impact."
    )
    timestamp: str = field(default_factory=now)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["baseline"] = self.baseline.to_dict()
        d["tathyon_response"] = self.tathyon_response.to_dict()
        d["do_nothing"] = self.baseline.to_dict()
        d["act"] = self.tathyon_response.to_dict()
        d["seed"] = self.seed
        d["version"] = self.version
        d["initial_state_hash"] = self.initial_state_hash
        return d


TwinScenario = TwinSimulationResult


class EmergencyResilienceScenarioEngine:
    """Deterministic simulation engine over the Healthcare Resource Graph."""


    def __init__(self, graph: HealthcareResourceGraph, store: Optional[EventStore] = None):
        self.graph = graph
        self.store = store or graph.store
        self.planner = ResponsePlanner(self.graph, self.store)

    def run_simulation(
        self,
        shock: ShockType | str,
        resource_id: str = "MED-ARV-01",
        horizon_days: int = 14,
        target_district: Optional[str] = None,
        seed: int = 42,
    ) -> TwinSimulationResult:
        """Executes deterministic stress simulation for the selected shock."""
        if isinstance(shock, str):
            shock = ShockType(shock)

        sim_id = f"TWIN-{now()[:10].replace('-', '')}-{new_id('twin')[:8].upper()}"
        res = self.graph.resources.get(resource_id)
        default_safety_days = res.default_safety_stock_days if res else 7.0

        # Snapshot graph states for simulation
        sim_states: dict[str, dict[str, Any]] = {}
        for (fid, rid), st in self.graph.states.items():
            if rid != resource_id:
                continue
            fac = self.graph.facilities.get(fid)
            if not fac or not fac.is_active:
                continue

            sim_states[fid] = {
                "facility_id": fid,
                "facility_name": fac.name,
                "facility_type": fac.facility_type,
                "district": fac.district,
                "claimed_quantity": st.claimed_quantity,
                "usable_quantity": st.usable_quantity,
                "consumption_velocity": st.consumption_velocity,
                "incoming_quantity": st.incoming_quantity,
                "lead_time": st.lead_time,
                "lat": fac.lat,
                "lon": fac.lon,
            }

        if not target_district:
            # Pick first available district
            districts = list({s["district"] for s in sim_states.values()})
            target_district = districts[0] if districts else "Central"

        # Apply shock mutations to consumption velocities and lead times
        for fid, s in sim_states.items():
            if shock == ShockType.DEMAND_PLUS_20:
                s["consumption_velocity"] *= 1.20
            elif shock == ShockType.DEMAND_PLUS_50:
                s["consumption_velocity"] *= 1.50
            elif shock == ShockType.DEMAND_PLUS_100:
                s["consumption_velocity"] *= 2.00
            elif shock == ShockType.DISTRICT_WAREHOUSE_UNAVAILABLE:
                if s["facility_type"] in (FacilityType.DISTRICT_WAREHOUSE, FacilityType.STATE_WAREHOUSE):
                    s["usable_quantity"] = 0.0
                    s["incoming_quantity"] = 0.0
            elif shock == ShockType.LEAD_TIME_X2:
                s["lead_time"] *= 2.0
            elif shock == ShockType.ONE_DISTRICT_OUTBREAK_UPLIFT:
                if s["district"] == target_district:
                    s["consumption_velocity"] *= 2.50  # +150% uplift

        # Causal Invariant: compute identical initial state hash before branching
        state_repr = {
            fid: {k: v for k, v in s.items() if k != "facility_type"}
            for fid, s in sim_states.items()
        }
        initial_state_hash = sha256(json.dumps(state_repr, sort_keys=True, default=str))

        # ------------------------------------------------------------------
        # 1. BASELINE SIMULATION (No intervention / No Tathyon response)
        # ------------------------------------------------------------------
        b_stockouts = 0
        b_shortage_qty = 0.0
        b_crit_affected = set()
        b_total_demand = 0.0
        b_total_supplied = 0.0
        b_expiry_waste = 0.0

        for fid, s in sim_states.items():
            daily_v = s["consumption_velocity"]
            demand = daily_v * horizon_days
            b_total_demand += demand

            # Available stock: current usable stock + replenishment if lead_time <= horizon
            avail = s["usable_quantity"]
            if s["lead_time"] <= horizon_days:
                avail += s["incoming_quantity"]

            if avail >= demand:
                b_total_supplied += demand
            else:
                b_total_supplied += max(avail, 0.0)
                shortfall = demand - max(avail, 0.0)
                b_shortage_qty += shortfall
                b_stockouts += 1
                if s["facility_type"] in (FacilityType.PHC, FacilityType.CHC, FacilityType.DISTRICT_HOSPITAL):
                    b_crit_affected.add(fid)

        b_cov = round((b_total_supplied / b_total_demand * 100.0), 1) if b_total_demand > 0 else 100.0

        baseline_metrics = SimulationMetrics(
            stockouts=b_stockouts,
            shortage_quantity=round(b_shortage_qty, 1),
            critical_facilities_affected=len(b_crit_affected),
            distance=0.0,
            cost=0.0,
            expiry_waste=round(b_expiry_waste, 1),
            service_coverage=b_cov,
            response_time=0.0,
        )

        # ------------------------------------------------------------------
        # 2. TATHYON RESPONSE SIMULATION (OR-Tools Redistribution)
        # ------------------------------------------------------------------
        # Run response planner over shocked simulation graph
        sim_graph = copy.deepcopy(self.graph)
        for fid, s in sim_states.items():
            st_key = (fid, resource_id)
            if st_key in sim_graph.states:
                sim_st = sim_graph.states[st_key]
                sim_st.consumption_velocity = s["consumption_velocity"]
                sim_st.usable_quantity = s["usable_quantity"]
                sim_st.incoming_quantity = s["incoming_quantity"]
                sim_st.lead_time = s["lead_time"]

        shocked_planner = ResponsePlanner(sim_graph, self.store)
        plan = shocked_planner.plan_redistribution(
            resource_id=resource_id,
            simulation_id=sim_id,
            min_safety_days=float(horizon_days),
        )

        # Simulate receipt of transferred stock at shortage facilities
        t_states = copy.deepcopy(sim_states)
        total_distance = 0.0
        total_transit_hours = 0.0

        if plan.transfers:
            for t in plan.transfers:
                total_distance += t.distance
                total_transit_hours += t.travel_hours
                # Deduct from donor usable stock
                if t.source in t_states:
                    t_states[t.source]["usable_quantity"] = max(
                        t_states[t.source]["usable_quantity"] - t.quantity, 0.0
                    )
                # Add to recipient shortage usable stock
                if t.destination in t_states:
                    t_states[t.destination]["usable_quantity"] += t.quantity

        t_stockouts = 0
        t_shortage_qty = 0.0
        t_crit_affected = set()
        t_total_supplied = 0.0

        for fid, s in t_states.items():
            daily_v = s["consumption_velocity"]
            demand = daily_v * horizon_days

            avail = s["usable_quantity"]
            if s["lead_time"] <= horizon_days:
                avail += s["incoming_quantity"]

            if avail >= demand:
                t_total_supplied += demand
            else:
                t_total_supplied += max(avail, 0.0)
                shortfall = demand - max(avail, 0.0)
                t_shortage_qty += shortfall
                t_stockouts += 1
                if s["facility_type"] in (FacilityType.PHC, FacilityType.CHC, FacilityType.DISTRICT_HOSPITAL):
                    t_crit_affected.add(fid)

        t_cov = round((t_total_supplied / b_total_demand * 100.0), 1) if b_total_demand > 0 else 100.0
        avg_response_hours = (
            round(total_transit_hours / len(plan.transfers), 1) if plan.transfers else 0.0
        )
        # Cost formula: ₹25 per kilometer + ₹200 handling per transfer
        logistics_cost = round((total_distance * 25.0) + (len(plan.transfers) * 200.0), 2)

        tathyon_metrics = SimulationMetrics(
            stockouts=t_stockouts,
            shortage_quantity=round(t_shortage_qty, 1),
            critical_facilities_affected=len(t_crit_affected),
            distance=round(total_distance, 1),
            cost=logistics_cost,
            expiry_waste=0.0,
            service_coverage=t_cov,
            response_time=avg_response_hours,
        )

        delta = {
            "stockouts_prevented": max(baseline_metrics.stockouts - tathyon_metrics.stockouts, 0),
            "shortage_reduction_units": round(
                max(baseline_metrics.shortage_quantity - tathyon_metrics.shortage_quantity, 0.0), 1
            ),
            "coverage_improvement_pct": round(
                tathyon_metrics.service_coverage - baseline_metrics.service_coverage, 1
            ),
            "critical_facilities_saved": max(
                baseline_metrics.critical_facilities_affected - tathyon_metrics.critical_facilities_affected, 0
            ),
        }

        sim_result = TwinSimulationResult(
            simulation_id=sim_id,
            shock=shock.value,
            shock_description=SHOCK_DESCRIPTIONS[shock],
            resource_id=resource_id,
            horizon_days=horizon_days,
            baseline=baseline_metrics,
            tathyon_response=tathyon_metrics,
            delta=delta,
            response_plan=plan.to_dict(),
            seed=seed,
            version="1.0.0",
            initial_state_hash=initial_state_hash,
        )

        # Record simulation event in the EventStore
        self.store.append(
            event_type=EventType.TWIN_RUN,
            facility_id="NETWORK",
            resource_type=ResourceType.MEDICINE,
            resource_key=resource_id,
            payload={
                "simulation_id": sim_id,
                "shock": shock.value,
                "resource_id": resource_id,
                "stockouts_baseline": baseline_metrics.stockouts,
                "stockouts_tathyon": tathyon_metrics.stockouts,
                "stockouts_prevented": delta["stockouts_prevented"],
            },
            actor="ResilienceScenarioEngine",
        )

        return sim_result


    def compare_plans(
        self,
        shock: ShockType | str,
        resource_id: str,
        plans: list[dict],
        horizon_days: int = 14,
        target_district: Optional[str] = None,
        seed: int = 42,
    ) -> dict:
        """TWIN 2.0: Compare DO NOTHING vs multiple plans.
        
        Each plan in `plans` is a dict with:
          - plan_name: str (e.g. "PLAN_A_CLOSEST_DONOR")
          - transfers: list[dict] with source, destination, quantity
        
        Returns a structured comparison across:
          stockouts, unmet_demand, coverage, distance, reserve,
          time_to_recovery, affected_facilities
        
        This is the DECISION INSTRUMENT: the officer sees tradeoffs,
        not a single "best" recommendation with unexplained AI.
        """
        if isinstance(shock, str):
            shock = ShockType(shock)

        # Run baseline (DO NOTHING) simulation
        baseline_result = self.run_simulation(
            shock=shock,
            resource_id=resource_id,
            horizon_days=horizon_days,
            target_district=target_district,
            seed=seed,
        )

        comparison = {
            "shock": shock.value,
            "shock_description": SHOCK_DESCRIPTIONS[shock],
            "resource_id": resource_id,
            "horizon_days": horizon_days,
            "seed": seed,
            "do_nothing": {
                "plan_name": "DO_NOTHING",
                "stockouts": baseline_result.baseline.stockouts,
                "shortage_quantity": baseline_result.baseline.shortage_quantity,
                "service_coverage_pct": baseline_result.baseline.service_coverage,
                "critical_facilities_affected": baseline_result.baseline.critical_facilities_affected,
                "distance_km": 0.0,
                "cost_inr": 0.0,
                "response_time_hours": 0.0,
            },
            "plans": [],
            "recommendation_note": (
                "These are counterfactual projections. The officer must evaluate "
                "tradeoffs and select the plan that best fits operational constraints. "
                "TATHYON does not auto-select."
            ),
            "provenance": "SIMULATION",
        }

        # Simulate each plan
        for plan_spec in plans:
            plan_name = plan_spec.get("plan_name", "UNNAMED_PLAN")
            plan_transfers = plan_spec.get("transfers", [])

            # Deep-copy the shocked simulation states from baseline
            sim_graph = copy.deepcopy(self.graph)

            # Apply shock mutations
            for (fid, rid), st in sim_graph.states.items():
                if rid != resource_id:
                    continue
                if shock == ShockType.DEMAND_PLUS_20:
                    st.consumption_velocity *= 1.20
                elif shock == ShockType.DEMAND_PLUS_50:
                    st.consumption_velocity *= 1.50
                elif shock == ShockType.DEMAND_PLUS_100:
                    st.consumption_velocity *= 2.00
                elif shock == ShockType.DISTRICT_WAREHOUSE_UNAVAILABLE:
                    fac = sim_graph.facilities.get(fid)
                    if fac and fac.facility_type in (FacilityType.DISTRICT_WAREHOUSE, FacilityType.STATE_WAREHOUSE):
                        st.usable_quantity = 0.0
                        st.incoming_quantity = 0.0
                elif shock == ShockType.LEAD_TIME_X2:
                    st.lead_time *= 2.0
                elif shock == ShockType.ONE_DISTRICT_OUTBREAK_UPLIFT:
                    fac = sim_graph.facilities.get(fid)
                    td = target_district or ""
                    if fac and fac.district == td:
                        st.consumption_velocity *= 2.50

            # Apply plan transfers
            total_distance = 0.0
            total_transit_hours = 0.0
            for t in plan_transfers:
                src = t.get("source", "")
                dst = t.get("destination", "")
                qty = float(t.get("quantity", 0.0))

                src_st = sim_graph.states.get((src, resource_id))
                dst_st = sim_graph.states.get((dst, resource_id))
                if src_st and qty > 0:
                    src_st.usable_quantity = max(src_st.usable_quantity - qty, 0.0)
                if dst_st:
                    dst_st.usable_quantity += qty

                dist = sim_graph.distance_km(src, dst) if src in sim_graph.facilities and dst in sim_graph.facilities else 25.0
                total_distance += dist
                total_transit_hours += dist / 40.0

            # Evaluate post-plan metrics
            p_stockouts = 0
            p_shortage = 0.0
            p_crit = set()
            p_supplied = 0.0
            p_total_demand = 0.0

            for (fid, rid), st in sim_graph.states.items():
                if rid != resource_id:
                    continue
                fac = sim_graph.facilities.get(fid)
                if not fac or not fac.is_active:
                    continue

                demand = st.consumption_velocity * horizon_days
                p_total_demand += demand
                avail = st.usable_quantity
                if st.lead_time <= horizon_days:
                    avail += st.incoming_quantity

                if avail >= demand:
                    p_supplied += demand
                else:
                    p_supplied += max(avail, 0.0)
                    shortfall = demand - max(avail, 0.0)
                    p_shortage += shortfall
                    p_stockouts += 1
                    if fac.facility_type in (FacilityType.PHC, FacilityType.CHC, FacilityType.DISTRICT_HOSPITAL):
                        p_crit.add(fid)

            p_coverage = round((p_supplied / p_total_demand * 100.0), 1) if p_total_demand > 0 else 100.0
            logistics_cost = round((total_distance * 25.0) + (len(plan_transfers) * 200.0), 2)

            plan_result = {
                "plan_name": plan_name,
                "stockouts": p_stockouts,
                "shortage_quantity": round(p_shortage, 1),
                "service_coverage_pct": p_coverage,
                "critical_facilities_affected": len(p_crit),
                "distance_km": round(total_distance, 1),
                "cost_inr": logistics_cost,
                "response_time_hours": round(total_transit_hours / max(len(plan_transfers), 1), 1),
                "transfers_count": len(plan_transfers),
                "delta_vs_do_nothing": {
                    "stockouts_prevented": max(baseline_result.baseline.stockouts - p_stockouts, 0),
                    "shortage_reduction": round(max(baseline_result.baseline.shortage_quantity - p_shortage, 0.0), 1),
                    "coverage_improvement_pct": round(p_coverage - baseline_result.baseline.service_coverage, 1),
                },
            }
            comparison["plans"].append(plan_result)

        return comparison


ScenarioEngine = EmergencyResilienceScenarioEngine
