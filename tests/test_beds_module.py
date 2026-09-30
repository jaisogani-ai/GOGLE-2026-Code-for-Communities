"""Tests for TATHYON Beds Module: Capacity, Trust Scoring, Patient Diversion & Early Warnings."""
import pytest
import numpy as np
import pandas as pd

from tathyon.beds import (
    BED_TYPES, BED_TRUST_FEATURES, generate_beds_dataset,
    extract_bed_trust_features, BedTrustScorer,
    BedFacilityNode, solve_patient_diversion, evaluate_bed_surge_warnings
)


@pytest.fixture(scope="module")
def district_beds_data():
    f_ids = ["FAC001", "FAC002", "FAC003", "FAC004", "FAC005"]
    f_types = {
        "FAC001": "DISTRICT_HOSPITAL",
        "FAC002": "CHC",
        "FAC003": "CHC",
        "FAC004": "PHC",
        "FAC005": "PHC",
    }
    df_truth, df_obs = generate_beds_dataset(f_ids, f_types, days=28, seed=1001)
    return df_truth, df_obs


def test_anti_leakage_features_do_not_contain_truth(district_beds_data):
    df_truth, df_obs = district_beds_data
    features = extract_bed_trust_features(df_obs)
    
    # Assert all documented features are present
    assert tuple(features.columns) == BED_TRUST_FEATURES
    
    # Assert zero truth columns or labels leaked
    forbidden = {"true_occupied", "true_available", "true_broken", "is_materially_wrong", "label"}
    assert not any(f in features.columns for f in forbidden)


def test_bed_trust_scorer_train_and_predict(district_beds_data):
    df_truth, df_obs = district_beds_data
    scorer = BedTrustScorer(n_estimators=50, max_depth=3, learning_rate=0.05)
    scorer.fit(df_obs, df_truth)
    
    p = scorer.predict_p_wrong(df_obs)
    assert len(p) == len(df_obs)
    assert np.all((p >= 0.0) & (p <= 1.0))
    # Discrimination check
    high_risk = p[df_truth["is_materially_wrong"] == 1]
    low_risk = p[df_truth["is_materially_wrong"] == 0]
    assert high_risk.mean() > low_risk.mean()


def test_patient_diversion_verifies_gate_and_diverts_optimally():
    candidates = [
        BedFacilityNode("DH_BASTAR", "District Hospital", "ICU_O2", 20, 19, 1, 0.05, True, 50.0, 50.0),
        # Destination A: Verified, available safe headroom
        BedFacilityNode("CHC_NORTH", "CHC North", "ICU_O2", 10, 3, 7, 0.10, True, 60.0, 55.0),
        # Destination B: High phantom risk, unverified (must be rejected by gate)
        BedFacilityNode("CHC_PHANTOM", "CHC Phantom", "ICU_O2", 10, 2, 8, 0.75, False, 52.0, 54.0),
        # Destination C: Full occupancy (must be rejected by safe ceiling)
        BedFacilityNode("CHC_FULL", "CHC Full", "ICU_O2", 10, 9, 1, 0.05, True, 48.0, 50.0),
    ]
    
    plan = solve_patient_diversion(
        surging_facility_id="DH_BASTAR",
        bed_type="ICU_O2",
        excess_patients_seeking_admission=4,
        candidate_facilities=candidates,
        max_travel_km=65.0,
        safe_occupancy_ceiling=0.85,
        p_wrong_verification_threshold=0.40,
    )
    
    assert plan.solver_status in ("OPTIMAL", "FEASIBLE")
    assert plan.diverted_patients_count == 4
    assert len(plan.routes) == 1
    assert plan.routes[0].destination_id == "CHC_NORTH"
    assert plan.routes[0].patients_diverted == 4
    
    # Assert phantom facility was rejected by verification gate
    rejected_ids = [r["facility_id"] for r in plan.rejected_destinations]
    assert "CHC_PHANTOM" in rejected_ids
    assert "CHC_FULL" in rejected_ids


def test_bed_surge_early_warning_detection(district_beds_data):
    _, df_obs = district_beds_data
    warnings = evaluate_bed_surge_warnings(df_obs, days_runway_threshold=2.0)
    assert isinstance(warnings, list)
    for w in warnings:
        assert "facility_id" in w
        assert "bed_type" in w
        assert "days_until_saturation" in w
        assert w["severity"] in ("CRITICAL", "WARNING")
