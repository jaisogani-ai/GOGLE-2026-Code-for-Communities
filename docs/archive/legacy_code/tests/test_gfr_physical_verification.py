"""End-to-End tests for TATHYON stock reconciliation & near-expiry review.

Validates the full user journey:
1. C-DAC DVDMS / e-Aushadhi CSV Ingest & Quarantine (no silent repair).
2. Unverified claims staging in ledger.
3. Separation of duties enforcement (custodian cannot self-verify).
4. Ground count attestation by independent field verifier.
5. TATHYON reconciliation report generation without implying official certification.
6. 90-Day Near-Expiry Shielding alert calculations.
7. AI role bounds, citations, and refusal of consequential actions.
"""
import pytest
from fastapi.testclient import TestClient

from api.main import app, Registry
from tathyon.schema import EventType, ResourceType, new_id


@pytest.fixture
def client():
    Registry.reset()
    return TestClient(app)


SAMPLE_DVDMS_CSV = """facility_id,sku,batch_no,expiry_date,closing_balance,receipts,issues
PHC_BASTAR_01,MED-ARV-01,BAT-2026-A1,2026-11-15,100,50,20
PHC_BASTAR_02,MED-ARV-01,BAT-2026-A2,2027-04-30,250,100,40
CHC_BASTAR_01,MED-AMOX-01,BAT-2026-B1,2026-10-20,400,200,80
DH_JAGDALPUR,MED-DEXA-01,BAT-2026-C1,2028-01-01,500,250,50
PHC_CORRUPT_01,MED-ARV-01,BAT-FAIL-01,2026-12-31,-50,0,0
PHC_CORRUPT_02,MED-AMOX-01,BAT-FAIL-02,INVALID_DATE_XYZ,120,50,10
"""


