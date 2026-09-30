"""Tests for TATHYON Emergency Surge Mode: Outbreak Profiles & Joint Prioritization."""
import pytest

from tathyon.surge import (
    OutbreakType, SURGE_PROFILES, evaluate_joint_fragility,
    generate_emergency_surge_plan
)


def test_outbreak_profiles_exist_and_multiplier_bounds():
    for ot in OutbreakType:
        profile = SURGE_PROFILES[ot]
        assert profile.threshold_runway_days >= 14.0
        assert len(profile.medicine_multipliers) >= 1
        assert len(profile.bed_multipliers) >= 1
        assert len(profile.staff_multipliers) >= 1
        for m in profile.medicine_multipliers.values():
            assert m >= 1.0


def test_joint_fragility_evaluates_clinical_bottleneck():
    # Facility with medicine stockout (0.5 days left) but plenty of beds & staff
    frag = evaluate_joint_fragility(
        facility_id="PHC_BOK",
        facility_name="PHC Bokawand",
        medicine_runway_days=0.5,
        bed_occupancy_pct=50.0,
        staff_deficit_count=0,
        active_outbreak=OutbreakType.RESPIRATORY_EPIDEMIC,
    )
    assert frag.primary_bottleneck == "MEDICINE"
    assert frag.medicine_stockout_risk > 0.8
    assert frag.joint_fragility_score > 0.5
    assert any("EXPEDITE_STOCK_TRANSFER" in act for act in frag.recommended_interventions)


def test_district_emergency_surge_plan_generation():
    snapshots = [
        {"facility_id": "DH_BASTAR", "facility_name": "District Hospital", "medicine_runway_days": 2.0, "bed_occupancy_pct": 98.0, "staff_deficit_count": 4},
        {"facility_id": "CHC_BAK", "facility_name": "CHC Bakawand", "medicine_runway_days": 18.0, "bed_occupancy_pct": 60.0, "staff_deficit_count": 0},
        {"facility_id": "PHC_TOK", "facility_name": "PHC Tokapal", "medicine_runway_days": 3.0, "bed_occupancy_pct": 92.0, "staff_deficit_count": 1},
    ]
    
    plan = generate_emergency_surge_plan(
        outbreak_type=OutbreakType.DENGUE_VECTOR,
        facility_snapshots=snapshots,
    )
    assert plan.outbreak_type == OutbreakType.DENGUE_VECTOR
    assert len(plan.critical_facilities) >= 2
    # DH_BASTAR has severe bed saturation (98%) and staff deficit (4)
    assert plan.critical_facilities[0].facility_id == "DH_BASTAR"
    assert plan.critical_facilities[0].joint_fragility_score > 0.7
    assert "DENGUE_VECTOR" in plan.action_summary
