"""
Tathyon Field Verification Service.

Implements the frontline operational verification flow for healthcare workers
(nurses, pharmacists, bio-medical technicians) in primary and community health centres:

    VERIFICATION REQUEST
           ↓
    Why verification is required
           ↓
    Asset / medicine identity
           ↓
    Single-use verification nonce
           ↓
    Multi-frame camera/evidence capture (3 frames)
           ↓
    Evidence quality check (assess_capture, bilingual EN + HI)
           ↓
    Gemini extraction ("Gemini observed", never "Gemini verified")
           ↓
    Human confirmation (custodian / independent facility in-charge)
           ↓
    ATTEST / REPORT DISCREPANCY / REQUEST RETAKE
           ↓
    HASH-CHAIN COMMIT (EventStore SHA-256 ledger)
           ↓
    UPDATED TRUST STATE (VerificationEngine projection)

Designed for mobile viewports (Android phone), low bandwidth, large touch targets,
minimal typing, with TARGET VERIFICATION TIME < 60s (Field test pending).

PROVENANCE:
- Cryptographic byte digests (SHA-256): IMPLEMENTED
- Perceptual hash fallback (dHash without PIL): SIMULATED
- Nonce binding & hash-chain commit: IMPLEMENTED
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .gemini import Extraction, extract
from .schema import (
    Attestation,
    Claim,
    Evidence,
    EventType,
    Provenance,
    ResourceType,
    StateEvent,
    VerificationState,
    new_id,
    now,
    sha256,
)
from .store import EventStore
from .verify import VerificationEngine


class VerificationRequestStatus(str, Enum):
    PENDING = "PENDING"
    CAPTURED = "CAPTURED"
    ATTESTED = "ATTESTED"
    DISCREPANCY = "DISCREPANCY"
    RETAKE_REQUESTED = "RETAKE_REQUESTED"
    EXPIRED = "EXPIRED"


@dataclass
class CapturedFrame:
    """A single frame in the 3-frame burst capture."""
    frame_index: int                    # 1: Overview, 2: Identifier/Batch, 3: Proof/Expiry
    label: str                          # e.g. "Asset / Shelf Overview"
    artifact_hash: str                  # SHA-256 byte digest
    artifact_hash_mode: str = "IMPLEMENTED"
    perceptual_hash: str = ""           # dHash representation
    perceptual_hash_mode: str = "SIMULATED"  # Clearly distinguished
    image_meta: dict = field(default_factory=dict)
    nonce_detected: Optional[str] = None
    captured_at: str = field(default_factory=now)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationRequest:
    """Challenge issued by Tathyon when a proposal or policy demands physical proof."""
    request_id: str
    facility_id: str
    resource_type: ResourceType
    resource_key: str
    why_required: str
    nonce: str
    nonce_issued_at: str
    expires_at: str
    status: VerificationRequestStatus = VerificationRequestStatus.PENDING
    claim_id: Optional[str] = None
    target_time_seconds: int = 60
    metric_label: str = "TARGET VERIFICATION TIME (Field test pending)"
    created_at: str = field(default_factory=now)
    latest_capture: Optional[dict] = None
    attestation_id: Optional[str] = None
    discrepancy_id: Optional[str] = None

    def is_expired(self) -> bool:
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) > exp
        except Exception:
            return False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["resource_type"] = self.resource_type.value
        d["status"] = self.status.value
        return d


@dataclass
class MultiFrameCaptureResult:
    """Result of processing 3 frames of evidence with Field Guide and Gemini."""
    request_id: str
    composite_artifact_hash: str
    frames: list[CapturedFrame]
    nonce: str
    nonce_matched: bool
    field_guidance: dict
    gemini_observation: dict
    ready_for_attestation: bool
    warnings: list[str] = field(default_factory=list)
    provenance: str = "PROTOTYPE_MULTI_FRAME_BURST"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["frames"] = [f.to_dict() if isinstance(f, CapturedFrame) else f for f in self.frames]
        return d


@dataclass
class FieldVerificationResult:
    """Final outcome of the human attestation / discrepancy report committed to ledger."""
    request_id: str
    status: str                         # ATTESTED | DISCREPANCY_RECORDED | RETAKE_ORDERED
    action_taken: str
    evidence_id: Optional[str]
    attestation_id: Optional[str]
    block_offset: int
    block_hash: str
    verified_state: dict
    tathyon_gate_action: str
    narrative: str
    provenance: str = "REAL_USER_ATTESTED_CHAIN"

    def to_dict(self) -> dict:
        return asdict(self)


def compute_frame_hashes(frame_bytes: bytes, frame_index: int) -> Tuple[str, str, str, str]:
    """Computes SHA-256 byte digest and perceptual hash.
    
    If PIL is absent, uses a deterministic 64-bit spatial byte projection labeled SIMULATED.
    """
    sha_hash = hashlib.sha256(frame_bytes).hexdigest()
    hash_mode = "IMPLEMENTED"

    # Perceptual hash calculation
    try:
        from PIL import Image  # type: ignore
        import io
        img = Image.open(io.BytesIO(frame_bytes)).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        diff = []
        for row in range(8):
            for col in range(8):
                diff.append(pixels[row * 9 + col] > pixels[row * 9 + col + 1])
        dhash_int = sum([2 ** i for (i, v) in enumerate(diff) if v])
        phash = f"dhash_{dhash_int:016x}"
        phash_mode = "IMPLEMENTED"
    except Exception:
        # Algorithmic fallback clearly labeled as SIMULATED
        sample_step = max(len(frame_bytes) // 64, 1)
        samples = [frame_bytes[i % len(frame_bytes)] for i in range(0, 64 * sample_step, sample_step)][:64]
        diff = [samples[i] > samples[i + 1] for i in range(63)] + [samples[-1] > samples[0]]
        sim_val = sum([2 ** i for (i, v) in enumerate(diff) if v])
        phash = f"sim_dhash_{sim_val:016x}"
        phash_mode = "SIMULATED"

    return sha_hash, hash_mode, phash, phash_mode


# ---------------------------------------------------------------------------
# Capture-quality check
# ---------------------------------------------------------------------------

@dataclass
class FieldGuidance:
    """Capture-quality feedback for a frontline health worker. Read-only."""
    ready_to_attest: bool
    quality_score: float
    suggestions: list[str] = field(default_factory=list)
    hindi_suggestions: list[str] = field(default_factory=list)
    missing_angles: list[str] = field(default_factory=list)
    detected_fields: dict = field(default_factory=dict)
    can_write_state: bool = False

    def to_dict(self) -> dict:
        return {
            "ready_to_attest": self.ready_to_attest,
            "quality_score": round(self.quality_score, 2),
            "suggestions": self.suggestions,
            "hindi_suggestions": self.hindi_suggestions,
            "missing_angles": self.missing_angles,
            "detected_fields": self.detected_fields,
            "can_write_state": self.can_write_state,
        }


def assess_capture(
    resource_type: str,
    image_meta: dict,
    extraction: Optional[dict] = None,
    expected_nonce: Optional[str] = None,
) -> FieldGuidance:
    """Score a capture for legibility and completeness before a human attests.

    Advisory only: it cannot attest and cannot change trusted state. A low score
    asks for a retake; it never fills in a missing field.
    """
    suggestions = []
    hindi_suggestions = []
    missing_angles = []
    score = 1.0

    brightness = image_meta.get("brightness", 100)
    if brightness < 45:
        score -= 0.25
        suggestions.append("Low lighting detected. Please enable camera flash or move closer to ambient light.")
        hindi_suggestions.append("कम रोशनी: कृपया टॉर्च चालू करें या बेहतर रोशनी में फोटो लें।")

    width = image_meta.get("width", 1280)
    height = image_meta.get("height", 720)
    if width < 800 or height < 600:
        score -= 0.15
        suggestions.append("Low image resolution. Please ensure camera lens is clean and focused.")
        hindi_suggestions.append("कम रिज़ॉल्यूशन: कृपया कैमरे का लेंस साफ करें और फोकस करें।")

    ext = extraction or {}
    confidence = ext.get("confidence", 0.0)
    if confidence < 0.70 and ext:
        score -= 0.20
        suggestions.append("Optical character recognition confidence low. Text on asset label is blurry.")
        hindi_suggestions.append("अक्षर स्पष्ट नहीं हैं। कृपया लेबल के थोड़ा और पास से फोटो लें।")

    if resource_type.lower() == "equipment":
        if not ext.get("serial_number"):
            score -= 0.20
            missing_angles.append("nameplate_serial_tag")
            suggestions.append("Asset serial number plate not detected. Capture a clear close-up of the metal nameplate.")
            hindi_suggestions.append("सीरियल नंबर प्लेट नहीं दिखी। कृपया मशीन की मेटल प्लेट का साफ फोटो लें।")
        if ext.get("meter_reading") is None:
            missing_angles.append("runtime_hour_meter")
            suggestions.append("Run-time hour meter or pressure gauge reading not captured. Frame display screen.")
            hindi_suggestions.append("घंटे का मीटर या प्रेशर गेज नहीं दिखा। स्क्रीन का साफ फोटो लें।")
    elif resource_type.lower() == "medicine":
        if not ext.get("batch_number"):
            score -= 0.20
            missing_angles.append("box_batch_panel")
            suggestions.append("Medicine batch number missing. Position box flat without flash glare over text.")
            hindi_suggestions.append("बैच नंबर स्पष्ट नहीं है। कृपया बॉक्स को बिना चमक के सीधा रखें।")
        if not ext.get("expiry_date"):
            score -= 0.20
            missing_angles.append("expiry_date_label")
            suggestions.append("Expiry date missing. Ensure expiry month/year is in frame.")
            hindi_suggestions.append("एक्सपायरी डेट नहीं दिखी। कृपया एक्सपायरी तारीख को फ्रेम में लाएं।")

    if expected_nonce:
        extracted_nonce = ext.get("nonce") or image_meta.get("detected_nonce")
        if extracted_nonce != expected_nonce:
            score -= 0.30
            suggestions.append(f"Audit verification code ({expected_nonce}) not clearly detected in frame. Place handwritten code paper beside asset.")
            hindi_suggestions.append(f"सत्यापन कोड ({expected_nonce}) फ्रेम में नहीं दिखा। कृपया पर्ची पर कोड लिखकर पास रखें।")

    final_score = max(round(score, 2), 0.0)
    ready = final_score >= 0.70 and len(missing_angles) == 0

    return FieldGuidance(
        ready_to_attest=ready,
        quality_score=final_score,
        suggestions=suggestions,
        hindi_suggestions=hindi_suggestions,
        missing_angles=missing_angles,
        detected_fields=ext,
        can_write_state=False,
    )


class FieldVerificationService:
    """Manages the lifecycle of field verification requests, multi-frame captures,
    quality evaluation, and immutable hash-chain commits.
    """

    def __init__(self, store: EventStore, engine: Optional[VerificationEngine] = None):
        self.store = store
        self.engine = engine or VerificationEngine(store)
        self._requests: dict[str, VerificationRequest] = {}
        self._consumed_nonces: set[str] = set()

    def create_verification_request(
        self,
        facility_id: str,
        resource_type: ResourceType,
        resource_key: str,
        why_required: str,
        claim_id: Optional[str] = None,
        nonce_valid_minutes: int = 30,
    ) -> VerificationRequest:
        """Generates an audit challenge with a single-use monotonic nonce."""
        now_dt = datetime.now(timezone.utc)
        expires_dt = now_dt + timedelta(minutes=nonce_valid_minutes)
        nonce_code = f"NONCE-{secrets.randbelow(9000) + 1000}"
        req_id = f"vreq_{secrets.token_hex(6)}"

        if claim_id is None:
            c = self.store.latest_claim(facility_id, resource_key)
            if c:
                claim_id = c.claim_id

        req = VerificationRequest(
            request_id=req_id,
            facility_id=facility_id,
            resource_type=resource_type,
            resource_key=resource_key,
            why_required=why_required,
            nonce=nonce_code,
            nonce_issued_at=now_dt.isoformat(),
            expires_at=expires_dt.isoformat(),
            status=VerificationRequestStatus.PENDING,
            claim_id=claim_id,
            target_time_seconds=60,
            metric_label="TARGET VERIFICATION TIME (Field test pending)",
            created_at=now(),
        )
        self._requests[req_id] = req
        return req

    def get_verification_request(self, request_id: str) -> Optional[VerificationRequest]:
        return self._requests.get(request_id)

    def process_multi_frame_capture(
        self,
        request_id: str,
        frames_input: list[dict],
        device_id: str = "android_phc_mobile_01",
    ) -> MultiFrameCaptureResult:
        """Ingests 3 captured frames, runs SHA-256 byte hashing, perceptual dHash,
        FieldGuide quality evaluation, and structured Gemini observation.
        """
        req = self._requests.get(request_id)
        if req is None:
            raise KeyError(f"Verification request '{request_id}' not found.")
        if req.nonce in self._consumed_nonces:
            raise ValueError(f"NONCE_ALREADY_USED: Nonce '{req.nonce}' has already been consumed.")
        if req.status in (VerificationRequestStatus.ATTESTED, VerificationRequestStatus.DISCREPANCY):
            raise ValueError(f"REQUEST_ALREADY_FINALIZED: Verification request '{request_id}' has already been finalized.")
        if req.is_expired():
            req.status = VerificationRequestStatus.EXPIRED
            raise ValueError(f"Verification request '{request_id}' has expired. Request a new nonce.")

        if len(frames_input) < 3:
            raise ValueError(f"A 3-frame burst is required (received {len(frames_input)} frames).")

        labels = [
            "Frame 1: Asset / Shelf Overview",
            "Frame 2: Identifier / Serial / Batch Panel",
            "Frame 3: Operational Evidence / Expiry / Condition Proof",
        ]

        processed_frames: list[CapturedFrame] = []
        frame_hashes: list[str] = []

        for idx, f_data in enumerate(frames_input[:3], start=1):
            raw_bytes = b""
            if "image_base64" in f_data and f_data["image_base64"]:
                try:
                    raw_bytes = base64.b64decode(f_data["image_base64"])
                except Exception:
                    raw_bytes = f_data["image_base64"].encode("utf-8", errors="ignore")
            elif "simulated_seed" in f_data:
                raw_bytes = f_data["simulated_seed"].encode("utf-8")
            else:
                raw_bytes = f"frame_{idx}_{req.resource_key}_{req.nonce}".encode()

            sha_h, sha_mode, phash, phash_mode = compute_frame_hashes(raw_bytes, idx)
            frame_hashes.append(sha_h)

            meta = f_data.get("image_meta", {"brightness": 75, "width": 1280, "height": 720})
            nonce_detected = f_data.get("detected_nonce")

            processed_frames.append(CapturedFrame(
                frame_index=idx,
                label=labels[idx - 1],
                artifact_hash=sha_h,
                artifact_hash_mode=sha_mode,
                perceptual_hash=phash,
                perceptual_hash_mode=phash_mode,
                image_meta=meta,
                nonce_detected=nonce_detected,
                captured_at=now(),
            ))

        composite_hash = hashlib.sha256("||".join(frame_hashes).encode()).hexdigest()

        # Extract structured candidate from Frame 2 / Frame 3 using Gemini
        frame_2_bytes = None
        if "image_base64" in frames_input[1] and frames_input[1]["image_base64"]:
            try:
                frame_2_bytes = base64.b64decode(frames_input[1]["image_base64"])
            except Exception:
                frame_2_bytes = None

        kind = "register" if req.resource_type == ResourceType.MEDICINE else "equipment"
        seed = frames_input[1].get("simulated_seed", f"{req.resource_key}:{req.nonce}")

        gemini_raw: Extraction = extract(
            image_bytes=frame_2_bytes,
            kind=kind,
            expected_nonce=req.nonce,
            seed_material=seed,
        )
        gemini_obs = gemini_raw.to_structured_extraction()

        # If simulated preset metadata provided specific overrides (e.g. expired batch or damaged asset)
        for fin in frames_input:
            if "preset_override" in fin:
                gemini_obs.update(fin["preset_override"])

        # Populate canonical aliases read by assess_capture
        gemini_obs["batch_number"] = gemini_obs.get("batch")
        gemini_obs["expiry_date"] = gemini_obs.get("expiry")
        gemini_obs["serial_number"] = gemini_obs.get("identifier")

        # Check nonce binding
        nonce_matched = (
            gemini_obs.get("nonce_matched") is True
            or any(f.nonce_detected == req.nonce for f in processed_frames)
            or any(fin.get("detected_nonce") == req.nonce for fin in frames_input)
        )
        gemini_obs["nonce_matched"] = nonce_matched

        # Capture-quality check
        avg_brightness = sum(f.image_meta.get("brightness", 75) for f in processed_frames) // len(processed_frames)
        guidance = assess_capture(
            resource_type=req.resource_type.value,
            image_meta={"brightness": avg_brightness, "width": 1280, "height": 720},
            extraction=gemini_obs,
            expected_nonce=req.nonce,
        )

        warnings = []
        if not nonce_matched:
            warnings.append(f"Audit nonce {req.nonce} was not visually verified in frame.")
        if guidance.quality_score < 0.70:
            warnings.append("Frame quality score below 0.70. Retake recommended.")

        ready = guidance.ready_to_attest and nonce_matched

        result = MultiFrameCaptureResult(
            request_id=req.request_id,
            composite_artifact_hash=composite_hash,
            frames=processed_frames,
            nonce=req.nonce,
            nonce_matched=nonce_matched,
            field_guidance=guidance.to_dict(),
            gemini_observation=gemini_obs,
            ready_for_attestation=ready,
            warnings=warnings,
            provenance="PROTOTYPE_MULTI_FRAME_BURST",
        )

        req.latest_capture = result.to_dict()
        req.status = VerificationRequestStatus.CAPTURED
        return result

    def submit_attestation(
        self,
        request_id: str,
        observed_fields: dict,
        attestor_id: str,
        attestor_role: str = "facility_incharge",
        is_custodian: bool = False,
        is_vendor: bool = False,
        is_beneficiary: bool = False,
        delegation_id: Optional[str] = None,
        seconds_spent: float = 42.0,
        signature: Optional[str] = None,
    ) -> FieldVerificationResult:
        """Commits human confirmation of physical state to the immutable EventStore hash-chain."""
        req = self._requests.get(request_id)
        if req is None:
            raise KeyError(f"Verification request '{request_id}' not found.")
        if req.nonce in self._consumed_nonces:
            raise ValueError(f"NONCE_ALREADY_USED: Nonce '{req.nonce}' has already been consumed.")
        if req.status in (VerificationRequestStatus.ATTESTED, VerificationRequestStatus.DISCREPANCY):
            raise ValueError(f"REQUEST_ALREADY_FINALIZED: Verification request '{request_id}' has already been finalized.")
        if req.latest_capture is None:
            raise ValueError(f"No evidence capture on record for verification request '{request_id}'.")
        if "facility_id" in observed_fields and observed_fields["facility_id"] != req.facility_id:
            raise ValueError(
                f"FACILITY_MISMATCH: Observed facility '{observed_fields['facility_id']}' does not match request facility '{req.facility_id}'."
            )

        claim = self.store.latest_claim(req.facility_id, req.resource_key)
        if claim is None:
            claim_id = req.claim_id or f"clm_auto_{new_id('c')[:8]}"
            claim = Claim(
                claim_id=claim_id,
                facility_id=req.facility_id,
                resource_type=req.resource_type,
                resource_key=req.resource_key,
                state={"claimed_stock": observed_fields.get("present_qty", 100.0)},
                source_system="FieldRequestAutoClaim",
                source_actor=attestor_id,
                effective_at=now(),
                ingested_at=now(),
                provenance=Provenance.SYNTHETIC,
            )
            self.store.put_claim(claim)

        # 1. Commit Evidence Event
        ev_id = f"evd_field_{new_id('e')[:8]}"
        capture_dict = req.latest_capture
        composite_hash = capture_dict.get("composite_artifact_hash", sha256({"req": req.request_id}))
        gemini_obs = capture_dict.get("gemini_observation", {})

        ev = Evidence(
            evidence_id=ev_id,
            claim_id=claim.claim_id,
            facility_id=req.facility_id,
            kind="photo_multi_frame_burst",
            captured_at=now(),
            device_id="mobile_phc_verified",
            nonce=req.nonce,
            nonce_issued_at=req.nonce_issued_at,
            frame_count=len(capture_dict.get("frames", [1, 2, 3])),
            artifact_hash=composite_hash,
            perceptual_hash=capture_dict.get("frames", [{}])[0].get("perceptual_hash", "dhash_sample"),
            extraction=gemini_obs,
            extraction_model=gemini_obs.get("model", "gemini-2.5-flash"),
            extraction_confidence=gemini_obs.get("confidence", 0.90),
            extraction_abstained=gemini_obs.get("abstained", False),
            provenance=Provenance.REAL_USER_PROVIDED,
        )
        self.store.put_evidence(ev)

        # 2. Commit Attestation Event
        att_id = f"att_field_{new_id('a')[:8]}"
        sig = signature or sha256(f"{attestor_id}:{att_id}:{now()}")[:32]

        att = Attestation(
            attestation_id=att_id,
            claim_id=claim.claim_id,
            evidence_refs=[ev_id],
            observed=observed_fields,
            observed_at=now(),
            attestor_id=attestor_id,
            attestor_role=attestor_role,
            delegation_id=delegation_id or f"del_{req.facility_id.lower()}",
            is_custodian=is_custodian,
            seconds_spent=seconds_spent,
            signature=sig,
            is_vendor=is_vendor,
            is_beneficiary=is_beneficiary,
        )
        state_event = self.store.put_attestation(att)
        self._consumed_nonces.add(req.nonce)

        req.status = VerificationRequestStatus.ATTESTED
        req.attestation_id = att_id

        # 3. Project updated trust state
        vs = self.engine.state_for(
            facility_id=req.facility_id,
            resource_type=req.resource_type,
            resource_key=req.resource_key,
            as_of=now(),
        )
        state_dict = vs.to_dict()
        usable = vs.verified_usable_qty if vs.verified_usable_qty is not None else 0.0

        if usable == 0.0 and req.resource_type == ResourceType.MEDICINE:
            gate_action = "REFUSE"
            narrative = (
                f"TATHYON GATE REFUSES PROPOSALS. Attestation confirms 0 usable vials "
                f"(Batch expired or damaged). Unverified digital claims superseded."
            )
        else:
            gate_action = "ALLOW_HUMAN_APPROVAL"
            narrative = (
                f"TATHYON CONFIRMS PHYSICAL TRUTH. Observed physical state attested by {attestor_id} "
                f"({attestor_role}). Ready for human authority approval."
            )

        return FieldVerificationResult(
            request_id=req.request_id,
            status="ATTESTED",
            action_taken="HUMAN_ATTESTATION_COMMITTED",
            evidence_id=ev_id,
            attestation_id=att_id,
            block_offset=state_event.offset if state_event else len(self.store.events),
            block_hash=state_event.hash if state_event else self.store.chain_root(),
            verified_state=state_dict,
            tathyon_gate_action=gate_action,
            narrative=narrative,
            provenance="REAL_USER_ATTESTED_CHAIN",
        )

    def submit_discrepancy(
        self,
        request_id: str,
        discrepancy_code: str,
        discrepancy_notes: str,
        attestor_id: str,
        attestor_role: str = "facility_incharge",
        seconds_spent: float = 30.0,
    ) -> FieldVerificationResult:
        """Records a physical discrepancy (e.g. Expired Stock, Tampered Seal, Broken Equipment)."""
        req = self._requests.get(request_id)
        if req is None:
            raise KeyError(f"Verification request '{request_id}' not found.")
        if req.nonce in self._consumed_nonces:
            raise ValueError(f"NONCE_ALREADY_USED: Nonce '{req.nonce}' has already been consumed.")
        if req.status in (VerificationRequestStatus.ATTESTED, VerificationRequestStatus.DISCREPANCY):
            raise ValueError(f"REQUEST_ALREADY_FINALIZED: Verification request '{request_id}' has already been finalized.")

        claim = self.store.latest_claim(req.facility_id, req.resource_key)
        if claim is None:
            claim_id = req.claim_id or f"clm_disc_{new_id('c')[:8]}"
            claim = Claim(
                claim_id=claim_id,
                facility_id=req.facility_id,
                resource_type=req.resource_type,
                resource_key=req.resource_key,
                state={"claimed_stock": 0.0},
                source_system="FieldDiscrepancyAutoClaim",
                source_actor=attestor_id,
                effective_at=now(),
                ingested_at=now(),
                provenance=Provenance.SYNTHETIC,
            )
            self.store.put_claim(claim)
        else:
            claim_id = claim.claim_id

        disc_att_id = f"att_disc_{new_id('d')[:8]}"
        observed_payload = {
            "discrepancy": discrepancy_code,
            "discrepancy_notes": discrepancy_notes,
            "usable_qty": 0.0,
            "status": "DEFECTIVE_OR_EXPIRED",
        }

        att = Attestation(
            attestation_id=disc_att_id,
            claim_id=claim_id,
            evidence_refs=[],
            observed=observed_payload,
            observed_at=now(),
            attestor_id=attestor_id,
            attestor_role=attestor_role,
            delegation_id=f"del_disc_{req.facility_id.lower()}",
            is_custodian=False,
            seconds_spent=seconds_spent,
            signature=sha256(f"discrepancy:{disc_att_id}")[:32],
            is_vendor=False,
            is_beneficiary=False,
        )
        state_event = self.store.put_attestation(att)
        self._consumed_nonces.add(req.nonce)

        req.status = VerificationRequestStatus.DISCREPANCY
        req.discrepancy_id = disc_att_id

        vs = self.engine.state_for(
            facility_id=req.facility_id,
            resource_type=req.resource_type,
            resource_key=req.resource_key,
            as_of=now(),
        )

        return FieldVerificationResult(
            request_id=req.request_id,
            status="DISCREPANCY_RECORDED",
            action_taken="DISCREPANCY_NOTED_IN_LEDGER",
            evidence_id=None,
            attestation_id=disc_att_id,
            block_offset=state_event.offset if state_event else len(self.store.events),
            block_hash=state_event.hash if state_event else self.store.chain_root(),
            verified_state=vs.to_dict(),
            tathyon_gate_action="BLOCK_AND_ESCALATE",
            narrative=(
                f"PHYSICAL DISCREPANCY LOGGED: {discrepancy_code}. "
                f"Tathyon immediately zeroes transferable stock and blocks dependent transfer proposals."
            ),
            provenance="DISCREPANCY_AUDIT_LOG",
        )

    def get_preset_scenario(self, scenario_id: str) -> dict:
        """Returns pre-loaded synthetic frames and challenges for instant testing."""
        normalized = scenario_id.lower().strip()
        if normalized in ("scenario_a", "medicine", "rabies"):
            nonce = "NONCE-7719"
            req = self.create_verification_request(
                facility_id="CHC_RURAL_NORTH",
                resource_type=ResourceType.MEDICINE,
                resource_key="MED_ANTI_RABIES_VACCINE",
                why_required="An automated transfer proposal would move 200 vials to PHC_REMOTE_EAST on an unverified ledger balance",
            )
            req.nonce = nonce
            return {
                "scenario_id": "scenario_a",
                "title": "Scenario A: Anti-Rabies Vaccine Emergency Transfer Challenge",
                "request": req.to_dict(),
                "preset_frames": [
                    {
                        "frame_index": 1,
                        "label": "Frame 1: Pharmacy Cupboard Overview",
                        "simulated_seed": "overview_chc_north_pharmacy_shelf_200_boxes",
                        "detected_nonce": nonce,
                        "image_meta": {"brightness": 78, "width": 1920, "height": 1080},
                    },
                    {
                        "frame_index": 2,
                        "label": "Frame 2: Batch & Box Barcode Panel",
                        "simulated_seed": "label_arb_2024_x9_batch_panel_zoom",
                        "detected_nonce": nonce,
                        "image_meta": {"brightness": 82, "width": 1920, "height": 1080},
                        "preset_override": {
                            "identifier": "MED_ANTI_RABIES_VACCINE",
                            "batch": "ARB-2024-X9",
                            "expiry": "2026-03-01",  # Expired!
                            "visible_status": "EXPIRED_ON_SHELF",
                            "confidence": 0.94,
                            "reasons": ["DATE_IN_PAST: 2026-03-01 < current_date"],
                        },
                    },
                    {
                        "frame_index": 3,
                        "label": "Frame 3: Expiry Date & Nonce Proof Card",
                        "simulated_seed": "expiry_stamp_03_2026_with_handwritten_nonce_7719",
                        "detected_nonce": nonce,
                        "image_meta": {"brightness": 80, "width": 1920, "height": 1080},
                    },
                ],
                "expected_observed": {
                    "present_qty": 200.0,
                    "usable_qty": 0.0,
                    "batch": "ARB-2024-X9",
                    "condition": "EXPIRED_ON_SHELF",
                },
                "why_text": "An automated transfer proposal would move 200 vials to PHC_REMOTE_EAST. Tathyon demands physical proof.",
            }
        elif normalized in ("scenario_b", "equipment", "ventilator"):
            nonce = "NONCE-4182"
            req = self.create_verification_request(
                facility_id="DH_DISTRICT_SOUTH",
                resource_type=ResourceType.EQUIPMENT,
                resource_key="VENT_ICU_04",
                why_required="Vendor MedServ claims 100% uptime and submits ₹150,000 monthly maintenance invoice",
            )
            req.nonce = nonce
            return {
                "scenario_id": "scenario_b",
                "title": "Scenario B: ICU Ventilator Maintenance SLA Adjudication Challenge",
                "request": req.to_dict(),
                "preset_frames": [
                    {
                        "frame_index": 1,
                        "label": "Frame 1: ICU Bed 04 Equipment Context",
                        "simulated_seed": "icu_ventilator_vent_icu_04_powered_off",
                        "detected_nonce": nonce,
                        "image_meta": {"brightness": 70, "width": 1920, "height": 1080},
                    },
                    {
                        "frame_index": 2,
                        "label": "Frame 2: Serial Tag & Service Sticker",
                        "simulated_seed": "metal_nameplate_ventilator_sl_9921",
                        "detected_nonce": nonce,
                        "image_meta": {"brightness": 74, "width": 1920, "height": 1080},
                        "preset_override": {
                            "identifier": "VENT_ICU_04",
                            "batch": None,
                            "expiry": None,
                            "visible_status": "DEFECTIVE",
                            "confidence": 0.91,
                            "reasons": ["PRESSURE_TRANSDUCER_FAULT_ALARM", "MACHINE_OFFLINE"],
                        },
                    },
                    {
                        "frame_index": 3,
                        "label": "Frame 3: LCD Error Screen & Nonce Proof Card",
                        "simulated_seed": "screen_error_code_e402_transducer_fail_with_nonce_4182",
                        "detected_nonce": nonce,
                        "image_meta": {"brightness": 68, "width": 1920, "height": 1080},
                    },
                ],
                "expected_observed": {
                    "status": "DEFECTIVE",
                    "downtime_days": 18,
                    "condition": "FAULT_CODE_E402_UNREPAIRED",
                },
                "why_text": "Vendor claims 100% uptime. Tathyon demands physical proof of operating state before clearance.",
            }
        else:
            raise KeyError(f"Unknown preset scenario '{scenario_id}'. Choose 'scenario_a' or 'scenario_b'.")
