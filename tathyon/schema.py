"""
Tathyon core object model.

Design decisions (see docs/04_SDA.md):
  - IMMUTABLE write objects: Claim, Evidence, Attestation, StateEvent, plus the
    two decision-loop events VerificationVisit and OutcomeMeasurement.
  - One DERIVED read projection: VerifiedState. Never stored, always recomputed.
  - Computed values (trust, freshness, status) are NEVER frozen into storage,
    because they are functions of a versioned policy. Freezing them would
    require a backfill on every policy change.
  - State is a pure function of the event log: replaying the log reproduces it.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def sha256(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

class ResourceType(str, Enum):
    """Closed, registry-governed set. Tenant-defined types are rejected in v1:
    genericity without a schema destroys the gate, because a policy cannot
    reason about 'enough amoxicillin' if quantity is an opaque blob."""
    MEDICINE = "medicine"
    EQUIPMENT = "equipment"
    VACCINE = "vaccine"
    BED = "bed"
    PERSONNEL = "personnel"


class FacilityType(str, Enum):
    """Sovereign public health facility hierarchy."""
    PHC = "PHC"
    CHC = "CHC"
    DISTRICT_HOSPITAL = "DISTRICT_HOSPITAL"
    DISTRICT_WAREHOUSE = "DISTRICT_WAREHOUSE"
    STATE_WAREHOUSE = "STATE_WAREHOUSE"


class StateKind(str, Enum):
    """What the freshness engine and the gate actually read.
    Three kinds, not twenty resource types."""
    QUANTITY_MULTIDIM = "quantity_multidim"   # medicine/vaccine: qty x batch x expiry
    ENUM_PLUS_HEALTH = "enum_plus_health"     # equipment: present + functional
    CAPACITY_PLUS_LOAD = "capacity_plus_load" # beds/personnel: capacity x utilisation


class VerificationState(str, Enum):
    """The record's state. A state machine, not a score.

    An auditor asking 'why is this unverified?' must hear
    'no attestation since 2026-04-11', not '0.43 < 0.5'.

    NOTE: STALE is deliberately NOT a state. It is a read-time projection of
    VERIFIED(as_of) through the decay model. Making it a stored state would
    mean a record's state changes with no event, breaking replayability.
    """
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    CONFLICTED = "CONFLICTED"
    REJECTED = "REJECTED"
    OVERRIDDEN = "OVERRIDDEN"


class Provenance(str, Enum):
    """Never silently mix these. The UI must surface SYNTHETIC."""
    REAL_PUBLIC = "REAL_PUBLIC"
    REAL_USER_PROVIDED = "REAL_USER_PROVIDED"
    SYNTHETIC = "SYNTHETIC"
    SAMPLE = "SAMPLE"          # opt-in realistic sample data for demonstration (judging rules allow it)
    SIMULATED = "SIMULATED"
    DERIVED = "DERIVED"
    HYBRID = "HYBRID"


class EventType(str, Enum):
    CLAIM_INGESTED = "claim_ingested"
    FLAGGED = "flagged"
    VERIFICATION_REQUESTED = "verification_requested"
    EVIDENCE_CAPTURED = "evidence_captured"
    EXTRACTED = "extracted"
    ATTESTED = "attested"
    RECONCILED = "reconciled"
    CONSUMED = "consumed"
    RECEIVED = "received"
    REJECTED = "rejected"
    OVERRIDDEN = "overridden"
    DECISION_MADE = "decision_made"
    TRANSFER_APPROVED = "transfer_approved"
    OBLIGATION_CREATED = "obligation_created"
    CONFLICT_RAISED = "conflict_raised"
    SLA_EVALUATED = "sla_evaluated"
    GFR22_REFUSED = "gfr22_refused"
    GFR22_ISSUED = "gfr22_issued"
    # Phase 1-7 Event Lifecycle
    FORECAST_COMPUTED = "forecast_computed"
    STOCKOUT_PREDICTED = "stockout_predicted"
    SURGE_DETECTED = "surge_detected"
    TWIN_RUN = "twin_run"
    PLAN_PROPOSED = "plan_proposed"
    PLAN_APPROVED = "plan_approved"
    OUTCOME_RECORDED = "outcome_recorded"
    OBSERVATION = "observation"
    # Supply Reliability Engine: the two immutable decision-loop events
    VERIFICATION_VISIT = "verification_visit"
    OUTCOME_MEASURED = "outcome_measured"
    # Operational workspace loop (tathyon/workspace.py)
    WORKSPACE_LOADED = "workspace_loaded"
    FACILITY_REGISTERED = "facility_registered"
    SKU_REGISTERED = "sku_registered"
    ROW_QUARANTINED = "row_quarantined"
    SHIPMENT_DISPATCHED = "shipment_dispatched"
    SHIPMENT_DELAYED = "shipment_delayed"
    REPLAN_REQUIRED = "replan_required"
    ESCALATED = "escalated"
    CLOCK_ADVANCED = "clock_advanced"
    # Bounded agents (tathyon/agents): every run and refusal is on the ledger
    AGENT_RUN = "agent_run"
    AGENT_REFUSED = "agent_refused"


# --------------------------------------------------------------------------
# Immutable write objects
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Claim:
    """What a system of record asserts. Author: e-Aushadhi / asset register.
    Immutable: a corrected claim is a NEW claim that supersedes this one."""
    claim_id: str
    facility_id: str
    resource_type: ResourceType
    resource_key: str               # sku or asset_id
    state: dict                     # type-specific body, schema-validated
    source_system: str
    source_actor: str
    effective_at: str               # when the source says this was true
    ingested_at: str
    provenance: Provenance
    supersedes: Optional[str] = None

    def digest(self) -> str:
        return sha256(asdict(self))


@dataclass(frozen=True)
class Evidence:
    """A captured artifact plus its extraction. Author: the device."""
    evidence_id: str
    claim_id: str
    facility_id: str
    kind: str                       # photo_shelf | photo_register | photo_asset
    captured_at: str
    device_id: str
    nonce: str                      # server-issued, must appear in frame
    nonce_issued_at: str
    frame_count: int                # 3-frame burst defeats photo-of-a-photo
    artifact_hash: str              # hash of the image bytes
    perceptual_hash: str            # dedupe against facility history
    extraction: Optional[dict] = None
    extraction_model: Optional[str] = None
    extraction_confidence: Optional[float] = None
    extraction_abstained: bool = False
    provenance: Provenance = Provenance.SYNTHETIC

    def digest(self) -> str:
        return sha256(asdict(self))


@dataclass(frozen=True)
class Attestation:
    """A signed human assertion referencing evidence.

    HARD RULES:
    1. The attester must NOT be the custodian of the resource.
    2. The attester must NOT be the maintenance vendor or contractor.
    3. The attester must NOT be an invoice beneficiary.
    Every audit tradition separates custody, recording and verification.
    """
    attestation_id: str
    claim_id: str
    evidence_refs: list
    observed: dict                  # what the human asserts is physically true
    observed_at: str
    attestor_id: str
    attestor_role: str
    delegation_id: str
    is_custodian: bool              # must be False; enforced at write time
    seconds_spent: float            # < 20s median => rubber-stamping
    signature: str                  # software keypair in prototype; see LIMITATIONS
    is_vendor: bool = False         # must be False; vendor cannot verify its own work
    is_beneficiary: bool = False    # must be False; payee cannot verify its own payment
    vendor_id: Optional[str] = None
    extraction_agreement: Optional[dict] = None

    def digest(self) -> str:
        return sha256(asdict(self))


class VisitStatus(str, Enum):
    """Lifecycle of a physical count. A status change is a NEW VerificationVisit
    event with the same visit_id; nothing is updated in place."""
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    MISSED = "MISSED"
    CANCELLED = "CANCELLED"


# Doctrine: a priority score may order the verification queue, but it is never
# persisted, exported or attested. These names may therefore never become
# fields of a stored event. tests/test_decision_events.py enforces it.
FORBIDDEN_STORED_SCORE_FIELDS = frozenset({
    "priority", "score", "value", "rank", "p_wrong", "p_materially_wrong",
    "expected_value", "trust_score", "consequence",
})


def _require_text(obj: object, *names: str) -> None:
    for name in names:
        v = getattr(obj, name)
        if not isinstance(v, str) or not v.strip():
            raise ValueError(f"{type(obj).__name__}.{name} must be a non-empty string")


def _require_timestamp(obj: object, name: str) -> None:
    v = getattr(obj, name)
    try:
        datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{type(obj).__name__}.{name} is not an ISO-8601 timestamp: {v!r}") from exc


@dataclass(frozen=True)
class VerificationVisit:
    """A physical count of one SKU at one facility, assigned to a named person.

    Carries NO priority, score or probability. The ranking that selected this
    visit lives only in memory (verify.target_verifications); what is stored is
    the operational fact: who counts what, by when, and whether it happened.
    """
    visit_id: str
    facility_id: str
    sku_id: str
    assignee: str
    due_date: str                   # ISO-8601
    status: VisitStatus = VisitStatus.SCHEDULED

    def __post_init__(self) -> None:
        _require_text(self, "visit_id", "facility_id", "sku_id", "assignee")
        _require_timestamp(self, "due_date")
        if not isinstance(self.status, VisitStatus):
            object.__setattr__(self, "status", VisitStatus(self.status))

    def with_status(self, status: VisitStatus) -> "VerificationVisit":
        """A new visit record with a new status; the original is untouched."""
        return VerificationVisit(self.visit_id, self.facility_id, self.sku_id,
                                 self.assignee, self.due_date, VisitStatus(status))

    def to_payload(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass(frozen=True)
class OutcomeMeasurement:
    """What physically arrived against an approved plan. variance = delivered - planned."""
    plan_id: str
    delivered_qty: float
    variance: float
    measured_at: str                # ISO-8601

    def __post_init__(self) -> None:
        _require_text(self, "plan_id")
        _require_timestamp(self, "measured_at")
        for name in ("delivered_qty", "variance"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
                raise ValueError(f"OutcomeMeasurement.{name} must be a finite number, got {v!r}")
        if self.delivered_qty < 0:
            raise ValueError("OutcomeMeasurement.delivered_qty cannot be negative")

    def to_payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SLAContract:
    """Maintenance SLA definition for medical equipment (e.g. BEMMP)."""
    contract_id: str
    asset_id: str
    vendor_id: str
    vendor_name: str
    sla_target: float = 0.95        # e.g. 95% required uptime
    monthly_base_fee_inr: float = 150_000.0
    penalty_rate_per_pct: float = 10_000.0   # deduction per 1% shortfall


@dataclass(frozen=True)
class SLAInvoiceLine:
    """Evaluated payable status for equipment maintenance."""
    contract_id: str
    asset_id: str
    vendor_id: str
    claimed_uptime: float
    attested_downtime_hours: float
    verified_uptime: Optional[float]
    sla_target: float
    payable_status: str             # PAYABLE | BLOCKED_EVIDENCE_REQUIRED | PENALTY_APPLIED | REJECTED
    base_fee_inr: float
    penalty_inr: float
    net_payable_inr: float
    conflicts: list[str]
    reasons: list[str]
    evidence_refs: list[str]


@dataclass(frozen=True)
class ConflictRecord:
    """Explicit discrepancy between claims, vendor reports, and physical reality."""
    conflict_id: str
    resource_type: ResourceType
    resource_key: str
    facility_id: str
    claim_id: str
    evidence_refs: list[str]
    conflict_code: str
    description: str
    detected_at: str
    resolved: bool = False


@dataclass
class StateEvent:
    """The append-only ledger. The ONLY writable table."""
    event_id: str
    offset: int
    event_type: EventType
    facility_id: str
    resource_type: ResourceType
    resource_key: str
    payload: dict
    occurred_at: str
    recorded_at: str
    actor: str
    prev_hash: str = ""
    hash: str = ""

    def seal(self, prev_hash: str) -> "StateEvent":
        self.prev_hash = prev_hash
        body = {k: v for k, v in asdict(self).items() if k != "hash"}
        self.hash = sha256(body)
        return self


# --------------------------------------------------------------------------
# Derived read projection
# --------------------------------------------------------------------------

@dataclass
class VerifiedState:
    """NEVER a stored row. Recomputed per (resource, ledger_offset, policy).

    For QUANTITY_MULTIDIM we return a posterior, not a point estimate:
    a verified state is only verified at an instant, and stock decays.
    The consumer supplies alpha, because the consumer owns the risk.
    """
    facility_id: str
    resource_type: ResourceType
    resource_key: str
    state: VerificationState
    reasons: list                   # machine-readable reason codes
    as_of: str
    anchor: Optional[dict] = None
    staleness_s: Optional[float] = None
    # quantity resources
    reported_qty: Optional[float] = None
    verified_usable_qty: Optional[float] = None
    unusable_qty: Optional[float] = None
    posterior: Optional[dict] = None     # {point, q_alpha, alpha, model}
    # enum resources
    claimed_status: Optional[str] = None
    verified_status: Optional[str] = None
    survival: Optional[float] = None     # P(still functional | verified at t0)
    policy_version: str = "v1"
    provenance: Provenance = Provenance.SYNTHETIC

    def to_dict(self) -> dict:
        d = asdict(self)
        d["state"] = self.state.value
        d["resource_type"] = self.resource_type.value
        d["provenance"] = self.provenance.value
        return d


# --------------------------------------------------------------------------
# District action boundary
# --------------------------------------------------------------------------

# The district user's actions are VERIFY, TRANSFER (between verified district
# holders), WAIT and NO_ACTION. Expediting a supplier order or procuring
# externally is not something a District Drug Warehouse officer executes: it is
# a recommendation escalated to the State Drug Warehouse. Every district-facing
# remedy that needs supply from outside the district uses this text.
STATE_ESCALATION = (
    "Recommend state escalation: the State Drug Warehouse decides whether to "
    "expedite a pending order or procure; the district does not execute either."
)


# --------------------------------------------------------------------------
# Reason codes -- machine readable, human legible, audit defensible
# --------------------------------------------------------------------------

REASONS = {
    "NO_ATTESTATION": "No physical attestation on record for this resource.",
    "ATTESTATION_STALE": "Last physical attestation older than the policy window.",
    "BATCH_EXPIRY_CONFLICT": "Same batch number recorded with different expiry dates.",
    "LEDGER_ARITHMETIC": "Closing balance != opening + receipts - issues.",
    "NEGATIVE_IMPLIED_CONSUMPTION": "Implied consumption is negative.",
    "ISSUE_AGAINST_EXPIRED": "Stock issued against a batch already past expiry.",
    "ABSURD_UNIT_VALUE": "Unit value outside any plausible range.",
    "MISSING_EXPIRY": "Dated commodity recorded with no expiry date.",
    "NEGATIVE_QUANTITY": "A quantity field is negative; physically impossible.",
    "NON_FINITE_QUANTITY": "A quantity field is NaN or infinite; the record cannot be relied on.",
    "FUTURE_DATED_RECORD": "Record is dated in the future relative to ingestion.",
    "IMPOSSIBLE_CHRONOLOGY": "Record was entered before the event it describes.",
    "QTY_OUTLIER": "Quantity is a large deviation from this facility's own history.",
    "RETROACTIVE_BULK_ENTRY": "Many records entered simultaneously long after the fact.",
    "EXPIRED_STOCK_COUNTED_LIVE": "Physically present stock is past expiry and not usable.",
    "EXTRACTION_DISAGREEMENT": "Human count and document transcription disagree.",
    "ASSET_NOT_PRESENT": "Asset recorded as present was not found.",
    "ASSET_NOT_COMMISSIONED": "Asset present but never commissioned.",
    "ASSET_NON_FUNCTIONAL": "Asset present but not in working condition.",
    "UPTIME_SELF_REPORTED": "Uptime claim has no independent evidence.",
    "ATTESTOR_IS_CUSTODIAN": "Attester is the custodian of record; separation of duties violated.",
    "ATTESTOR_IS_VENDOR": "Attestor is a maintenance vendor or contractor; separation of duties violated.",
    "ATTESTOR_BENEFICIARY_CONFLICT": "Attestor is an invoice beneficiary; cannot self-verify.",
    "RUBBER_STAMP_SUSPECTED": "Attestation completed faster than a physical check permits.",
    "NONCE_MISSING": "Server-issued nonce not present in captured evidence.",
    "NONCE_EXPIRED": "Server-issued nonce has expired or was issued outside the valid time window.",
    "EVIDENCE_REUSED": "Evidence perceptually matches a prior submission.",
    "EVIDENCE_REPLAY_DETECTED": "Evidence hash or perceptual match indicates reused photographic evidence.",
    "SLA_UPTIME_CONTRADICTED": "Vendor self-reported uptime contradicted by physical verification.",
    "GFR22_CERTIFICATE_REFUSED": "policy-based physical verification certificate refused due to unverified or rejected physical state.",
}

