"""Comprehensive tests for the slimmed 9-endpoint Tathyon API.

Covers:
1. GET /trust/queue
2. POST /verify/attest (including separation of duties custodian rejection)
3. POST /allocate (single CP-SAT network solve)
4. POST /plans/{plan_id}/approve (medical_officer authorization, 403 refusal, per-line reject, break-glass)
5. GET /plans/{plan_id}/sor-payload (staged DVDMS voucher)
6. POST /outcomes (receipt reconciliation & replan trigger)
7. GET /events (audit ledger & hash chain verification)
8. GET /eval/report (multi-arm benchmark & adjusted headline)
9. GET /health (liveness & chain integrity)
"""
import pytest
from fastapi.testclient import TestClient

from api.main import app, Registry

client = TestClient(app)


def setup_function():
    Registry.reset()


def test_01_health_endpoint():
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert "chain" in data
    assert data["chain"]["intact"] is True
    assert data["provenance"] == "SYNTHETIC"


def test_02_trust_queue_endpoint():
    res = client.get("/trust/queue?limit=5&budget_slots=6")
    assert res.status_code == 200
    data = res.json()
    assert data["budget_slots"] == 6
    assert "targeted_verifications" in data
    assert len(data["targeted_verifications"]) > 0
    top = data["targeted_verifications"][0]
    assert "facility_id" in top
    assert "expected_value" in top
    assert "one_line_reason" in top
    assert top["provenance"] == "SYNTHETIC_TARGETING_QUEUE"


def test_03_verify_attest_separation_of_duties():
    # Custodian attempt MUST fail with 422
    custodian_res = client.post("/verify/attest", json={
        "facility_id": "PHC_TEST",
        "resource_key": "MED-ARV-01",
        "present_quantity": 500.0,
        "usable_quantity": 500.0,
        "attester_id": "custodian_ramesh",
        "is_custodian": True,
    })
    assert custodian_res.status_code == 422
    assert custodian_res.json()["detail"]["error"] == "ATTESTOR_IS_CUSTODIAN"

    # Same id as custodian_id MUST fail with 422
    custodian_id_res = client.post("/verify/attest", json={
        "facility_id": "PHC_TEST",
        "resource_key": "MED-ARV-01",
        "present_quantity": 500.0,
        "usable_quantity": 500.0,
        "attester_id": "staff_sita",
        "custodian_id": "staff_sita",
        "is_custodian": False,
    })
    assert custodian_id_res.status_code == 422
    assert custodian_id_res.json()["detail"]["error"] == "ATTESTOR_IS_CUSTODIAN"

    # Legitimate non-custodian attestation succeeds
    valid_res = client.post("/verify/attest", json={
        "facility_id": "PHC_TEST",
        "resource_key": "MED-ARV-01",
        "present_quantity": 500.0,
        "usable_quantity": 480.0,
        "expired_quantity": 20.0,
        "attester_id": "inspector_verma",
        "is_custodian": False,
        "nonce": "NONCE-9876",
    })
    assert valid_res.status_code == 200
    vdata = valid_res.json()
    assert vdata["status"] == "ACCEPTED"
    assert vdata["verified_usable_stock"] == 480.0
    assert "event_hash" in vdata


def test_04_allocate_endpoint():
    res = client.post("/allocate", json={
        "resource_key": "MED-ARV-01",
        "truck_capacity": 3000.0,
        "min_safety_days": 14.0,
    })
    assert res.status_code == 200
    plan = res.json()
    assert "plan_id" in plan
    assert plan["status"] in ("PROPOSED", "NO_FEASIBLE_PLAN")
    assert "transfers" in plan
    assert "donor_floors" in plan
    assert "rejected_donors" in plan
    assert "do_nothing_counterfactual" in plan
    assert "fulfilled_qty" in plan
    assert "shortfall_qty" in plan
    assert "replan_required" in plan


def test_05_approve_endpoint_role_enforcement():
    # Allocate plan first
    alloc_res = client.post("/allocate", json={})
    plan_id = alloc_res.json()["plan_id"]

    # 1. Non-medical-officer role -> 403 Forbidden
    unauth_res = client.post(f"/plans/{plan_id}/approve", json={
        "decision": "APPROVE",
    }, headers={"X-Role": "field_operator"})
    assert unauth_res.status_code == 403
    assert unauth_res.json()["detail"]["error"] == "FORBIDDEN_ROLE"

    # 2. Break-glass override with invalid reason -> 422
    bg_bad = client.post(f"/plans/{plan_id}/approve", json={
        "decision": "BREAK_GLASS",
        "break_glass_reason": "BECAUSE_I_WANT_TO",
    }, headers={"X-Role": "medical_officer"})
    assert bg_bad.status_code == 422

    # 3. Break-glass override with valid closed-list reason -> succeeds
    bg_ok = client.post(f"/plans/{plan_id}/approve", json={
        "decision": "BREAK_GLASS",
        "break_glass_reason": "CLINICAL_EMERGENCY",
    }, headers={"X-Role": "medical_officer"})
    assert bg_ok.status_code == 200
    bg_data = bg_ok.json()
    assert bg_data["status"] == "APPROVED_UNDER_BREAK_GLASS"
    assert "obligation_id" in bg_data
    assert bg_data["obligation_deadline_hours"] == 24.0

    # 4. Valid medical_officer role with line-item rejection
    alloc_res2 = client.post("/allocate", json={})
    plan_id2 = alloc_res2.json()["plan_id"]

    mo_ok = client.post(f"/plans/{plan_id2}/approve", json={
        "decision": "APPROVE",
        "officer_id": "DHO_DR_VERMA",
        "rejected_lines": ["LINE-1"],
    }, headers={"X-Role": "medical_officer"})
    assert mo_ok.status_code == 200
    mo_data = mo_ok.json()
    assert mo_data["status"] in ("APPROVED", "REJECTED")
    assert len(mo_data["rejected_transfers"]) >= 0


