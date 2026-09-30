"""Agent 1 — Intake Agent. Turns an incoming count sheet into a DRAFT for human confirmation.

Pipeline (fixed order, every step an allowlisted tool call):
    extract_count_sheet -> validate_schema -> quarantine_row | file_attestation_record

Rules: uncertain = null; never invent numbers; never approve or allocate; never
write verified state. `file_attestation_record` writes an EXTRACTED event with
status DRAFT_PENDING_HUMAN_CONFIRMATION. Only a human verifier's
POST /verify/attest turns a count into an attestation.

Text sheets are parsed deterministically. Photographed sheets are transcribed
by Gemini only when GEMINI_API_KEY is configured; fields below the confidence
threshold come back null. Without a key, image intake reports NOT_CONFIGURED.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from ..schema import EventType
from .base import AI_LABEL, AgentSpec, BoundedAgent, RunContext, Tool, redact

FIELD_PATTERNS = {
    "facility_id": r"(?:facility|facility[_ ]id|phc|chc)\s*[:=]\s*([A-Za-z]{2,4}-[A-Za-z0-9]+)",
    "sku": r"(?:sku|item|drug[_ ]?code)\s*[:=]\s*([A-Za-z0-9-]{2,20})",
    "present_qty": r"(?:present|on[_ ]?hand|total)\s*(?:qty|quantity)?\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    "usable_qty": r"(?:usable|good)\s*(?:qty|quantity)?\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    "expired_qty": r"(?:expired|damaged)\s*(?:qty|quantity)?\s*[:=]\s*(-?\d+(?:\.\d+)?)",
    "counted_by": r"(?:counted[_ ]?by|verifier|attester)\s*[:=]\s*([A-Za-z0-9._-]{2,40})",
}
INJECTION_RE = re.compile(r"ignore (all|previous|prior)|system prompt|you are now|approve|disregard|"
                          r"override|<\s*script|\bexecute\b", re.I)
MIN_FIELD_CONFIDENCE = 0.7
NUMERIC_FIELDS = ("present_qty", "usable_qty", "expired_qty")


class ExtractArgs(BaseModel):
    text: Optional[str] = Field(default=None, max_length=4000)
    image_sha256: Optional[str] = Field(default=None, max_length=64)


class Candidate(BaseModel):
    facility_id: Optional[str] = None
    sku: Optional[str] = None
    present_qty: Optional[float] = None
    usable_qty: Optional[float] = None
    expired_qty: Optional[float] = None
    counted_by: Optional[str] = None


class QuarantineArgs(BaseModel):
    candidate: Candidate
    reasons: list[str] = Field(min_length=1, max_length=10)


class FileArgs(BaseModel):
    candidate: Candidate
    source_sha256: str = Field(min_length=16, max_length=64)


def parse_text_sheet(text: str) -> dict:
    """Each field: a single unambiguous match, else null. Nothing is guessed."""
    out: dict[str, Any] = {}
    for field, pattern in FIELD_PATTERNS.items():
        values = {m.group(1).strip() for m in re.finditer(pattern, text, re.I)}
        if len(values) != 1:
            out[field] = None
            continue
        value = values.pop()
        out[field] = float(value) if field in NUMERIC_FIELDS else value.upper() if field in ("facility_id", "sku") \
            else value
    return out


class IntakeAgent(BoundedAgent):
    SPEC = AgentSpec(
        name="intake_agent", title="Intake Agent",
        trigger="A verifier uploads a count sheet (text or photo) in the Trust Queue.",
        purpose="Extract a count sheet into a structured draft, validate it, and either quarantine it or file it "
                "for human confirmation.",
        forbidden_actions=("approve", "allocate", "write verified state", "fill in unreadable values",
                           "attest on behalf of a human"),
        human_boundary="A human verifier confirms the draft; only then is an attestation written.",
        failure_behavior="Unreadable or ambiguous fields become null; any validation issue quarantines the sheet "
                         "with typed reasons.",
        max_tool_calls=4,
    )

    def __init__(self, ws, llm=None, vision=None):
        super().__init__(ws, None)
        self.vision = vision  # callable(image_bytes, mime) -> {"fields":{}, "confidences":{}, "model": str}

    def tools(self) -> list[Tool]:
        ws = self.ws

        def extract(a: ExtractArgs) -> dict:
            if a.text is not None:
                return {"candidate": parse_text_sheet(a.text), "method": "DETERMINISTIC_TEXT_PARSE",
                        "injection_suspected": bool(INJECTION_RE.search(a.text))}
            return {"error": "NO_INPUT"}

        def validate(c: Candidate) -> dict:
            issues = []
            for f in ("facility_id", "sku", "present_qty", "usable_qty", "counted_by"):
                if getattr(c, f) in (None, ""):
                    issues.append(f"MISSING_OR_UNREADABLE_{f.upper()}")
            for f in NUMERIC_FIELDS:
                v = getattr(c, f)
                if v is not None and (not math.isfinite(v) or v < 0):
                    issues.append("NEGATIVE_OR_NON_FINITE_QUANTITY")
            if c.present_qty is not None and c.usable_qty is not None and c.usable_qty > c.present_qty:
                issues.append("USABLE_EXCEEDS_PRESENT")
            if c.facility_id and c.facility_id not in ws.facilities:
                issues.append("UNKNOWN_FACILITY")
            if c.sku and c.sku not in ws.skus:
                issues.append("UNKNOWN_SKU")
            custodian = ws.facilities.get(c.facility_id or "", {}).get("custodian_id")
            if custodian and c.counted_by == custodian:
                issues.append("COUNTED_BY_CUSTODIAN")
            return {"valid": not issues, "issues": sorted(set(issues))}

        def quarantine(a: QuarantineArgs) -> dict:
            ev = ws._emit(EventType.ROW_QUARANTINED, (a.candidate.facility_id or "UNKNOWN")[:60],
                          a.candidate.sku or "*", {"source": "intake_agent", "row_number": 1,
                                                   "reasons": a.reasons, "raw": a.candidate.model_dump(),
                                                   "human_review_required": True}, "agent:intake_agent")
            return {"quarantined": True, "event_id": ev.event_id}

        def file_draft(a: FileArgs) -> dict:
            c = a.candidate
            ev = ws._emit(EventType.EXTRACTED, c.facility_id or "UNKNOWN", c.sku or "*", {
                "status": "DRAFT_PENDING_HUMAN_CONFIRMATION", "candidate": c.model_dump(),
                "source_sha256": a.source_sha256, "writes_verified_state": False,
                "note": "A human verifier must re-enter or confirm these values via POST /verify/attest."},
                "agent:intake_agent")
            return {"draft_event_id": ev.event_id, "status": "DRAFT_PENDING_HUMAN_CONFIRMATION"}

        return [
            Tool("extract_count_sheet", "Extract fields from a count sheet; unreadable = null.", ExtractArgs, extract),
            Tool("validate_schema", "Validate a candidate against registry and arithmetic rules.", Candidate, validate),
            Tool("quarantine_row", "Quarantine a sheet with typed reasons.", QuarantineArgs, quarantine,
                 effect="PROPOSE"),
            Tool("file_attestation_record", "File a DRAFT for human confirmation (not an attestation).", FileArgs,
                 file_draft, effect="PROPOSE"),
        ]

    def plan_deterministic(self, request: str, ctx: RunContext) -> list[tuple[str, dict]]:
        return []

    def compose_deterministic(self, request: str, ctx: RunContext):  # pragma: no cover - run() is overridden
        raise NotImplementedError

    def run(self, request: str, actor: str) -> dict:
        return self._pipeline(text=request, image=None, mime=None, actor=actor)

    def run_image(self, image: bytes, mime: str, actor: str) -> dict:
        return self._pipeline(text=None, image=image, mime=mime, actor=actor)

    def _pipeline(self, text: Optional[str], image: Optional[bytes], mime: Optional[str], actor: str) -> dict:
        started = time.monotonic()
        ctx = RunContext()
        source = (text or "").encode() if image is None else image
        digest = hashlib.sha256(source).hexdigest()
        provider = {"mode": "DETERMINISTIC", "model": None, "degraded_reason": None}
        if image is not None:
            if self.vision is None:
                return self._finish(ctx, "NOT_CONFIGURED", "Photo transcription needs GEMINI_API_KEY on the server. "
                                    "Enter the counted values manually.", provider, digest, actor, started)
            try:
                vis = self.vision(image, mime or "image/jpeg")
            except Exception as exc:
                return self._finish(ctx, "DEGRADED", f"Photo transcription failed ({type(exc).__name__}); "
                                    "enter values manually.", provider, digest, actor, started)
            provider = {"mode": "GEMINI", "model": vis.get("model"), "degraded_reason": None}
            fields = {k: (v if vis.get("confidences", {}).get(k, 0.0) >= MIN_FIELD_CONFIDENCE else None)
                      for k, v in vis.get("fields", {}).items() if k in Candidate.model_fields}
            extracted = {"candidate": fields, "method": "GEMINI_TRANSCRIPTION", "injection_suspected": False}
            ctx.tools_used.append({"tool": "extract_count_sheet", "args": {"image_sha256": digest}, "ok": True})
        else:
            extracted = self._call("extract_count_sheet", {"text": text or ""}, ctx)
        if "error" in extracted:
            return self._finish(ctx, "REJECTED", extracted["error"], provider, digest, actor, started)
        candidate = Candidate.model_validate({k: v for k, v in extracted["candidate"].items()
                                              if k in Candidate.model_fields})
        check = self._call("validate_schema", candidate.model_dump(), ctx)
        reasons = list(check.get("issues", []))
        if extracted.get("injection_suspected"):
            reasons.append("PROMPT_INJECTION_SUSPECTED")
        if reasons:
            q = self._call("quarantine_row", {"candidate": candidate.model_dump(), "reasons": reasons}, ctx)
            out = self._finish(ctx, "QUARANTINED", "Sheet quarantined for human review: " + ", ".join(reasons),
                               provider, digest, actor, started)
            return {**out, "candidate": candidate.model_dump(), "quarantine_event_id": q.get("event_id"),
                    "reasons": reasons}
        filed = self._call("file_attestation_record", {"candidate": candidate.model_dump(),
                                                       "source_sha256": digest}, ctx)
        out = self._finish(ctx, "DRAFT_FILED", "Draft filed. A human verifier must confirm it before it counts.",
                           provider, digest, actor, started)
        return {**out, "candidate": candidate.model_dump(), "draft_event_id": filed.get("draft_event_id")}

    def _finish(self, ctx: RunContext, status: str, answer: str, provider: dict, digest: str,
                actor: str, started: float) -> dict:
        ev = self.ws._emit(EventType.AGENT_RUN, "DISTRICT", "*", {
            "agent": self.SPEC.name, "status": status, "request_sha256": digest[:16], "provider": provider,
            "tools_used": [{"tool": t["tool"], "error": t.get("error")} for t in ctx.tools_used],
            "requested_by": actor, "duration_ms": round((time.monotonic() - started) * 1000, 1)},
            "agent:intake_agent")
        return {"agent": self.SPEC.name, "status": status, "label": AI_LABEL, "answer": redact(answer),
                "statements": [], "tools_used": ctx.tools_used, "provider": provider,
                "environment": self.ws.environment, "run_event_id": ev.event_id,
                "unsupported_statements_dropped": 0, "proposals": []}
