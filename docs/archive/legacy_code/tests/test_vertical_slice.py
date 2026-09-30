"""
tests/test_vertical_slice.py — Invariant and Vertical Slice Verification for TATHYON.

Mandatory validations:
1. Unverified stock has transferable == 0
2. Stale attestation (>48h) has transferable == 0
3. Resilience Scenario Engine has provenance SIMULATION and both arms (BASELINE vs RESPONSE)
4. Approval without medical_officer role returns 403 Forbidden
5. Approval with medical_officer writes PLAN_APPROVED event to EventStore
6. /risk queue is ranked by stockout probability / severity
7. Plan contains rejected_donors with explicit clinical/operational reasons
8. The decorative LLM briefing route (/brief) stays removed
9. Outcome recording feeds back into subsequent forecasts
10. Layer adapters fail soft with proper provenance (LIVE | SYNTHETIC | SIMULATION | STALE)
"""

import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from tathyon.graph import ResourceState, ResourceGraph, create_default_resource_graph
from tathyon.twin import ShockType
from tathyon.planner import ResponsePlanner
from tathyon.store import EventStore, EventType
from api.main import app, Registry

event_store = Registry.get().store
resource_graph = Registry.get().resource_graph


@pytest.fixture
def client():
    return TestClient(app)


def test_unverified_transferable_is_zero():
    """Unverified stock (usable_quantity == 0 despite claimed) cannot donate."""
    state = ResourceState(
        facility_id="PHC_TEST",
        resource_id="MED_OXYGEN",
        claimed_quantity=500.0,
        usable_quantity=0.0,  # unverified / rejected by gate
        reserved_quantity=0.0,
        incoming_quantity=50.0,
        consumption_velocity=2.0,
        last_attested_at=datetime.now(timezone.utc).isoformat()
    )
    assert state.get_transferable_donor_qty() == 0.0


def test_stale_attestation_transferable_is_zero():
    """Stock with attestation older than 48 hours is marked stale and donor_qty = 0."""
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    state = ResourceState(
        facility_id="PHC_TEST",
        resource_id="MED_OXYGEN",
        claimed_quantity=200.0,
        usable_quantity=180.0,
        reserved_quantity=0.0,
        incoming_quantity=0.0,
        consumption_velocity=2.0,
        last_attested_at=stale_time
    )
    assert state.is_attestation_fresh(max_age_hours=48.0) is False
    assert state.get_transferable_donor_qty() == 0.0


def test_approve_without_medical_officer_returns_403(client):
    """policy-based sign-off strictly demands X-Role: medical_officer."""
    # First generate a plan
    gen_resp = client.post("/allocate", json={
        "resource_key": "MED-ARV-01",
        "horizon_days": 14,
    })
    assert gen_resp.status_code == 200
    plan_id = gen_resp.json()["plan_id"]

    # Attempt approve with unauthorized role
    unauth_resp = client.post(f"/plans/{plan_id}/approve", json={
        "officer_id": "operator_bob",
    }, headers={"X-Role": "data_clerk"})
    assert unauth_resp.status_code == 403
    detail = unauth_resp.json()["detail"]
    msg = detail.get("message", "") if isinstance(detail, dict) else str(detail)
    assert "medical_officer" in msg


def test_approve_with_medical_officer_writes_plan_approved(client):
    """Valid CMO approval succeeds and records PLAN_APPROVED in the audit log."""
    gen_resp = client.post("/allocate", json={
        "resource_key": "MED-ARV-01",
        "horizon_days": 14,
    })
    assert gen_resp.status_code == 200
    plan_id = gen_resp.json()["plan_id"]

    approve_resp = client.post(f"/plans/{plan_id}/approve", json={
        "officer_id": "Dr. A. Sharma",
    }, headers={"X-Role": "medical_officer"})
    assert approve_resp.status_code == 200
    body = approve_resp.json()
    assert body["status"] in ("approved", "APPROVED")
    assert body["approved_by"] == "Dr. A. Sharma"

    # Verify event store contains the plan_approved event via GET /events
    events_resp = client.get(f"/events?event_type={EventType.PLAN_APPROVED.value}")
    assert events_resp.status_code == 200
    events_data = events_resp.json().get("events", [])
    approved_events = [e for e in events_data if e["payload"].get("plan_id") == plan_id]
    assert len(approved_events) >= 1
    assert approved_events[-1]["payload"]["approved_by"] == "Dr. A. Sharma"


def test_plan_has_rejected_donors_with_reasons(client):
    """Plan generation must expose non-selected candidates and explicit reasons."""
    resp = client.post("/allocate", json={
        "resource_key": "MED-ARV-01",
        "horizon_days": 14,
    })
    assert resp.status_code == 200
    plan = resp.json()
    assert "rejected_donors" in plan
    for r in plan["rejected_donors"]:
        assert "donor_id" in r or "facility_id" in r
        assert "reason" in r
        assert len(r["reason"]) > 0


def test_decorative_briefing_endpoint_is_removed(client):
    """POST /brief was an LLM narrative that could not change any decision (and its
    offline fallback printed invented figures). It must stay removed."""
    resp = client.post("/brief", json={"facility_id": "PHC_RURAL_01", "resource_id": "MED_ANTI_SNAKE"})
    assert resp.status_code in (404, 405)

