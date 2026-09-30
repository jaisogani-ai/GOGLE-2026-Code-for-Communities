"""Phase 10: closed-loop rescue. Golden scenario, data-age, indent-lag, mutation and no-feasible tests."""
from datetime import datetime, timedelta, timezone

import pytest

from tathyon.cases import CaseManager, CaseStatus
from tathyon.graph import Facility, HealthcareResourceGraph, Resource, ResourceState
from tathyon.planner import ResponsePlanner
from tathyon.rescue_loop import (
    LoopPolicy, RescueLoop, NO_FEASIBLE_RESCUE, NO_RESCUE_NEEDED, PENDING_SUPPLY_SUFFICIENT,
    RESCUE_FEASIBLE, RESCUE_REQUIRES_VERIFICATION,
)
from tathyon.runway import (
    AGING, FRESH, STALE, VERIFY_REQUIRED, FreshnessPolicy, age_adjusted_runway, learn_indent_lag,
    LEAD_TIME_INSUFFICIENT_DATA,
)
from tathyon.schema import FacilityType, ResourceType
from tathyon.shipment import ShipmentIntelligence, ShipmentOrder
from tathyon.store import EventStore

T0 = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)
RID = "CRITICAL_CONSUMABLE"


def iso(dt):
    return dt.isoformat()


def h(hours):
    return T0 + timedelta(hours=hours)


def history(days_list, dest="TOKAPAL"):
    """Delivered indents: (indent -> receipt) durations in days, in the past of T0."""
    out = []
    for i, d in enumerate(days_list):
        start = T0 - timedelta(days=60 - i * 10)
        out.append(ShipmentOrder(
            order_id=f"HIST-{i}", supplier_id="CMS", supplier_name="State depot", source_facility_id="CMS",
            dest_facility_id=dest, resource_id=RID, ordered_quantity=100, expected_delivery_date=iso(start),
            actual_delivery_date=iso(start + timedelta(days=d)), status="DELIVERED", indent_date=iso(start),
            received_quantity=100))
    return out


def build(*, stock=40.0, reported=100.0, rate=20.0, observed_at=None, indent_qty=100.0, indent_eta_days=6.0,
          lags=(5, 6, 7), donors=(("DON_A", 60.0, 72.0), ("DON_B", 35.0, 8.0)), policy=None, indent=True):
    graph, store = HealthcareResourceGraph(), EventStore()
    graph.store = store
    graph.add_resource(Resource(RID, "Critical consumable", ResourceType.MEDICINE, "unit", criticality=8.0))
    graph.add_facility(Facility("TOKAPAL", "Tokapal PHC", FacilityType.PHC, "Bastar", "CG", 0.0, 0.0))
    st = ResourceState("TOKAPAL", RID, claimed_quantity=reported, observed_quantity=stock,
                       consumption_velocity=rate)
    st.last_attested_at = iso(observed_at or T0)
    graph.set_resource_state(st)
    planner = ResponsePlanner(graph, store)
    donor_rate, floor_days = 10.0, 14.0
    for name, transferable, eta in donors:
        graph.add_facility(Facility(name, name, FacilityType.CHC, "Bastar", "CG", 0.0, 0.0))
        usable = donor_rate * floor_days + transferable
        ds = ResourceState(name, RID, claimed_quantity=usable, observed_quantity=usable, usable_quantity=usable,
                           consumption_velocity=donor_rate)
        ds.last_attested_at = iso(datetime.now(timezone.utc))
        graph.set_resource_state(ds)
        planner.router.set_synthetic_override(name, "TOKAPAL", eta_hours=eta)
    ships = ShipmentIntelligence()
    for o in history(list(lags)):
        ships.record_order(o)
    if indent:
        ships.record_order(ShipmentOrder(
            order_id="INDENT-1", supplier_id="CMS", supplier_name="State depot", source_facility_id="CMS",
            dest_facility_id="TOKAPAL", resource_id=RID, ordered_quantity=indent_qty,
            expected_delivery_date=iso(T0 + timedelta(days=indent_eta_days)), indent_date=iso(T0),
            status="ORDERED"))
    # Synthetic route overrides imply distance = ETA x 40 km/h; widen the radius so it does not mask the ETA logic.
    loop = RescueLoop(graph, planner, CaseManager(), ships, policy or LoopPolicy(max_distance_km=5000.0))
    return loop, graph, ships


