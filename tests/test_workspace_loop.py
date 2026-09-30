"""The closed operational loop on the event-sourced workspace (synthetic TEST fixture only)."""
from __future__ import annotations

import pytest

from tathyon.workspace import Workspace, WorkspaceError
from tests.fixtures import phantom_scenario as sp


@pytest.fixture
def ws() -> Workspace:
    w = Workspace()
    sp.load(w)
    return w


def test_fixture_intake_quarantines_bad_rows_with_typed_reasons(ws):
    reasons = sorted(r for q in ws.quarantine for r in q["reasons"])
    assert reasons == ["MISSING_REQUIRED_FIELD", "UNKNOWN_FACILITY"]


def test_trust_queue_picks_the_count_that_protects_most_units_within_budget(ws):
    rows = {r["facility_id"]: r for r in ws.trust_queue("OXY-10", verifier_hours=6)["rows"]}
    assert rows["PHC-X"]["recommended_action"] == "VERIFY"
    assert rows["PHC-U"]["recommended_action"] == "WAIT"   # does not fit alongside PHC-X
    assert rows["PHC-Y"]["recommended_action"] == "TRANSFER"
    assert "SURPLUS_OUTLIER_VS_DISTRICT" in rows["PHC-X"]["signals"]


def test_plan_never_makes_unverified_stock_dispatchable(ws):
    plan = sp.run_scene(ws, 0)["result"]
    assert plan["recommended_option"] == "VERIFY_THEN_TRANSFER"
    assert all(l["kind"] == "CONTINGENT_ON_VERIFICATION" for l in plan["lines"] if l["from_facility"] == "PHC-X")
    naive = next(o for o in plan["options"] if o["option"] == "TRUST_REPORTED")
    assert naive["approvable"] is False
    gated = next(o for o in plan["options"] if o["option"] == "TRANSFER_VERIFIED_NOW")
    assert "PHC-X" not in {l["from_facility"] for l in gated["lines"]}
    assert ("PHC-V", "COLD_CHAIN_INCOMPATIBLE") in {(d["facility_id"], d["reason"]) for d in plan["rejected_donors"]}


def test_contingent_line_cannot_dispatch_before_a_count(ws):
    sp.run_scene(ws, 0)
    sp.run_scene(ws, 1)
    plan = max(ws.plans.values(), key=lambda p: p["version"])
    line = next(l for l in plan["lines"] if l["status"] == "APPROVED_CONTINGENT")
    with pytest.raises(WorkspaceError) as e:
        ws.dispatch(plan["plan_id"], line["line_id"], actor="dr-meera-dmo", role="district_medical_officer")
    assert e.value.code == "VERIFICATION_REQUIRED"


def test_phantom_count_invalidates_plan_and_replan_uses_verified_donors(ws):
    for i in range(3):
        out = sp.run_scene(ws, i)["result"]
    assert out["finding"] == "PHANTOM_STOCK"
    v1 = min(ws.plans.values(), key=lambda p: p["version"])
    assert v1["status"] == "REPLAN_REQUIRED"
    assert all(l["status"] == "INVALIDATED" for l in v1["lines"] if l["from_facility"] == "PHC-X")
    v2 = sp.run_scene(ws, 3)["result"]
    assert v2["parent_plan_id"] == v1["plan_id"]
    assert {l["from_facility"] for l in v2["lines"]} <= {"CHC-Z", "CHC-W"}
    assert ws.plans[v1["plan_id"]]["status"] == "SUPERSEDED"


def test_partial_receipt_triggers_replan_and_outcome_is_measured(ws):
    for i in range(6):
        sp.run_scene(ws, i)
    o = ws.outcome_summary()
    assert o["phantom_units_blocked"] > 0
    assert o["verification_hit_rate"] == 1.0
    assert {c for r in o["replan_events"] for c in r["codes"]} >= {"DONOR_COUNT_BELOW_LINE", "PARTIAL_RECEIPT"}
    assert o["stockout_days_projected"]["current"] < o["stockout_days_projected"]["baseline_at_load"]
    assert any(s["status"] == "RECEIVED_PARTIAL" for s in o["shipments"])


def test_replay_from_ledger_reproduces_every_projection(ws):
    for i in range(6):
        sp.run_scene(ws, i)
    again = Workspace.replay(ws.store)
    assert {k: v["status"] for k, v in again.plans.items()} == {k: v["status"] for k, v in ws.plans.items()}
    assert again.outcome_summary()["phantom_units_blocked"] == ws.outcome_summary()["phantom_units_blocked"]
    assert again.baseline == ws.baseline
    assert ws.store.verify_chain() == (True, None)


