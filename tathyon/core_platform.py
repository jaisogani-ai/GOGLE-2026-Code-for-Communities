"""TATHYON v2 Core Platform — Resource-Agnostic Trust, Verification & Allocation Engine.

Build with AI: Code for Communities 2.0 (Track 3) — Smart Health & Supply Chain Resilience.

DOCTRINES:
1. THE LOOP IS IDENTICAL:
       Trust Scoring -> Verification Targeting -> Gated Allocation -> Sovereign Approval -> Outcome Reconciliation
   Only the features and allocation physics differ per resource:
     - MEDICINES: Trucks move stock; donor facilities retain 14d safety runway.
     - BEDS: Beds do not move; patients are diverted; destinations retain safe capacity.
     - PERSONNEL: Staff are redeployed; donor facilities retain statutory minimum clinical staff.
2. DOCTRINES UNBROKEN:
     - HTTP 200 typed refusals.
     - AI never counts (humans count physical shelves, beds, staff; Gemini transcribes).
     - Priority scores never persisted in EventStore or export.
     - Attester != Custodian separation of duties enforced.
     - Break-glass with mandatory 72h audit obligation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import pandas as pd

from .schema import (
    ResourceType, FacilityType, VerificationState, Provenance,
    VerificationVisit, VisitStatus, now, new_id, sha256
)
from .optimize import knapsack, Decision as MedicineDecision, Facility as MedFacility, Need as MedNeed, SourceStock as MedSourceStock, optimise as solve_medicine_network
from .beds import solve_patient_diversion, BedFacilityNode, PatientDiversionPlan
from .personnel import solve_staff_redeployment, PersonnelFacilityState, StaffRedeploymentPlan


# --------------------------------------------------------------------------
# Resource-Agnostic Candidate Specification
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceCandidate:
    """A single facility x resource candidate for inspection prioritization."""
    facility_id: str
    facility_name: str
    resource_type: ResourceType
    resource_key: str
    p_wrong: float
    consequence_days: float
    essentiality_weight: float
    visit_cost_slots: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def expected_value(self) -> float:
        """E[unobserved consequence averted by inspection] = P(wrong) * consequence * essentiality."""
        return float(self.p_wrong * self.consequence_days * self.essentiality_weight)


# --------------------------------------------------------------------------
# Unified Trust Queue
# --------------------------------------------------------------------------

class UnifiedTrustQueue(list):
    """List of scheduled VerificationVisit items under slot budget, with in-memory ranking."""

    def __init__(self, visits: List[VerificationVisit], ranking: pd.DataFrame, budget: int, budget_used: int, resource_type: Optional[ResourceType] = None):
        super().__init__(visits)
        self._ranking = ranking
        self.budget = budget
        self.budget_used = budget_used
        self.resource_type = resource_type

    @property
    def ranking(self) -> pd.DataFrame:
        return self._ranking.copy()


def target_unified_verifications(
    budget_slots: int,
    candidates: List[ResourceCandidate],
    resource_type: Optional[ResourceType] = None,
    as_of: Optional[pd.Timestamp] = None,
    assignee: str = "district-verification-team",
) -> UnifiedTrustQueue:
    """Resource-agnostic verification knapsack targeting under finite inspection budget.
    
    Orders candidates by E[consequence prevented] per visit slot.
    """
    if budget_slots < 0:
        raise ValueError("budget_slots must be non-negative")
        
    filtered = [c for c in candidates if resource_type is None or c.resource_type == resource_type]
    if not filtered:
        empty_df = pd.DataFrame(columns=["facility_id", "resource_type", "resource_key", "p_wrong", "expected_value", "visit_cost_slots", "selected"])
        return UnifiedTrustQueue([], empty_df, budget_slots, 0, resource_type)

    records = []
    for c in filtered:
        records.append({
            "facility_id": c.facility_id,
            "facility_name": c.facility_name,
            "resource_type": c.resource_type.value,
            "resource_key": c.resource_key,
            "p_wrong": round(c.p_wrong, 4),
            "consequence_days": round(c.consequence_days, 2),
            "essentiality_weight": round(c.essentiality_weight, 2),
            "expected_value": round(c.expected_value, 4),
            "visit_cost_slots": c.visit_cost_slots,
        })
        
    df = pd.DataFrame(records)
    # Deterministic sort: highest expected_value, then tie-break on IDs
    df = df.sort_values(
        ["expected_value", "facility_id", "resource_key"],
        ascending=[False, True, True]
    ).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    
    # Solve knapsack
    chosen_indices = knapsack(df["expected_value"], df["visit_cost_slots"], int(budget_slots))
    df["selected"] = False
    df.loc[chosen_indices, "selected"] = True

    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC").normalize()
    due_date = (as_of + timedelta(days=7)).isoformat()
    stamp = as_of.strftime("%Y%m%d")
    
    visits = []
    for r in df.loc[chosen_indices].itertuples():
        visits.append(VerificationVisit(
            visit_id=f"vv_{stamp}_{r.facility_id}_{r.resource_key}",
            facility_id=r.facility_id,
            sku_id=r.resource_key,
            assignee=assignee,
            due_date=due_date,
            status=VisitStatus.SCHEDULED,
        ))
        
    budget_used = int(df.loc[chosen_indices, "visit_cost_slots"].sum()) if chosen_indices else 0
    return UnifiedTrustQueue(visits, df, budget_slots, budget_used, resource_type)


# --------------------------------------------------------------------------
# Unified Allocation Dispatcher
# --------------------------------------------------------------------------

@dataclass
class UnifiedAllocationResponse:
    """Unified response envelope for any resource-type allocation solve."""
    plan_id: str
    resource_type: ResourceType
    decision: str
    details: Union[MedicineDecision, PatientDiversionPlan, StaffRedeploymentPlan]
    rejected_entities: List[Dict[str, Any]]
    remedy: str
    provenance: str = "SYNTHETIC_CP_SAT_SOLVER"


class UnifiedAllocationDispatcher:
    """Routes allocation solve requests to the proper resource-specific CP-SAT physics."""
    
    @staticmethod
    def dispatch_allocation(
        resource_type: ResourceType,
        **kwargs: Any
    ) -> UnifiedAllocationResponse:
        """Dispatches to Medicine (trucks), Beds (patient diversion), or Personnel (staff redeployment)."""
        if resource_type in (ResourceType.MEDICINE, ResourceType.VACCINE):
            # Medicine / Vaccine stock redistribution
            decision = solve_medicine_network(
                needs=kwargs["needs"],
                sources=kwargs["sources"],
                facilities=kwargs["facilities"],
                truck_capacity=kwargs.get("truck_capacity", 3000.0),
                min_safety_days=kwargs.get("min_safety_days", 14.0),
                time_limit_s=kwargs.get("time_limit_s", 5.0),
            )
            return UnifiedAllocationResponse(
                plan_id=new_id("plan_med"),
                resource_type=resource_type,
                decision=decision.status,
                details=decision,
                rejected_entities=decision.rejected_donors,
                remedy=decision.remedy,
            )

        elif resource_type == ResourceType.BED:
            # Bed capacity / patient diversion
            plan = solve_patient_diversion(
                surging_facility_id=kwargs["surging_facility_id"],
                bed_type=kwargs["bed_type"],
                excess_patients_seeking_admission=kwargs["excess_patients_seeking_admission"],
                candidate_facilities=kwargs["candidate_facilities"],
                max_travel_km=kwargs.get("max_travel_km", 65.0),
                safe_occupancy_ceiling=kwargs.get("safe_occupancy_ceiling", 0.88),
                p_wrong_verification_threshold=kwargs.get("p_wrong_verification_threshold", 0.40),
            )
            return UnifiedAllocationResponse(
                plan_id=plan.plan_id,
                resource_type=ResourceType.BED,
                decision="DIVERT_PATIENTS_APPROVED" if plan.diverted_patients_count > 0 else "NO_VERIFIED_BEDS_AVAILABLE",
                details=plan,
                rejected_entities=plan.rejected_destinations,
                remedy="Patient diversion routes calculated preserving destination safety ceilings." if plan.diverted_patients_count > 0 else "All nearby candidate beds failed verification gate or safe occupancy ceiling.",
            )

        elif resource_type == ResourceType.PERSONNEL:
            # Personnel redeployment
            plan = solve_staff_redeployment(
                role=kwargs["role"],
                facilities=kwargs["facilities"],
                max_travel_km=kwargs.get("max_travel_km", 75.0),
                p_wrong_donor_threshold=kwargs.get("p_wrong_donor_threshold", 0.35),
            )
            return UnifiedAllocationResponse(
                plan_id=plan.plan_id,
                resource_type=ResourceType.PERSONNEL,
                decision="STAFF_REDEPLOYMENT_APPROVED" if plan.total_staff_redeployed > 0 else "NO_ELIGIBLE_STAFF_DONORS",
                details=plan,
                rejected_entities=plan.rejected_donors,
                remedy="Inter-facility staff redeployment calculated preserving donor statutory floors." if plan.total_staff_redeployed > 0 else "All potential donor facilities are at minimum statutory staffing floor or unverified.",
            )

        else:
            raise ValueError(f"UNSUPPORTED_RESOURCE_TYPE: {resource_type}")


# --------------------------------------------------------------------------
# Unified Sovereign Approval & Break-Glass Workflow
# --------------------------------------------------------------------------

@dataclass
class SovereignApprovalResult:
    plan_id: str
    resource_type: ResourceType
    decision: str
    officer_id: str
    approved_lines_count: int
    rejected_lines: List[str]
    break_glass: bool
    obligation_id: Optional[str]
    audit_hash: str
    staged_payload: Dict[str, Any]


def execute_sovereign_approval(
    plan_id: str,
    resource_type: ResourceType,
    officer_id: str,
    action: str = "APPROVE", # APPROVE, REJECT, BREAK_GLASS
    rejected_lines: Optional[List[str]] = None,
    break_glass_reason: Optional[str] = None,
    all_lines: Optional[List[str]] = None,
) -> SovereignApprovalResult:
    """Records human review decisions across resource types with a break-glass policy path.
    
    Enforces TATHYON's configured human-approval policy and audit logging.
    """
    rejected = rejected_lines or []
    all_l = all_lines or ["line_01"]
    approved_count = len([l for l in all_l if l not in rejected]) if action != "REJECT" else 0
    
    obligation_id = None
    if action == "BREAK_GLASS":
        if not break_glass_reason or len(break_glass_reason.strip()) < 10:
            raise ValueError("BREAK_GLASS_REASON_MANDATORY: Minimum 10 characters required justifying clinical emergency")
        obligation_id = new_id("oblg_72h_audit")
        
    audit_payload = {
        "plan_id": plan_id,
        "resource_type": resource_type.value,
        "officer_id": officer_id,
        "action": action,
        "approved_count": approved_count,
        "rejected_lines": rejected,
        "obligation_id": obligation_id,
        "timestamp": now(),
    }
    audit_hash = sha256(audit_payload)
    
    staged_payload = {
        "voucher_id": f"SOV-VCHR-{resource_type.value[:3].upper()}-{plan_id[:8]}",
        "action": action,
        "resource_type": resource_type.value,
        "audit_hash": audit_hash,
        "approved_by": officer_id,
        "authority_basis": "TATHYON policy; configure against applicable department/state procedures",
    }
    
    return SovereignApprovalResult(
        plan_id=plan_id,
        resource_type=resource_type,
        decision="APPROVED" if action in ("APPROVE", "BREAK_GLASS") and approved_count > 0 else "REJECTED",
        officer_id=officer_id,
        approved_lines_count=approved_count,
        rejected_lines=rejected,
        break_glass=(action == "BREAK_GLASS"),
        obligation_id=obligation_id,
        audit_hash=audit_hash,
        staged_payload=staged_payload,
    )