def assess(loop, at=T0):
    case = loop.open_case("TOKAPAL", RID, at)
    return case, loop.assess_rescue(case.case_id, at)


# ---------------------------------------------------------------- runway (feature 1)
def test_age_changes_range_and_status():
    fresh = age_adjusted_runway(100, iso(h(-2)), 20, T0)
    old = age_adjusted_runway(100, iso(T0 - timedelta(days=10)), 20, T0)
    assert fresh.freshness_status == FRESH and old.freshness_status == VERIFY_REQUIRED
    assert (fresh.stock_high - fresh.stock_low) < (old.stock_high - old.stock_low) or old.stock_high == 0
    assert fresh.stock_estimate > old.stock_estimate
    assert fresh.stock_high <= 100 and old.stock_high <= 100      # never invents stock


def test_freshness_thresholds_are_configurable():
    strict = FreshnessPolicy(fresh_hours=1, aging_hours=2, stale_hours=3)
    assert age_adjusted_runway(100, iso(h(-5)), 20, T0, strict).freshness_status == VERIFY_REQUIRED
    assert age_adjusted_runway(100, iso(h(-5)), 20, T0).freshness_status == FRESH
    assert age_adjusted_runway(100, iso(h(-48)), 20, T0).freshness_status == AGING
    assert age_adjusted_runway(100, iso(h(-100)), 20, T0).freshness_status == STALE


def test_future_observation_rejected():
    with pytest.raises(ValueError):
        age_adjusted_runway(100, iso(h(5)), 20, T0)


# ---------------------------------------------------------------- indent lag (feature 3)
def test_indent_lag_learned_from_history_not_hardcoded():
    lag = learn_indent_lag([(iso(T0), iso(T0 + timedelta(days=d))) for d in (4, 9, 5)])
    assert (lag.sample_count, lag.median_days, lag.min_days, lag.max_days) == (3, 5.0, 4.0, 9.0)
    assert lag.last_observed_days == 9.0          # the most recently RECEIVED indent, not list order
    assert learn_indent_lag([(iso(T0), iso(T0 + timedelta(days=15)))] * 2).status == LEAD_TIME_INSUFFICIENT_DATA


def test_indent_lag_is_per_facility_resource():
    loop, _, ships = build(lags=(2, 2, 2))
    _, lag_a = loop._pending_and_lag("TOKAPAL", RID)[0], loop._pending_and_lag("TOKAPAL", RID)[1]
    assert lag_a.median_days == 2.0
    assert loop._pending_and_lag("TOKAPAL", "OTHER_DRUG")[1].status == LEAD_TIME_INSUFFICIENT_DATA


