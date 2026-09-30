"""Local structured-record validation; image/OCR and Gemini extraction are disabled.

Uncertain fields remain null. This intake helper never verifies truth, approves,
allocates, or edits verified state. Submitted observations remain unverified.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tathyon.schema import (
    EventType,
    Provenance,
    ResourceType,
    StateEvent,
    VerificationState,
    new_id,
    now,
)
from tathyon.store import EventStore

log = logging.getLogger(__name__)

INTAKE_TOOL_ALLOWLIST = {
    "extract_count_sheet",
    "validate_schema",
    "quarantine_row",
    "file_attestation_record",
}

MIN_FIELD_CONFIDENCE = 0.70


class IntakeSecurityViolation(Exception):
    """Raised when an attempt is made to call a non-allowlisted tool or perform a forbidden mutation."""


@dataclass
class ExtractionCandidate:
    facility_id: Optional[str]
    resource_key: Optional[str]
    present_quantity: Optional[float]
    usable_quantity: Optional[float]
    expired_quantity: Optional[float]
    batch_number: Optional[str]
    expiry_date: Optional[str]
    nonce: Optional[str]
    attester_id: Optional[str]
    is_custodian: bool
    confidences: Dict[str, float] = field(default_factory=dict)
    raw_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ValidationResult:
    is_valid: bool
    cleaned_record: Optional[Dict[str, Any]]
    errors: List[str] = field(default_factory=list)
    uncertain_fields: List[str] = field(default_factory=list)


@dataclass
class IntakeResult:
    status: str  # "FILED" | "QUARANTINED"
    event_id: Optional[str]
    quarantined: bool
    reason: Optional[str]
    extracted_record: Dict[str, Any]
    validation_errors: List[str] = field(default_factory=list)
    human_notified: bool = False
    audit_events: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class IntakeAgent:
    """Local intake validation agent; external document processing is disabled."""

    ALLOWED_TOOLS = INTAKE_TOOL_ALLOWLIST

    def __init__(self, store: Optional[EventStore] = None, client: Optional[Any] = None):
        self.store = store or EventStore()
        # Do not send health documents to an external model provider. The
        # legacy vision path is intentionally disabled for local-only use.
        self.client = None

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Public alias for guarded tool execution."""
        return self._call_tool(tool_name, *args, **kwargs)

    def _init_client(self) -> None:
        """External Gemini intake remains disabled to keep health documents local."""
        self.client = None

    def _call_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Guarded dispatcher enforcing the strict 4-tool allowlist."""
        if tool_name not in INTAKE_TOOL_ALLOWLIST:
            # Block forbidden tool and log security violation to audit ledger
            self.store.append(
                event_type=EventType.FLAGGED,
                facility_id="DISTRICT_HQ",
                resource_type=ResourceType.MEDICINE,
                resource_key="SECURITY_GATE",
                payload={
                    "violation": "FORBIDDEN_TOOL_ATTEMPT",
                    "attempted_tool": tool_name,
                    "agent": "intake_agent",
                    "status": "BLOCKED",
                },
                actor="agent:intake",
            )
            raise IntakeSecurityViolation(
                f"Disallowed tool '{tool_name}'. Allowed: {sorted(INTAKE_TOOL_ALLOWLIST)}. "
                "Intake Agent cannot approve, allocate, or execute actions."
            )

        handler: Callable = getattr(self, tool_name)
        return handler(*args, **kwargs)

    # --------------------------------------------------------------------------
    # Tool 1: extract structured, user-provided fields without external upload
    # --------------------------------------------------------------------------
    def extract_count_sheet(
        self,
        file_input: Any,
        filename: str = "count_sheet.jpg",
        mime_type: str = "image/jpeg",
    ) -> ExtractionCandidate:
        """Parse explicitly supplied structured data; never invent image values."""
        # External vision extraction is deliberately unavailable in local-only mode.
        if self.client is not None:
            raise RuntimeError("EXTERNAL_DOCUMENT_PROCESSING_DISABLED")

        # Deterministic structured parsing only; there is no image/OCR fallback.
        raw_str = ""
        if isinstance(file_input, dict):
            parsed = file_input
            raw_str = json.dumps(file_input)
        elif isinstance(file_input, str):
            raw_str = file_input
            try:
                parsed = json.loads(file_input)
            except Exception:
                parsed = {"raw_text": file_input}
        elif isinstance(file_input, bytes):
            try:
                raw_str = file_input.decode("utf-8")
                parsed = json.loads(raw_str)
            except Exception:
                return ExtractionCandidate(None, None, None, None, None, None, None, None, None,
                                           False, {}, "[NO_CONFIGURED_DOCUMENT_EXTRACTOR]")
        else:
            parsed = {}

        if parsed.get("unreadable"):
            return ExtractionCandidate(
                facility_id=None,
                resource_key=None,
                present_quantity=None,
                usable_quantity=None,
                expired_quantity=None,
                batch_number=None,
                expiry_date=None,
                nonce=None,
                attester_id=None,
                is_custodian=False,
                confidences={"all": 0.0},
                raw_text="[UNREADABLE_IMAGE_OR_FILE]",
            )

        # Do not invent extraction confidence for user-entered structured fields.
        confidences = parsed.get("confidences") or {}

        return ExtractionCandidate(
            facility_id=parsed.get("facility_id"),
            resource_key=parsed.get("resource_key"),
            present_quantity=parsed.get("present_quantity"),
            usable_quantity=parsed.get("usable_quantity"),
            expired_quantity=parsed.get("expired_quantity"),
            batch_number=parsed.get("batch_number"),
            expiry_date=parsed.get("expiry_date"),
            nonce=parsed.get("nonce"),
            attester_id=parsed.get("attester_id"),
            is_custodian=bool(parsed.get("is_custodian", False)),
            confidences=confidences,
            raw_text=raw_str,
        )

    # --------------------------------------------------------------------------
    # Tool 2: validate_schema
    # --------------------------------------------------------------------------
    def validate_schema(self, candidate: ExtractionCandidate) -> ValidationResult:
        """Validates candidate against schema invariants:
        - Required identifiers: facility_id, resource_key
        - Numerical invariants: present_quantity >= 0, usable_quantity <= present_quantity
        - Field uncertainty rule: any field with confidence < MIN_FIELD_CONFIDENCE is set to null, never guessed.
        """
        errors = []
        uncertain = []
        cleaned = candidate.to_dict()

        # Check required fields
        if not candidate.facility_id:
            errors.append("MISSING_FACILITY_ID: Document does not contain a legible facility identifier.")
        if not candidate.resource_key:
            errors.append("MISSING_RESOURCE_KEY: Document does not identify the medicine or asset SKU.")

        # Check confidence scores: suppress uncertain fields to null
        for f_name, conf_val in candidate.confidences.items():
            if isinstance(conf_val, (int, float)) and conf_val < MIN_FIELD_CONFIDENCE:
                uncertain.append(f_name)
                if f_name in cleaned:
                    cleaned[f_name] = None

        # Numerical consistency checks
        p_qty = cleaned.get("present_quantity")
        u_qty = cleaned.get("usable_quantity")
        e_qty = cleaned.get("expired_quantity")

        if p_qty is None:
            errors.append("MISSING_PRESENT_QUANTITY: Present quantity is null or illegible.")
        elif p_qty < 0:
            errors.append(f"NEGATIVE_QUANTITY: Present quantity cannot be negative ({p_qty}).")

        if u_qty is None:
            errors.append("MISSING_USABLE_QUANTITY: Usable quantity is null or illegible.")
        elif u_qty < 0:
            errors.append(f"NEGATIVE_USABLE_QUANTITY: Usable quantity cannot be negative ({u_qty}).")

        if p_qty is not None and u_qty is not None:
            if u_qty > p_qty:
                errors.append(
                    f"ARITHMETIC_ANOMALY: Usable quantity ({u_qty}) exceeds present physical quantity ({p_qty})."
                )
            if e_qty is not None and e_qty < 0:
                errors.append(f"NEGATIVE_EXPIRED_QUANTITY: Expired quantity cannot be negative ({e_qty}).")

        is_valid = len(errors) == 0
        return ValidationResult(
            is_valid=is_valid,
            cleaned_record=cleaned if is_valid else None,
            errors=errors,
            uncertain_fields=uncertain,
        )

    # --------------------------------------------------------------------------
    # Tool 3: quarantine_row
    # --------------------------------------------------------------------------
    def quarantine_row(
        self,
        candidate_or_row: Any,
        reason: str,
        human_notification_channel: str = "DISTRICT_AUDIT_QUEUE",
    ) -> Dict[str, Any]:
        """Quarantines invalid row, appends audit event, and raises a human review notification."""
        raw_data = candidate_or_row.to_dict() if hasattr(candidate_or_row, "to_dict") else candidate_or_row
        fac_id = (raw_data.get("facility_id") if isinstance(raw_data, dict) else None) or "UNRESOLVED_FACILITY"
        res_key = (raw_data.get("resource_key") if isinstance(raw_data, dict) else None) or "UNSPECIFIED_RESOURCE"

        ev = self.store.append(
            event_type=EventType.FLAGGED,
            facility_id=fac_id,
            resource_type=ResourceType.MEDICINE,
            resource_key=res_key,
            payload={
                "status": "QUARANTINED",
                "quarantine_reason": reason,
                "notification_channel": human_notification_channel,
                "human_review_required": True,
                "facility_identity_resolved": fac_id != "UNRESOLVED_FACILITY",
                "resource_identity_resolved": res_key != "UNSPECIFIED_RESOURCE",
                "raw_candidate": raw_data,
            },
            actor="agent:intake",
        )

        event_tag = f"[EVT-{ev.offset:04d}]" if ev else "[EVT-QUARANTINE]"
        return {
            "status": "QUARANTINED",
            "event_id": event_tag,
            "event_hash": getattr(ev, "hash", ""),
            "quarantined": True,
            "reason": reason,
            "human_notified": False,
            "human_notification": (
                f"Review required for intake record {fac_id}/{res_key}. No notification was sent. Reason: {reason}."
            ),
        }

    # --------------------------------------------------------------------------
    # Tool 4: file_attestation_record
    # --------------------------------------------------------------------------
    def file_attestation_record(
        self,
        validated_record: Dict[str, Any],
        attester_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Files a validated intake record as a staged claim event in the audit store.
        INVARIANT: Does NOT mark inventory as VERIFIED. It files a staged claim record.
        """
        required = ("facility_id", "resource_key", "present_quantity", "usable_quantity")
        missing = [key for key in required if validated_record.get(key) in (None, "")]
        if missing:
            raise ValueError(f"REQUIRED_FIELDS_MISSING: {', '.join(missing)}")
        fac_id = str(validated_record["facility_id"])
        res_key = str(validated_record["resource_key"])
        p_qty = float(validated_record["present_quantity"])
        u_qty = float(validated_record["usable_quantity"])
        if p_qty < 0 or u_qty < 0 or u_qty > p_qty:
            raise ValueError("QUANTITY_INVARIANT_FAILED")
        att_id = attester_id or validated_record.get("attester_id") or "INTAKE_STAGE"
        is_cust = bool(validated_record.get("is_custodian", False))

        ev = self.store.append(
            event_type=EventType.EXTRACTED,
            facility_id=fac_id,
            resource_type=ResourceType.MEDICINE,
            resource_key=res_key,
            payload={
                "status": "INTAKE_STAGED",
                "verified_state": VerificationState.UNVERIFIED.value,  # Stays UNVERIFIED until physical witness sign-off
                "staged_present_qty": p_qty,
                "staged_usable_qty": u_qty,
                "staged_expired_qty": validated_record.get("expired_quantity"),
                "attester_id": att_id,
                "is_custodian": is_cust,
                "nonce": validated_record.get("nonce"),
                "provenance": Provenance.REAL_USER_PROVIDED.value,
            },
            actor="agent:intake",
        )

        event_tag = f"[EVT-{ev.offset:04d}]" if ev else "[EVT-FILED]"
        return {
            "status": "FILED",
            "event_id": event_tag,
            "event_hash": getattr(ev, "hash", ""),
            "facility_id": fac_id,
            "usable_quantity_staged": u_qty,
            "verified_state": VerificationState.UNVERIFIED.value,
        }

    # --------------------------------------------------------------------------
    # Bounded Loop Execution: process_document
    # --------------------------------------------------------------------------
    def process_document(
        self,
        document_input: Any,
        filename: str = "count_sheet.jpg",
        mime_type: str = "image/jpeg",
    ) -> IntakeResult:
        """Executes the bounded document intake loop:
        extract_count_sheet -> validate_schema -> (file_attestation_record | quarantine_row).
        """
        # Step 1: Tool Call 1
        candidate: ExtractionCandidate = self._call_tool(
            "extract_count_sheet", document_input, filename=filename, mime_type=mime_type
        )

        # Step 2: Tool Call 2
        val_result: ValidationResult = self._call_tool("validate_schema", candidate)

        # Step 3: Branching on validation
        if val_result.is_valid and val_result.cleaned_record:
            filed = self._call_tool(
                "file_attestation_record",
                val_result.cleaned_record,
                attester_id=candidate.attester_id,
            )
            return IntakeResult(
                status="FILED",
                event_id=filed.get("event_id"),
                quarantined=False,
                reason=None,
                extracted_record=val_result.cleaned_record,
                validation_errors=[],
                human_notified=False,
                audit_events=[filed.get("event_id", "")],
            )
        else:
            combined_reason = " | ".join(val_result.errors) or "SCHEMA_VALIDATION_FAILURE"
            quarantine = self._call_tool(
                "quarantine_row",
                candidate,
                reason=combined_reason,
            )
            return IntakeResult(
                status="QUARANTINED",
                event_id=quarantine.get("event_id"),
                quarantined=True,
                reason=combined_reason,
                extracted_record=candidate.to_dict(),
                validation_errors=val_result.errors,
                human_notified=False,
                audit_events=[quarantine.get("event_id", "")],
            )
