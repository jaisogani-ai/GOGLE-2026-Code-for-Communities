"""Tests for TATHYON Personnel Module: Roster Integrity, Attendance Trust & Staff Redeployment."""
import pytest
import numpy as np
import pandas as pd

from tathyon.personnel import (
    STAFF_ROLES, PERSONNEL_TRUST_FEATURES, generate_personnel_dataset,
    extract_personnel_trust_features, PersonnelTrustScorer,
    PersonnelFacilityState, solve_staff_redeployment
)


@pytest.fixture(scope="module")
def district_personnel_data():
    f_ids = ["FAC001", "FAC002", "FAC003", "FAC004", "FAC005"]
    f_types = {
        "FAC001": "DISTRICT_HOSPITAL",
        "FAC002": "CHC",
        "FAC003": "CHC",
        "FAC004": "PHC",
        "FAC005": "PHC",
    }
    df_truth, df_obs = generate_personnel_dataset(f_ids, f_types, days=21, seed=1001)
    return df_truth, df_obs


def test_personnel_anti_leakage(district_personnel_data):
    df_truth, df_obs = district_personnel_data
    features = extract_personnel_trust_features(df_obs)
    
    assert tuple(features.columns) == PERSONNEL_TRUST_FEATURES
    forbidden = {"true_present_hours", "true_clinical_encounters", "is_materially_wrong", "is_ghost_worker"}
    assert not any(f in features.columns for f in forbidden)


def test_personnel_trust_scorer(district_personnel_data):
    df_truth, df_obs = district_personnel_data
    scorer = PersonnelTrustScorer(n_estimators=50, max_depth=3, learning_rate=0.05)
    scorer.fit(df_obs, df_truth)
    
    p = scorer.predict_p_wrong(df_obs)
    assert len(p) == len(df_obs)
    assert np.all((p >= 0.0) & (p <= 1.0))
    # Discrimination check
    high_risk = p[df_truth["is_materially_wrong"] == 1]
    low_risk = p[df_truth["is_materially_wrong"] == 0]
    assert high_risk.mean() > low_risk.mean()


def test_staff_redeployment_preserves_safety_floor_and_verification_gate():
    facilities = [
        # Donor A: Verified surplus above statutory floor
        PersonnelFacilityState(
            facility_id="CHC_DONOR_A", facility_name="CHC Donor A", role="STAFF_NURSE",
            rostered_staff=8, verified_present_staff=7, p_wrong=0.10, is_verified=True,
            required_statutory_floor=3, surge_deficit=0, x_km=50.0, y_km=50.0
        ),
        # Donor B: Ghost worker risk (unverified, p_wrong=0.70) -> MUST BE REJECTED
        PersonnelFacilityState(
            facility_id="CHC_GHOST_DONOR", facility_name="CHC Ghost Donor", role="STAFF_NURSE",
            rostered_staff=6, verified_present_staff=6, p_wrong=0.70, is_verified=False,
            required_statutory_floor=2, surge_deficit=0, x_km=55.0, y_km=52.0
        ),
        # Recipient: Severe clinical surge deficit
        PersonnelFacilityState(
            facility_id="DH_EMERGENCY", facility_name="District Hospital ICU", role="STAFF_NURSE",
            rostered_staff=10, verified_present_staff=10, p_wrong=0.05, is_verified=True,
            required_statutory_floor=10, surge_deficit=3, x_km=60.0, y_km=55.0
        ),
    ]
    
    plan = solve_staff_redeployment(
        role="STAFF_NURSE",
        facilities=facilities,
        max_travel_km=50.0,
        p_wrong_donor_threshold=0.35,
    )
    
    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.total_staff_redeployed == 3
    assert len(plan.transfers) == 1
    t = plan.transfers[0]
    assert t.origin_facility_id == "CHC_DONOR_A"
    assert t.destination_facility_id == "DH_EMERGENCY"
    assert t.staff_count == 3
    # Check donor statutory floor retention: 7 verified - 3 redeployed = 4 retained (floor was 3)
    assert t.donor_retained_staff == 4
    
    # Assert ghost donor was rejected
    rejected_ids = [rj["facility_id"] for rj in plan.rejected_donors]
    assert "CHC_GHOST_DONOR" in rejected_ids