# ---------------------------------------------------------------- golden scenario
def test_golden_scenario_tokapal_end_to_end():
    loop, graph, ships = build()
    case, d = assess(loop)

    assert d["observation"]["runway_hours_low"] == pytest.approx(48.0)
    assert d["indent_lag"]["median_days"] == 6.0 and d["indent_lag"]["sample_count"] == 3
    assert d["pending_indent"]["status"] == "PENDING_INDENT_TOO_LATE"
    assert d["bridge_hours"] == pytest.approx(144.0)
    assert d["need_units"] == pytest.approx(80.0)
    verdicts = {x["donor"]: x["verdict"] for x in d["donors"]}
    assert verdicts == {"DON_A": "ARRIVES_AFTER_FAILURE", "DON_B": "FEASIBLE"}
    assert d["status"] == RESCUE_FEASIBLE and case.status == CaseStatus.AWAITING_APPROVAL
    assert d["plan"]["allocated_quantity"] == 35.0 and d["plan"]["unmet_quantity"] == 45.0
    assert d["plan"]["covers_need"] is False
    assert d["plan"]["donor_remaining_stock"] == {"DON_B": 140.0}     # exactly its 14-day reserve

    with pytest.raises(PermissionError):
        loop.approve(case.case_id, "clerk", "storekeeper", T0)
    ap = loop.approve(case.case_id, "dr_rao", "medical_officer", T0)
    assert case.status == CaseStatus.READY_FOR_EXECUTION
    order = ships.get_order(ap["shipment_ids"][0])
    assert order.status == "ORDERED" and order.ordered_quantity == 35.0

    loop.dispatch(case.case_id, T0)
    assert order.status == "IN_TRANSIT" and case.status == CaseStatus.EXECUTING
    assert graph.get_resource_state("DON_B", RID).usable_quantity == 140.0

    r = loop.receive(case.case_id, order.order_id, received=27, damaged=3, at=h(8))
    assert order.status == "DELIVERED" and order.usable_quantity == 24
    assert r["result"] == "REPLAN" and r["still_unsafe"] is True
    assert r["outcome"]["usable_received"] == 24 and r["outcome"]["failure_avoided"] is True   # arrived before failure
    st = graph.get_resource_state("TOKAPAL", RID)
    assert st.usable_quantity == 64.0                                  # 40 counted + 24 usable received
    replan = r["replan"]
    # DON_B has nothing above its reserve any more and DON_A still cannot arrive in time.
    assert replan["status"] == NO_FEASIBLE_RESCUE
    assert replan["explanation"]["shortfall_units"] > 0 and replan["explanation"]["available_on_time_units"] == 0
    assert case.status == CaseStatus.ASSESSING                          # stays open
    assert case.evidence["escalation_required"] is True

    steps = [e["step"] for e in loop.timeline(case.case_id)]
    for needed in ("STOCK_OBSERVATION", "PHYSICAL_VERIFICATION", "RUNWAY", "PENDING_INDENT", "RESCUE_ASSESSMENT",
                   "DONOR_SELECTION", "APPROVAL", "SHIPMENT_ORDERED", "SHIPMENT_DISPATCHED", "SHIPMENT_RECEIVED",
                   "RECALCULATION", "RISK_RECALCULATION", "REPLAN_REQUIRED"):
        assert needed in steps
    hist = [x["to_status"] for x in case.history]
    assert hist[:5] == ["DETECTED", "ASSESSING", "PLANNING", "AWAITING_APPROVAL", "APPROVED"]
    assert {"RISK_RECALCULATED", "REPLAN_REQUIRED", "DELIVERED", "MEASURED"} <= set(hist)


def test_delivery_that_solves_the_shortage_closes_the_case():
    loop, graph, ships = build(donors=(("DON_A", 60.0, 72.0), ("DON_B", 100.0, 8.0)))
    case, d = assess(loop)
    assert d["plan"]["allocated_quantity"] == 80.0 and d["plan"]["covers_need"] is True
    loop.approve(case.case_id, "dr", "medical_officer", T0)
    loop.dispatch(case.case_id, T0)
    oid = ships.orders[-1].order_id
    # Storekeeper physically recounts at receipt: 40 - 6.7 consumed + 80 received = ~113.
    r = loop.receive(case.case_id, oid, received=80, damaged=0, at=h(8), recipient_count=115.0)
    assert r["result"] == "CLOSED" and case.status == CaseStatus.CLOSED and r["still_unsafe"] is False


def test_exact_cover_with_aged_count_is_not_silently_closed():
    """Without a recount, 8h of unrecorded-consumption uncertainty keeps the case open."""
    loop, graph, ships = build(donors=(("DON_A", 60.0, 72.0), ("DON_B", 100.0, 8.0)))
    case, _ = assess(loop)
    loop.approve(case.case_id, "dr", "medical_officer", T0)
    loop.dispatch(case.case_id, T0)
    r = loop.receive(case.case_id, ships.orders[-1].order_id, received=80, damaged=0, at=h(8))
    assert r["result"] == "REPLAN" and case.status != CaseStatus.CLOSED


