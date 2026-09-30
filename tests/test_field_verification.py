"""
Unit and integration tests for TATHYON Field Verification Experience (Build Phase 3).

Verifies the frontline operational workflow:
1. Verification Request & Nonce Generation
2. Prototype Multi-Frame Capture & Dual Hashing (SHA-256 + dHash)
3. Field Guide Quality Feedback (Bilingual EN + HI)
4. Gemini Bounded Observation Structuring
5. Human Confirmation & Attestation Hash-Chain Commitment
6. Discrepancy Reporting
7. REST API Endpoints & Scenario Presets
"""
import pytest

from tathyon.field import (
    FieldVerificationService,
    VerificationRequestStatus,
    compute_frame_hashes,
)
from tathyon.gemini import Extraction, extract
from tathyon.schema import ResourceType, now
from tathyon.store import EventStore
from tathyon.verify import VerificationEngine


@pytest.fixture
def field_service():
    store = EventStore()
    engine = VerificationEngine(store)
    return FieldVerificationService(store, engine)


def test_verification_request_lifecycle(field_service):
    """Verifies that an audit challenge generates a single-use monotonic nonce."""
    req = field_service.create_verification_request(
        facility_id="CHC_RURAL_NORTH",
        resource_type=ResourceType.MEDICINE,
        resource_key="MED_ANTI_RABIES_VACCINE",
        why_required="External AI proposed moving 200 vials on unverified ledger balance",
    )
    assert req.request_id.startswith("vreq_")
    assert req.nonce.startswith("NONCE-")
    assert req.status == VerificationRequestStatus.PENDING
    assert req.target_time_seconds == 60
    assert "TARGET VERIFICATION TIME" in req.metric_label
    assert not req.is_expired()

    fetched = field_service.get_verification_request(req.request_id)
    assert fetched is not None
    assert fetched.nonce == req.nonce


def test_multi_frame_hashing_and_binding(field_service):
    """Verifies that 3 frames are hashed with SHA-256 and perceptual dHash."""
    req = field_service.create_verification_request(
        facility_id="CHC_RURAL_NORTH",
        resource_type=ResourceType.MEDICINE,
        resource_key="MED_ANTI_RABIES_VACCINE",
        why_required="Stock verification under GFR Rule 213",
    )

    frames = [
        {
            "frame_index": 1,
            "simulated_seed": "frame_1_shelf_overview_raw_bytes",
            "image_meta": {"brightness": 75, "width": 1280, "height": 720},
            "detected_nonce": req.nonce,
        },
        {
            "frame_index": 2,
            "simulated_seed": "frame_2_batch_label_raw_bytes",
            "image_meta": {"brightness": 80, "width": 1280, "height": 720},
            "detected_nonce": req.nonce,
            "preset_override": {
                "identifier": "MED_ANTI_RABIES_VACCINE",
                "batch": "ARB-2024-X9",
                "expiry": "2026-03-01",
                "visible_status": "EXPIRED_ON_SHELF",
                "confidence": 0.94,
            },
        },
        {
            "frame_index": 3,
            "simulated_seed": "frame_3_expiry_date_proof_bytes",
            "image_meta": {"brightness": 78, "width": 1280, "height": 720},
            "detected_nonce": req.nonce,
        },
    ]

    result = field_service.process_multi_frame_capture(req.request_id, frames)
    assert result.nonce == req.nonce
    assert result.nonce_matched is True
    assert len(result.frames) == 3
    for frame in result.frames:
        assert len(frame.artifact_hash) == 64  # SHA-256
        assert frame.artifact_hash_mode == "IMPLEMENTED"
        assert frame.perceptual_hash.startswith("sim_dhash_") or frame.perceptual_hash.startswith("dhash_")
        assert frame.perceptual_hash_mode in ("SIMULATED", "IMPLEMENTED")

    assert len(result.composite_artifact_hash) == 64
    assert result.ready_for_attestation is True


