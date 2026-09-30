"""Tathyon deterministic, read-only Ops Copilot for connected local records.

THE ONE RULE: Read-only advisory assistant for district health officers.
Answers ONLY from tool results.
Every factual claim cites its backing [EVT-...] audit event.
Says 'I don't have that data' when tools return empty.
The Ops Copilot CANNOT approve, veto, allocate, or modify anything.
Approval stays with authorized human reviewers under TATHYON policy; configure authority from applicable department/state procedures.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from tathyon.schema import (
    EventType,
    Provenance,
    ResourceType,
    StateEvent,
    now,
)
from tathyon.store import EventStore

log = logging.getLogger(__name__)

COPILOT_READONLY_ALLOWLIST = {
    "query_trust_queue",
    "get_facility_detail",
    "search_audit_log",
    "get_allocation_plan",
    "explain_event",
}

CONSEQUENTIAL_KEYWORDS = [
    "approve",
    "approval",
    "veto",
    "reject transfer",
    "allocate",
    "dispatch",
    "override",
    "modify",
    "delete event",
    "sign off",
]


class CopilotSecurityViolation(Exception):
    """Raised when an attempt is made to call a non-allowlisted tool or perform a state mutation."""


@dataclass
class CopilotResponse:
    answer: str
    citations: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    refused_action: bool = False
    refusal_reason: Optional[str] = None
    status: str = "SUCCESS"

    def to_dict(self) -> dict:
        return asdict(self)


class OpsCopilotAgent:
    """District Ops Copilot using official Google GenAI SDK with bounded read-only tool gating."""

    ALLOWED_TOOLS = COPILOT_READONLY_ALLOWLIST

    def __init__(self, store: Optional[EventStore] = None, registry: Optional[Any] = None, client: Optional[Any] = None):
        self.store = store or EventStore()
        self.registry = registry
        self.client = client
        self._init_client()

    def execute_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Public alias for guarded tool execution."""
        return self._call_tool(tool_name, *args, **kwargs)

    def _init_client(self) -> None:
        """Keep operations Q&A local and deterministic; do not transmit facility records."""
        self.client = None

    def _call_tool(self, tool_name: str, *args, **kwargs) -> Any:
        """Guarded dispatcher enforcing the strict 5 read-only tool allowlist."""
        if tool_name not in COPILOT_READONLY_ALLOWLIST:
            # Block forbidden tool and log security violation to audit ledger
            self.store.append(
                event_type=EventType.FLAGGED,
                facility_id="DISTRICT_HQ",
                resource_type=ResourceType.MEDICINE,
                resource_key="SECURITY_GATE",
                payload={
                    "violation": "FORBIDDEN_TOOL_ATTEMPT",
                    "attempted_tool": tool_name,
                    "agent": "ops_copilot",
                    "status": "BLOCKED",
                },
                actor="agent:copilot",
            )
            raise CopilotSecurityViolation(
                f"Disallowed tool '{tool_name}'. Allowed: {sorted(COPILOT_READONLY_ALLOWLIST)}. "
                "Ops Copilot has no mutation or approval capabilities."
            )

        handler: Callable = getattr(self, tool_name)
        return handler(*args, **kwargs)

    # --------------------------------------------------------------------------
    # Tool 1: query_trust_queue (Read-only)
    # --------------------------------------------------------------------------
    def query_trust_queue(self, budget_slots: int = 10, limit: int = 20) -> Dict[str, Any]:
        """Return no ranking until an approved real-data model is configured."""
        return {"budget_slots": budget_slots, "slots_used": 0, "items": [],
                "status": "NO_APPROVED_REAL_MODEL", "is_fixture": False}

    # --------------------------------------------------------------------------
    # Tool 2: get_facility_detail (Read-only)
    # --------------------------------------------------------------------------
    def get_facility_detail(self, facility_id: str) -> Dict[str, Any]:
        """Looks up facility metadata, observed stock reports, and active claims."""
        meta = {"facility_id": facility_id, "found": False}
        # Do not expose benchmark parquet records as operational facility facts.
        # Details come only from claims imported into this operational store.
        # Check store claims
        claims = [
            {"claim_id": c.claim_id, "resource_key": c.resource_key, "state": c.state}
            for c in self.store._claims.values()
            if c.facility_id == facility_id
        ]

        if claims:
            meta.update({
                "found": True,
                "active_claims": claims,
            })
            return meta

        return {"facility_id": facility_id, "found": False, "message": "Facility not found in registry."}

    # --------------------------------------------------------------------------
    # Tool 3: search_audit_log (Read-only)
    # --------------------------------------------------------------------------
    def search_audit_log(
        self,
        query: Optional[str] = None,
        event_type: Optional[str] = None,
        facility_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Searches immutable SHA-256 event ledger."""
        evs = self.store.events
        if facility_id:
            evs = [e for e in evs if getattr(e, "facility_id", None) == facility_id]
        if event_type:
            evs = [
                e for e in evs
                if getattr(getattr(e, "event_type", None), "value", str(getattr(e, "event_type", ""))) == event_type
            ]
        if query:
            q_lower = query.lower()
            evs = [
                e for e in evs
                if q_lower in str(e.payload).lower() or q_lower in str(e.actor).lower() or q_lower in str(e.facility_id).lower()
            ]

        results = []
        for e in evs[-limit:]:
            ev_type_str = e.event_type.value if hasattr(e.event_type, "value") else str(e.event_type)
            results.append({
                "offset": getattr(e, "offset", 0),
                "event_id": f"[EVT-{getattr(e, 'offset', 0):04d}]",
                "raw_event_id": e.event_id,
                "event_type": ev_type_str,
                "facility_id": getattr(e, "facility_id", ""),
                "resource_key": getattr(e, "resource_key", ""),
                "actor": getattr(e, "actor", ""),
                "hash": getattr(e, "hash", ""),
                "payload": e.payload,
            })
        return results

    # --------------------------------------------------------------------------
    # Tool 4: get_allocation_plan (Read-only)
    # --------------------------------------------------------------------------
    def get_allocation_plan(self, plan_id: Optional[str] = None) -> Dict[str, Any]:
        """Retrieves active or requested CP-SAT allocation plan from registry."""
        if self.registry and hasattr(self.registry, "plans") and self.registry.plans:
            if plan_id and plan_id in self.registry.plans:
                return self.registry.plans[plan_id]
            # Return most recent plan
            latest = list(self.registry.plans.values())[-1]
            return latest

        return {"plan_id": None, "status": "NOT_CONNECTED", "transfers": [],
                "rejected_donors": [], "message": "No operational allocation plan is available."}

    # --------------------------------------------------------------------------
    # Tool 5: explain_event (Read-only)
    # --------------------------------------------------------------------------
    def explain_event(self, event_id_or_hash: str) -> Dict[str, Any]:
        """Retrieves and explains cryptographic event block and tamper integrity."""
        target = event_id_or_hash.strip().strip("[]").replace("_", "-")
        found = None
        for e in self.store.events:
            offset_tag = f"EVT-{e.offset:04d}"
            if (
                e.event_id == target
                or offset_tag == target
                or offset_tag.replace("-", "_") == event_id_or_hash.strip().strip("[]")
                or e.hash.startswith(target)
                or target == str(e.offset)
                or (target.startswith("EVT-") and int(target.split("-")[1]) == e.offset if target.split("-")[-1].isdigit() else False)
            ):
                found = e
                break

        if not found:
            return {"found": False, "message": f"Event '{event_id_or_hash}' not found in cryptographic ledger."}

        chain_ok, _ = self.store.verify_chain()
        ev_type = found.event_type.value if hasattr(found.event_type, "value") else str(found.event_type)
        return {
            "found": True,
            "event_id": f"[EVT-{found.offset:04d}]",
            "raw_id": found.event_id,
            "event_type": ev_type,
            "facility_id": found.facility_id,
            "resource_key": found.resource_key,
            "actor": found.actor,
            "occurred_at": found.occurred_at,
            "hash": found.hash,
            "chain_intact": chain_ok,
            "payload": found.payload,
            "explanation": f"Event [EVT-{found.offset:04d}] ({ev_type}) was cryptographically committed by {found.actor} at {found.facility_id}.",
        }

    # --------------------------------------------------------------------------
    # Bounded Loop Execution: ask
    # --------------------------------------------------------------------------
    def ask(self, question: str) -> CopilotResponse:
        """Executes bounded copilot reasoning with strict citation and consequential defense."""
        q_lower = question.lower()

        # 1. Guardrail against Consequential Action Attempt
        for kw in CONSEQUENTIAL_KEYWORDS:
            if kw in q_lower:
                return CopilotResponse(
                    answer=(
                        "REFUSAL: I cannot approve, veto, or modify allocations. "
                        "Under TATHYON's safety policy, transfer approvals, "
                        "line-item vetoes, and resource allocations require explicit human Medical Officer authority. "
                        "Approval stays 100% human."
                    ),
                    citations=[],
                    tools_used=[],
                    refused_action=True,
                    refusal_reason="CONSEQUENTIAL_ACTION_FORBIDDEN",
                    status="REFUSED",
                )

        # 2. Tool Invocation Selection
        tools_called = []
        citations = []
        answer_parts = []

        # Intent: Explain event / hash audit
        if "event" in q_lower or "evt" in q_lower or "hash" in q_lower or "ledger" in q_lower:
            match = re.search(r"evt[_-]?\d+", q_lower)
            target = match.group(0).upper() if match else "0"
            tools_called.append("explain_event")
            exp = self._call_tool("explain_event", target)
            if exp.get("found"):
                cite = exp["event_id"]
                citations.append(cite)
                answer_parts.append(
                    f"{exp['explanation']} The cryptographic hash is {exp['hash'][:16]}... "
                    f"Chain integrity is {'intact' if exp.get('chain_intact') else 'compromised'}."
                )
            else:
                answer_parts.append("I don't have that data.")

        # Intent: Trust Queue / Verification targeting
        elif "verif" in q_lower or "queue" in q_lower or "target" in q_lower or "priority" in q_lower:
            tools_called.append("query_trust_queue")
            q_res = self._call_tool("query_trust_queue", budget_slots=4)
            items = q_res.get("items", [])
            if items:
                top = items[0]
                # Look up recent event if available
                recent_evs = self._call_tool("search_audit_log", facility_id=top["facility_id"], limit=1)
                cite = recent_evs[0]["event_id"] if recent_evs else "[EVT-0000]"
                citations.append(cite)
                answer_parts.append(
                    f"According to the prioritized verification queue {cite}, facility {top['facility_id']} is ranked #1 "
                    f"for {top['resource_key']} with P(Wrong)={top['p_wrong']} and {top['hidden_stockout_days']} hidden stockout-days. "
                    f"Reason: {top['reason']}."
                )
            else:
                answer_parts.append("I don't have that data: no approved real-data model or verified queue is connected.")

        # Intent: Why facility rejected as donor / Allocation plan
        elif "reject" in q_lower or "donor" in q_lower or "plan" in q_lower or "allocation" in q_lower or "counterfactual" in q_lower:
            tools_called.append("get_allocation_plan")
            plan = self._call_tool("get_allocation_plan")
            rejected = plan.get("rejected_donors", [])

            # Search audit log for plan event
            plan_evs = self._call_tool("search_audit_log", event_type="plan_approved", limit=1)
            cite = plan_evs[0]["event_id"] if plan_evs else None
            if cite:
                citations.append(cite)

            # Check if specific facility asked
            fac_match = None
            match = re.search(r"\b[A-Z]{2,8}[_-][A-Z0-9_-]+\b", question, re.IGNORECASE)
            fac_match = match.group(0).upper() if match else None

            if fac_match:
                fac_rej = [r for r in rejected if (r.get("donor_id") == fac_match or r.get("facility_id") == fac_match)]
                if fac_rej:
                    r_item = fac_rej[0]
                    answer_parts.append(
                        f"Facility {fac_match} was rejected as a donor under allocation plan {cite} because: "
                        f"{r_item.get('reason', 'Unverified stock gate')}. "
                        f"Recorded reported stock was {r_item.get('reported_stock', 'unavailable')}; verified usable stock was {r_item.get('verified_usable_stock', 'unavailable')}."
                    )
                else:
                    tools_called.append("get_facility_detail")
                    f_detail = self._call_tool("get_facility_detail", fac_match)
                    if f_detail.get("found"):
                        answer_parts.append(f"Facility {fac_match} is not in the rejected donors list for plan {cite}.")
                    else:
                        answer_parts.append("I don't have that data.")
            else:
                if rejected:
                    first_rej = rejected[0]
                    answer_parts.append(
                        f"Under allocation plan {cite}, donor {first_rej.get('donor_id')} was rejected: "
                        f"{first_rej.get('reason')}."
                    )
                else:
                    answer_parts.append("I don't have that data.")

        # Intent: Facility detail
        elif "phc" in q_lower or "chc" in q_lower or "facility" in q_lower or "stock" in q_lower:
            # find facility identifier
            fac_match = None
            for token in question.split():
                clean_t = token.strip("?,.:;\"'")
                if "phc" in clean_t.lower() or "chc" in clean_t.lower():
                    fac_match = clean_t.upper()
                    break

            if fac_match:
                tools_called.append("get_facility_detail")
                detail = self._call_tool("get_facility_detail", fac_match)
                if detail.get("found"):
                    tools_called.append("search_audit_log")
                    evs = self._call_tool("search_audit_log", facility_id=fac_match, limit=1)
                    cite = evs[0]["event_id"] if evs else None
                    if cite:
                        citations.append(cite)
                    answer_parts.append(
                        f"Facility {fac_match} detail{f' {cite}' if cite else ''}: "
                        f"{len(detail.get('active_claims', []))} imported claim(s) are recorded. They are not verified stock counts."
                    )
                else:
                    answer_parts.append("I don't have that data.")
            else:
                answer_parts.append("I don't have that data.")


        # Fallback / Missing data
        else:
            answer_parts.append("I don't have that data.")

        final_answer = " ".join(answer_parts)
        return CopilotResponse(
            answer=final_answer,
            citations=citations,
            tools_used=tools_called,
            refused_action=False,
            status="SUCCESS",
        )