def test_human_boundaries_are_enforced(ws):
    plan = sp.run_scene(ws, 0)["result"]
    with pytest.raises(WorkspaceError) as e:
        ws.decide_plan(plan["plan_id"], officer_id="x", role="field_verifier", decision="APPROVE", reason="r")
    assert e.value.code == "ROLE_NOT_AUTHORISED"
    with pytest.raises(WorkspaceError) as e:
        ws.decide_plan(plan["plan_id"], officer_id="d", role="district_medical_officer", decision="APPROVE", reason=" ")
    assert e.value.code == "REASON_REQUIRED"
    with pytest.raises(WorkspaceError) as e:
        ws.decide_plan(plan["plan_id"], officer_id="d", role="district_medical_officer", decision="APPROVE",
                       option="TRUST_REPORTED", reason="try naive")
    assert e.value.code == "OPTION_NOT_APPROVABLE"
    ws.decide_plan(plan["plan_id"], officer_id="d", role="district_medical_officer", decision="APPROVE", reason="ok")
    with pytest.raises(WorkspaceError) as e:  # replaying a decision is refused
        ws.decide_plan(plan["plan_id"], officer_id="d", role="district_medical_officer", decision="APPROVE", reason="ok")
    assert e.value.code == "PLAN_NOT_PENDING"


def test_custodian_cannot_count_and_counts_are_validated(ws):
    with pytest.raises(WorkspaceError) as e:
        ws.submit_attestation("PHC-X", "OXY-10", attester_id="cust-x", attester_role="field_verifier",
                              present_qty=10, usable_qty=10)
    assert e.value.code == "ATTESTOR_IS_CUSTODIAN"
    with pytest.raises(WorkspaceError) as e:
        ws.submit_attestation("PHC-X", "OXY-10", attester_id="fv", attester_role="field_verifier",
                              present_qty=10, usable_qty=11)
    assert e.value.code == "INVALID_COUNT"
    with pytest.raises(WorkspaceError) as e:
        ws.submit_attestation("PHC-X", "OXY-10", attester_id="agent:x", attester_role="llm",
                              present_qty=1, usable_qty=1)
    assert e.value.code == "ROLE_NOT_AUTHORISED"


def test_receipt_rules_refuse_replay_and_self_confirmation(ws):
    for i in range(4):
        sp.run_scene(ws, i)
    sh = next(iter(ws.shipments.values()))
    with pytest.raises(WorkspaceError) as e:
        ws.receive(sh["shipment_id"], received_qty=1, damaged_qty=0, receiver_id=sh["dispatched_by"],
                   receiver_role="facility_incharge")
    assert e.value.code == "RECEIVER_IS_DISPATCHER"
    with pytest.raises(WorkspaceError) as e:
        ws.receive(sh["shipment_id"], received_qty=sh["qty"] + 1, damaged_qty=0, receiver_id="r",
                   receiver_role="facility_incharge")
    assert e.value.code == "INVALID_RECEIPT"
    ws.receive(sh["shipment_id"], received_qty=sh["qty"], damaged_qty=0, receiver_id="r", receiver_role="facility_incharge")
    with pytest.raises(WorkspaceError) as e:
        ws.receive(sh["shipment_id"], received_qty=sh["qty"], damaged_qty=0, receiver_id="r",
                   receiver_role="facility_incharge")
    assert e.value.code == "SHIPMENT_CLOSED"


def test_break_glass_requires_reason_and_creates_obligation(ws):
    sp.run_scene(ws, 0)
    sp.run_scene(ws, 1)
    plan = max(ws.plans.values(), key=lambda p: p["version"])
    line = next(l for l in plan["lines"] if l["status"] == "APPROVED_CONTINGENT")
    with pytest.raises(WorkspaceError):
        ws.break_glass(plan["plan_id"], line["line_id"], officer_id="d", role="district_medical_officer",
                       reason_code="BECAUSE", justification="short")
    ws.break_glass(plan["plan_id"], line["line_id"], officer_id="d", role="district_medical_officer",
                   reason_code="CLINICAL_EMERGENCY", justification="Postpartum haemorrhage cases, no verified donor in time")
    assert ws.obligations and ws.obligations[-1]["kind"] == "POST_HOC_VERIFICATION"
    shipped = ws.dispatch(plan["plan_id"], line["line_id"], actor="d", role="district_medical_officer")
    assert shipped["status"] == "IN_TRANSIT"


def test_tampering_is_detected(ws):
    ws.store.events[5].payload["reported_qty"] = 999999
    ok, bad = ws.store.verify_chain()
    assert ok is False and bad == 5


def test_sandbox_never_writes_to_the_real_ledger(ws):
    before = len(ws.store.events)
    sb = ws.sandbox()
    sb.propose_plan("OXY-10", actor="sandbox")
    assert len(ws.store.events) == before and len(sb.store.events) == before + 1
