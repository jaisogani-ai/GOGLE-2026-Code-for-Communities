"""
Tests for SLA contracts, GFR-22 policy-based physical verification,
and anti-fraud governance invariants (Vendor & Beneficiary segregation).
"""
import pytest
import pandas as pd
from fastapi.testclient import TestClient

from tathyon.schema import (
    Attestation, Claim, Evidence, EventType, Provenance, ResourceType,
    SLAContract, VerificationState, new_id, now,
)
from tathyon.store import EventStore
from tathyon.verify import VerificationEngine


@pytest.fixture
def store():
    return EventStore()



def test_vendor_self_attestation_is_rejected_at_write_time(store):
    """Rule: A maintenance contractor cannot attest to equipment it maintains."""
    claim = Claim(
        claim_id=new_id("clm"),
        facility_id="FAC001",
        resource_type=ResourceType.EQUIPMENT,
        resource_key="VENT-01",
        state={"register_status": "FUNCTIONAL", "vendor_reported_uptime": 0.99},
        source_system="asset_register",
        source_actor="vendor_tech_01",
        effective_at=now(),
        ingested_at=now(),
        provenance=Provenance.SYNTHETIC,
    )
    store.put_claim(claim)

    att = Attestation(
        attestation_id=new_id("att"),
        claim_id=claim.claim_id,
        evidence_refs=[],
        observed={"status": "FUNCTIONAL"},
        observed_at=now(),
        attestor_id="vendor_rep_raj",
        attestor_role="service_engineer",
        delegation_id="dlg_01",
        is_custodian=False,
        is_vendor=True,  # Prohibited!
        is_beneficiary=False,
        seconds_spent=180.0,
        signature="sig_vendor",
    )
    with pytest.raises(ValueError, match="ATTESTOR_IS_VENDOR"):
        store.put_attestation(att)


def test_beneficiary_conflict_is_rejected_at_write_time(store):
    """Rule: An invoice payee/beneficiary cannot sign verification unlocking its payment."""
    claim = Claim(
        claim_id=new_id("clm"),
        facility_id="FAC001",
        resource_type=ResourceType.EQUIPMENT,
        resource_key="XRAY-01",
        state={"register_status": "FUNCTIONAL"},
        source_system="asset_register",
        source_actor="vendor_01",
        effective_at=now(),
        ingested_at=now(),
        provenance=Provenance.SYNTHETIC,
    )
    store.put_claim(claim)

    att = Attestation(
        attestation_id=new_id("att"),
        claim_id=claim.claim_id,
        evidence_refs=[],
        observed={"status": "FUNCTIONAL"},
        observed_at=now(),
        attestor_id="payee_accountant",
        attestor_role="finance_rep",
        delegation_id="dlg_02",
        is_custodian=False,
        is_vendor=False,
        is_beneficiary=True,  # Prohibited!
        seconds_spent=120.0,
        signature="sig_payee",
    )
    with pytest.raises(ValueError, match="ATTESTOR_BENEFICIARY_CONFLICT"):
        store.put_attestation(att)


def test_evidence_replay_is_detected_and_rejected(store):
    """Rule: Replaying the same photo hash is rejected."""
    claim = Claim(
        claim_id=new_id("clm"),
        facility_id="FAC001",
        resource_type=ResourceType.MEDICINE,
        resource_key="AMX250",
        state={"reported_stock": 500},
        source_system="e-Aushadhi",
        source_actor="clerk_01",
        effective_at=now(),
        ingested_at=now(),
        provenance=Provenance.SYNTHETIC,
    )
    store.put_claim(claim)

    ev1 = Evidence(
        evidence_id="ev_original",
        claim_id=claim.claim_id,
        facility_id="FAC001",
        kind="photo_register",
        captured_at=now(),
        device_id="dev_01",
        nonce="NONCE123",
        nonce_issued_at=now(),
        frame_count=3,
        artifact_hash="hash_aabbcc112233",
        perceptual_hash="phash_11223344",
    )
    store.put_evidence(ev1)

    # Attempt to replay the exact same artifact hash with a new evidence_id
    ev_replay = Evidence(
        evidence_id="ev_second_try",
        claim_id=claim.claim_id,
        facility_id="FAC001",
        kind="photo_register",
        captured_at=now(),
        device_id="dev_02",
        nonce="NONCE456",
        nonce_issued_at=now(),
        frame_count=3,
        artifact_hash="hash_aabbcc112233",  # Duplicate artifact hash!
        perceptual_hash="phash_99999999",
    )
    with pytest.raises(ValueError, match="EVIDENCE_REUSED"):
        store.put_evidence(ev_replay)


