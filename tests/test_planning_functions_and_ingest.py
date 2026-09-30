"""
Unit & integration tests for the read-only planning functions (SLA reconciliation,
capture guidance, shortage donor screen), ingestion parsers, and their routes.
"""
import pytest

from tathyon.field import FieldGuidance, assess_capture
from tathyon.optimize import ResponseCandidates, plan_response
from tathyon.verify import SLAReconciliation, reconcile_equipment_sla
from tathyon.ingest import (
    parse_bemmp_contracts_csv,
    parse_bemmp_downtime_claims_csv,
    parse_eaushadhi_stock_csv,
)
from tathyon.schema import SLAContract



def test_sla_reconciliation_calculation():
    """reconcile_equipment_sla computes downtime discrepancy and penalties without mutating state."""
    contract = SLAContract(
        contract_id="cntr_vent_01",
        asset_id="VENT_ICU_01",
        vendor_id="VND_MEDTECH",
        vendor_name="MedTech Concessionaire",
        sla_target=0.95,
        monthly_base_fee_inr=150_000.0,
        penalty_rate_per_pct=10_000.0,
    )

    # Vendor claims 100% uptime, but ground attestation shows 48 hours downtime (2 full days in 30 days)
    # Total hours = 720. 720 - 48 = 672. Uptime = 672/720 = 93.33%.
    # SLA target is 95%. Shortfall = 1.67%. Penalty = 1.67 * 10,000 = 16,666.67 INR.
    dossier = reconcile_equipment_sla(
        asset_id="VENT_ICU_01",
        contract=contract,
        claimed_uptime=1.00,
        attested_downtime_hours=48.0,
        evidence_refs=["ev_photo_vent_01"],
    )

    assert isinstance(dossier, SLAReconciliation)
    assert dossier.can_write_state is False
    assert dossier.claimed_uptime == 1.00
    assert dossier.verified_uptime is not None
    assert dossier.verified_uptime < 0.95
    assert dossier.penalty_inr > 15_000.0
    assert dossier.net_payable_inr < dossier.base_fee_inr
    assert dossier.payable_status == "PENALTY_APPLIED"
    assert any("VENDOR_OVERSTATED_UPTIME" in c for c in dossier.conflicts)
    assert "Discrepancy" in dossier.narrative


@pytest.mark.parametrize("claimed, downtime", [(1.2, 0.0), (0.99, -100.0), (0.99, 800.0)])
def test_sla_reconciliation_refuses_impossible_inputs(claimed, downtime):
    """Negative downtime used to yield >100% uptime and an unearned PAYABLE."""
    with pytest.raises(ValueError):
        reconcile_equipment_sla(asset_id="A1", contract=None, claimed_uptime=claimed,
                                attested_downtime_hours=downtime)



def test_capture_guidance_heuristics():
    """assess_capture provides actionable capture guidance and bilingual feedback."""

    # Case 1: Low lighting and missing nameplate on equipment
    guidance = assess_capture(
        resource_type="equipment",
        image_meta={"brightness": 30, "width": 640, "height": 480},
        extraction={"confidence": 0.50},
        expected_nonce="NONCE-8821",
    )

    assert isinstance(guidance, FieldGuidance)
    assert guidance.can_write_state is False
    assert guidance.ready_to_attest is False
    assert guidance.quality_score < 0.70
    assert any("Low lighting" in s for s in guidance.suggestions)
    assert any("कम रोशनी" in s for s in guidance.hindi_suggestions)
    assert "nameplate_serial_tag" in guidance.missing_angles

    # Case 2: Clean, focused capture
    clean_guidance = assess_capture(
        resource_type="equipment",
        image_meta={"brightness": 120, "width": 1920, "height": 1080},
        extraction={
            "serial_number": "SN-99823",
            "meter_reading": 1420.5,
            "confidence": 0.95,
            "nonce": "NONCE-8821",
        },
        expected_nonce="NONCE-8821",
    )

    assert clean_guidance.ready_to_attest is True
    assert clean_guidance.quality_score >= 0.90
    assert len(clean_guidance.missing_angles) == 0


