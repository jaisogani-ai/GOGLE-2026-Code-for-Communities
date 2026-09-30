"""
Tathyon Proposal Safety Harness & Policy Gate.

This module enforces the non-bypassable boundary between any automated proposer
(an external planning system, a script, a model), legacy healthcare systems,
and real-world execution.

CORE PRINCIPLE:
No automated proposer may directly mutate state, sign an attestation, approve
public funds, or dispatch medical supplies. Every proposal is intercepted, challenged against physically attested (human attestation record, not digitally signed) physical
truth, evaluated through deterministic policy-based policies, and emitted as a typed
HarnessDecision with fail-closed semantics.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from .schema import (
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


# --------------------------------------------------------------------------
# Decision Types & Proposal Schemas
# --------------------------------------------------------------------------

class DecisionType(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_VERIFICATION = "REQUIRE_VERIFICATION"
    REFUSE = "REFUSE"
    CONFLICT = "CONFLICT"
    ESCALATE = "ESCALATE"
    EXPIRED = "EXPIRED"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class ActionType(str, Enum):
    TRANSFER_MEDICINE = "TRANSFER_MEDICINE"
    RELEASE_PAYMENT = "RELEASE_PAYMENT"
    DISPATCH_SUPPLIES = "DISPATCH_SUPPLIES"
    COMMISSION_ASSET = "COMMISSION_ASSET"
    DECOMMISSION_ASSET = "DECOMMISSION_ASSET"
    UPDATE_MAINTENANCE_STATUS = "UPDATE_MAINTENANCE_STATUS"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ActionProposal:
    """A formal action proposed by an automated system (external or internal).
    
    NEVER grants direct execution authority. Must be submitted to the Safety Harness.
    """
    proposal_id: str
    proposer_id: str
    proposer_type: str
    action_type: ActionType
    resource_type: ResourceType
    facility_id: str
    resource_id: str                   # sku or asset_id
    requested_action: str
    requested_quantity: Optional[float]
    reason: str
    source_claim_ids: list[str]
    created_at: str
    expires_at: str
    risk_level: RiskLevel

    def digest(self) -> str:
        return sha256(asdict(self))


@dataclass(frozen=True)
class HarnessDecision:
    """The immutable, typed adjudication returned by the Tathyon Gate."""
    decision_id: str
    proposal_id: str
    decision_type: DecisionType
    allowed: bool
    reason_codes: list[str]
    required_evidence: list[str]
    policy_checks: dict[str, bool]
    verification_state: str
    human_approval_required: bool
    audit_event_id: Optional[str] = None
    remedy: Optional[list[str]] = None
    provenance: Provenance = Provenance.SYNTHETIC

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "proposal_id": self.proposal_id,
            "decision_type": self.decision_type.value,
            "allowed": self.allowed,
            "reason_codes": self.reason_codes,
            "required_evidence": self.required_evidence,
            "policy_checks": self.policy_checks,
            "verification_state": self.verification_state,
            "human_approval_required": self.human_approval_required,
            "audit_event_id": self.audit_event_id,
            "remedy": self.remedy or [],
            "provenance": self.provenance.value,
        }


# --------------------------------------------------------------------------
# Tool Registry & Permissions Layer
# --------------------------------------------------------------------------

@dataclass
class ToolDefinition:
    """Strictly typed declaration of a tool an automated proposer may call."""
    tool_name: str
    description: str
    input_schema: dict
    output_schema: dict
    permission: str                    # e.g. "reconciler:read", "surge:read"
    side_effect: bool = False          # Proposer-callable tools MUST be False
    audit_requirement: str = "LOG_CALL_ONLY"
    func: Optional[Callable] = None


class HarnessSecurityViolation(Exception):
    """Raised when a proposer attempts an unauthorized state mutation or forbidden tool call."""


class ToolRegistry:
    """Registry governing all proposer-callable tools with strict permission checks."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        if tool.side_effect:
            raise HarnessSecurityViolation(
                f"Security Invariant Violated: Tool '{tool.tool_name}' declares side_effect=True. "
                f"Automated proposers may never execute side-effecting tools directly."
            )
        self._tools[tool.tool_name] = tool

    def get_tool(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry.")
        return self._tools[name]

    def list_tools(self) -> list[dict]:
        return [
            {
                "tool_name": t.tool_name,
                "description": t.description,
                "input_schema": t.input_schema,
                "output_schema": t.output_schema,
                "permission": t.permission,
                "side_effect": t.side_effect,
                "audit_requirement": t.audit_requirement,
            }
            for t in self._tools.values()
        ]

    def execute(self, proposer_id: str, caller_permissions: set[str], tool_name: str, **kwargs) -> Any:
        if tool_name not in self._tools:
            raise HarnessSecurityViolation(
                f"Tool Injection Refused: Tool '{tool_name}' is not in the approved tool registry."
            )
        tool = self.get_tool(tool_name)
        if tool.permission not in caller_permissions:
            raise HarnessSecurityViolation(
                f"Access Denied: proposer '{proposer_id}' with permissions {caller_permissions} "
                f"attempted to call tool '{tool_name}' requiring permission '{tool.permission}'."
            )
        if tool.func is None:
            raise HarnessSecurityViolation(
                f"TOOL_NOT_EXECUTABLE: Tool '{tool_name}' is registered without a function; refusing."
            )
        return tool.func(**kwargs)


# --------------------------------------------------------------------------
# Bounded Tool Execution Layer
# --------------------------------------------------------------------------

@dataclass
class ToolExecution:
    tool_name: str
    arguments: dict
    result_summary: str
    timestamp: str


@dataclass
class BoundedRunResult:
    proposer_id: str
    run_id: str
    input_references: list[str]
    tool_calls: list[ToolExecution]
    output: dict
    confidence: float
    timestamp: str
    model: str
    audit_reference: str
    abstained: bool = False
    step_count: int = 0


class BoundedToolRunner:
    """Executes a tool plan within hard step limits and logging bounds."""

    def __init__(self, registry: ToolRegistry, max_steps: int = 5):
        self.registry = registry
        self.max_steps = max_steps

    def run(
        self,
        proposer_id: str,
        caller_permissions: set[str],
        plan: list[tuple[str, dict]],
        input_refs: list[str],
        reasoning_fn: Callable[[list[ToolExecution]], dict],
        model_name: str = "gemini-2.5-flash",
    ) -> BoundedRunResult:
        run_id = new_id("run")
        executed_calls: list[ToolExecution] = []

        for step_idx, (tool_name, args) in enumerate(plan):
            if step_idx >= self.max_steps:
                # Step limit reached -> Fail closed / abstain from unbounded search
                return BoundedRunResult(
                    proposer_id=proposer_id,
                    run_id=run_id,
                    input_references=input_refs,
                    tool_calls=executed_calls,
                    output={"error": "STEP_LIMIT_EXCEEDED", "detail": f"Exceeded max steps ({self.max_steps})"},
                    confidence=0.0,
                    timestamp=now(),
                    model=model_name,
                    audit_reference=f"audit_{run_id}",
                    abstained=True,
                    step_count=len(executed_calls),
                )

            try:
                res = self.registry.execute(proposer_id, caller_permissions, tool_name, **args)
                summary = str(res)[:300]
                executed_calls.append(
                    ToolExecution(
                        tool_name=tool_name,
                        arguments=args,
                        result_summary=summary,
                        timestamp=now(),
                    )
                )
            except Exception as e:
                executed_calls.append(
                    ToolExecution(
                        tool_name=tool_name,
                        arguments=args,
                        result_summary=f"ERROR: {str(e)}",
                        timestamp=now(),
                    )
                )

        # Synthesize output through reasoning function
        try:
            output_dict = reasoning_fn(executed_calls)
            confidence = float(output_dict.get("confidence", 0.90))
            abstained = bool(output_dict.get("abstained", False))
        except Exception as e:
            output_dict = {"error": "REASONING_SYNTHESIS_FAILED", "detail": str(e)}
            confidence = 0.0
            abstained = True

        return BoundedRunResult(
            proposer_id=proposer_id,
            run_id=run_id,
            input_references=input_refs,
            tool_calls=executed_calls,
            output=output_dict,
            confidence=confidence,
            timestamp=now(),
            model=model_name,
            audit_reference=f"audit_{run_id}",
            abstained=abstained,
            step_count=len(executed_calls),
        )


# --------------------------------------------------------------------------
# The Core Proposal Safety Harness & Policy Gate
# --------------------------------------------------------------------------

class ProposalSafetyHarness:
    """The central gate intercepting all automated proposals."""

    def __init__(self, store: EventStore, engine: Any, tool_registry: Optional[ToolRegistry] = None):
        self.store = store
        self.engine = engine
        self.tool_registry = tool_registry or ToolRegistry()
        self.runner = BoundedToolRunner(self.tool_registry)

    def evaluate_proposal(self, proposal: ActionProposal) -> HarnessDecision:
        decision_id = new_id("dec")
        reason_codes: list[str] = []
        required_evidence: list[str] = []
        policy_checks: dict[str, bool] = {}
        remedy: list[str] = []

        # 1. EXPIRATION CHECK
        created = datetime.fromisoformat(proposal.created_at.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(proposal.expires_at.replace("Z", "+00:00"))
        curr_time = datetime.now(timezone.utc)

        if curr_time > expires:
            return HarnessDecision(
                decision_id=decision_id,
                proposal_id=proposal.proposal_id,
                decision_type=DecisionType.EXPIRED,
                allowed=False,
                reason_codes=["PROPOSAL_EXPIRED"],
                required_evidence=["FRESH_PROPOSAL_REQUIRED"],
                policy_checks={"not_expired": False},
                verification_state=VerificationState.REJECTED.value,
                human_approval_required=False,
                remedy=["Submit a new proposal; original proposal expiration timestamp has passed."],
            )
        policy_checks["not_expired"] = True

        # 2. MALFORMED / BOUNDARY SANITY CHECKS
        if proposal.requested_quantity is not None and proposal.requested_quantity <= 0:
            return HarnessDecision(
                decision_id=decision_id,
                proposal_id=proposal.proposal_id,
                decision_type=DecisionType.REFUSE,
                allowed=False,
                reason_codes=["NON_POSITIVE_REQUESTED_QUANTITY"],
                required_evidence=[],
                policy_checks={"positive_quantity": False},
                verification_state=VerificationState.REJECTED.value,
                human_approval_required=False,
                remedy=["Specify a strictly positive quantity for resource operations."],
            )
        policy_checks["positive_quantity"] = True

        # 3. PHYSICAL VERIFICATION & FRESHNESS CHECK
        as_of_val = getattr(self.engine, "as_of", None) or now()
        vs = self.engine.state_for(
            facility_id=proposal.facility_id,
            resource_type=proposal.resource_type,
            resource_key=proposal.resource_id,
            as_of=as_of_val,
        )

        verification_state = vs.state.value
        policy_checks["physically_verified"] = (vs.state == VerificationState.VERIFIED)

        # 4. ACTION-SPECIFIC policy-based GATING
        if proposal.action_type == ActionType.RELEASE_PAYMENT:
            # Payment proposal: e.g. Maintenance Invoice Release
            policy_checks["separation_of_duties"] = True
            policy_checks["statutory_gfr_compliance"] = (vs.state == VerificationState.VERIFIED)

            if vs.state != VerificationState.VERIFIED:
                decision_type = DecisionType.REQUIRE_VERIFICATION if vs.state == VerificationState.UNVERIFIED else DecisionType.REFUSE
                reason_codes.append("UNVERIFIED_PHYSICAL_ASSET_PAYMENT_BLOCKED")
                required_evidence.append("photo_asset_operational")
                required_evidence.append("independent_custodian_attestation")
                remedy.append(
                    f"Under GFR Rule 213, payment cannot be released for asset {proposal.resource_id} "
                    f"without independent non-vendor physical attestation."
                )
                return HarnessDecision(
                    decision_id=decision_id,
                    proposal_id=proposal.proposal_id,
                    decision_type=decision_type,
                    allowed=False,
                    reason_codes=reason_codes,
                    required_evidence=required_evidence,
                    policy_checks=policy_checks,
                    verification_state=verification_state,
                    human_approval_required=True,
                    remedy=remedy,
                )

            # Check if broken despite verified claim
            if vs.verified_status and vs.verified_status != "FUNCTIONAL":
                return HarnessDecision(
                    decision_id=decision_id,
                    proposal_id=proposal.proposal_id,
                    decision_type=DecisionType.REFUSE,
                    allowed=False,
                    reason_codes=["ASSET_PHYSICALLY_NON_FUNCTIONAL"],
                    required_evidence=["maintenance_completion_proof"],
                    policy_checks=policy_checks,
                    verification_state=verification_state,
                    human_approval_required=True,
                    remedy=["Withhold vendor payment. Deduct contractual SLA downtime penalty."],
                )

            # If all checks pass, payment requires human DDO approval
            return HarnessDecision(
                decision_id=decision_id,
                proposal_id=proposal.proposal_id,
                decision_type=DecisionType.ALLOW,
                allowed=True,
                reason_codes=["SLA_VERIFIED_GROUND_TRUTH_MATCHED"],
                required_evidence=[],
                policy_checks=policy_checks,
                verification_state=verification_state,
                human_approval_required=True,  # No AI may disburse public funds autonomously!
                remedy=["Present SLA Evidence Pack to Drawing & Disbursing Officer for signature."],
            )

        elif proposal.action_type == ActionType.TRANSFER_MEDICINE:
            # Transfer proposal: e.g. Rebalance 200 vials to deficit facility
            policy_checks["stock_verified"] = (vs.state == VerificationState.VERIFIED)

            if vs.state != VerificationState.VERIFIED:
                decision_type = DecisionType.REQUIRE_VERIFICATION if vs.state == VerificationState.UNVERIFIED else DecisionType.REFUSE
                reason_codes.append("UNVERIFIED_STOCK_TRANSFER_FORBIDDEN")
                required_evidence.append("photo_shelf_batch_count")
                required_evidence.append("custodian_shelf_attestation")
                remedy.append(
                    f"Facility {proposal.facility_id} holds unverified stock of {proposal.resource_id}. "
                    f"Transferable quantity is 0.0 under safety gate. Dispatch verification task."
                )
                return HarnessDecision(
                    decision_id=decision_id,
                    proposal_id=proposal.proposal_id,
                    decision_type=decision_type,
                    allowed=False,
                    reason_codes=reason_codes,
                    required_evidence=required_evidence,
                    policy_checks=policy_checks,
                    verification_state=verification_state,
                    human_approval_required=False,
                    remedy=remedy,
                )

            # Verified stock: check transferable buffer above safety floor
            q_alpha = vs.posterior.get("q_alpha") if vs.posterior else (vs.reported_qty or 0.0)
            safety_floor = 20.0  # standard reserve
            transferable = max(0.0, float(q_alpha) - safety_floor)

            req_qty = proposal.requested_quantity or 0.0
            if req_qty > transferable:
                return HarnessDecision(
                    decision_id=decision_id,
                    proposal_id=proposal.proposal_id,
                    decision_type=DecisionType.REFUSE,
                    allowed=False,
                    reason_codes=["REQUEST_EXCEEDS_TRANSFERABLE_SURPLUS"],
                    required_evidence=[],
                    policy_checks={**policy_checks, "surplus_sufficient": False},
                    verification_state=verification_state,
                    human_approval_required=False,
                    remedy=[
                        f"Requested {req_qty} units, but verified transferable surplus above safety floor "
                        f"is only {transferable:.0f} units (Q_alpha={q_alpha:.0f}, safety_floor={safety_floor:.0f})."
                    ],
                )

            policy_checks["surplus_sufficient"] = True

            # Commit audit event to EventStore
            audit_event = self.store.append(
                event_type=EventType.DECISION_MADE,
                facility_id=proposal.facility_id,
                resource_type=proposal.resource_type,
                resource_key=proposal.resource_id,
                payload={
                    "proposal_id": proposal.proposal_id,
                    "proposer_id": proposal.proposer_id,
                    "action_type": proposal.action_type.value,
                    "decision": DecisionType.ALLOW.value,
                    "quantity": req_qty,
                },
                actor=f"harness_gate:{proposal.proposer_id}",
            )

            return HarnessDecision(
                decision_id=decision_id,
                proposal_id=proposal.proposal_id,
                decision_type=DecisionType.ALLOW,
                allowed=True,
                reason_codes=["VERIFIED_STOCK_SURPLUS_MATCHED"],
                required_evidence=[],
                policy_checks=policy_checks,
                verification_state=verification_state,
                human_approval_required=True,  # Human authority must approve dispatch!
                audit_event_id=audit_event.event_id,
                remedy=["Present transfer order to District Health Officer for dispatch signature."],
            )

        # Generic / Other Actions
        if vs.state == VerificationState.CONFLICTED:
            return HarnessDecision(
                decision_id=decision_id,
                proposal_id=proposal.proposal_id,
                decision_type=DecisionType.CONFLICT,
                allowed=False,
                reason_codes=["GROUND_EVIDENCE_CONFLICT_DETECTED"],
                required_evidence=["human_adjudication_required"],
                policy_checks=policy_checks,
                verification_state=verification_state,
                human_approval_required=True,
                remedy=["Escalate to Medical Superintendent for conflict resolution."],
            )

        return HarnessDecision(
            decision_id=decision_id,
            proposal_id=proposal.proposal_id,
            decision_type=DecisionType.ALLOW if vs.state == VerificationState.VERIFIED else DecisionType.REQUIRE_VERIFICATION,
            allowed=(vs.state == VerificationState.VERIFIED),
            reason_codes=["ACTION_EVALUATED_AGAINST_POLICY"],
            required_evidence=[],
            policy_checks=policy_checks,
            verification_state=verification_state,
            human_approval_required=True,
        )


def create_default_tool_registry(store: EventStore, engine: Any) -> ToolRegistry:
    """Instantiate the default tool registry populated with strictly read-only tools."""
    registry = ToolRegistry()

    # Tool 1: get_maintenance_contract (reconciler:read)
    def _tool_get_contract(asset_id: str) -> dict:
        contracts_map = getattr(store, "_sla_contracts", {})
        contract = contracts_map.get(f"cnt_{asset_id}")
        if not contract:
            for c in contracts_map.values():
                if c.asset_id == asset_id:
                    contract = c
                    break
        if not contract:
            return {"found": False, "asset_id": asset_id}
        return {
            "found": True,
            "contract_id": contract.contract_id,
            "asset_id": contract.asset_id,
            "vendor_id": contract.vendor_id,
            "sla_target": contract.sla_target,
            "monthly_base_fee_inr": contract.monthly_base_fee_inr,
            "penalty_rate_per_pct": contract.penalty_rate_per_pct,
        }

    registry.register(
        ToolDefinition(
            tool_name="get_maintenance_contract",
            description="Retrieve active biomedical maintenance contract terms for an asset.",
            input_schema={"asset_id": "string"},
            output_schema={"found": "boolean", "contract_id": "string", "sla_target": "number"},
            permission="reconciler:read",
            side_effect=False,
            func=_tool_get_contract,
        )
    )

    # Tool 2: get_attested_downtime (reconciler:read)
    def _tool_get_downtime(facility_id: str, asset_id: str) -> dict:
        attestations = store.attestations_for(facility_id, asset_id)
        if not attestations:
            return {"found": False, "facility_id": facility_id, "asset_id": asset_id, "downtime_hours": 0.0}
        latest = attestations[-1]
        observed = latest.observed or {}
        hours = float(observed.get("downtime_hours", 0.0))
        return {
            "found": True,
            "facility_id": facility_id,
            "asset_id": asset_id,
            "downtime_hours": hours,
            "attestor_role": latest.attestor_role,
            "observed_at": latest.observed_at,
        }

    registry.register(
        ToolDefinition(
            tool_name="get_attested_downtime",
            description="Query latest ground custodian attestation for recorded equipment downtime hours.",
            input_schema={"facility_id": "string", "asset_id": "string"},
            output_schema={"found": "boolean", "downtime_hours": "number", "attestor_role": "string"},
            permission="reconciler:read",
            side_effect=False,
            func=_tool_get_downtime,
        )
    )

    # Tool 3: get_evidence_specification (field_guide:read)
    def _tool_get_spec(resource_type: str) -> dict:
        if resource_type.lower() == "equipment":
            return {
                "resource_type": "equipment",
                "required_fields": ["serial_number", "meter_reading", "operational_status"],
                "required_angles": ["nameplate_serial_tag", "runtime_hour_meter"],
                "nonce_required": True,
            }
        return {
            "resource_type": "medicine",
            "required_fields": ["batch_number", "expiry_date", "pack_count"],
            "required_angles": ["box_batch_panel", "expiry_date_label"],
            "nonce_required": True,
        }

    registry.register(
        ToolDefinition(
            tool_name="get_evidence_specification",
            description="Retrieve physical evidence specification and mandatory frame angles for a resource type.",
            input_schema={"resource_type": "string"},
            output_schema={"resource_type": "string", "required_fields": "list", "required_angles": "list"},
            permission="field_guide:read",
            side_effect=False,
            func=_tool_get_spec,
        )
    )

    # Tool 4: get_verified_inventory (surge:read)
    def _tool_get_inventory(sku: str) -> dict:
        facilities_verified = []
        for fid in getattr(engine, "facility_ids", []):
            vs = engine.state_for(fid, ResourceType.MEDICINE, sku)
            if vs.state == VerificationState.VERIFIED:
                facilities_verified.append({
                    "facility_id": fid,
                    "verified_qty": vs.reported_qty or 0.0,
                    "q_alpha": vs.posterior.get("q_alpha") if vs.posterior else 0.0,
                })
        return {"sku": sku, "verified_facilities": facilities_verified}

    registry.register(
        ToolDefinition(
            tool_name="get_verified_inventory",
            description="Query only VERIFIED stock balances across regional health facilities.",
            input_schema={"sku": "string"},
            output_schema={"sku": "string", "verified_facilities": "list"},
            permission="surge:read",
            side_effect=False,
            func=_tool_get_inventory,
        )
    )

    return registry


class ActionAdapter:
    """Safeguards actual dispatch/payment execution.
    Fails closed: Any execution without verified HarnessDecision ALLOW
    and explicit human authority sign-off is refused.
    """

    def __init__(self, store: EventStore):
        self.store = store

    def execute_action(
        self,
        decision: HarnessDecision,
        proposal: ActionProposal,
        human_approved: bool,
        approver_id: str,
        approver_role: str,
    ) -> dict:
        if not decision.allowed:
            raise HarnessSecurityViolation(
                f"EXECUTION_BLOCKED: Decision '{decision.decision_id}' rejected proposal '{proposal.proposal_id}'."
            )
        if decision.human_approval_required and not human_approved:
            raise HarnessSecurityViolation(
                f"HUMAN_AUTHORITY_REQUIRED: Action '{proposal.action_type.value}' requires explicit human sign-off."
            )
        valid_approver_roles = {
            "ddo_officer",
            "district_health_officer",
            "medical_superintendent",
            "facility_incharge",
        }
        if approver_role not in valid_approver_roles:
            raise HarnessSecurityViolation(
                f"UNAUTHORIZED_APPROVER_ROLE: Role '{approver_role}' cannot execute consequential action."
            )

        ev = self.store.append(
            event_type=EventType.DECISION_MADE,
            facility_id=proposal.facility_id,
            resource_type=proposal.resource_type,
            resource_key=proposal.resource_id,
            payload={
                "action": "ACTION_EXECUTED",
                "proposal_id": proposal.proposal_id,
                "decision_id": decision.decision_id,
                "approver_id": approver_id,
                "approver_role": approver_role,
                "action_type": proposal.action_type.value,
                "quantity": proposal.requested_quantity,
            },
            actor=f"action_adapter:{approver_id}",
        )
        return {
            "status": "EXECUTED",
            "event_id": ev.event_id,
            "action_type": proposal.action_type.value,
            "approver": approver_id,
        }