def test_failed_shipment_triggers_replan_not_closure():
    loop, graph, ships = build()
    case, _ = assess(loop)
    loop.approve(case.case_id, "dr", "medical_officer", T0)
    loop.dispatch(case.case_id, T0)
    oid = ships.orders[-1].order_id
    r = loop.fail_shipment(case.case_id, oid, "vehicle breakdown", h(6))
    assert ships.get_order(oid).status == "FAILED"
    assert r["result"] == "REPLAN" and case.status != CaseStatus.CLOSED
    hist = [x["to_status"] for x in case.history]
    assert "DELIVERY_FAILED" in hist and "RISK_RECALCULATED" in hist and "REPLAN_REQUIRED" in hist


# ---------------------------------------------------------------- feature 11
def test_no_feasible_rescue_never_fabricates_supply():
    loop, _, _ = build(donors=(("DON_A", 60.0, 72.0),), indent=False, lags=())
    case, d = assess(loop)
    assert d["status"] == NO_FEASIBLE_RESCUE
    e = d["explanation"]
    assert e["available_on_time_units"] == 0 and e["available_network_units"] == 60.0
    assert e["shortfall_units"] == e["required_units"] and e["time_remaining_hours"] == pytest.approx(48.0)
    assert e["constraints"] and case.status == CaseStatus.ASSESSING


def test_partial_supply_policy_is_explicit():
    donors = (("DON_C", 40.0, 8.0),)
    # need = 20/24*168 - 40 = 100 (no indent, no history -> configured 7-day bridge)
    loop, _, _ = build(donors=donors, indent=False, lags=())
    _, d = assess(loop)
    assert d["need_units"] == pytest.approx(100.0)
    assert d["status"] == RESCUE_FEASIBLE and d["plan"]["unmet_quantity"] == pytest.approx(60.0)

    strict, _, _ = build(donors=donors, indent=False, lags=(), policy=LoopPolicy(require_full_coverage=True, max_distance_km=5000.0))
    _, d2 = assess(strict)
    assert d2["status"] == NO_FEASIBLE_RESCUE
    assert d2["explanation"]["shortfall_units"] == pytest.approx(60.0)


# ---------------------------------------------------------------- feature 12: data age
def test_decision_changes_with_observation_age():
    kw = dict(stock=200.0, reported=200.0, indent=False, lags=(), donors=(("DON_B", 35.0, 96.0),))
    fresh_loop, _, _ = build(observed_at=h(-1), **kw)
    _, fresh = assess(fresh_loop)
    old_loop, _, _ = build(observed_at=T0 - timedelta(days=5), **kw)
    _, old = assess(old_loop)
    assert fresh["status"] == NO_RESCUE_NEEDED                          # ~10 days of stock
    assert old["observation"]["freshness_status"] == STALE
    assert old["status"] == RESCUE_REQUIRES_VERIFICATION                # donor ETA sits inside the uncertainty window
    assert old["observation"]["stock_estimate"] < fresh["observation"]["stock_estimate"]


def test_very_old_observation_forces_verification():
    loop, _, _ = build(stock=200.0, reported=200.0, indent=False, lags=(), observed_at=T0 - timedelta(days=20),
                       donors=(("DON_B", 35.0, 2.0),))
    _, d = assess(loop)
    assert d["observation"]["freshness_status"] == VERIFY_REQUIRED
    assert d["status"] in (RESCUE_REQUIRES_VERIFICATION, NO_RESCUE_NEEDED)


# ---------------------------------------------------------------- feature 13: indent lag
def test_indent_before_failure_is_sufficient():
    loop, _, _ = build(stock=60.0, reported=60.0, indent_eta_days=2.0, lags=())     # runway 3 days, indent 2 days
    case, d = assess(loop)
    assert d["status"] == PENDING_SUPPLY_SUFFICIENT and case.status == CaseStatus.CLOSED