def test_plan_response_excludes_unverified_stock():
    """plan_response enforces the safety gate: unverified stock is strictly blocked."""

    candidate_facilities = [
        {
            "facility_id": "CHC_VERIFIED_01",
            "tier": "CHC",
            "reported_stock": 200.0,
            "verification_state": "VERIFIED",
            "q_alpha": 180.0,
            "safety_stock": 30.0,
            "travel_hours": 1.5,
        },
        {
            "facility_id": "CHC_GHOST_STOCK_02",
            "tier": "CHC",
            "reported_stock": 500.0,
            "verification_state": "UNVERIFIED",  # Large reported stock, but unverified!
            "q_alpha": 450.0,
            "safety_stock": 20.0,
            "travel_hours": 1.0,
        },
    ]

    dossier = plan_response(
        sku="MED_AMOXICILLIN",
        target_facility_id="PHC_DEFICIT_01",
        needed_qty=100.0,
        candidate_facilities=candidate_facilities,
    )

    assert isinstance(dossier, ResponseCandidates)
    assert dossier.can_write_state is False
    # Only CHC_VERIFIED_01 should be an eligible candidate (180 - 30 = 150 transferable)
    assert len(dossier.candidates) == 1
    assert dossier.candidates[0]["facility_id"] == "CHC_VERIFIED_01"
    assert dossier.candidates[0]["transferable_qty"] == 150.0

    # CHC_GHOST_STOCK_02 must be blocked
    assert len(dossier.unverified_blocked) == 1
    assert dossier.unverified_blocked[0]["facility_id"] == "CHC_GHOST_STOCK_02"
    assert dossier.unverified_blocked[0]["blocked_reason"] == "UNVERIFIED_STOCK_EXCLUDED_BY_SAFETY_GATE"
    assert dossier.total_verified_available == 150.0


def test_ingestion_parsers():
    """Verify e-Aushadhi and BEMMP CSV parsers generate canonical objects."""
    sample_eaushadhi_csv = """facility_id,sku,drug_name,batch,expiry_date,reported_stock
DH_CENTRAL,MED_PARACETAMOL,Paracetamol 500mg,BATCH_A1,2027-05-01,1000
CHC_RURAL,MED_AMOX,Amoxicillin 250mg,BATCH_B2,2026-11-30,450
"""
    claims = parse_eaushadhi_stock_csv(sample_eaushadhi_csv)
    assert len(claims) == 2
    assert claims[0].facility_id == "DH_CENTRAL"
    assert claims[0].resource_key == "MED_PARACETAMOL"
    assert claims[0].state["quantity"] == 1000.0
    assert claims[1].state["batch"] == "BATCH_B2"

    sample_contracts_csv = """contract_id,asset_id,vendor_id,vendor_name,sla_target,monthly_base_fee_inr,penalty_rate_per_pct
cntr_xray_01,XRAY_DIGITAL_01,VND_ALPHA,Alpha Diagnostics,0.95,200000,15000
"""
    contracts = parse_bemmp_contracts_csv(sample_contracts_csv)
    assert len(contracts) == 1
    assert contracts[0].contract_id == "cntr_xray_01"
    assert contracts[0].monthly_base_fee_inr == 200000.0

    sample_downtime_csv = """asset_id,facility_id,claimed_uptime,vendor_ticket_id,reported_breakdown_hours
XRAY_DIGITAL_01,DH_CENTRAL,0.99,TCK_8831,12.0
"""
    records = parse_bemmp_downtime_claims_csv(sample_downtime_csv)
    assert len(records) == 1
    assert records[0]["claimed_uptime"] == 0.99
    assert records[0]["reported_breakdown_hours"] == 12.0




def test_plan_verifications_lists_only_unverified_holders():
    """The verification half of a shortage response: every holder that is not
    VERIFIED becomes a counting task with zero transferable stock; the target
    facility itself is never its own donor."""
    from tathyon.verify import BLOCKED_DONOR_REASON, plan_verifications

    tasks = plan_verifications("PHC_TARGET", [
        {"facility_id": "PHC_TARGET", "verification_state": "UNVERIFIED", "reported_stock": 5.0},
        {"facility_id": "CHC_OK", "verification_state": "VERIFIED", "reported_stock": 300.0},
        {"facility_id": "CHC_STALE", "verification_state": "unverified", "reported_stock": 400.0},
        {"facility_id": "CHC_DISPUTED", "verification_state": "CONFLICTED", "reported_stock": 90.0},
    ])
    assert [t["facility_id"] for t in tasks] == ["CHC_STALE", "CHC_DISPUTED"]
    assert all(t["transferable_qty"] == 0.0 and t["blocked_reason"] == BLOCKED_DONOR_REASON
               for t in tasks)
    assert tasks[0]["verified_state"] == "UNVERIFIED"
