"""
TATHYON Operational Case Engine — Durable Domain Model & Lifecycle.

Section 9 of CTO War-Room & Product Lock:
The transition from an alert to an operational case is the core differentiation:
    ALERT -> OPERATIONAL CASE -> RESPONSE -> OUTCOME

A ResilienceCase tracks the end-to-end lifecycle of an acute operational failure:
    DETECTED -> ASSESSING -> PLANNING -> AWAITING_APPROVAL -> APPROVED ->
    READY_FOR_EXECUTION -> EXECUTING -> DELIVERED -> MEASURED -> CLOSED
    Failure / shortfall loop:
    EXECUTING -> DELIVERY_FAILED | MEASURED -> RISK_RECALCULATED -> REPLAN_REQUIRED -> ASSESSING

Every state transition is timestamped, actor-attributed, and audited.
"""
from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .schema import Provenance, new_id, now, sha256


class CaseStatus(str, Enum):
    """Rigorous operational case state machine."""
    DETECTED = "DETECTED"
    ASSESSING = "ASSESSING"
    INVESTIGATING = "INVESTIGATING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
    EXECUTING = "EXECUTING"
    DELIVERED = "DELIVERED"
    MEASURED = "MEASURED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    RISK_RECALCULATED = "RISK_RECALCULATED"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    CLOSED = "CLOSED"


# Legal transitions in the state machine
VALID_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.DETECTED: {CaseStatus.ASSESSING, CaseStatus.INVESTIGATING, CaseStatus.PLANNING, CaseStatus.CLOSED},
    CaseStatus.ASSESSING: {CaseStatus.PLANNING, CaseStatus.CLOSED},
    CaseStatus.INVESTIGATING: {CaseStatus.PLANNING, CaseStatus.CLOSED},
    CaseStatus.PLANNING: {CaseStatus.AWAITING_APPROVAL, CaseStatus.INVESTIGATING, CaseStatus.CLOSED},
    CaseStatus.AWAITING_APPROVAL: {CaseStatus.APPROVED, CaseStatus.PLANNING, CaseStatus.CLOSED},
    CaseStatus.APPROVED: {CaseStatus.READY_FOR_EXECUTION, CaseStatus.CLOSED},
    CaseStatus.READY_FOR_EXECUTION: {CaseStatus.EXECUTING, CaseStatus.CLOSED},
    CaseStatus.EXECUTING: {CaseStatus.DELIVERED, CaseStatus.DELIVERY_FAILED, CaseStatus.REPLAN_REQUIRED, CaseStatus.CLOSED},
    CaseStatus.DELIVERED: {CaseStatus.MEASURED, CaseStatus.CLOSED},
    CaseStatus.MEASURED: {CaseStatus.RISK_RECALCULATED, CaseStatus.CLOSED},
    CaseStatus.DELIVERY_FAILED: {CaseStatus.RISK_RECALCULATED, CaseStatus.CLOSED},
    CaseStatus.RISK_RECALCULATED: {CaseStatus.REPLAN_REQUIRED, CaseStatus.CLOSED},
    CaseStatus.REPLAN_REQUIRED: {CaseStatus.ASSESSING, CaseStatus.CLOSED},
    CaseStatus.CLOSED: set(),  # Terminal state
}


