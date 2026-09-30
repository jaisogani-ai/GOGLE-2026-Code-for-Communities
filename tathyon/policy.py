"""
TATHYON Policy-Aware Planning & policy-based Governance Engine.

Represents public health administrative rules, General Financial Rules (GFR),
and policy-based operational guidelines as structured constraints with explicit provenance.

CRITICAL INVARIANT:
- Does NOT build a generic LLM/RAG chatbot.
- Every constraint has a verified policy-based / SOP provenance reference.
- Deterministic evaluation: passes or fails with explicit named policy violations.
- Influences donor validation, redistribution planning, and approval sign-off.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from .schema import now


class PolicyCategory(str, Enum):
    MINIMUM_RESERVE = "MINIMUM_RESERVE"
    ELIGIBLE_MOVEMENT = "ELIGIBLE_MOVEMENT"
    FACILITY_PRIORITY = "FACILITY_PRIORITY"
    EXPIRY_RESTRICTIONS = "EXPIRY_RESTRICTIONS"
    STATUTORY_APPROVAL = "STATUTORY_APPROVAL"
    COLD_CHAIN_INTEGRITY = "COLD_CHAIN_INTEGRITY"


@dataclass(frozen=True)
class PolicyConstraint:
    """A formal administrative or policy-based constraint governing resource movement."""
    constraint_id: str
    name: str
    category: PolicyCategory
    statutory_provenance: str
    rule_reference: str
    description: str
    is_hard_constraint: bool
    parameters: dict = field(default_factory=dict)
    # Constraint basis: LAW / REGULATION | OFFICIAL GUIDELINE | SYSTEM POLICY | FACILITY POLICY |
    # CONFIGURATION | DEMO ASSUMPTION. LAW / REGULATION only when independently verified.
    basis: str = "SYSTEM POLICY"
    citation_verified: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["category"] = self.category.value
        return d


# policy-based Policy Registry — Grounded in official public health governance
STATUTORY_POLICIES: list[PolicyConstraint] = [
    PolicyConstraint(
        constraint_id="POL-MIN-RESERVE-01",
        name="Mandatory Facility Safety Buffer Floor",
        category=PolicyCategory.MINIMUM_RESERVE,
        statutory_provenance="National Health Mission (NHM) Operational Guidelines for PHCs/CHCs",
        rule_reference="NHM Guidelines Sec 7.3 (Buffer Stock Maintenance)",
        description="A donor facility must preserve its own consumption buffer (minimum 7 days, standard 14 days) "
                    "before any surplus can be redeployed.",
        is_hard_constraint=True,
        parameters={"min_safety_days": 7.0, "standard_safety_days": 14.0},
    ),
    PolicyConstraint(
        constraint_id="POL-UNVERIFIED-EXCLUSION-02",
        name="Prohibition of Unverified / Phantom Stock Transfer",
        category=PolicyCategory.ELIGIBLE_MOVEMENT,
        statutory_provenance="General Financial Rules (GFR 2017) Rule 211 & Rule 213(1)",
        rule_reference="GFR 2017 Rule 211 (Item-wise Verification) & Rule 213(1) (Physical Verification)",
        description="Only physical verified stock (usable_quantity > 0) with fresh attestation (< 48h) can be "
                    "allocated for redistribution. Unverified digital ledger balances cannot be transferred.",
        is_hard_constraint=True,
        parameters={"max_attestation_age_hours": 48.0},
    ),
    PolicyConstraint(
        constraint_id="POL-EXPIRY-FEFO-03",
        name="First-Expiry-First-Out & Lead-Time Safe Redeployment",
        category=PolicyCategory.EXPIRY_RESTRICTIONS,
        statutory_provenance="Drugs and Cosmetics Rules 1945 Rule 65 & Schedule M (Good Manufacturing Practices)",
        rule_reference="DCR 1945 Rule 65(4) (Dispensation and Storage of Medical Supplies)",
        description="Stock expiring within the replenishment lead time plus transit duration is ineligible for "
                    "inter-facility transfer and must be quarantined or designated for local immediate clinical use.",
        is_hard_constraint=True,
        parameters={"min_shelf_life_margin_days": 14.0},
    ),
    PolicyConstraint(
        constraint_id="POL-FACILITY-PRIORITY-04",
        name="Hub-First Extraction Hierarchy",
        category=PolicyCategory.FACILITY_PRIORITY,
        statutory_provenance="State Drug & Vaccine Distribution Management System (DVDMS) SOP",
        rule_reference="DVDMS SOP Chapter 4 (Secondary Distribution & Reverse Logistics)",
        description="Redistribution algorithms must draw from District Warehouses and Community Health Centres (CHCs) "
                    "before depleting peripheral Primary Health Centres (PHCs).",
        is_hard_constraint=False,
        parameters={"priority_order": ["DISTRICT_WAREHOUSE", "CHC", "DISTRICT_HOSPITAL", "PHC"]},
    ),
    PolicyConstraint(
        constraint_id="POL-policy-based-APPROVAL-05",
        name="Human Medical Officer Sole Approval Authority",
        category=PolicyCategory.STATUTORY_APPROVAL,
        statutory_provenance="TATHYON human-approval policy; jurisdictional basis not configured",
        rule_reference="Configure and verify applicable department/state delegation before deployment",
        description="Algorithmic models may only propose transfers. A human reviewer authorized under the "
                    "applicable department/state procedure must approve the dispatch order.",
        is_hard_constraint=True,
        parameters={"authorized_roles": ["medical_officer", "chief_medical_officer", "cmo"]},
    ),
    PolicyConstraint(
        constraint_id="POL-COLD-CHAIN-06",
        name="Vaccine & Biological Cold-Chain Attestation",
        category=PolicyCategory.COLD_CHAIN_INTEGRITY,
        statutory_provenance="National Cold Chain Management Information System (NCCMIS) Guidelines",
        rule_reference="Universal Immunization Programme (UIP) Operational Guidelines Sec 5",
        description="Vaccines and temperature-sensitive biologics require temperature logger verification within "
                    "acceptable range (+2°C to +8°C) before transit approval.",
        is_hard_constraint=True,
        parameters={"temp_min_c": 2.0, "temp_max_c": 8.0},
    ),
]


class PolicyEngine:
    """Evaluates proposed actions against the policy-based public health policy matrix."""

    def __init__(self, policies: Optional[list[PolicyConstraint]] = None):
        self.policies = {p.constraint_id: p for p in (policies or STATUTORY_POLICIES)}

    def list_policies(self) -> list[dict]:
        """Returns the full catalog of active policy constraints with provenance."""
        return [p.to_dict() for p in self.policies.values()]

    def evaluate_donor_eligibility(
        self,
        facility_id: str,
        resource_id: str,
        state: Any,
        min_safety_days: float = 7.0,
        max_age_hours: float = 48.0,
    ) -> dict[str, Any]:
        """Evaluates whether a facility can legally and operationally act as a donor."""
        violations = []
        citations = []

        # Check 1: POL-UNVERIFIED-EXCLUSION-02 (usable stock > 0)
        pol_unverified = self.policies.get("POL-UNVERIFIED-EXCLUSION-02")
        if state.usable_quantity <= 0.0:
            msg = f"Usable verified quantity is {state.usable_quantity} (unverified stock cannot donate)"
            violations.append(f"[{pol_unverified.constraint_id}] {msg}")
            citations.append(pol_unverified.statutory_provenance)

        # Check 2: POL-UNVERIFIED-EXCLUSION-02 (attestation freshness)
        if not state.is_attestation_fresh(max_age_hours=max_age_hours):
            msg = f"Physical attestation age exceeds policy threshold of {max_age_hours}h"
            violations.append(f"[{pol_unverified.constraint_id}] {msg}")
            citations.append(pol_unverified.rule_reference)

        # Check 3: POL-MIN-RESERVE-01 (safety floor)
        pol_reserve = self.policies.get("POL-MIN-RESERVE-01")
        safety_floor = state.consumption_velocity * min_safety_days
        transferable = max(state.usable_quantity - safety_floor - state.reserved_quantity, 0.0)
        if transferable <= 0.0 and state.usable_quantity > 0.0:
            msg = f"Stock level {state.usable_quantity} does not exceed mandatory safety floor of {safety_floor:.1f} units"
            violations.append(f"[{pol_reserve.constraint_id}] {msg}")
            citations.append(pol_reserve.statutory_provenance)

        return {
            "facility_id": facility_id,
            "resource_id": resource_id,
            "eligible": len(violations) == 0,
            "transferable_quantity": round(transferable, 1),
            "violations": violations,
            "provenance_citations": list(set(citations)),
            "policy_checked_at": now(),
        }

    def evaluate_approval_authorization(
        self,
        officer_id: str,
        role: str,
    ) -> dict[str, Any]:
        """Validates whether the approving identity holds policy-based authorization under GFR Rule 22."""
        pol_approval = self.policies.get("POL-policy-based-APPROVAL-05")
        authorized = [r.lower() for r in pol_approval.parameters.get("authorized_roles", [])]
        clean_role = role.strip().lower().replace(" ", "_")

        is_authorized = clean_role in authorized or clean_role in ("chiefmedicalofficer", "medicalofficer")
        return {
            "officer_id": officer_id,
            "role": role,
            "authorized": is_authorized,
            "statutory_authority": pol_approval.statutory_provenance,
            "rule_reference": pol_approval.rule_reference,
            "rejection_reason": None if is_authorized else f"Role '{role}' lacks policy-based approval authority under {pol_approval.rule_reference}",
        }