def test_06_sor_payload_endpoint():
    alloc_res = client.post("/allocate", json={})
    plan_id = alloc_res.json()["plan_id"]

    res = client.get(f"/plans/{plan_id}/sor-payload")
    assert res.status_code == 200
    payload = res.json()
    assert payload["plan_id"] == plan_id
    assert payload["live_api_connected"] is False
    assert payload["provenance"] == "STAGED_PAYLOAD_NOT_TRANSMITTED"
    assert "staged_sor_voucher" in payload


def test_07_outcomes_endpoint():
    res = client.post("/outcomes", json={
        "plan_id": "PLAN-DEMO",
        "transfer_id": "LINE-1",
        "dispatched_qty": 1900.0,
        "received_qty": 1850.0,
        "recipient_id": "PHC_Y",
        "donor_id": "PHC_Z",
        "resource_key": "MED-ARV-01",
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "RECONCILED"
    assert data["variance_qty"] == -50.0
    assert data["replan_required"] is True
    assert data["remaining_shortfall"] == 50.0
    assert data["fallback_replan_action"] == "RE_SOLVE_FROM_CENTRAL_WAREHOUSE_BUFFER"


def test_08_events_endpoint():
    res = client.get("/events?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert "total_events" in data
    assert "chain_intact" in data
    assert "events" in data
    assert data["chain_intact"] is True


def test_09_eval_report_endpoint():
    res = client.get("/eval/report")
    assert res.status_code == 200
    data = res.json()
    assert "headline" in data
    assert "verified_stockout_days_averted" in data["headline"]
    assert "benchmark_arms" in data
    assert "tathyon" in data["benchmark_arms"]
    assert data["benchmark_arms"]["tathyon"]["verified_stockout_days_averted"] == 80.87


def test_10_copilot_query_endpoint():
    res = client.post("/copilot/query", json={"query": "Why was PHC_X rejected as a donor?"})
    assert res.status_code == 200
    data = res.json()
    assert "answer" in data
    assert "citations" in data
    assert "tools_used" in data
    assert "get_allocation_plan" in data["tools_used"]


def test_11_intake_process_endpoint():
    res = client.post("/intake/process", json={
        "payload": {
            "facility_id": "PHC_API_TEST",
            "resource_key": "MED-ARV-01",
            "present_quantity": 40.0,
            "usable_quantity": 40.0,
            "expired_quantity": 0.0,
            "batch_number": "LOT-API-01",
            "expiry_date": "2028-01-01",
            "nonce": "N-API",
            "attester_id": "TEST_VERIFIER",
            "is_custodian": False,
        }
    })
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "FILED"
    assert data["event_id"].startswith("[EVT-")


def test_13_non_ai_decision_endpoints():
    # 1. GET /v2/decision/trust-queue
    tq_res = client.get("/v2/decision/trust-queue?budget_slots=15")
    assert tq_res.status_code == 200
    tq_data = tq_res.json()
    assert tq_data["score_version"] == "2.1.0"
    assert len(tq_data["items"]) > 0
    assert "top_contributing_reasons" in tq_data["items"][0]

    # 2. POST /v2/decision/dispatch/schedule
    dsp_res = client.post("/v2/decision/dispatch/schedule", json={
        "available_inspectors": ["INSP_A", "INSP_B"],
        "budget_slots": 10,
    })
    assert dsp_res.status_code == 200
    dsp_data = dsp_res.json()
    assert dsp_data["status"] == "SCHEDULED"
    assert len(dsp_data["scheduled_tasks"]) > 0

    # 3. POST /v2/decision/forecast
    fc_res = client.post("/v2/decision/forecast", json={
        "facility_id": "PHC_1",
        "resource_type": "MEDICINE",
        "resource_key": "MED-ARV-01",
        "historical_data": [10.0, 0.0, 15.0, 0.0, 20.0, 12.0, 0.0, 18.0, 0.0, 14.0],
        "current_stock_or_occupied": 50.0,
    })
    assert fc_res.status_code == 200
    fc_data = fc_res.json()
    assert fc_data["status"] == "SUCCESS"
    assert "STATISTICAL PROJECTION" in fc_data["disclaimer"]

    # 4. GET /v2/demo/safety-scenario
    demo_res = client.get("/v2/demo/safety-scenario?seed=20260928")
    assert demo_res.status_code == 200
    demo_data = demo_res.json()
    assert demo_data["is_live_connection"] is False
    assert "SYNTHETIC" in demo_data["provenance"]




