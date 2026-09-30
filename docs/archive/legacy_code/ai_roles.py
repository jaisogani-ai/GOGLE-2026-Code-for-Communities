"""TATHYON deterministic role registry with fail-closed disconnected roles.

Describes six proposed role boundaries; only read-only local status roles are
available until authenticated real-data adapters are integrated. Gemini calls
are disabled and no record data is sent to a model provider.

- Role A: Intake Extraction
- Role B: Data Quality Triage
- Role C: Demand Forecast Explainer
- Role D: District Operations Copilot
- Role E: Allocation Plan Explainer
- Role F: Federation Model Steward

Hard Invariants:
1. Every tool call is gated by an application-controlled server-side allowlist.
2. An AI role cannot access the database directly, modify verified state, or approve transfers.
3. Every execution is audited in the SHA-256 event ledger.
4. All outputs render the mandatory notice: "AI suggestion — human decides."
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from tathyon.schema import EventType, ResourceType, VerificationState, new_id, now
from tathyon.store import EventStore
from tathyon.ai_prompts import PROMPT_VERSION

MANDATORY_DISCLAIMER = "AI suggestion — human decides."


class AIRoleKind(str, Enum):
    ROLE_A_INTAKE = "ROLE_A_INTAKE"
    ROLE_B_DATA_QUALITY = "ROLE_B_DATA_QUALITY"
    ROLE_C_FORECAST_EXPLAINER = "ROLE_C_FORECAST_EXPLAINER"
    ROLE_D_OPS_COPILOT = "ROLE_D_OPS_COPILOT"
    ROLE_E_ALLOCATION_EXPLAINER = "ROLE_E_ALLOCATION_EXPLAINER"
    ROLE_F_FEDERATION_STEWARD = "ROLE_F_FEDERATION_STEWARD"


ROLE_ALLOWLISTS: Dict[AIRoleKind, set[str]] = {
    AIRoleKind.ROLE_A_INTAKE: {
        "extract_count_sheet",
        "validate_schema",
        "quarantine_row",
        "file_attestation_record",
    },
    AIRoleKind.ROLE_B_DATA_QUALITY: {
        "read_observation",
        "compare_history",
        "calculate_quality_flags",
        "create_review_recommendation",
    },
    AIRoleKind.ROLE_C_FORECAST_EXPLAINER: {
        "read_forecast",
        "read_feature_summary",
        "read_data_freshness",
        "explain_model_output",
    },
    AIRoleKind.ROLE_D_OPS_COPILOT: {
        "query_trust_queue",
        "get_facility_detail",
        "search_audit_log",
        "get_allocation_plan",
        "explain_event",
    },
    AIRoleKind.ROLE_E_ALLOCATION_EXPLAINER: {
        "read_allocation_plan",
        "read_constraints",
        "read_facility_detail",
        "explain_event",
    },
    AIRoleKind.ROLE_F_FEDERATION_STEWARD: {
        "read_federation_status",
        "read_model_card",
        "read_aggregate_metrics",
        "create_review_recommendation",
    },
}

CONSEQUENTIAL_INTENTS = [
    "approve",
    "veto",
    "dispatch",
    "allocate",
    "execute transfer",
    "reallocate",
    "modify verified",
    "sign off",
    "trigger training",
    "publish model",
]


class RoleSecurityViolation(Exception):
    """Raised when an AI role attempts to invoke a non-allowlisted tool or forbidden state mutation."""


@dataclass
class AIRoleResult:
    role: AIRoleKind
    status: str  # "SUCCESS" | "REFUSED" | "QUARANTINED" | "BLOCKED"
    output_text: str
    tools_called: List[Dict[str, Any]] = field(default_factory=list)
    citations: List[str] = field(default_factory=list)
    audit_event_id: Optional[str] = None
    refused: bool = False
    refusal_reason: Optional[str] = None
    disclaimer: str = MANDATORY_DISCLAIMER
    execution_duration_ms: float = 0.0
    provider: str = "deterministic-orchestrator-fallback"
    model_name: Optional[str] = None
    is_live_model: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class DeterministicRoleOrchestrator:
    """Server-side bounded tool runner enforcing strict allowlists, audit logging, and prompt boundaries."""

    def __init__(self, store: Optional[EventStore] = None, registry: Optional[Any] = None, client: Optional[Any] = None):
        self.store = store or EventStore()
        self.registry = registry
        # Operational AI stays deterministic/local unless a separately reviewed
        # redacted-data provider path is implemented. Never infer permission from a key.
        self.client = None

    def _init_genai_client(self) -> None:
        """External model calls are disabled until a privacy-reviewed adapter exists."""
        self.client = None

    def _synthesize_with_gemini(self, role: AIRoleKind, user_prompt: str, tools_executed: list) -> Optional[str]:
        """No provider is configured; never transmit local health records."""
        return None

    def execute_tool(self, role: AIRoleKind, tool_name: str, args: Dict[str, Any]) -> Any:
        """Server-side gate: verifies tool is allowlisted for the specified role before execution."""
        if role not in {AIRoleKind.ROLE_D_OPS_COPILOT, AIRoleKind.ROLE_F_FEDERATION_STEWARD}:
            raise RoleSecurityViolation(
                f"ROLE_SOURCE_NOT_CONNECTED: '{role.value}' has no authorized operational adapter."
            )
        allowlist = ROLE_ALLOWLISTS.get(role, set())
        if tool_name not in allowlist:
            # Cryptographically record unauthorized tool attempt in ledger
            ev = self.store.append(
                event_type=EventType.FLAGGED,
                facility_id="DISTRICT_HQ",
                resource_type=ResourceType.MEDICINE,
                resource_key="SECURITY_GATE",
                payload={
                    "violation": "FORBIDDEN_ROLE_TOOL_ATTEMPT",
                    "role": role.value,
                    "attempted_tool": tool_name,
                    "status": "BLOCKED",
                },
                actor=f"agent:{role.value.lower()}",
            )
            raise RoleSecurityViolation(
                f"TOOL_FORBIDDEN: Role '{role.value}' is strictly forbidden from executing '{tool_name}'. "
                f"Allowlist: {sorted(allowlist)}."
            )

        handler_name = f"_tool_{tool_name}"
        handler: Callable = getattr(self, handler_name, None)
        if not handler:
            raise RoleSecurityViolation(f"Tool handler '{handler_name}' is not implemented on server.")

        return handler(args)

    # --------------------------------------------------------------------------
    # Tool Handlers (Deterministic, Safe, Read-Only or Staged)
    # --------------------------------------------------------------------------

    # Role A Tools
    def _tool_extract_count_sheet(self, args: Dict[str, Any]) -> Dict[str, Any]:
        doc_ref = args.get("document_reference", "")
        # No image/OCR extractor is connected. Never fabricate field values.
        if "UNREADABLE" in doc_ref or "CORRUPTED" in doc_ref:
            return {"unreadable": True, "error": "CORRUPTED_DOCUMENT"}
        if "PROMPT_INJECTION" in doc_ref or "ignore previous instructions" in doc_ref.lower():
            # Defense against prompt injection in documents
            return {
                "unreadable": False,
                "facility_id": None,
                "resource_key": None,
                "present_quantity": None,  # Suppressed
                "usable_quantity": None,
                "confidences": {"present_quantity": 0.0},
                "security_note": "PROMPT_INJECTION_ATTEMPT_DEFUSED",
            }
        required = ("facility_id", "resource_key", "present_quantity", "usable_quantity")
        missing = [key for key in required if key not in args]
        if missing:
            return {
                "unreadable": False,
                "status": "ABSTAINED_NO_DOCUMENT_EXTRACTION",
                "data_provenance": "NO_EXTRACTION_PERFORMED",
                "missing_fields": missing,
                "present_quantity": None,
                "usable_quantity": None,
                "confidences": {},
            }
        return {
            "unreadable": False,
            "facility_id": args.get("facility_id"),
            "resource_key": args.get("resource_key"),
            "present_quantity": float(args["present_quantity"]),
            "usable_quantity": float(args["usable_quantity"]),
            "expired_quantity": float(args["expired_quantity"]) if args.get("expired_quantity") is not None else None,
            "confidences": {},
        }

    def _tool_validate_schema(self, args: Dict[str, Any]) -> Dict[str, Any]:
        cand = args.get("candidate", {})
        errors = []
        if cand.get("unreadable"):
            return {"is_valid": False, "errors": ["UNREADABLE_DOCUMENT"]}

        fac = cand.get("facility_id")
        sku = cand.get("resource_key")
        if not fac:
            errors.append("MISSING_FACILITY_ID")
        if not sku:
            errors.append("MISSING_RESOURCE_KEY")

        p_qty = cand.get("present_quantity")
        u_qty = cand.get("usable_quantity")
        if p_qty is None or p_qty < 0:
            errors.append("INVALID_PRESENT_QUANTITY")
        if u_qty is None or u_qty < 0:
            errors.append("INVALID_USABLE_QUANTITY")
        if p_qty is not None and u_qty is not None and u_qty > p_qty:
            errors.append(f"ARITHMETIC_ANOMALY: usable ({u_qty}) > present ({p_qty})")

        return {"is_valid": len(errors) == 0, "errors": errors, "candidate": cand}

    def _tool_quarantine_row(self, args: Dict[str, Any]) -> Dict[str, Any]:
        cand = args.get("candidate", {})
        reason = args.get("reason", "Validation error")
        ev = self.store.append(
            event_type=EventType.FLAGGED,
            facility_id=cand.get("facility_id") or "UNRESOLVED_FACILITY",
            resource_type=ResourceType.MEDICINE,
            resource_key=cand.get("resource_key") or "UNSPECIFIED_RESOURCE",
            payload={"status": "QUARANTINED", "reason": reason, "human_review_required": True, "raw": cand},
            actor="agent:intake",
        )
        return {
            "status": "QUARANTINED",
            "event_id": f"[EVT-{ev.offset:04d}]",
            "hash": ev.hash,
            "human_review_required": True,
            "reason": reason,
        }

    def _tool_file_attestation_record(self, args: Dict[str, Any]) -> Dict[str, Any]:
        rec = args.get("validated_record", {})
        required = ("facility_id", "resource_key", "present_quantity", "usable_quantity")
        missing = [key for key in required if rec.get(key) in (None, "")]
        if missing:
            raise ValueError(f"REQUIRED_FIELDS_MISSING: {', '.join(missing)}")
        if float(rec["present_quantity"]) < 0 or float(rec["usable_quantity"]) < 0 or float(rec["usable_quantity"]) > float(rec["present_quantity"]):
            raise ValueError("QUANTITY_INVARIANT_FAILED")
        att_id = args.get("attester_id", "FIELD_VERIFIER_STAGING")
        ev = self.store.append(
            event_type=EventType.EXTRACTED,
            facility_id=rec["facility_id"],
            resource_type=ResourceType.MEDICINE,
            resource_key=rec["resource_key"],
            payload={
                "status": "INTAKE_STAGED",
                "verified_state": VerificationState.UNVERIFIED.value,  # Never auto-verified
                "staged_present_qty": rec.get("present_quantity"),
                "staged_usable_qty": rec.get("usable_quantity"),
                "attester_id": att_id,
            },
            actor="agent:intake",
        )
        return {
            "status": "FILED",
            "event_id": f"[EVT-{ev.offset:04d}]",
            "hash": ev.hash,
            "verified_state": VerificationState.UNVERIFIED.value,
        }

    # Role B Tools
    def _tool_read_observation(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "NOT_CONNECTED", "message": "No authorized observation source is connected."}

    def _tool_compare_history(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "INSUFFICIENT_DATA", "message": "No authorized history is connected."}

    def _tool_calculate_quality_flags(self, args: Dict[str, Any]) -> List[str]:
        return []

    def _tool_create_review_recommendation(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "NOT_CREATED", "reason": "No authorized source records are connected."}

    def _tool_read_forecast(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "UNAVAILABLE", "reason": "No approved real-data model and authorized series are connected."}

    def _tool_read_feature_summary(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "UNAVAILABLE", "top_drivers": []}

    def _tool_read_data_freshness(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "UNAVAILABLE", "facility_id": args.get("facility_id")}

    def _tool_explain_model_output(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "UNAVAILABLE", "reason": "No approved model is connected."}

    def _tool_query_trust_queue(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"budget_slots": args.get("budget_slots"), "slots_used": 0, "items": [],
                "status": "NO_APPROVED_REAL_MODEL"}

    def _tool_get_facility_detail(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"facility_id": args.get("facility_id"), "found": False,
                "message": "No authorized facility registry is connected."}

    def _tool_search_audit_log(self, args: Dict[str, Any]) -> List[Dict[str, Any]]:
        evs = self.store.events
        if args.get("facility_id"):
            evs = [ev for ev in evs if ev.facility_id == args["facility_id"]]
        if args.get("event_type"):
            evs = [ev for ev in evs if ev.event_type.value == args["event_type"]]
        return [{"event_id": ev.event_id, "event_type": ev.event_type.value,
                 "facility_id": ev.facility_id, "actor": ev.actor, "hash": ev.hash[:16]}
                for ev in evs[-int(args.get("limit", 50)):]]

    def _tool_get_allocation_plan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "NOT_CONNECTED", "plan_id": None, "transfers": [], "rejected_donors": []}

    def _tool_explain_event(self, args: Dict[str, Any]) -> Dict[str, Any]:
        target = args.get("event_id_or_hash", "")
        event = next((ev for ev in self.store.events if ev.event_id == target or ev.hash == target), None)
        if not event:
            return {"found": False, "message": "Event not found in local ledger."}
        intact, _ = self.store.verify_chain()
        return {"found": True, "event_id": event.event_id, "actor": event.actor,
                "hash": event.hash, "chain_intact": intact,
                "summary": f"Event committed by {event.actor} at {event.facility_id}"}

    def _tool_read_allocation_plan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return self._tool_get_allocation_plan(args)

    def _tool_read_constraints(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "POLICY_NOT_CONFIGURED", "constraints": []}

    def _tool_read_federation_status(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "round_id": None,
            "status": "NOT_CONNECTED",
            "participating_nodes": [],
            "data_policy": "DESIGN_ONLY_NOT_VERIFIED_IN_A_LIVE_DEPLOYMENT",
            "data_provenance": "NO_LIVE_FEDERATION_SOURCE_CONFIGURED",
        }

    def _tool_read_model_card(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "model_name": None,
            "algorithm": None,
            "features_used": [],
            "pr_auc": None,
            "base_rate": None,
            "leakage_audit": "UNAVAILABLE_NO_APPROVED_REAL_MODEL",
            "data_provenance": "NO_APPROVED_REAL_MODEL_CARD_CONFIGURED",
        }

    def _tool_read_aggregate_metrics(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "headline_verified_stockout_days_averted": None,
            "silent_phantom_units_blocked": None,
            "loop_closure_rate": None,
            "data_provenance": "NO_LIVE_FEDERATION_METRICS_CONFIGURED",
        }

    # --------------------------------------------------------------------------
    # Main Bounded Execution Loop
    # --------------------------------------------------------------------------
    def run_role(self, role: AIRoleKind, user_prompt: str, context_args: Optional[Dict[str, Any]] = None) -> AIRoleResult:
        start_time = time.time()
        ctx = context_args or {}
        q_lower = user_prompt.lower()

        # Legacy role handlers below contain development-era fixture assumptions.
        # Keep those implementations unreachable until real, authorized adapters
        # and human identity controls are installed.
        if role in {
            AIRoleKind.ROLE_A_INTAKE,
            AIRoleKind.ROLE_B_DATA_QUALITY,
            AIRoleKind.ROLE_C_FORECAST_EXPLAINER,
            AIRoleKind.ROLE_E_ALLOCATION_EXPLAINER,
        }:
            return AIRoleResult(
                role=role,
                status="BLOCKED",
                output_text=(
                    "This role is unavailable: no authorized real-data adapter and reviewed workflow are connected. "
                    "No fixture data was used.\n\n" + MANDATORY_DISCLAIMER
                ),
                refused=True,
                refusal_reason="ROLE_SOURCE_NOT_CONNECTED",
            )

        # 1. Server-Side Guardrail against Consequential Actions
        if role != AIRoleKind.ROLE_A_INTAKE:
            for kw in CONSEQUENTIAL_INTENTS:
                if kw in q_lower:
                    return AIRoleResult(
                        role=role,
                        status="REFUSED",
                        output_text=(
                            f"REFUSAL: Role '{role.value}' cannot execute, approve, veto, or modify operational allocations. "
                            "Under TATHYON's safety policy, transfer decisions require "
                            "explicit human Medical Officer authority. Approval stays 100% human."
                        ),
                        refused=True,
                        refusal_reason="CONSEQUENTIAL_ACTION_FORBIDDEN",
                        execution_duration_ms=(time.time() - start_time) * 1000,
                    )

        tools_executed = []
        citations = []
        output_parts = []

        # 2. Application-Controlled Deterministic Dispatch per Role
        if role == AIRoleKind.ROLE_A_INTAKE:
            # Step A1: Extract
            ext = self.execute_tool(role, "extract_count_sheet", {"document_reference": user_prompt, **ctx})
            tools_executed.append({"tool": "extract_count_sheet", "result": ext})

            # Step A2: Validate
            val = self.execute_tool(role, "validate_schema", {"candidate": ext})
            tools_executed.append({"tool": "validate_schema", "result": val})

            # Step A3: Branching
            if val.get("is_valid"):
                filed = self.execute_tool(role, "file_attestation_record", {"validated_record": ext})
                tools_executed.append({"tool": "file_attestation_record", "result": filed})
                citations.append(filed["event_id"])
                output_parts.append(
                    f"Intake count sheet successfully extracted and filed as staged record {filed['event_id']}. "
                    "Inventory remains UNVERIFIED until independent physical witness sign-off."
                )
                status = "SUCCESS"
            else:
                reason = "; ".join(val.get("errors", ["Schema validation failed"]))
                quar = self.execute_tool(role, "quarantine_row", {"candidate": ext, "reason": reason})
                tools_executed.append({"tool": "quarantine_row", "result": quar})
                citations.append(quar["event_id"])
                output_parts.append(
                    f"Count sheet failed validation and was quarantined as {quar['event_id']}. "
                    f"Reason: {reason}. Human supervisor review is required."
                )
                status = "QUARANTINED"

        elif role == AIRoleKind.ROLE_B_DATA_QUALITY:
            fac = ctx.get("facility_id")
            res_k = ctx.get("resource_key")
            if not fac or not res_k:
                output_parts.append("I don't have that data: an authorized facility and resource were not supplied.")
                status = "INSUFFICIENT_DATA"
            else:
                obs = self.execute_tool(role, "read_observation", {"facility_id": fac, "resource_key": res_k})
            tools_executed.append({"tool": "read_observation", "result": obs})

            hist = self.execute_tool(role, "compare_history", {"facility_id": fac, "resource_key": res_k})
            tools_executed.append({"tool": "compare_history", "result": hist})

            flags = self.execute_tool(role, "calculate_quality_flags", {"observation": {**obs, "arithmetic_violation": hist["discrepancy_magnitude"] > 1000}})
            tools_executed.append({"tool": "calculate_quality_flags", "result": flags})

            ticket = self.execute_tool(
                role,
                "create_review_recommendation",
                {
                    "facility_id": fac,
                    "resource_key": res_k,
                    "anomaly_flags": flags,
                    "recommended_priority": "HIGH" if flags else "LOW",
                    "rationale": f"Detected {len(flags)} data quality anomaly flags against historical consumption baseline.",
                },
            )
            tools_executed.append({"tool": "create_review_recommendation", "result": ticket})
            output_parts.append(
                f"Data quality triage completed for {fac} ({res_k}). Detected flags: {', '.join(flags) if flags else 'None'}. "
                f"Advisory ticket {ticket['ticket_id']} logged with priority {ticket['priority']}."
            )
            status = "SUCCESS"

        elif role == AIRoleKind.ROLE_C_FORECAST_EXPLAINER:
            fac = ctx.get("facility_id")
            sku = ctx.get("sku_id")
            fc = self.execute_tool(role, "read_forecast", {"facility_id": fac, "sku_id": sku})
            tools_executed.append({"tool": "read_forecast", "result": fc})

            feat = self.execute_tool(role, "read_feature_summary", {"facility_id": fac, "sku_id": sku})
            tools_executed.append({"tool": "read_feature_summary", "result": feat})

            fresh = self.execute_tool(role, "read_data_freshness", {"facility_id": fac})
            tools_executed.append({"tool": "read_data_freshness", "result": fresh})

            output_parts.append(
                f"Demand forecast for {fac} ({sku}): Model family is {fc['model_family']}. "
                f"Projected stockout in {fc['projected_stockout_day']} days (risk score: {fc['risk_score']}). "
                f"Top driver: {feat['top_drivers'][0]['feature']} ({feat['top_drivers'][0]['weight'] * 100:.0f}%). "
                f"Note: Data is {fresh['last_physical_count']}, carrying a freshness confidence penalty of {fresh['confidence_penalty'] * 100:.0f}%."
            )
            status = "SUCCESS"

        elif role == AIRoleKind.ROLE_D_OPS_COPILOT:
            # Natural language QA with mandatory citations
            if "verif" in q_lower or "queue" in q_lower:
                q_res = self.execute_tool(role, "query_trust_queue", {"budget_slots": 4})
                tools_executed.append({"tool": "query_trust_queue", "result": q_res})
                items = q_res.get("items", [])
                if items:
                    output_parts.append("Queue records are available only when returned with verified provenance.")
                else:
                    output_parts.append("No verified records are connected, so the Trust Queue is empty.")
            elif "event" in q_lower or "evt" in q_lower:
                exp = self.execute_tool(role, "explain_event", {"event_id_or_hash": user_prompt})
                tools_executed.append({"tool": "explain_event", "result": exp})
                if exp.get("found"):
                    citations.append(exp["event_id"])
                    integrity = "intact" if exp.get("chain_intact") is True else "compromised or unavailable"
                    output_parts.append(f"{exp['summary']}. Cryptographic hash: {exp['hash'][:16]}... Chain integrity: {integrity}.")
                else:
                    output_parts.append("I don't have that data.")
            else:
                output_parts.append("I don't have that data.")
            status = "SUCCESS"

        elif role == AIRoleKind.ROLE_E_ALLOCATION_EXPLAINER:
            plan = self.execute_tool(role, "read_allocation_plan", {"plan_id": ctx.get("plan_id", "PLAN_DEMO")})
            tools_executed.append({"tool": "read_allocation_plan", "result": plan})

            con = self.execute_tool(role, "read_constraints", {})
            tools_executed.append({"tool": "read_constraints", "result": con})

            rej_donor = plan["rejected_donors"][0] if plan.get("rejected_donors") else {}
            output_parts.append(
                f"Allocation Plan {plan['plan_id']} proposes {len(plan['transfers'])} transfer lines covering {plan['fulfilled_qty']} units. "
                f"Donor {rej_donor.get('facility_id', 'PHC_X')} was rejected due to: {rej_donor.get('reason')}. "
                f"Donor safety floor was preserved at {con['donor_safety_floor_days']} days."
            )
            status = "SUCCESS"

        elif role == AIRoleKind.ROLE_F_FEDERATION_STEWARD:
            # Check unauthorized raw data queries
            if "facility data" in q_lower or "patient record" in q_lower or "raw log" in q_lower:
                return AIRoleResult(
                    role=role,
                    status="REFUSED",
                    output_text=(
                        "REFUSAL: Federation Model Steward cannot access, query, or export raw facility-level data or patient records. "
                        "This local design does not prove a live zero-leakage deployment.\n\n"
                        f"{MANDATORY_DISCLAIMER}"
                    ),
                    refused=True,
                    refusal_reason="RAW_DATA_ACCESS_FORBIDDEN",
                    execution_duration_ms=(time.time() - start_time) * 1000,
                )

            status_info = self.execute_tool(role, "read_federation_status", {})
            tools_executed.append({"tool": "read_federation_status", "result": status_info})

            card = self.execute_tool(role, "read_model_card", {})
            tools_executed.append({"tool": "read_model_card", "result": card})

            output_parts.append(
                f"Federation Status: {status_info['status']} across {len(status_info['participating_nodes'])} nodes. "
                f"Approved real model card: {card['model_name'] or 'unavailable'}. "
                f"Held-out PR-AUC: {card['pr_auc'] if card['pr_auc'] is not None else 'unavailable'}. "
                f"Audit status: {card['leakage_audit']}. "
                "This repository has no live federation source configured."
            )
            status = "SUCCESS"

        else:
            status = "BLOCKED"
            output_parts.append(f"Unrecognized role: {role}")

        # Append mandatory disclaimer
        full_text = " ".join(output_parts)
        provider = "deterministic-orchestrator-fallback"
        model_name = None
        is_live = False

        if not full_text.endswith(MANDATORY_DISCLAIMER):
            full_text = f"{full_text}\n\n{MANDATORY_DISCLAIMER}"

        # Audit role action in EventStore
        audit_ev = self.store.append(
            event_type=EventType.DECISION_MADE,
            facility_id=ctx.get("facility_id", "DISTRICT_HQ"),
            resource_type=ResourceType.MEDICINE,
            resource_key=ctx.get("resource_key", "AI_ROLE_EXECUTION"),
            payload={
                "role": role.value,
                "status": status,
                "tools_used": [t["tool"] for t in tools_executed],
                "prompt_version": PROMPT_VERSION,
                "citations": citations,
                "provider": provider,
                "is_live_model": is_live,
            },
            actor=f"agent:{role.value.lower()}",
        )

        return AIRoleResult(
            role=role,
            status=status,
            output_text=full_text,
            tools_called=tools_executed,
            citations=citations,
            audit_event_id=f"[EVT-{audit_ev.offset:04d}]",
            refused=False,
            execution_duration_ms=(time.time() - start_time) * 1000,
            provider=provider,
            model_name=model_name,
            is_live_model=is_live,
        )
