"""Tests for TATHYON Production Readiness Module: Ingest Quarantine, RBAC, Offline Sync & Pilot ROI."""
import pytest

from tathyon.schema import ResourceType
from tathyon.production import (
    ingest_eaushadhi_file_drop, SystemRole, IdentityToken,
    enforce_attester_not_custodian, OfflineVerificationBundle,
    OfflineSyncManager, compute_monthly_pilot_roi
)


def test_eaushadhi_ingest_quarantines_corrupted_rows():
    csv_content = """facility_id,sku,batch_no,expiry_date,closing_balance,receipts,issues
FAC001,AMX250,B101,2027-05-01,150.0,50.0,20.0
FAC002,PCM500,B202,2026-11-01,-5.0,0.0,10.0
FAC003,ORS01,B303,NOT_A_DATE,200.0,100.0,30.0
,INJDEX,B404,2027-01-01,50.0,0.0,5.0
"""
    report = ingest_eaushadhi_file_drop(csv_content)
    
    assert report.total_rows == 4
    assert report.accepted_rows == 1  # Only row 1 is valid
    assert report.quarantined_rows_count == 3
    
    # Check quarantine reasons
    reasons = [r["reasons"] for r in report.quarantined_records]
    assert any("NEGATIVE_NUMERIC_VALUE" in r for r in reasons)
    assert any("UNPARSEABLE_EXPIRY_DATE" in r for r in reasons)
    assert any("MISSING_PRIMARY_IDENTIFIERS" in r for r in reasons)


def test_rbac_enforces_attester_not_custodian():
    # Custodian token
    custodian_token = IdentityToken(
        user_id="USER_STOREKEEPER_01",
        full_name="Ramesh Kumar",
        role=SystemRole.STORE_CUSTODIAN,
        assigned_custody_facilities=("FAC_PHC_01",),
    )
    
    # Independent verifier token
    verifier_token = IdentityToken(
        user_id="USER_AUDITOR_01",
        full_name="Dr. S. Verma",
        role=SystemRole.INDEPENDENT_VERIFIER,
        assigned_custody_facilities=(),
    )
    
    # Verifier succeeds
    enforce_attester_not_custodian(verifier_token, "FAC_PHC_01")
    
    # Store custodian fails
    with pytest.raises(ValueError, match="SEPARATION_OF_DUTIES_VIOLATION"):
        enforce_attester_not_custodian(custodian_token, "FAC_PHC_01")


def test_offline_mobile_verification_sync_and_replay_protection():
    sync_mgr = OfflineSyncManager()
    verifier_token = IdentityToken(
        user_id="AUDITOR_02", full_name="A. Singh",
        role=SystemRole.INDEPENDENT_VERIFIER,
    )
    
    bundle = OfflineVerificationBundle(
        bundle_id="bndl_001",
        client_timestamp="2026-09-28T09:30:00Z",
        device_id="TAB_BASTAR_04",
        facility_id="PHC_BOK",
        resource_type=ResourceType.MEDICINE,
        resource_key="AMX250",
        observed_quantity=120.0,
        usable_quantity=120.0,
        damaged_or_expired_quantity=0.0,
        device_nonce="nonce_unique_99812",
        photo_hash="hash_img_12345",
        client_signature="sig_verified",
    )
    
    # First sync succeeds
    res1 = sync_mgr.submit_offline_bundle(bundle, verifier_token)
    assert res1["status"] == "SYNCED_OK"
    assert "att_sync" in res1["attestation_id"]
    
    # Replay with same nonce is rejected
    res2 = sync_mgr.submit_offline_bundle(bundle, verifier_token)
    assert res2["status"] == "REJECTED_DUPLICATE_NONCE"


def test_monthly_pilot_roi_calculation():
    roi = compute_monthly_pilot_roi(
        district_id="BASTAR_CG",
        visits_spent=20, # 20 visit slots = Rs 24,000
        phantom_medicine_units=380.0, # 380 units * Rs 65 = Rs 24,700
        verified_stockout_days=80.87, # 80.87 days * Rs 3,500 = Rs 283,045
        phantom_beds_intercepted=4, # 4 events * Rs 15,000 = Rs 60,000
        ghost_worker_hours=48.0, # 48 hours * Rs 350 = Rs 16,800
        slot_cost_inr=1200.0,
    )
    
    assert roi.inspection_budget_spent_inr == 24000.0
    assert roi.total_quantified_savings_inr > 350000.0
    assert roi.net_return_on_investment_ratio > 10.0  # > 10x ROI
