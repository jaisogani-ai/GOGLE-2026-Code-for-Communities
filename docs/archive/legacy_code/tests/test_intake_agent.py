"""Unit and adversarial tests for Agent 1 — Intake Agent (Document Processing Workflow).

Tests 15 distinct behaviors, safety boundaries, schema validations, and audit invariants.
"""

import os
import json
import pytest
from unittest.mock import MagicMock, patch

from tathyon.store import EventStore, EventType
from tathyon.intake_agent import (
    IntakeAgent,
    IntakeSecurityViolation,
    ExtractionCandidate,
    MIN_FIELD_CONFIDENCE,
)


@pytest.fixture
def clean_event_store(tmp_path):
    ledger_file = tmp_path / "test_events.jsonl"
    store = EventStore(str(ledger_file))
    return store


@pytest.fixture
def intake_agent(clean_event_store):
    return IntakeAgent(clean_event_store)


def test_1_extract_count_sheet_structured_parsing(intake_agent):
    """Test 1: Valid count sheet extraction returns structured ExtractionCandidate."""
    mock_payload = {
        "facility_id": "FAC_BASTAR_01",
        "resource_key": "ACT",
        "present_quantity": 100.0,
        "usable_quantity": 95.0,
        "expired_quantity": 5.0,
        "batch_number": "LOT-2026-X",
        "expiry_date": "2027-12-31",
        "nonce": "NONCE-1234",
        "attester_id": "FIELD_NURSE_1",
        "is_custodian": False,
        "confidences": {
            "facility_id": 0.95,
            "resource_key": 0.98,
            "present_quantity": 0.92,
            "usable_quantity": 0.91,
            "batch_number": 0.88,
            "expiry_date": 0.85,
        },
    }

    # Extracting from dictionary input
    candidate = intake_agent.extract_count_sheet(mock_payload)
    assert isinstance(candidate, ExtractionCandidate)
    assert candidate.facility_id == "FAC_BASTAR_01"
    assert candidate.resource_key == "ACT"
    assert candidate.present_quantity == 100.0
    assert candidate.usable_quantity == 95.0
    assert candidate.batch_number == "LOT-2026-X"


def test_2_validate_schema_passing_valid_candidate(intake_agent):
    """Test 2: Schema validation passes clean, mathematically consistent candidate."""
    candidate = ExtractionCandidate(
        facility_id="FAC_BASTAR_01",
        resource_key="ACT",
        present_quantity=50.0,
        usable_quantity=45.0,
        expired_quantity=5.0,
        batch_number="LOT-1",
        expiry_date="2027-01-01",
        nonce="NONCE-1",
        attester_id="VERIFIER_1",
        is_custodian=False,
        confidences={
            "facility_id": 0.9,
            "resource_key": 0.9,
            "present_quantity": 0.9,
            "usable_quantity": 0.9,
        },
    )
    val_result = intake_agent.validate_schema(candidate)
    assert val_result.is_valid is True
    assert len(val_result.errors) == 0
    assert val_result.cleaned_record is not None


def test_3_successful_filing_returns_evt_id(intake_agent, clean_event_store):
    """Test 3: Successful filing returns an [EVT-...] ID and logs to ledger."""
    record = {
        "facility_id": "FAC_BASTAR_02",
        "resource_key": "RDT",
        "present_quantity": 200.0,
        "usable_quantity": 200.0,
        "expired_quantity": 0.0,
        "batch_number": "LOT-RDT-9",
        "expiry_date": "2028-06-30",
        "nonce": "NONCE-99",
        "attester_id": "PHC_NURSE_01",
        "is_custodian": False,
    }
    result = intake_agent.file_attestation_record(record, attester_id="PHC_NURSE_01")
    evt_id = result["event_id"]
    assert evt_id.startswith("[EVT-")
    assert evt_id.endswith("]")

    events = clean_event_store.events
    assert len(events) == 1
    event = events[0]
    assert f"[EVT-{event.offset:04d}]" == evt_id
    assert event.actor == "agent:intake"
    assert event.event_type == EventType.EXTRACTED
    assert event.payload["verified_state"] == "UNVERIFIED"
    assert event.payload["status"] == "INTAKE_STAGED"


def test_4_full_pipeline_process_document_success(intake_agent, clean_event_store):
    """Test 4: Full process_document pipeline completes end-to-end on clean document."""
    mock_payload = {
        "facility_id": "FAC_BASTAR_03",
        "resource_key": "OXY",
        "present_quantity": 10.0,
        "usable_quantity": 10.0,
        "expired_quantity": 0.0,
        "batch_number": "LOT-OXY-01",
        "expiry_date": "2029-01-01",
        "nonce": "N-101",
        "attester_id": "STOREKEEPER_01",
        "is_custodian": False,
        "confidences": {
            "facility_id": 0.95,
            "resource_key": 0.95,
            "present_quantity": 0.95,
            "usable_quantity": 0.95,
        },
    }

    result = intake_agent.process_document(mock_payload)
    assert result.status == "FILED"
    assert result.event_id.startswith("[EVT-")
    assert result.quarantined is False


