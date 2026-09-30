"""Tests for TATHYON Resource-Agnostic Core Platform: Trust Queue, Dispatcher & Approval."""
import pytest
import pandas as pd

from tathyon.schema import ResourceType
from tathyon.core_platform import (
    ResourceCandidate, UnifiedTrustQueue, target_unified_verifications,
    UnifiedAllocationDispatcher, execute_sovereign_approval
)
from tathyon.beds import BedFacilityNode
from tathyon.personnel import PersonnelFacilityState
from tathyon.optimize import Facility as MedFacility, Need as MedNeed, SourceStock as MedSourceStock


def test_unified_trust_queue_prioritization():
    candidates = [
        # Medicine candidate
        ResourceCandidate("FAC001", "PHC North", ResourceType.MEDICINE, "AMX250", p_wrong=0.85, consequence_days=10.0, essentiality_weight=3.0, visit_cost_slots=1),
        # Bed candidate
        ResourceCandidate("DH001", "District Hospital", ResourceType.BED, "ICU_O2", p_wrong=0.75, consequence_days=12.0, essentiality_weight=3.0, visit_cost_slots=2),
        # Personnel candidate
        ResourceCandidate("CHC002", "CHC Central", ResourceType.PERSONNEL, "STAFF_NURSE", p_wrong=0.40, consequence_days=5.0, essentiality_weight=2.0, visit_cost_slots=1),
    ]
    
    # Run targeting with 3 visit slots
    queue = target_unified_verifications(budget_slots=3, candidates=candidates)
    assert len(queue) >= 1
    assert queue.budget_used <= 3
    
    ranking = queue.ranking
    assert "expected_value" in ranking.columns
    # Check that highest expected value is ranked 1
    assert ranking.iloc[0]["expected_value"] >= ranking.iloc[1]["expected_value"]


def test_unified_allocation_dispatcher_beds():
    candidates = [
        BedFacilityNode("DH_01", "District Hospital", "ICU_O2", 20, 19, 1, 0.05, True, 50.0, 50.0),
        BedFacilityNode("CHC_01", "CHC East", "ICU_O2", 10, 4, 6, 0.10, True, 55.0, 50.0),
    ]
    
    res = UnifiedAllocationDispatcher.dispatch_allocation(
        resource_type=ResourceType.BED,
        surging_facility_id="DH_01",
        bed_type="ICU_O2",
        excess_patients_seeking_admission=2,
        candidate_facilities=candidates,
    )
    assert res.resource_type == ResourceType.BED
    assert res.decision == "DIVERT_PATIENTS_APPROVED"
    assert res.details.diverted_patients_count == 2


def test_unified_allocation_dispatcher_personnel():
    facilities = [
        PersonnelFacilityState("FAC_A", "PHC A", "MEDICAL_OFFICER", 3, 3, 0.10, True, 1, 0, 50.0, 50.0),
        PersonnelFacilityState("FAC_B", "CHC B", "MEDICAL_OFFICER", 2, 2, 0.05, True, 2, 1, 55.0, 52.0),
    ]
    
    res = UnifiedAllocationDispatcher.dispatch_allocation(
        resource_type=ResourceType.PERSONNEL,
        role="MEDICAL_OFFICER",
        facilities=facilities,
    )
    assert res.resource_type == ResourceType.PERSONNEL
    assert res.decision == "STAFF_REDEPLOYMENT_APPROVED"
    assert res.details.total_staff_redeployed == 1


def test_sovereign_approval_with_break_glass():
    # Regular approval
    appr = execute_sovereign_approval(
        plan_id="plan_12345",
        resource_type=ResourceType.MEDICINE,
        officer_id="CMO_BASTAR",
        action="APPROVE",
        all_lines=["line_01", "line_02"],
    )
    assert appr.decision == "APPROVED"
    assert appr.approved_lines_count == 2
    assert not appr.break_glass
    assert appr.obligation_id is None
    
    # Break-glass with mandatory reason
    bg = execute_sovereign_approval(
        plan_id="plan_99999",
        resource_type=ResourceType.BED,
        officer_id="CMO_BASTAR",
        action="BREAK_GLASS",
        break_glass_reason="Severe vector-borne outbreak; emergency patient diversion override required",
        all_lines=["line_01"],
    )
    assert bg.decision == "APPROVED"
    assert bg.break_glass
    assert bg.obligation_id is not None
    assert "oblg_72h_audit" in bg.obligation_id
    
    # Break-glass fails if reason is missing or trivial
    with pytest.raises(ValueError, match="BREAK_GLASS_REASON_MANDATORY"):
        execute_sovereign_approval(
            plan_id="plan_99999",
            resource_type=ResourceType.BED,
            officer_id="CMO_BASTAR",
            action="BREAK_GLASS",
            break_glass_reason="emergency", # too short (<10 chars)
        )
