"""
TATHYON closed-loop rescue: assess -> plan -> approve -> ship -> receive -> recalculate -> replan.

DVDMS / e-Aushadhi stay the system of record. This layer decides whether a shortage can still
be prevented, stages the order, tracks physical execution and re-decides when reality differs.

Wiring (no second optimizer):
  runway.py        data-age runway, indent lag, pending-indent check   (pure)
  rescue.py        per-donor deadline verdicts                          (pure)
  planner.py       CP-SAT allocation, fed by the assessment above
  cases.py         case state machine
  shipment.py      order lifecycle
  graph.py         physical ledger

Every method takes an explicit `at` timestamp so runs are reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import rescue as rescue_engine
from .cases import CaseManager, CaseStatus, ResilienceCase
from .graph import HealthcareResourceGraph
from .planner import ResponsePlan, ResponsePlanner
from .runway import (
    NO_PENDING_INDENT, PENDING_SUPPLY_SUFFICIENT, VERIFY_REQUIRED,
    FreshnessPolicy, LagPolicy, PendingIndent, age_adjusted_runway,
    check_pending_indents, learn_indent_lag, parse_ts,
)
from .schema import EventType, ResourceType
from .shipment import ShipmentIntelligence, ShipmentOrder

RESCUE_FEASIBLE = "RESCUE_FEASIBLE"
RESCUE_REQUIRES_VERIFICATION = "RESCUE_REQUIRES_VERIFICATION"
NO_FEASIBLE_RESCUE = "NO_FEASIBLE_RESCUE"
NO_RESCUE_NEEDED = "NO_RESCUE_NEEDED"

RESCUE_ROUTE_TAG = "TATHYON_RESCUE"          # marks shipments created by this loop (not supplier indents)
_OPEN_SHIPMENT = ("ORDERED", "DISPATCHED", "IN_TRANSIT", "DELAYED")
_TERMINAL_SHIPMENT = ("DELIVERED", "FAILED")


@dataclass(frozen=True)
class LoopPolicy:
    """All CONFIGURATION. Nothing here is a legal or clinical limit."""
    freshness: FreshnessPolicy = FreshnessPolicy()
    lag: LagPolicy = LagPolicy()
    default_bridge_days: float = 7.0          # only used when there is no pending indent and no learned lag
    min_safety_days: float = 14.0             # donor reserve
    max_donor_fraction: float = 0.5
    donor_freshness_hours: float = 48.0
    max_distance_km: float = 200.0            # planner radius cap (CONFIGURATION)
    access_eta_multiplier: float = rescue_engine.DEFAULT_ACCESS_ETA_MULTIPLIER
    require_full_coverage: bool = False       # True: a partial rescue is reported as NO_FEASIBLE_RESCUE
    approver_roles: tuple[str, ...] = ("medical_officer",)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _risk_label(runway_days: float) -> str:
    # Same bands the planner's outcome recorder already uses.
    return "CRITICAL" if runway_days <= 1.0 else "HIGH" if runway_days <= 3.0 else "MEDIUM" if runway_days <= 7.0 else "LOW"


class RescueLoop:
    def __init__(
        self,
        graph: HealthcareResourceGraph,
        planner: ResponsePlanner,
        cases: CaseManager,
        shipments: ShipmentIntelligence,
        policy: LoopPolicy = LoopPolicy(),
    ):
        self.graph = graph
        self.planner = planner
        self.cases = cases
        self.shipments = shipments
        self.policy = policy
        self.plans: dict[str, ResponsePlan] = {}
        self.case_shipments: dict[str, list[str]] = {}
        self._timeline: dict[str, list[dict]] = {}
        # Time between "approved" and "truck leaves" per donor (loading, paperwork, vehicle).
        # Supplied by the deployment; SYNTHETIC in the demo. Added on top of route travel time.
        self.donor_prep_hours: dict[str, float] = {}

    # ------------------------------------------------------------ timeline
    def _log(self, case_id: str, at: datetime, step: str, detail: dict) -> None:
        self._timeline.setdefault(case_id, []).append({"at": _iso(at), "step": step, **detail})

    def timeline(self, case_id: str) -> list[dict]:
        return list(self._timeline.get(case_id, []))

    # ------------------------------------------------------------ open
    def open_case(self, facility_id: str, resource_id: str, at: datetime,
                  trigger: str = "RUNWAY_BELOW_BRIDGE") -> ResilienceCase:
        st = self.graph.get_resource_state(facility_id, resource_id)
        if st is None:
            raise KeyError(f"NO_RESOURCE_STATE: {facility_id}/{resource_id}")
        case = self.cases.create_case(
            facility=facility_id, resource=resource_id, trigger=trigger, risk=0.0,
            evidence={"note": "risk field unused; decisions come from assess_rescue()"},
            provenance="SYNTHETIC",
        )
        self._log(case.case_id, at, "STOCK_OBSERVATION", {
            "source": st.source, "reported_quantity": st.claimed_quantity, "reported_at": st.last_updated})
        self._log(case.case_id, at, "PHYSICAL_VERIFICATION", {
            "observed_quantity": st.observed_quantity, "usable_quantity": st.usable_quantity,
            "verified_at": st.last_attested_at, "consumption_per_day": st.consumption_velocity})
        return case

    # ------------------------------------------------------------ helpers
    def _pending_and_lag(self, facility_id: str, resource_id: str):
        pending, history = [], []
        for o in self.shipments.orders:
            if o.dest_facility_id != facility_id or o.resource_id != resource_id:
                continue
            start = o.indent_date or o.created_at
            if o.status in _OPEN_SHIPMENT:
                pending.append(PendingIndent(o.order_id, o.ordered_quantity, start, o.expected_delivery_date))
            elif o.status == "DELIVERED" and o.route_id != RESCUE_ROUTE_TAG and o.actual_delivery_date:
                history.append((start, o.actual_delivery_date))
        return pending, learn_indent_lag(history, self.policy.lag)

    def _donor_inputs(self, recipient_id: str, resource_id: str, at: datetime):
        p = self.policy
        recipient = self.graph.facilities[recipient_id]
        donors = self.graph.get_donor_inventory(
            resource_id, min_safety_days=p.min_safety_days,
            max_attestation_age_hours=p.donor_freshness_hours, exclude_facility_ids=[recipient_id], as_of=at)
        out = []
        for d in donors:
            fac = self.graph.facilities[d["facility_id"]]
            transferable = round(min(d["surplus_transferable"], d["usable_quantity"] * p.max_donor_fraction), 3)
            info = self.planner.router.get_route_intelligence(
                fac.facility_id, recipient_id, fac.lat, fac.lon, recipient.lat, recipient.lon)
            signal = info.access_risk.signal_type
            signal = signal if signal in rescue_engine.VALID_ACCESS_SIGNALS else "NOT_EVALUATED"
            out.append(rescue_engine.Donor(
                fac.facility_id, transferable, eta_hours=info.eta_hours, access_risk=signal,
                handling_hours=self.donor_prep_hours.get(fac.facility_id, 0.0)))
        return out

    # ------------------------------------------------------------ assessment
    def assess_rescue(self, case_id: str, at: datetime) -> dict[str, Any]:
        """The one authoritative decision. Returns one of RESCUE_FEASIBLE,
        RESCUE_REQUIRES_VERIFICATION, NO_FEASIBLE_RESCUE, PENDING_SUPPLY_SUFFICIENT, NO_RESCUE_NEEDED."""
        case = self.cases.get_case(case_id)
        if case is None:
            raise KeyError(f"CASE_NOT_FOUND: {case_id}")
        if case.status in (CaseStatus.DETECTED, CaseStatus.REPLAN_REQUIRED):
            case.transition(CaseStatus.ASSESSING, actor="rescue_engine", notes="Rescue assessment started")
        if case.status != CaseStatus.ASSESSING:
            raise ValueError(f"INVALID_STATE: assess_rescue requires ASSESSING, case is {case.status.value}")

        p = self.policy
        st = self.graph.get_resource_state(case.facility, case.resource)
        obs = age_adjusted_runway(
            st.usable_quantity, st.last_attested_at or st.last_updated, st.consumption_velocity, at, p.freshness)
        self._log(case_id, at, "RUNWAY", {
            "observation_age_hours": obs.observation_age_hours, "freshness_status": obs.freshness_status,
            "stock_range": [obs.stock_low, obs.stock_estimate, obs.stock_high],
            "runway_hours_range": [obs.runway_hours_low, obs.runway_hours_estimate, obs.runway_hours_high]})

        base = {"case_id": case_id, "at": _iso(at), "observation": obs.to_dict()}
        if obs.runway_hours_low is None:
            return self._close(case, at, base, NO_RESCUE_NEEDED, "Zero consumption: no failure is projected.")

        pending, lag = self._pending_and_lag(case.facility, case.resource)
        pend = check_pending_indents(pending, lag, at, obs.runway_hours_low)
        self._log(case_id, at, "PENDING_INDENT", {
            "status": pend.status, "details": pend.details, "lead_time": lag.to_dict()})
        base.update(pending_indent=pend.to_dict(), indent_lag=lag.to_dict())

        if pend.status == PENDING_SUPPLY_SUFFICIENT:
            return self._close(case, at, base, PENDING_SUPPLY_SUFFICIENT,
                               "Supply already on order arrives before the facility fails. Do not rescue; monitor.")

        if pend.earliest_late_arrival_hours is not None:
            bridge_h, bridge_basis = pend.earliest_late_arrival_hours, "PENDING_INDENT_ARRIVAL"
        elif lag.median_days is not None:
            bridge_h, bridge_basis = lag.median_days * 24.0, "LEARNED_MEDIAN_LEAD_TIME"
        else:
            bridge_h, bridge_basis = p.default_bridge_days * 24.0, "CONFIGURED_DEFAULT_BRIDGE"
        base.update(bridge_hours=round(bridge_h, 2), bridge_basis=bridge_basis)

        recipient = rescue_engine.Recipient(
            case.facility, usable_stock=obs.stock_low, usable_stock_optimistic=obs.stock_high,
            consumption_per_day=st.consumption_velocity, bridge_hours=bridge_h)
        donors = self._donor_inputs(case.facility, case.resource, at)
        ra = rescue_engine.assess_rescue(recipient, donors, access_eta_multiplier=p.access_eta_multiplier)
        base.update(need_units=ra.need_units, donors=[
            {"donor": d.facility_id, "verdict": d.verdict, "reason": d.reason, "transferable": d.transferable,
             "eta_hours": d.eta_hours, "effective_arrival_hours": d.effective_arrival_hours,
             "rescue_margin_hours": d.rescue_margin_hours, "eta_source": d.eta_source} for d in ra.donors])

        if ra.status == rescue_engine.STATUS_NOT_NEEDED:
            return self._close(case, at, base, NO_RESCUE_NEEDED, ra.reasoning)

        if obs.freshness_status == VERIFY_REQUIRED:
            return self._open(case, at, base, RESCUE_REQUIRES_VERIFICATION,
                              "Observation is older than the verify threshold. Physically count before moving stock.")
        if ra.status == rescue_engine.STATUS_VERIFY:
            return self._open(case, at, base, RESCUE_REQUIRES_VERIFICATION, ra.reasoning)

        feasible_ids = {d.facility_id for d in ra.donors if d.verdict == rescue_engine.VERDICT_FEASIBLE}
        if ra.status == rescue_engine.STATUS_NO_SAFE or not feasible_ids:
            return self._no_feasible(case, at, base, obs, ra.need_units, donors, feasible_ids)

        plan = self.planner.plan_redistribution(
            case.resource, min_safety_days=p.min_safety_days, max_donor_fraction=p.max_donor_fraction,
            max_freshness_hours=p.donor_freshness_hours, max_distance_km=p.max_distance_km, as_of=at,
            recipient_overrides={case.facility: {
                "usable_quantity": obs.stock_low, "incoming_quantity": 0.0,
                "lead_time_days": bridge_h / 24.0, "runway_hours": obs.runway_hours_low}},
            exclude_donor_ids=[d.facility_id for d in donors if d.facility_id not in feasible_ids])
        allocated = round(sum(t.quantity for t in plan.transfers), 3)
        if plan.status == "NO_FEASIBLE_PLAN" or allocated <= 0:
            return self._no_feasible(case, at, base, obs, ra.need_units, donors, feasible_ids,
                                     extra="Planner found no feasible allocation under its constraints.")
        unmet = round(max(ra.need_units - allocated, 0.0), 3)
        if p.require_full_coverage and unmet > 1e-6:
            return self._no_feasible(case, at, base, obs, ra.need_units, donors, feasible_ids,
                                     extra=f"Policy requires full coverage; only {allocated:g} of {ra.need_units:g} available on time.")

        arrivals = tuple((t.quantity, t.travel_hours) for t in plan.transfers)
        failure_after = rescue_engine.time_to_failure(obs.stock_low, st.consumption_velocity, arrivals)
        base.update(plan={
            "plan_id": plan.plan_id,
            "transfers": [{"donor": t.source, "quantity": t.quantity, "eta_hours": t.travel_hours,
                           "route_data_source": t.data_source, "route_risk": t.route_risk} for t in plan.transfers],
            "allocated_quantity": allocated, "unmet_quantity": unmet,
            "donor_remaining_stock": {
                t.source: round(self.graph.get_resource_state(t.source, case.resource).usable_quantity - t.quantity, 2)
                for t in plan.transfers},
            "recipient_projected_stock_at_bridge": round(
                obs.stock_estimate + allocated - st.consumption_velocity / 24.0 * bridge_h, 2),
            "recipient_failure_hours_after_rescue": None if failure_after is None else round(failure_after, 2),
            "covers_need": unmet <= 1e-6,
            "planner_rejected_donors": plan.rejected_donors,
            "constraints": plan.constraints,
            "why": {"donor": plan.why_donor, "quantity": plan.why_quantity, "recipient": plan.why_recipient,
                    "route": plan.why_route}})
        self.plans[plan.plan_id] = plan
        case.evidence = {**case.evidence, "rescue": {
            "assessed_at": _iso(at), "baseline_stock_estimate": obs.stock_estimate,
            "consumption_per_day": st.consumption_velocity, "bridge_deadline_at": _iso(at + timedelta(hours=bridge_h)),
            "need_units": ra.need_units}}
        case.transition(CaseStatus.PLANNING, actor="planner", notes=f"CP-SAT plan {plan.plan_id} generated")
        case.candidate_plans = [plan.to_dict()]
        case.select_plan(plan.plan_id, actor="rescue_engine")
        self._log(case_id, at, "RESCUE_ASSESSMENT", {"status": RESCUE_FEASIBLE, "need": ra.need_units,
                                                     "allocated": allocated, "unmet": unmet})
        for d in ra.donors:
            self._log(case_id, at, "DONOR_VERDICT", {"donor": d.facility_id, "verdict": d.verdict,
                                                     "eta_hours": d.eta_hours, "reason": d.reason})
        for t in plan.transfers:
            self._log(case_id, at, "DONOR_SELECTION", {"donor": t.source, "quantity": t.quantity})
        return {**base, "status": RESCUE_FEASIBLE, "case_status": case.status.value,
                "reasoning": f"{allocated:g} of {ra.need_units:g} units can arrive before failure. "
                             f"Unmet after this plan: {unmet:g}. Human approval required."}

    # decision helpers -------------------------------------------------
    def _close(self, case, at, base, status, reasoning):
        case.transition(CaseStatus.CLOSED, actor="rescue_engine", notes=f"{status}: {reasoning}")
        self._log(case.case_id, at, "RESCUE_ASSESSMENT", {"status": status, "reasoning": reasoning})
        return {**base, "status": status, "case_status": case.status.value, "reasoning": reasoning}

    def _open(self, case, at, base, status, reasoning):
        case.evidence = {**case.evidence, "last_decision": status}
        self._log(case.case_id, at, "RESCUE_ASSESSMENT", {"status": status, "reasoning": reasoning})
        return {**base, "status": status, "case_status": case.status.value, "reasoning": reasoning}

    def _no_feasible(self, case, at, base, obs, need, donors, feasible_ids, extra=""):
        on_time = round(sum(d.transferable for d in donors if d.facility_id in feasible_ids), 3)
        total = round(sum(d.transferable for d in donors), 3)
        explanation = {
            "required_units": round(need, 3), "available_on_time_units": on_time,
            "available_network_units": total, "shortfall_units": round(max(need - on_time, 0.0), 3),
            "time_remaining_hours": obs.runway_hours_low,
            "constraints": [f"donor reserve {self.policy.min_safety_days:g} days of donor consumption",
                            f"max {int(self.policy.max_donor_fraction * 100)}% of donor usable stock",
                            f"donor attestation <= {self.policy.donor_freshness_hours:g}h",
                            f"route distance <= {self.policy.max_distance_km:g} km",
                            f"arrival must precede failure at {obs.runway_hours_low}h"],
        }
        reasoning = (f"Required {explanation['required_units']:g}, available on time {on_time:g} "
                     f"(network total {total:g}), shortfall {explanation['shortfall_units']:g}, "
                     f"{obs.runway_hours_low}h remaining. {extra} Case stays open: escalate to the State Drug Warehouse.").strip()
        case.evidence = {**case.evidence, "last_decision": NO_FEASIBLE_RESCUE, "escalation_required": True}
        self._log(case.case_id, at, "RESCUE_ASSESSMENT", {"status": NO_FEASIBLE_RESCUE, **explanation})
        return {**base, "status": NO_FEASIBLE_RESCUE, "case_status": case.status.value,
                "explanation": explanation, "reasoning": reasoning}

    # ------------------------------------------------------------ approval -> shipment
    def approve(self, case_id: str, officer_id: str, role: str, at: datetime) -> dict[str, Any]:
        if role not in self.policy.approver_roles:
            raise PermissionError(f"ROLE_NOT_AUTHORISED: {role!r} cannot approve rescue plans")
        case = self.cases.get_case(case_id)
        if case is None or case.status != CaseStatus.AWAITING_APPROVAL or not case.selected_plan:
            raise ValueError("INVALID_STATE: case is not awaiting approval")
        plan = self.plans[case.selected_plan["plan_id"]]
        self.planner.approve_plan(plan, officer_id, role)
        case.record_approval({"officer_id": officer_id, "role": role, "at": _iso(at)}, actor=f"{role}:{officer_id}")
        case.attach_execution_payload(plan.system_of_record_payload, system_name="DVDMS (STAGED, NOT EXECUTED)")
        ids = []
        already = len(self.case_shipments.get(case_id, []))          # unique across replans
        for i, t in enumerate(plan.transfers, start=already + 1):
            order = ShipmentOrder(
                order_id=f"SHP-{case_id}-{i}", supplier_id=t.source,
                supplier_name=self.graph.facilities[t.source].name, source_facility_id=t.source,
                dest_facility_id=t.destination, resource_id=t.resource, ordered_quantity=t.quantity,
                expected_delivery_date=_iso(at + timedelta(hours=t.travel_hours + self.donor_prep_hours.get(t.source, 0.0))),
                route_id=RESCUE_ROUTE_TAG, indent_date=_iso(at), status="ORDERED")
            self.shipments.record_order(order)
            ids.append(order.order_id)
        self.case_shipments[case_id] = self.case_shipments.get(case_id, []) + ids
        case.evidence = {**case.evidence, "shipment_ids": self.case_shipments[case_id]}
        self._log(case_id, at, "APPROVAL", {"officer": officer_id, "role": role, "plan_id": plan.plan_id})
        self._log(case_id, at, "SHIPMENT_ORDERED", {"orders": ids})
        return {"case_status": case.status.value, "shipment_ids": ids, "plan_id": plan.plan_id,
                "execution_boundary": "STAGED_NOT_EXECUTED_IN_DVDMS"}

    def dispatch(self, case_id: str, at: datetime, dispatched: Optional[dict[str, float]] = None) -> dict[str, Any]:
        case = self.cases.get_case(case_id)
        if case is None or case.status != CaseStatus.READY_FOR_EXECUTION:
            raise ValueError("INVALID_STATE: case is not ready for execution")
        out = {}
        for oid in self.case_shipments[case_id]:
            order = self.shipments.get_order(oid)
            if order.status != "ORDERED":
                continue                                   # earlier legs are already moving
            qty = (dispatched or {}).get(oid, order.ordered_quantity)
            if qty > order.ordered_quantity + 1e-9:
                raise ValueError(f"DISPATCH_EXCEEDS_PLAN: {qty:g} > {order.ordered_quantity:g}")
            self.shipments.advance_status(oid, "DISPATCHED", dispatched_quantity=qty)
            self.shipments.advance_status(oid, "IN_TRANSIT")
            src = self.graph.facilities[order.source_facility_id]
            dst = self.graph.facilities[order.dest_facility_id]
            route = self.planner.router.get_route_intelligence(
                src.facility_id, dst.facility_id, src.lat, src.lon, dst.lat, dst.lon)
            order.dispatched_at = _iso(at)
            order.expected_delivery_date = _iso(at + timedelta(hours=route.eta_hours))   # prep is already spent
            donor = self.graph.get_resource_state(order.source_facility_id, order.resource_id)
            donor.usable_quantity = round(max(donor.usable_quantity - qty, 0.0), 3)
            donor.observed_quantity = round(max(donor.observed_quantity - qty, 0.0), 3)
            out[oid] = qty
            self._log(case_id, at, "SHIPMENT_DISPATCHED", {"order": oid, "planned": order.ordered_quantity,
                                                           "dispatched": qty})
        case.record_dispatch({"orders": out}, actor="dispatch_officer")
        return {"case_status": case.status.value, "dispatched": out}

    # ------------------------------------------------------------ receipt -> recalculation
    def receive(self, case_id: str, order_id: str, received: float, damaged: float, at: datetime,
                recipient_count: Optional[float] = None) -> dict[str, Any]:
        case = self.cases.get_case(case_id)
        order = self.shipments.get_order(order_id)
        if case is None or order is None or order.status != "IN_TRANSIT":
            raise ValueError("INVALID_STATE: shipment is not in transit for this case")
        if received < 0 or damaged < 0 or damaged > received or received > order.dispatched_quantity + 1e-9:
            raise ValueError("INVALID_RECEIPT: need 0 <= damaged <= received <= dispatched")

        usable_received = received - damaged
        st = self.graph.get_resource_state(order.dest_facility_id, order.resource_id)
        before = age_adjusted_runway(
            st.usable_quantity, st.last_attested_at or st.last_updated, st.consumption_velocity, at,
            self.policy.freshness)
        if recipient_count is not None:
            st.usable_quantity = st.observed_quantity = round(recipient_count, 3)
            st.last_attested_at = _iso(at)
        elif before.stock_estimate <= 0:
            # Facility ran dry before the delivery landed: the count restarts from what arrived.
            st.usable_quantity = st.observed_quantity = round(usable_received, 3)
            st.last_attested_at = _iso(at)
        else:
            # Count as of last_attested_at plus documented receipts; consumption since is age-adjusted.
            st.usable_quantity = round(st.usable_quantity + usable_received, 3)
            st.observed_quantity = round(st.observed_quantity + usable_received, 3)
        st.last_updated = _iso(at)
        after = age_adjusted_runway(
            st.usable_quantity, st.last_attested_at, st.consumption_velocity, at, self.policy.freshness)
        if after.runway_hours_estimate is not None:
            st.risk = _risk_label(after.runway_hours_estimate / 24.0)

        self.shipments.record_delivery(
            order_id, received_quantity=received, actual_delivery_date=_iso(at),
            loss_quantity=max(order.dispatched_quantity - received, 0.0), damaged_quantity=damaged,
            notes="Physical receipt recorded by storekeeper")
        meta = case.evidence.get("rescue", {})
        elapsed_h = (at - parse_ts(meta["assessed_at"])).total_seconds() / 3600.0
        outcome = rescue_engine.evaluate_outcome(
            rescue_engine.Recipient(case.facility, meta["baseline_stock_estimate"], meta["consumption_per_day"]),
            planned_quantity=order.ordered_quantity, dispatched_quantity=order.dispatched_quantity,
            received_quantity=received, damaged_quantity=damaged, arrival_hours=elapsed_h)
        order.stockout_avoided = outcome.failure_avoided
        self.graph.store.append(
            event_type=EventType.OUTCOME_RECORDED, facility_id=order.dest_facility_id,
            resource_type=ResourceType.MEDICINE, resource_key=order.resource_id,
            payload={"order_id": order_id, **outcome.to_dict(), "provenance": "OBSERVED"},
            actor="RescueLoop")
        self._log(case_id, at, "SHIPMENT_RECEIVED", {
            "order": order_id, "planned": order.ordered_quantity, "dispatched": order.dispatched_quantity,
            "received": received, "damaged": damaged, "usable": usable_received})
        self._log(case_id, at, "RECALCULATION", {
            "stock_range": [after.stock_low, after.stock_estimate, after.stock_high],
            "runway_hours_range": [after.runway_hours_low, after.runway_hours_estimate, after.runway_hours_high],
            "recipient_risk": st.risk})
        return self._finish_if_done(case, at, outcome.to_dict())

    def fail_shipment(self, case_id: str, order_id: str, reason: str, at: datetime) -> dict[str, Any]:
        case = self.cases.get_case(case_id)
        order = self.shipments.get_order(order_id)
        if case is None or order is None or order.status not in ("DISPATCHED", "IN_TRANSIT"):
            raise ValueError("INVALID_STATE: shipment cannot be failed from its current status")
        self.shipments.advance_status(order_id, "FAILED", notes=reason)
        self._log(case_id, at, "SHIPMENT_FAILED", {"order": order_id, "reason": reason})
        return self._finish_if_done(case, at, {"failed_order": order_id, "reason": reason})

    def _finish_if_done(self, case: ResilienceCase, at: datetime, outcome: dict) -> dict[str, Any]:
        if case.status != CaseStatus.EXECUTING:
            # A replan is already open: a late arrival must not close the case behind its back.
            if case.status == CaseStatus.ASSESSING:
                return {"case_status": case.status.value, "outcome": outcome, "result": "REASSESSED",
                        "replan": self.assess_rescue(case.case_id, at)}
            return {"case_status": case.status.value, "outcome": outcome, "result": "PLAN_OPEN"}
        orders = [self.shipments.get_order(o) for o in self.case_shipments[case.case_id]]
        if any(o.status not in _TERMINAL_SHIPMENT for o in orders):
            return {"case_status": case.status.value, "outcome": outcome, "result": "AWAITING_OTHER_SHIPMENTS"}

        delivered = [o for o in orders if o.status == "DELIVERED"]
        if delivered:
            case.record_delivery({"orders": [o.to_dict() for o in delivered]}, actor="storekeeper")
            case.record_outcome(outcome, actor="rescue_engine")
        else:
            case.transition(CaseStatus.DELIVERY_FAILED, actor="rescue_engine", notes="All shipments failed")

        st = self.graph.get_resource_state(case.facility, case.resource)
        now_obs = age_adjusted_runway(
            st.usable_quantity, st.last_attested_at or st.last_updated, st.consumption_velocity, at,
            self.policy.freshness)
        deadline = parse_ts(case.evidence["rescue"]["bridge_deadline_at"])
        hours_to_bridge = (deadline - at).total_seconds() / 3600.0
        unsafe = now_obs.runway_hours_low is not None and hours_to_bridge > 0 and now_obs.runway_hours_low < hours_to_bridge
        self._log(case.case_id, at, "RISK_RECALCULATION", {
            "runway_hours_low": now_obs.runway_hours_low, "hours_to_next_real_supply": round(hours_to_bridge, 2),
            "still_unsafe": unsafe})

        if not unsafe:
            case.transition(CaseStatus.CLOSED, actor="rescue_engine",
                            notes="Measured runway covers the bridge to the next real supply")
            self._log(case.case_id, at, "CLOSED", {"failure_avoided": True})
            return {"case_status": case.status.value, "outcome": outcome, "result": "CLOSED", "still_unsafe": False}

        case.transition(CaseStatus.RISK_RECALCULATED, actor="rescue_engine",
                        notes=f"Runway {now_obs.runway_hours_low}h < {hours_to_bridge:.1f}h to next real supply")
        case.transition(CaseStatus.REPLAN_REQUIRED, actor="rescue_engine", notes="REPLAN_REQUIRED")
        self._log(case.case_id, at, "REPLAN_REQUIRED", {"runway_hours_low": now_obs.runway_hours_low})
        replan = self.assess_rescue(case.case_id, at)
        return {"case_status": case.status.value, "outcome": outcome, "result": "REPLAN", "still_unsafe": True,
                "replan": replan}


    # ------------------------------------------------------------ shipment monitor
    def check_late_shipments(self, case_id: str, at: datetime) -> Optional[dict[str, Any]]:
        """If any in-transit shipment will land after the recipient's pessimistic failure time,
        the plan is no longer feasible: flag it, reopen the case and search alternatives."""
        case = self.cases.get_case(case_id)
        if case is None or case.status != CaseStatus.EXECUTING:
            return None
        st = self.graph.get_resource_state(case.facility, case.resource)
        obs = age_adjusted_runway(
            st.usable_quantity, st.last_attested_at or st.last_updated, st.consumption_velocity, at,
            self.policy.freshness)
        if obs.runway_hours_low is None:
            return None
        # A shipment is only too late if the facility still fails given ALL supply in transit.
        in_flight = []
        for oid in self.case_shipments.get(case_id, []):
            o = self.shipments.get_order(oid)
            if o.status in ("DISPATCHED", "IN_TRANSIT"):
                eta_h = (parse_ts(o.expected_delivery_date) - at).total_seconds() / 3600.0
                in_flight.append((oid, max(eta_h, 0.0), o.dispatched_quantity or o.ordered_quantity))
        failure_h = rescue_engine.time_to_failure(
            obs.stock_low, st.consumption_velocity, tuple((q, eta) for _, eta, q in in_flight))
        late = [{"order_id": oid, "eta_hours": round(eta, 2), "failure_hours_with_all_supply": None if failure_h is None else round(failure_h, 2)}
                for oid, eta, _ in in_flight if failure_h is not None and eta >= failure_h]
        if not late:
            return None
        self._log(case_id, at, "ARRIVAL_TOO_LATE", {"orders": late,
                  "message": "RESCUE PLAN NO LONGER FEASIBLE: shipment will arrive after failure"})
        case.transition(CaseStatus.REPLAN_REQUIRED, actor="shipment_monitor",
                        notes="Shipment ETA exceeds recipient failure window")
        replan = self.assess_rescue(case_id, at)
        return {"result": "RESCUE_PLAN_NO_LONGER_FEASIBLE", "late_orders": late, "replan": replan}