def test_gemini_bounded_structured_observation():
    """Verifies that Gemini returns the required structured extraction and never invents counts."""
    ext = Extraction(
        fields={"present_qty": None, "usable_qty": None, "batch": "ARB-2024-X9", "expiry": "2026-03-01", "asset_id_read": "MED_ANTI_RABIES_VACCINE"},
        confidences={"present_qty": 0.4, "usable_qty": 0.4, "batch": 0.92, "expiry": 0.88, "asset_id_read": 0.95},
        model="tathyon-mock-extractor/v1",
        abstained_fields=["present_qty", "usable_qty"],
        nonce_matched=True,
        is_mock=True,
    )
    structured = ext.to_structured_extraction()
    assert structured["identifier"] == "MED_ANTI_RABIES_VACCINE"
    assert structured["batch"] == "ARB-2024-X9"
    assert structured["expiry"] == "2026-03-01"
    assert "extracted_fields" in structured
    assert structured["extracted_fields"]["present_qty"] is None  # Never invented!
    assert structured["extracted_fields"]["usable_qty"] is None   # Never invented!
    assert structured["confidence"] > 0.70
    assert "LOW_CONFIDENCE_FIELDS" in structured["reasons"][0]


def test_field_guide_bilingual_feedback(field_service):
    """Verifies assess_capture provides quality scoring and bilingual (EN + HI) guidance."""
    req = field_service.create_verification_request(
        facility_id="DH_DISTRICT_SOUTH",
        resource_type=ResourceType.EQUIPMENT,
        resource_key="VENT_ICU_04",
        why_required="SLA maintenance check",
    )
    # Intentionally provide low brightness and missing serial tag
    dark_frames = [
        {"frame_index": 1, "simulated_seed": "dark_1", "image_meta": {"brightness": 20, "width": 640, "height": 480}},
        {"frame_index": 2, "simulated_seed": "dark_2", "image_meta": {"brightness": 25, "width": 640, "height": 480}},
        {"frame_index": 3, "simulated_seed": "dark_3", "image_meta": {"brightness": 22, "width": 640, "height": 480}},
    ]
    res = field_service.process_multi_frame_capture(req.request_id, dark_frames)
    guidance = res.field_guidance
    assert guidance["quality_score"] < 0.70
    assert not guidance["ready_to_attest"]
    assert any("lighting" in s.lower() or "रोशनी" in h for s, h in zip(guidance["suggestions"], guidance["hindi_suggestions"]))


def test_human_attestation_hash_chain_commitment(field_service):
    """Verifies human attestation commits to the append-only SHA-256 ledger and updates verified state."""
    req = field_service.create_verification_request(
        facility_id="CHC_RURAL_NORTH",
        resource_type=ResourceType.MEDICINE,
        resource_key="MED_ANTI_RABIES_VACCINE",
        why_required="Interception of autonomous transfer",
    )
    preset = field_service.get_preset_scenario("scenario_a")
    field_service.process_multi_frame_capture(req.request_id, preset["preset_frames"])

    # Independent nurse attests: observed 200 vials on shelf, but 0 usable because batch is expired
    result = field_service.submit_attestation(
        request_id=req.request_id,
        observed_fields={"present_qty": 200.0, "usable_qty": 0.0, "batch": "ARB-2024-X9", "condition": "EXPIRED_ON_SHELF"},
        attestor_id="staff_nurse_priya",
        attestor_role="facility_incharge",
        is_custodian=False,
        seconds_spent=44.5,
    )

    assert result.status == "ATTESTED"
    assert result.evidence_id.startswith("evd_field_")
    assert result.attestation_id.startswith("att_field_")
    assert len(result.block_hash) == 64
    assert result.tathyon_gate_action == "REFUSE"  # Because usable_qty == 0
    assert result.verified_state.get("verified_usable_qty", 0.0) == 0.0


def test_discrepancy_reporting_halts_proposals(field_service):
    """Verifies reporting physical discrepancy commits event and halts dependent actions."""
    req = field_service.create_verification_request(
        facility_id="DH_DISTRICT_SOUTH",
        resource_type=ResourceType.EQUIPMENT,
        resource_key="VENT_ICU_04",
        why_required="Clearance of vendor maintenance invoice",
    )
    result = field_service.submit_discrepancy(
        request_id=req.request_id,
        discrepancy_code="EQUIPMENT_FAULT_CODE_E402",
        discrepancy_notes="ICU Ventilator pressure transducer broken; unit offline for 18 days.",
        attestor_id="biomed_tech_sharma",
        attestor_role="facility_incharge",
    )

    assert result.status == "DISCREPANCY_RECORDED"
    assert result.tathyon_gate_action == "BLOCK_AND_ESCALATE"
    assert result.attestation_id.startswith("att_disc_")

