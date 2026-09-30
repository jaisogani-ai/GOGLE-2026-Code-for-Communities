"""
Append-only event store with a deterministic, replayable projector.

This is the primitive. If this is not real, nothing else in Tathyon means
anything, so it is implemented first and tested hardest.

Guarantees:
  - Append-only. No UPDATE, no DELETE. A correction is a new event.
  - Hash-chained. Each event seals the previous event's hash, so silently
    rewriting history is detectable. (This is tamper-EVIDENT, not
    tamper-PROOF -- see docs/09_SECURITY.md for the honest distinction.)
  - Idempotent. A client event id replayed on flaky sync is a no-op.
  - Replayable. project() is a pure function of the log + policy version.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Iterator, Optional

from .schema import (
    FORBIDDEN_STORED_SCORE_FIELDS, Attestation, Claim, ConflictRecord, Evidence,
    EventType, OutcomeMeasurement, ResourceType, SLAContract, SLAInvoiceLine,
    StateEvent, VerificationVisit, new_id, now, sha256,
)

GENESIS = "0" * 64

# Doctrine: the AI never counts. Humans count; a model may only transcribe.
# These are role labels an automated system could self-declare. They are the
# INPUTS this write-time guard rejects, so they are spelled out literally:
# obfuscating them would make the control unauditable.
AUTOMATED_ATTESTOR_ROLES = frozenset({
    "agent", "ai", "ai_agent", "model", "llm", "bot", "gemini", "autonomous_agent",
})
AUTOMATED_ATTESTOR_ID_PREFIXES = ("agent:", "ai:", "bot:")


class EventStore:
    def __init__(self, path: Optional[str] = None):
        self._events: list[StateEvent] = []
        self._claims: dict[str, Claim] = {}
        self._evidence: dict[str, Evidence] = {}
        self._attestations: dict[str, Attestation] = {}
        self._conflicts: dict[str, ConflictRecord] = {}
        self._sla_lines: dict[str, SLAInvoiceLine] = {}
        self._sla_contracts: dict[str, SLAContract] = {}
        self._client_ids: set[str] = set()
        self._evidence_hashes: dict[str, str] = {}  # artifact_hash -> evidence_id
        self._perceptual_hashes: dict[tuple[str, str], str] = {}  # (facility_id, phash) -> evidence_id
        self.path = path

    # -- write path ---------------------------------------------------------

    def append(
        self,
        event_type: EventType,
        facility_id: str,
        resource_type: ResourceType,
        resource_key: str,
        payload: dict,
        actor: str,
        occurred_at: Optional[str] = None,
        client_event_id: Optional[str] = None,
    ) -> Optional[StateEvent]:
        """Append one event. Returns None if this client_event_id was already
        applied -- idempotency, so an offline device can safely replay its
        whole queue after a partial sync."""
        if client_event_id and client_event_id in self._client_ids:
            return None
        prev = self._events[-1].hash if self._events else GENESIS
        ev = StateEvent(
            event_id=new_id("evt"),
            offset=len(self._events),
            event_type=event_type,
            facility_id=facility_id,
            resource_type=resource_type,
            resource_key=resource_key,
            payload=payload,
            occurred_at=occurred_at or now(),
            recorded_at=now(),
            actor=actor,
        ).seal(prev)
        self._events.append(ev)
        if client_event_id:
            self._client_ids.add(client_event_id)
        return ev

    def put_claim(self, claim: Claim) -> StateEvent:
        self._claims[claim.claim_id] = claim
        return self.append(
            EventType.CLAIM_INGESTED, claim.facility_id, claim.resource_type,
            claim.resource_key,
            {"claim_id": claim.claim_id, "state": claim.state,
             "source": claim.source_system, "digest": claim.digest(),
             "provenance": getattr(claim.provenance, "value", str(claim.provenance))},
            actor=claim.source_actor, occurred_at=claim.effective_at,
        )

    def put_evidence(self, ev: Evidence) -> StateEvent:
        """Stores evidence with anti-replay detection. Identical artifact hash
        or duplicate perceptual hash at the same facility is rejected."""
        if ev.claim_id not in self._claims:
            raise KeyError(f"CLAIM_NOT_FOUND: claim '{ev.claim_id}' does not exist.")
        claim = self._claims[ev.claim_id]
        if ev.facility_id != claim.facility_id:
            raise ValueError(
                f"FACILITY_MISMATCH: evidence facility '{ev.facility_id}' "
                f"does not match claim facility '{claim.facility_id}'."
            )

        if ev.artifact_hash in self._evidence_hashes:
            prior_id = self._evidence_hashes[ev.artifact_hash]
            if prior_id != ev.evidence_id:
                raise ValueError(
                    f"EVIDENCE_REUSED: exact artifact hash already exists on evidence '{prior_id}'. "
                    "Photographic evidence cannot be replayed."
                )

        if ev.perceptual_hash:
            pk = (ev.facility_id, ev.perceptual_hash)
            if pk in self._perceptual_hashes:
                prior_id = self._perceptual_hashes[pk]
                if prior_id != ev.evidence_id:
                    raise ValueError(
                        f"EVIDENCE_REPLAY_DETECTED: perceptual hash matches prior evidence '{prior_id}' "
                        f"at facility '{ev.facility_id}'."
                    )

        self._evidence[ev.evidence_id] = ev
        self._evidence_hashes[ev.artifact_hash] = ev.evidence_id
        if ev.perceptual_hash:
            self._perceptual_hashes[(ev.facility_id, ev.perceptual_hash)] = ev.evidence_id

        return self.append(
            EventType.EVIDENCE_CAPTURED, ev.facility_id, claim.resource_type,
            claim.resource_key,
            {"evidence_id": ev.evidence_id, "kind": ev.kind,
             "nonce": ev.nonce, "frames": ev.frame_count,
             "artifact_hash": ev.artifact_hash,
             "phash": ev.perceptual_hash},
            actor=ev.device_id, occurred_at=ev.captured_at,
            client_event_id=ev.evidence_id,
        )

    def put_attestation(self, att: Attestation) -> StateEvent:
        """Enforces write-time separation-of-duty invariants:
        1. Custodian of record may not attest.
        2. Maintenance vendor/contractor may not attest to equipment it maintains.
        3. Invoice payee/beneficiary may not attest to unlock its own disbursement.
        4. Automated systems (models, bots, LLMs) cannot sign physical attestations.
        5. Referenced claim and evidence must exist and match facility.
        """
        if att.is_custodian:
            raise ValueError(
                "ATTESTOR_IS_CUSTODIAN: the custodian of record may not attest "
                "to their own resource. Route to the facility in-charge, a block "
                "supervisor, or a peer facility."
            )
        if att.is_vendor:
            raise ValueError(
                "ATTESTOR_IS_VENDOR: a maintenance vendor or contractor cannot "
                "attest to the condition, uptime, or SLA of equipment they service."
            )
        if att.is_beneficiary:
            raise ValueError(
                "ATTESTOR_BENEFICIARY_CONFLICT: an invoice or payment beneficiary "
                "may not sign the verification that unlocks their own disbursement."
            )
        if (
            att.attestor_role.lower() in AUTOMATED_ATTESTOR_ROLES
            or att.attestor_id.lower().startswith(AUTOMATED_ATTESTOR_ID_PREFIXES)
        ):
            raise ValueError(
                "AUTOMATED_ATTESTATION_FORBIDDEN: automated systems and algorithmic models "
                "cannot sign physical attestations. Only authenticated human officers may attest."
            )
        if att.claim_id not in self._claims:
            raise KeyError(f"CLAIM_NOT_FOUND: claim '{att.claim_id}' does not exist.")

        claim = self._claims[att.claim_id]
        for ev_ref in att.evidence_refs:
            if ev_ref in self._evidence:
                ev = self._evidence[ev_ref]
                if ev.facility_id != claim.facility_id:
                    raise ValueError(
                        f"FACILITY_MISMATCH: evidence '{ev_ref}' facility '{ev.facility_id}' "
                        f"does not match claim facility '{claim.facility_id}'."
                    )

        self._attestations[att.attestation_id] = att
        claim = self._claims[att.claim_id]
        return self.append(
            EventType.ATTESTED, claim.facility_id, claim.resource_type,
            claim.resource_key,
            {"attestation_id": att.attestation_id,
             "observed": att.observed,
             "attestor": att.attestor_id,
             "role": att.attestor_role,
             "delegation": att.delegation_id,
             "seconds_spent": att.seconds_spent,
             "signature": att.signature,
             "is_vendor": att.is_vendor,
             "is_beneficiary": att.is_beneficiary,
             "evidence_refs": att.evidence_refs,
             "digest": att.digest()},
            actor=att.attestor_id, occurred_at=att.observed_at,
            client_event_id=att.attestation_id,
        )

    def put_conflict(self, conflict: ConflictRecord) -> StateEvent:
        self._conflicts[conflict.conflict_id] = conflict
        return self.append(
            EventType.CONFLICT_RAISED, conflict.facility_id, conflict.resource_type,
            conflict.resource_key,
            {"conflict_id": conflict.conflict_id,
             "code": conflict.conflict_code,
             "description": conflict.description,
             "claim_id": conflict.claim_id,
             "evidence_refs": conflict.evidence_refs},
            actor="verifier:engine", occurred_at=conflict.detected_at,
        )

    def put_sla_contract(self, contract: SLAContract) -> None:
        self._sla_contracts[contract.contract_id] = contract

    def put_sla_invoice_line(self, line: SLAInvoiceLine, facility_id: str) -> StateEvent:
        self._sla_lines[line.contract_id] = line
        return self.append(
            EventType.SLA_EVALUATED, facility_id, ResourceType.EQUIPMENT,
            line.asset_id,
            {"contract_id": line.contract_id,
             "vendor_id": line.vendor_id,
             "claimed_uptime": line.claimed_uptime,
             "verified_uptime": line.verified_uptime,
             "payable_status": line.payable_status,
             "base_fee_inr": line.base_fee_inr,
             "penalty_inr": line.penalty_inr,
             "net_payable_inr": line.net_payable_inr,
             "conflicts": line.conflicts,
             "reasons": line.reasons},
            actor="tathyon:sla_engine",
        )

    # -- decision-loop events ------------------------------------------------

    @staticmethod
    def _refuse_scores(payload: dict) -> dict:
        """Doctrine guard: a queue-ordering score is never persisted."""
        leaked = sorted(set(payload) & FORBIDDEN_STORED_SCORE_FIELDS)
        if leaked:
            raise ValueError(
                f"SCORE_NOT_PERSISTABLE: {leaked} may order a queue but may never "
                "be stored, exported or attested.")
        return payload

    def put_verification_visit(self, visit: VerificationVisit, actor: str) -> StateEvent:
        """Append a visit (or a status change of one). Idempotent per
        (visit_id, status), so a replayed offline queue is a no-op."""
        payload = self._refuse_scores(visit.to_payload())
        return self.append(
            EventType.VERIFICATION_VISIT, visit.facility_id, ResourceType.MEDICINE,
            visit.sku_id, payload, actor=actor,
            client_event_id=f"{visit.visit_id}:{visit.status.value}",
        )

    def put_outcome_measurement(self, measurement: OutcomeMeasurement,
                                facility_id: str, resource_key: str,
                                actor: str) -> StateEvent:
        payload = self._refuse_scores(measurement.to_payload())
        return self.append(
            EventType.OUTCOME_MEASURED, facility_id, ResourceType.MEDICINE,
            resource_key, payload, actor=actor, occurred_at=measurement.measured_at,
        )

    def verification_visits(self) -> dict[str, VerificationVisit]:
        """Current state of every visit, projected from the log (latest event wins)."""
        out: dict[str, VerificationVisit] = {}
        for e in self._events:
            if e.event_type == EventType.VERIFICATION_VISIT:
                out[e.payload["visit_id"]] = VerificationVisit(**e.payload)
        return out

    def outcome_measurements(self) -> list[OutcomeMeasurement]:
        return [OutcomeMeasurement(**e.payload) for e in self._events
                if e.event_type == EventType.OUTCOME_MEASURED]

    # -- read path ----------------------------------------------------------

    @property
    def events(self) -> list[StateEvent]:
        return list(self._events)

    def get_all(self) -> list[StateEvent]:
        return list(self._events)


    def claim(self, claim_id: str) -> Claim:
        return self._claims[claim_id]

    def evidence(self, evidence_id: str) -> Evidence:
        return self._evidence[evidence_id]

    def attestation(self, att_id: str) -> Attestation:
        return self._attestations[att_id]

    def claims_for(self, facility_id: str, resource_key: str) -> list[Claim]:
        return [c for c in self._claims.values()
                if c.facility_id == facility_id and c.resource_key == resource_key]

    def latest_claim(self, facility_id: str, resource_key: str) -> Optional[Claim]:
        cs = self.claims_for(facility_id, resource_key)
        return max(cs, key=lambda c: c.effective_at) if cs else None

    def attestations_for(self, facility_id: str, resource_key: str) -> list[Attestation]:
        out = []
        for a in self._attestations.values():
            c = self._claims[a.claim_id]
            if c.facility_id == facility_id and c.resource_key == resource_key:
                out.append(a)
        return sorted(out, key=lambda a: a.observed_at)

    def latest_attestation(self, facility_id: str, resource_key: str) -> Optional[Attestation]:
        a = self.attestations_for(facility_id, resource_key)
        return a[-1] if a else None

    def timeline(self, facility_id: str, resource_key: str) -> list[dict]:
        """Everything that happened to one resource, in order. This is the
        audit trail UI's data source and the CAG export."""
        return [
            {"offset": e.offset, "type": e.event_type.value,
             "at": e.occurred_at, "actor": e.actor,
             "payload": e.payload, "hash": e.hash[:12]}
            for e in self._events
            if e.facility_id == facility_id and e.resource_key == resource_key
        ]

    # -- integrity ----------------------------------------------------------

    def verify_chain(self) -> tuple[bool, Optional[int]]:
        """Recompute every hash. Returns (ok, first_bad_offset)."""
        prev = GENESIS
        for e in self._events:
            body = {k: v for k, v in asdict(e).items() if k != "hash"}
            body["prev_hash"] = prev
            if sha256(body) != e.hash or e.prev_hash != prev:
                return False, e.offset
            prev = e.hash
        return True, None

    def chain_root(self) -> str:
        """Returns the latest event hash, or GENESIS if empty."""
        return self._events[-1].hash if self._events else GENESIS

    # -- persistence --------------------------------------------------------

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            for e in self._events:
                d = asdict(e)
                d["event_type"] = e.event_type.value
                d["resource_type"] = e.resource_type.value
                f.write(json.dumps(d, default=str) + "\n")