def test_5_corrupted_sheet_triggers_quarantine_with_reason(intake_agent, clean_event_store):
    """Test 5: Adversarial unreadable/corrupted sheet triggers quarantine with notification."""
    corrupted_data = b"CORRUPTED_OR_UNREADABLE_IMAGE_BINARY"
    result = intake_agent.process_document(corrupted_data)
    assert result.status == "QUARANTINED"
    assert result.quarantined is True
    assert result.human_notified is True
    assert result.event_id.startswith("[EVT-")

    events = clean_event_store.events
    assert any(e.payload.get("status") == "QUARANTINED" for e in events)


def test_6_arithmetic_anomaly_usable_greater_than_present_quarantined(intake_agent):
    """Test 6: Adversarial arithmetic anomaly (usable > present) is caught and quarantined."""
    candidate = ExtractionCandidate(
        facility_id="FAC_BASTAR_04",
        resource_key="ACT",
        present_quantity=50.0,
        usable_quantity=100.0,  # Impossible!
        expired_quantity=0.0,
        batch_number="LOT-1",
        expiry_date="2027-01-01",
        nonce="N-1",
        attester_id="VERIFIER_1",
        is_custodian=False,
        confidences={
            "facility_id": 0.9,
            "resource_key": 0.9,
            "present_quantity": 0.9,
            "usable_quantity": 0.9,
        },
    )
    val_result = intake_agent.validate_schema(candidate)
    assert val_result.is_valid is False
    assert any("ARITHMETIC_ANOMALY" in err for err in val_result.errors)

    res = intake_agent.quarantine_row(candidate, "ARITHMETIC_ANOMALY: usable_quantity > present_quantity")
    assert res["event_id"].startswith("[EVT-")
    assert res["quarantined"] is True


def test_7_negative_quantity_quarantined(intake_agent):
    """Test 7: Adversarial negative quantity is caught by validation and quarantined."""
    candidate = ExtractionCandidate(
        facility_id="FAC_BASTAR_04",
        resource_key="ACT",
        present_quantity=-5.0,
        usable_quantity=-5.0,
        expired_quantity=0.0,
        batch_number="LOT-1",
        expiry_date="2027-01-01",
        nonce="N-1",
        attester_id="VERIFIER_1",
        is_custodian=False,
        confidences={
            "facility_id": 0.9,
            "resource_key": 0.9,
            "present_quantity": 0.9,
            "usable_quantity": 0.9,
        },
    )
    val_result = intake_agent.validate_schema(candidate)
    assert val_result.is_valid is False
    assert any("NEGATIVE" in err for err in val_result.errors)


def test_8_uncertain_field_suppressed_to_null(intake_agent):
    """Test 8: Fields with confidence < 0.70 are strictly suppressed to null, never guessed."""
    candidate = ExtractionCandidate(
        facility_id="FAC_BASTAR_05",
        resource_key="ACT",
        present_quantity=80.0,
        usable_quantity=75.0,
        expired_quantity=5.0,
        batch_number="LOT-BLURRED-GUESS",
        expiry_date="2026-10-10",
        nonce="N-5",
        attester_id="VERIFIER_1",
        is_custodian=False,
        confidences={
            "facility_id": 0.95,
            "resource_key": 0.92,
            "present_quantity": 0.85,
            "usable_quantity": 0.80,
            "batch_number": 0.45,  # Below 0.70 threshold!
            "expiry_date": 0.65,    # Below 0.70 threshold!
        },
        raw_text="Scanned document with blurry batch number",
    )

    val_result = intake_agent.validate_schema(candidate)
    assert val_result.is_valid is True
    cleaned = val_result.cleaned_record
    assert cleaned["batch_number"] is None
    assert cleaned["expiry_date"] is None
    assert cleaned["present_quantity"] == 80.0
    assert cleaned["facility_id"] == "FAC_BASTAR_05"


def test_9_missing_required_facility_or_sku_quarantined(intake_agent):
    """Test 9: Missing required facility_id or resource_key causes validation failure."""
    candidate_no_fac = ExtractionCandidate(
        facility_id=None,
        resource_key="ACT",
        present_quantity=10.0,
        usable_quantity=10.0,
        expired_quantity=0.0,
        batch_number=None,
        expiry_date=None,
        nonce=None,
        attester_id="V1",
        is_custodian=False,
        confidences={"resource_key": 0.9, "present_quantity": 0.9, "usable_quantity": 0.9},
    )
    val1 = intake_agent.validate_schema(candidate_no_fac)
    assert val1.is_valid is False
    assert any("MISSING_FACILITY_ID" in err for err in val1.errors)

    candidate_no_sku = ExtractionCandidate(
        facility_id="FAC_1",
        resource_key=None,
        present_quantity=10.0,
        usable_quantity=10.0,
        expired_quantity=0.0,
        batch_number=None,
        expiry_date=None,
        nonce=None,
        attester_id="V1",
        is_custodian=False,
        confidences={"facility_id": 0.9, "present_quantity": 0.9, "usable_quantity": 0.9},
    )
    val2 = intake_agent.validate_schema(candidate_no_sku)
    assert val2.is_valid is False
    assert any("MISSING_RESOURCE_KEY" in err for err in val2.errors)