def test_indent_after_failure_is_too_late_and_triggers_search():
    loop, _, _ = build(stock=60.0, reported=60.0, indent_eta_days=7.0, lags=())     # runway 3 days, indent 7 days
    _, d = assess(loop)
    assert d["pending_indent"]["status"] == "PENDING_INDENT_TOO_LATE"
    assert d["status"] == RESCUE_FEASIBLE and d["bridge_hours"] == pytest.approx(168.0)
    assert d["indent_lag"]["status"] == LEAD_TIME_INSUFFICIENT_DATA


def test_learned_lag_overrides_optimistic_stated_date():
    # Supplier says 2 days, this facility's own history says ~6 days: the later one is used.
    loop, _, _ = build(stock=60.0, reported=60.0, indent_eta_days=2.0, lags=(5, 6, 7))
    _, d = assess(loop)
    assert d["pending_indent"]["status"] == "PENDING_INDENT_TOO_LATE"
    assert d["pending_indent"]["details"][0]["arrival_hours"] == pytest.approx(144.0)


# ---------------------------------------------------------------- feature 14: hostile mutation
def _need_and_status(**kw):
    loop, _, _ = build(**kw)
    _, d = assess(loop)
    return d


def test_mutation_stock_and_consumption_move_the_result():
    base = _need_and_status()
    assert _need_and_status(stock=50.0)["need_units"] < base["need_units"]
    assert _need_and_status(rate=30.0, stock=40.0)["observation"]["runway_hours_low"] < 48.0


def test_mutation_indent_eta_and_quantity():
    late = _need_and_status(indent_eta_days=6.0, lags=())
    later = _need_and_status(indent_eta_days=9.0, lags=())
    assert later["bridge_hours"] > late["bridge_hours"] and later["need_units"] > late["need_units"]
    early = _need_and_status(indent_eta_days=1.0, lags=())
    assert early["status"] == PENDING_SUPPLY_SUFFICIENT


def test_mutation_donor_stock_reserve_and_eta():
    base = _need_and_status()
    more = _need_and_status(donors=(("DON_A", 60.0, 72.0), ("DON_B", 60.0, 8.0)))
    assert more["plan"]["allocated_quantity"] > base["plan"]["allocated_quantity"]
    slow = _need_and_status(donors=(("DON_A", 60.0, 72.0), ("DON_B", 35.0, 60.0)))
    assert slow["status"] == NO_FEASIBLE_RESCUE                        # 60h > 48h runway
    bigger_reserve = _need_and_status(policy=LoopPolicy(min_safety_days=20.0, max_distance_km=5000.0))
    assert bigger_reserve["status"] == NO_FEASIBLE_RESCUE              # DON_B no longer has surplus


def test_mutation_received_and_damaged_quantity():
    results = []
    for received, damaged in ((35, 0), (27, 3), (0, 0)):
        loop, graph, ships = build(donors=(("DON_A", 60.0, 72.0), ("DON_B", 100.0, 8.0)))
        case, _ = assess(loop)
        loop.approve(case.case_id, "dr", "medical_officer", T0)
        loop.dispatch(case.case_id, T0)
        oid = ships.orders[-1].order_id
        loop.receive(case.case_id, oid, received=received, damaged=damaged, at=h(8))
        results.append(graph.get_resource_state("TOKAPAL", RID).usable_quantity)
    assert results[0] > results[1] > results[2]


def test_dispatch_cannot_exceed_plan_and_receipt_cannot_exceed_dispatch():
    loop, _, ships = build()
    case, _ = assess(loop)
    loop.approve(case.case_id, "dr", "medical_officer", T0)
    oid = ships.orders[-1].order_id
    with pytest.raises(ValueError):
        loop.dispatch(case.case_id, T0, {oid: 999.0})
    loop.dispatch(case.case_id, T0, {oid: 30.0})
    with pytest.raises(ValueError):
        loop.receive(case.case_id, oid, received=35, damaged=0, at=h(8))
    with pytest.raises(ValueError):
        loop.receive(case.case_id, oid, received=10, damaged=11, at=h(8))


def test_illegal_case_transition_still_rejected():
    loop, _, _ = build()
    case = loop.open_case("TOKAPAL", RID, T0)
    with pytest.raises(ValueError):
        case.transition(CaseStatus.EXECUTING)