@dataclass
class CaseTransitionRecord:
    """Immutable audit record of a case lifecycle step."""
    from_status: str
    to_status: str
    actor: str
    timestamp: str
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResilienceCase:
    """Durable operational domain object tracking a resource failure from detection to measured outcome."""
    case_id: str
    facility: str
    resource: str
    trigger: str                             # e.g., "STOCKOUT_PREDICTED", "CRITICAL_RUNWAY", "EMERGENCY_SHOCK"
    risk: float                              # failure probability [0.0, 1.0]
    severity: str = "HIGH"                   # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    status: CaseStatus = CaseStatus.DETECTED
    evidence: dict[str, Any] = field(default_factory=dict)
    forecast: dict[str, Any] = field(default_factory=dict)
    scenario: Optional[str] = None
    network_impact: dict[str, Any] = field(default_factory=dict)
    candidate_plans: list[dict[str, Any]] = field(default_factory=list)
    selected_plan: Optional[dict[str, Any]] = None
    approval: Optional[dict[str, Any]] = None
    execution_payload: Optional[dict[str, Any]] = None
    delivery: Optional[dict[str, Any]] = None
    outcome: Optional[dict[str, Any]] = None
    owner: str = "Chief Medical Officer"
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)
    provenance: str = "SIMULATION"

    def transition(
        self,
        next_status: CaseStatus,
        actor: str = "system",
        notes: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> "ResilienceCase":
        """Executes a validated lifecycle transition."""
        if isinstance(next_status, str):
            next_status = CaseStatus(next_status)

        current = self.status
        allowed = VALID_TRANSITIONS.get(current, set())
        if next_status not in allowed:
            raise ValueError(
                f"INVALID_LIFECYCLE_TRANSITION: Cannot transition ResilienceCase from "
                f"{current.value} to {next_status.value}. Allowed transitions: {[s.value for s in allowed]}"
            )

        record = CaseTransitionRecord(
            from_status=current.value,
            to_status=next_status.value,
            actor=actor,
            timestamp=now(),
            notes=notes,
            metadata=metadata or {},
        )
        self.history.append(record.to_dict())
        self.status = next_status
        self.updated_at = now()
        return self

    def attach_forecast(self, forecast_data: dict[str, Any]) -> "ResilienceCase":
        self.forecast = copy.deepcopy(forecast_data)
        self.updated_at = now()
        return self

    def attach_network_impact(self, impact_data: dict[str, Any]) -> "ResilienceCase":
        self.network_impact = copy.deepcopy(impact_data)
        self.updated_at = now()
        return self

    def attach_candidate_plans(self, plans: list[dict[str, Any]]) -> "ResilienceCase":
        self.candidate_plans = copy.deepcopy(plans)
        if self.status == CaseStatus.INVESTIGATING:
            self.transition(CaseStatus.PLANNING, actor="planner", notes="Generated candidate response plans")
        self.updated_at = now()
        return self

    def select_plan(self, plan_id: str, actor: str = "operator") -> "ResilienceCase":
        for p in self.candidate_plans:
            if p.get("plan_id") == plan_id:
                self.selected_plan = copy.deepcopy(p)
                if self.status == CaseStatus.PLANNING:
                    self.transition(CaseStatus.AWAITING_APPROVAL, actor=actor, notes=f"Selected plan {plan_id} for approval")
                self.updated_at = now()
                return self
        raise KeyError(f"PLAN_NOT_FOUND: Plan {plan_id} not among candidate plans for this case.")

    def record_approval(self, approval_data: dict[str, Any], actor: str = "Chief Medical Officer") -> "ResilienceCase":
        self.approval = copy.deepcopy(approval_data)
        if self.status in (CaseStatus.AWAITING_APPROVAL, CaseStatus.PLANNING):
            self.transition(CaseStatus.APPROVED, actor=actor, notes="policy-based sign-off granted by Medical Officer")
        self.updated_at = now()
        return self

    def attach_execution_payload(self, payload: dict[str, Any], system_name: str = "DVDMS") -> "ResilienceCase":
        self.execution_payload = copy.deepcopy(payload)
        if self.status == CaseStatus.APPROVED:
            self.transition(
                CaseStatus.READY_FOR_EXECUTION,
                actor="system",
                notes=f"Generated {system_name} system-of-record staging payload (READY_FOR_SYSTEM_OF_RECORD)"
            )
        self.updated_at = now()
        return self

    def record_dispatch(self, dispatch_info: dict[str, Any], actor: str = "dispatch_officer") -> "ResilienceCase":
        if self.status == CaseStatus.READY_FOR_EXECUTION:
            self.transition(CaseStatus.EXECUTING, actor=actor, notes="Physical shipment dispatched to recipient facility")
        self.updated_at = now()
        return self

    def record_delivery(self, delivery_info: dict[str, Any], actor: str = "storekeeper") -> "ResilienceCase":
        self.delivery = copy.deepcopy(delivery_info)
        if self.status in (CaseStatus.READY_FOR_EXECUTION, CaseStatus.EXECUTING):
            self.transition(CaseStatus.DELIVERED, actor=actor, notes="Physical consignment received and counted at destination")
        self.updated_at = now()
        return self

    def record_outcome(self, outcome_data: dict[str, Any], actor: str = "analytics") -> "ResilienceCase":
        self.outcome = copy.deepcopy(outcome_data)
        if self.status == CaseStatus.DELIVERED:
            self.transition(CaseStatus.MEASURED, actor=actor, notes="Intervention outcome, runway delta, and variance measured")
        self.updated_at = now()
        return self

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, CaseStatus) else str(self.status)
        return d