def test_10_filing_record_never_edits_verified_state_to_verified(intake_agent, clean_event_store):
    """Test 10: Invariant: Intake agent files stage record as UNVERIFIED, never VERIFIED."""
    record = {
        "facility_id": "FAC_BASTAR_10",
        "resource_key": "ACT",
        "present_quantity": 100.0,
        "usable_quantity": 100.0,
        "expired_quantity": 0.0,
        "attester_id": "CUST_1",
        "is_custodian": True,
    }
    res = intake_agent.file_attestation_record(record)
    assert res["verified_state"] == "UNVERIFIED"

    events = clean_event_store.events
    assert len(events) == 1
    event = events[0]
    assert event.payload.get("verified_state") != "VERIFIED"
    assert event.payload.get("verified_state") == "UNVERIFIED"
    assert event.payload.get("status") == "INTAKE_STAGED"


def test_11_intake_agent_cannot_approve_transfers(intake_agent):
    """Test 11: Invariant: Intake agent does not have approval capabilities."""
    assert not hasattr(intake_agent, "approve_transfer")
    assert not hasattr(intake_agent, "approve_plan")
    assert "approve_transfer" not in intake_agent.ALLOWED_TOOLS


def test_12_non_allowlisted_tool_raises_security_violation(intake_agent):
    """Test 12: Guardrail: Attempting to invoke non-allowlisted tool raises IntakeSecurityViolation."""
    with pytest.raises(IntakeSecurityViolation) as excinfo:
        intake_agent.execute_tool("approve_transfer", {"transfer_id": "X"})
    assert "Disallowed tool 'approve_transfer'" in str(excinfo.value)

    with pytest.raises(IntakeSecurityViolation):
        intake_agent.execute_tool("reallocate_inventory", {})


def test_13_quarantine_notification_sets_human_review_required(intake_agent):
    """Test 13: Quarantine row explicitly sets human_review_required=True."""
    candidate = ExtractionCandidate(
        facility_id="FAC_ANOMALY",
        resource_key="ACT",
        present_quantity=10.0,
        usable_quantity=20.0,
        expired_quantity=0.0,
        batch_number=None,
        expiry_date=None,
        nonce=None,
        attester_id=None,
        is_custodian=False,
        confidences={"facility_id": 0.9, "resource_key": 0.9},
    )
    res = intake_agent.quarantine_row(candidate, "Usable exceeded present")
    assert res["event_id"].startswith("[EVT-")
    assert res["human_notified"] is True

    # In process_document flow
    bad_payload = {
        "facility_id": "FAC_ANOMALY",
        "resource_key": "ACT",
        "present_quantity": 10.0,
        "usable_quantity": 20.0,
        "expired_quantity": 0.0,
    }
    result = intake_agent.process_document(bad_payload)
    assert result.status == "QUARANTINED"
    assert result.human_notified is True


def test_14_audit_trail_recorded_with_agent_intake_actor(intake_agent, clean_event_store):
    """Test 14: Audit trail: every action appended to EventStore records actor='agent:intake'."""
    record = {
        "facility_id": "FAC_BASTAR_99",
        "resource_key": "ACT",
        "present_quantity": 50.0,
        "usable_quantity": 50.0,
    }
    intake_agent.file_attestation_record(record, attester_id="CUST_A")
    intake_agent.quarantine_row(record, "Manual quarantine test")

    events = clean_event_store.events
    assert len(events) == 2
    for evt in events:
        assert evt.actor == "agent:intake"
        assert f"[EVT-{evt.offset:04d}]".startswith("[EVT-")


def test_15_custodian_metadata_preserved_for_downstream_rbac(intake_agent, clean_event_store):
    """Test 15: Discloses custodian metadata in payload to maintain downstream separation of duties."""
    record = {
        "facility_id": "FAC_BASTAR_88",
        "resource_key": "ACT",
        "present_quantity": 120.0,
        "usable_quantity": 120.0,
        "attester_id": "PHC_STAFF_RAMESH",
        "is_custodian": True,
    }
    intake_agent.file_attestation_record(
        validated_record=record,
        attester_id="PHC_STAFF_RAMESH",
    )
    events = clean_event_store.events
    assert len(events) == 1
    payload = events[0].payload
    assert payload["attester_id"] == "PHC_STAFF_RAMESH"
    assert payload["is_custodian"] is True