def test_gfr22_certificate_refusal_when_equipment_unverified(store):
    """Rule: GFR-22 policy-based certificate refuses to emit on unverified equipment."""
    engine = VerificationEngine(store)
    claim = Claim(
        claim_id="clm_psa_01",
        facility_id="FAC001",
        resource_type=ResourceType.EQUIPMENT,
        resource_key="PSA-01",
        state={"register_status": "FUNCTIONAL"},
        source_system="asset_register",
        source_actor="vendor_01",
        effective_at=now(),
        ingested_at=now(),
        provenance=Provenance.SYNTHETIC,
    )
    store.put_claim(claim)

    cert = engine.generate_gfr22_certificate("FAC001", "PSA-01", as_of=now())
    assert cert["issued"] is False
    assert "REFUSED" in cert["refusal_reason"]
    assert cert["statutory_rule"] == "GFR 2017 Rule 213(1)"


def test_gfr22_certificate_emitted_when_equipment_verified_functional(store):
    """Rule: GFR-22 certificate emits with independent officer reference when verified."""
    engine = VerificationEngine(store)
    claim = Claim(
        claim_id="clm_psa_02",
        facility_id="FAC001",
        resource_type=ResourceType.EQUIPMENT,
        resource_key="PSA-02",
        state={"register_status": "FUNCTIONAL"},
        source_system="asset_register",
        source_actor="clerk_01",
        effective_at=now(),
        ingested_at=now(),
        provenance=Provenance.SYNTHETIC,
    )
    store.put_claim(claim)

    att = Attestation(
        attestation_id="att_biomed_01",
        claim_id=claim.claim_id,
        evidence_refs=["ev_psa_01"],
        observed={"status": "FUNCTIONAL", "age_months": 12, "amc_active": True, "last_service_months": 1},
        observed_at=now(),
        attestor_id="usr_biomed_inspector",
        attestor_role="biomedical_engineer",
        delegation_id="dlg_state_01",
        is_custodian=False,
        is_vendor=False,
        is_beneficiary=False,
        seconds_spent=150.0,
        signature="sig_inspector",
    )
    store.put_attestation(att)

    cert = engine.generate_gfr22_certificate("FAC001", "PSA-02", as_of=now())
    assert cert["issued"] is True
    assert cert["certified_by"] == "usr_biomed_inspector"
    assert cert["verified_status"] == "FUNCTIONAL"


def test_sla_evidence_pack_calculates_penalty_on_broken_equipment(store):
    """Rule: False self-reported uptime contradicted by physical verification withholds maintenance fee."""
    engine = VerificationEngine(store)
    claim = Claim(
        claim_id="clm_vent_01",
        facility_id="FAC001",
        resource_type=ResourceType.EQUIPMENT,
        resource_key="VENT-01",
        state={"register_status": "FUNCTIONAL", "vendor_reported_uptime": 0.995},
        source_system="bemmp_vendor_portal",
        source_actor="vendor_portal",
        effective_at=now(),
        ingested_at=now(),
        provenance=Provenance.SYNTHETIC,
    )
    store.put_claim(claim)

    # Independent engineer visits and discovers asset was never commissioned!
    att = Attestation(
        attestation_id="att_floor_audit",
        claim_id=claim.claim_id,
        evidence_refs=["ev_vent_photo"],
        observed={"status": "NOT_COMMISSIONED"},
        observed_at=now(),
        attestor_id="usr_independent_auditor",
        attestor_role="facility_incharge",
        delegation_id="dlg_audit_99",
        is_custodian=False,
        is_vendor=False,
        is_beneficiary=False,
        seconds_spent=95.0,
        signature="sig_auditor",
    )
    store.put_attestation(att)

    contract = SLAContract(
        contract_id="cnt_vent_01",
        asset_id="VENT-01",
        vendor_id="vendor_medtech",
        vendor_name="MedTech Services",
        sla_target=0.95,
        monthly_base_fee_inr=150_000.0,
    )

    pack = engine.calculate_sla_evidence_pack(
        facility_id="FAC001",
        asset_id="VENT-01",
        as_of=now(),
        contract=contract,
        claimed_uptime=0.995,
    )

    assert pack["payable_status"] == "PENALTY_APPLIED"
    assert pack["net_payable_inr"] == 0.0
    assert pack["penalty_inr"] == 150_000.0
    assert "SLA_UPTIME_CONTRADICTED" in pack["conflicts"]