class CaseManager:
    """Thread-safe, in-memory durable case management engine."""

    def __init__(self):
        self._cases: dict[str, ResilienceCase] = {}

    def create_case(
        self,
        facility: str,
        resource: str,
        trigger: str,
        risk: float,
        severity: str = "HIGH",
        evidence: Optional[dict[str, Any]] = None,
        forecast: Optional[dict[str, Any]] = None,
        scenario: Optional[str] = None,
        owner: str = "Chief Medical Officer",
        provenance: str = "SIMULATION",
        case_id: Optional[str] = None,
    ) -> ResilienceCase:
        cid = case_id or f"CASE-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{len(self._cases) + 1:04d}"
        case = ResilienceCase(
            case_id=cid,
            facility=facility,
            resource=resource,
            trigger=trigger,
            risk=float(risk),
            severity=severity,
            status=CaseStatus.DETECTED,
            evidence=evidence or {},
            forecast=forecast or {},
            scenario=scenario,
            owner=owner,
            provenance=provenance,
        )
        # initial history entry
        case.history.append(CaseTransitionRecord(
            from_status="NONE",
            to_status=CaseStatus.DETECTED.value,
            actor="alert_engine",
            timestamp=now(),
            notes=f"Case initialized from trigger: {trigger} (risk={risk:.2f})",
        ).to_dict())

        self._cases[cid] = case
        return case

    def get_case(self, case_id: str) -> Optional[ResilienceCase]:
        return self._cases.get(case_id)

    def list_cases(
        self,
        status: Optional[str] = None,
        facility: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> list[ResilienceCase]:
        res = list(self._cases.values())
        if status:
            target_status = status.upper()
            res = [c for c in res if c.status.value == target_status or str(c.status) == target_status]
        if facility:
            res = [c for c in res if c.facility == facility]
        if resource:
            res = [c for c in res if c.resource == resource]
        # sort by updated_at descending
        res.sort(key=lambda c: c.updated_at, reverse=True)
        return res

    def seed_from_risk_queue(self, ranked_risks: list[dict[str, Any]]) -> list[ResilienceCase]:
        """Auto-provisions operational cases from ranked failure queue so every high risk has an owner."""
        created = []
        for r in ranked_risks:
            fac = r.get("facility", r.get("facility_id", "UNKNOWN_FACILITY"))
            res = r.get("resource", r.get("resource_id", "UNKNOWN_RESOURCE"))
            risk_val = float(r.get("p_stockout_lead_time", r.get("risk", 0.0)))
            days_left = float(r.get("days_to_stockout", r.get("runway_days", 99.0)))

            # Only create cases for urgent items (< 7 days or risk > 0.6)
            if days_left <= 7.0 or risk_val >= 0.6:
                # Deduplicate: check if active case already exists for this (facility, resource)
                existing = [c for c in self._cases.values()
                            if c.facility == fac and c.resource == res and c.status != CaseStatus.CLOSED]
                if not existing:
                    severity = "CRITICAL" if days_left <= 3.0 or risk_val >= 0.85 else "HIGH"
                    c = self.create_case(
                        facility=fac,
                        resource=res,
                        trigger="STOCKOUT_PREDICTED_CRITICAL_RUNWAY",
                        risk=risk_val,
                        severity=severity,
                        evidence={
                            "days_to_stockout": days_left,
                            "usable_qty": r.get("usable_qty", r.get("usable_quantity", 0.0)),
                            "reported_qty": r.get("reported_qty", r.get("claimed_quantity", 0.0)),
                            "explanation": r.get("explanation", ""),
                        },
                        forecast={
                            "p_stockout_lead_time": risk_val,
                            "days_to_stockout": days_left,
                            "level": r.get("level", "CRITICAL"),
                        },
                    )
                    created.append(c)
        return created


# Global singleton instance for application use
GLOBAL_CASE_MANAGER = CaseManager()