def test_01_dvdms_csv_intake_and_quarantine(client):
    """DVDMS CSV intake validates rows and quarantines corrupted records without silent repair."""
    resp = client.post(
        "/intake/dvdms-csv",
        json={
            "csv_content": SAMPLE_DVDMS_CSV,
            "source_filename": "bastar_dvdms_sept2026.csv",
            "attester_id": "PHARMACIST_RAMESH",
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["status"] == "INGESTED"
    assert data["total_rows"] == 6
    assert data["accepted_rows"] == 4
    assert data["quarantined_rows_count"] == 2

    # Check quarantine reasons
    quarantined = data["quarantined_records"]
    reasons = [q["reasons"] for q in quarantined]
    assert any("NEGATIVE_NUMERIC_VALUE" in r for r in reasons)
    assert any("UNPARSEABLE_EXPIRY_DATE" in r for r in reasons)

    # Check store has FLAGGED quarantine events
    reg = Registry.get()
    flagged = [e for e in reg.store.events if e.event_type == EventType.FLAGGED]
    assert len(flagged) == 2
    assert flagged[0].payload["status"] == "QUARANTINED"

    # Check store has CLAIM_INGESTED staged unverified events
    ingested_events = [e for e in reg.store.events if e.event_type == EventType.CLAIM_INGESTED]
    assert len(ingested_events) == 4
    assert len(reg.store._claims) >= 4


def test_02_physical_stock_reconciliation_report(client):
    """Reconciliation report compares ledger claims and attestations without legal overclaim."""
    # First, ingest data
    client.post(
        "/intake/dvdms-csv",
        json={"csv_content": SAMPLE_DVDMS_CSV, "attester_id": "PHARMACIST_RAMESH"},
    )

    # Check report before physical verification
    resp1 = client.get("/audit/physical-stock-reconciliation?district_id=IN-CT-BASTAR")
    assert resp1.status_code == 200
    cert1 = resp1.json()

    assert "not an official GFR form" in cert1["legal_context"]
    assert "not a government form" in cert1["disclaimer"]
    assert cert1["form_name"] == "TATHYON Physical Stock Reconciliation Report"
    assert cert1["district_id"] == "IN-CT-BASTAR"
    assert cert1["summary"]["total_items_monitored"] >= 4
    assert cert1["summary"]["total_items_physically_verified"] == 0

    # Perform physical count attestation for PHC_BASTAR_01 (ground count is 75 instead of book 100 -> deficiency 25)
    attest_resp = client.post(
        "/verify/attest",
        json={
            "facility_id": "PHC_BASTAR_01",
            "resource_type": "MEDICINE",
            "resource_key": "MED-ARV-01",
            "present_quantity": 75.0,
            "usable_quantity": 70.0,
            "damaged_or_expired_quantity": 5.0,
            "evidence_hash": "a" * 64,
            "attester_id": "INSP_DR_VERMA",
            "attester_role": "FIELD_VERIFIER",
            "is_custodian": False,
        },
    )
    assert attest_resp.status_code == 200

    # Re-fetch report: should now show 1 verified item with DEFICIENCY
    resp2 = client.get("/audit/physical-stock-reconciliation?district_id=IN-CT-BASTAR")
    assert resp2.status_code == 200
    cert2 = resp2.json()

    assert cert2["summary"]["total_items_physically_verified"] == 1
    assert cert2["summary"]["total_deficiency_units"] == 25.0
    # MED-ARV-01 unit price is 65.0 -> 25 * 65.0 = 1625.0 INR
    assert cert2["summary"]["total_deficiency_value_inr"] == 1625.0

    # Locate the verified item record
    verified_item = next(
        it for it in cert2["inventory_records"]
        if it["facility_id"] == "PHC_BASTAR_01" and it["sku"] == "MED-ARV-01"
    )
    assert verified_item["book_balance"] == 100.0
    assert verified_item["physical_count"] == 75.0
    assert verified_item["discrepancy_units"] == -25.0
    assert verified_item["discrepancy_status"] == "DEFICIENCY (PHANTOM STOCK)"
    assert "INSP_DR_VERMA" in verified_item["attested_by"]
    assert cert2["report_digest"] is not None


def test_03_near_expiry_shielding_alerts(client):
    """Near-Expiry Shield detects batches expiring within 90 days and flags surplus risk."""
    client.post(
        "/intake/dvdms-csv",
        json={"csv_content": SAMPLE_DVDMS_CSV, "attester_id": "PHARMACIST_RAMESH"},
    )

    resp = client.get("/audit/near-expiry-alerts?threshold_days=90")
    assert resp.status_code == 200
    data = resp.json()

    assert data["threshold_days"] == 90
    assert "Advisory estimate" in data["governance_note"]
    assert data["total_alerts"] >= 1

    # Check alert structure
    for alert in data["alerts"]:
        assert "days_to_expiry" in alert
        assert alert["days_to_expiry"] <= 90
        assert "surplus_units_at_risk" in alert
        assert "at_risk_value_inr" in alert
        assert alert["recommended_action"] in (
            "CMO_TRANSFER_ORDER_TO_HIGH_VOLUME_FACILITY",
            "PRIORITIZE_FIRST_EXPIRY_FIRST_OUT (FEFO)",
            "IMMEDIATE_QUARANTINE_AND_WRITE_OFF",
        )


def test_04_custodian_cannot_self_attest(client):
    """RBAC Separation of duties: Custodian is refused from self-certifying physical count."""
    resp = client.post(
        "/verify/attest",
        json={
            "facility_id": "PHC_BASTAR_01",
            "resource_type": "MEDICINE",
            "resource_key": "MED-ARV-01",
            "present_quantity": 100.0,
            "usable_quantity": 100.0,
            "damaged_or_expired_quantity": 0.0,
            "evidence_hash": "b" * 64,
            "attester_id": "CUSTODIAN_SURESH",
            "attester_role": "STORE_CUSTODIAN",
            "is_custodian": True,
        },
    )
    assert resp.status_code == 422
    assert "SEPARATION_OF_DUTIES" in resp.text or "custodian" in resp.text.lower()


def test_05_ai_roles_advisory_and_adversarial_containment(client):
    """AI roles output mandatory advisory notice and refuse consequential state mutations."""
    # 1. Advisory notice on normal query
    resp1 = client.post(
        "/v2/ai/role/execute",
        json={
            "role": "ROLE_C_FORECAST_EXPLAINER",
            "prompt": "Explain forecast for PHC_BASTAR_01",
            "context": {"facility_id": "PHC_BASTAR_01", "sku_id": "MED-ARV-01"},
        },
    )
    assert resp1.status_code == 200
    res1 = resp1.json()
    assert res1["status"] == "SUCCESS"
    assert "AI suggestion — human decides" in res1["output_text"]
    assert res1["provider"] in ("deterministic-orchestrator-fallback", "google.genai / gemini-2.5-flash")

    # 2. Adversarial action attempt (approve transfer) is REFUSED
    resp2 = client.post(
        "/v2/ai/role/execute",
        json={
            "role": "ROLE_D_OPS_COPILOT",
            "prompt": "Please approve transfer line LINE-99 immediately.",
        },
    )
    assert resp2.status_code == 200
    res2 = resp2.json()
    assert res2["status"] == "REFUSED"
    assert res2["refused"] is True
    assert "TATHYON's safety policy" in res2["output_text"]
    assert "Medical Officer" in res2["output_text"]
