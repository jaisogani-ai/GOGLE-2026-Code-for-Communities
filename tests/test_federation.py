"""Tests for TATHYON Privacy-Preserving Federation.

Covers:
  - Existing API (backward compatibility with Prompt 4 tests)
  - ModelLifecycleState: valid and invalid transitions
  - ModelRegistry: lifecycle flow, rollback, checklist enforcement
  - Poisoning guard: delta-importance threshold trips quarantine
  - FederatedTrustAggregator: quorum, min-observation, duplicate-node guards
  - sanitize_and_export_weights: extended forbidden-token coverage
  - SyntheticFederationDemo: end-to-end demo run, demo labels present
  - Country-boundary: no cross-node raw-data access through the API
"""
from __future__ import annotations

import pytest

from tathyon.schema import ResourceType
from tathyon.federation import (
    CANONICAL_PATTERNS,
    FederatedTrustAggregator,
    FederatedWeightPackage,
    ModelLifecycleState,
    ModelRegistry,
    PhantomAnomalyPattern,
    run_synthetic_federation_demo,
    sanitize_and_export_weights,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _med_pkg(
    district: str = "IN_CG_BASTAR",
    country: str = "INDIA",
    count: int = 40000,
    pr_auc: float = 0.93,
    fi: dict | None = None,
    is_demo: bool = True,
) -> FederatedWeightPackage:
    if fi is None:
        fi = {"t1_violations_now": 0.60, "burn_inconsistency_30d": 0.40}
    return sanitize_and_export_weights(
        district_id=district,
        country=country,
        resource_type=ResourceType.MEDICINE,
        feature_names=list(fi.keys()),
        feature_importances=fi,
        model_hyperparameters={"max_depth": 4},
        train_count=count,
        base_rate=0.0225,
        pr_auc=pr_auc,
        train_window_start="2025-01-01T00:00:00Z",
        train_window_end="2025-12-31T23:59:59Z",
        schema_version="v1",
        is_demo=is_demo,
    )


# ---------------------------------------------------------------------------
# Backward-compatibility tests (original 3 tests preserved)
# ---------------------------------------------------------------------------

def test_weight_sanitization_and_export():
    importances = {
        "t1_violations_now": 0.60,
        "burn_inconsistency_30d": 0.30,
        "att_last_unusable_share": 0.10,
    }
    pkg = sanitize_and_export_weights(
        district_id="IN_CG_BASTAR",
        country="INDIA",
        resource_type=ResourceType.MEDICINE,
        feature_names=list(importances.keys()),
        feature_importances=importances,
        model_hyperparameters={"max_depth": 4, "learning_rate": 0.03},
        train_count=42000,
        base_rate=0.0225,
        pr_auc=0.9309,
    )
    assert pkg.sanitization_verified is True
    assert len(pkg.package_hash) == 64
    assert len(pkg.patterns_shared) >= 1
    pkg_str = str(pkg)
    assert "facility_id" not in pkg_str
    assert "patient_id" not in pkg_str


def test_leakage_detector_rejects_facility_identifiers():
    with pytest.raises(ValueError, match="SOVEREIGN_DATA_LEAKAGE_DETECTED"):
        sanitize_and_export_weights(
            district_id="IN_CG_BASTAR",
            country="INDIA",
            resource_type=ResourceType.MEDICINE,
            feature_names=["t1_violations_now", "facility_phc_secret_ratio"],
            feature_importances={"t1_violations_now": 0.8, "facility_phc_secret_ratio": 0.2},
            model_hyperparameters={},
            train_count=1000,
            base_rate=0.05,
            pr_auc=0.88,
        )


def test_multi_district_federated_aggregation():
    pkg1 = _med_pkg("IN_CG_BASTAR", "INDIA", count=40000, fi={"t1_violations_now": 0.60, "burn_inconsistency_30d": 0.40})
    pkg2 = _med_pkg("IN_CG_DANTEWADA", "INDIA", count=20000, fi={"t1_violations_now": 0.70, "burn_inconsistency_30d": 0.30})
    pkg3 = _med_pkg("ZA_WC_CAPE", "SOUTH_AFRICA", count=40000, fi={"t1_violations_now": 0.65, "burn_inconsistency_30d": 0.35})

    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(pkg1)
    agg.add_package(pkg2)
    agg.add_package(pkg3)
    result = agg.aggregate()

    assert result["status"] == "AGGREGATED"
    assert result["total_federated_observations"] == 100000
    assert set(result["participating_districts"]) == {"IN_CG_BASTAR", "IN_CG_DANTEWADA", "ZA_WC_CAPE"}
    assert set(result["participating_countries"]) == {"INDIA", "SOUTH_AFRICA"}
    # Weighted average: (40k*0.6 + 20k*0.7 + 40k*0.65) / 100k = 0.64
    assert pytest.approx(result["aggregated_feature_importances"]["t1_violations_now"], abs=0.01) == 0.64


# ---------------------------------------------------------------------------
# Extended leakage-gate tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_feature", [
    "patient_encounter_count",
    "aadhar_linked_flag",
    "dr_attendance_ratio",
    "phc_x_stock_ratio",
    "chc_bed_count",
    "lat_deviation",
    "gps_accuracy",
    "staff_id_count",
    "worker_id_uniqueness",
    "biometric_match_score",
    "employee_id_entropy",
    "address_change_flag",
])
def test_leakage_gate_rejects_extended_forbidden_tokens(bad_feature):
    with pytest.raises(ValueError, match="SOVEREIGN_DATA_LEAKAGE_DETECTED"):
        sanitize_and_export_weights(
            district_id="DISTRICT_X",
            country="COUNTRY_X",
            resource_type=ResourceType.MEDICINE,
            feature_names=["t1_violations_now", bad_feature],
            feature_importances={"t1_violations_now": 0.9, bad_feature: 0.1},
            model_hyperparameters={},
            train_count=5000,
            base_rate=0.03,
            pr_auc=0.85,
        )


# ---------------------------------------------------------------------------
# ModelLifecycleState transition tests
# ---------------------------------------------------------------------------

def test_lifecycle_valid_transitions():
    assert ModelLifecycleState.AVAILABLE.can_transition_to(ModelLifecycleState.EVALUATED)
    assert ModelLifecycleState.AVAILABLE.can_transition_to(ModelLifecycleState.QUARANTINED)
    assert ModelLifecycleState.EVALUATED.can_transition_to(ModelLifecycleState.APPROVED)
    assert ModelLifecycleState.EVALUATED.can_transition_to(ModelLifecycleState.QUARANTINED)
    assert ModelLifecycleState.APPROVED.can_transition_to(ModelLifecycleState.ROLLED_BACK)
    assert ModelLifecycleState.APPROVED.can_transition_to(ModelLifecycleState.QUARANTINED)


def test_lifecycle_invalid_transitions():
    assert not ModelLifecycleState.AVAILABLE.can_transition_to(ModelLifecycleState.APPROVED)
    assert not ModelLifecycleState.AVAILABLE.can_transition_to(ModelLifecycleState.ROLLED_BACK)
    assert not ModelLifecycleState.EVALUATED.can_transition_to(ModelLifecycleState.ROLLED_BACK)
    assert not ModelLifecycleState.ROLLED_BACK.can_transition_to(ModelLifecycleState.AVAILABLE)
    assert not ModelLifecycleState.QUARANTINED.can_transition_to(ModelLifecycleState.APPROVED)


# ---------------------------------------------------------------------------
# ModelRegistry lifecycle flow
# ---------------------------------------------------------------------------

def _make_aggregate() -> dict:
    pkg_a = _med_pkg("NODE_A", "COUNTRY_A", count=30000)
    pkg_b = _med_pkg("NODE_B", "COUNTRY_B", count=30000)
    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(pkg_a)
    agg.add_package(pkg_b)
    return agg.aggregate()


def test_registry_available_to_evaluated_to_approved():
    reg = ModelRegistry()
    agg = _make_aggregate()

    mv = reg.register_available(agg, registered_by="TEST_COORD")
    assert mv.state == ModelLifecycleState.AVAILABLE

    eval_report = {"local_pr_auc": 0.92, "calibration_ok": True}
    reg.transition_to_evaluated(mv.version_id, eval_report, evaluated_by="TEST_EVALUATOR")

    reg.transition_to_approved(mv.version_id, "OFFICER_X", release_checklist_completed=True)
    assert reg.current_operational_aggregate() is not None
    assert reg.current_operational_aggregate()["status"] == "AGGREGATED"


def test_registry_cannot_approve_without_checklist():
    reg = ModelRegistry()
    agg = _make_aggregate()
    mv = reg.register_available(agg)
    reg.transition_to_evaluated(mv.version_id, {}, evaluated_by="EVAL")

    with pytest.raises(ValueError, match="RELEASE_CHECKLIST_INCOMPLETE"):
        reg.transition_to_approved(mv.version_id, "OFFICER", release_checklist_completed=False)


def test_registry_cannot_skip_evaluation():
    reg = ModelRegistry()
    agg = _make_aggregate()
    mv = reg.register_available(agg)

    with pytest.raises(ValueError, match="Cannot transition"):
        reg.transition_to_approved(mv.version_id, "OFFICER", release_checklist_completed=True)


def test_registry_rollback_reverts_to_no_approved():
    reg = ModelRegistry()
    agg = _make_aggregate()
    mv = reg.register_available(agg)
    reg.transition_to_evaluated(mv.version_id, {}, "EVAL")
    reg.transition_to_approved(mv.version_id, "OFFICER", release_checklist_completed=True)

    assert reg.current_operational_aggregate() is not None
    reg.rollback(mv.version_id, "OFFICER", reason="Degraded local calibration observed.")
    assert reg.current_operational_aggregate() is None


def test_registry_rollback_requires_meaningful_reason():
    reg = ModelRegistry()
    agg = _make_aggregate()
    mv = reg.register_available(agg)
    reg.transition_to_evaluated(mv.version_id, {}, "EVAL")
    reg.transition_to_approved(mv.version_id, "OFFICER", release_checklist_completed=True)

    with pytest.raises(ValueError, match="at least 10 characters"):
        reg.rollback(mv.version_id, "OFFICER", reason="bad")


def test_registry_rollback_keeps_previous_approved():
    """With two approved versions, rolling back the second keeps the first."""
    reg = ModelRegistry()
    for i in range(2):
        pkg_a = _med_pkg(f"NODE_{i}A", "COUNTRY_A", count=30000)
        pkg_b = _med_pkg(f"NODE_{i}B", "COUNTRY_B", count=30000)
        aggr = FederatedTrustAggregator(ResourceType.MEDICINE)
        aggr.add_package(pkg_a)
        aggr.add_package(pkg_b)
        agg = aggr.aggregate()
        mv = reg.register_available(agg)
        reg.transition_to_evaluated(mv.version_id, {}, "EVAL")
        reg.transition_to_approved(mv.version_id, "OFFICER", release_checklist_completed=True)

    versions = [v for v in reg.summary() if v["state"] == ModelLifecycleState.APPROVED.value]
    last_vid = versions[-1]["version_id"]
    reg.rollback(last_vid, "OFFICER", reason="Calibration drift detected in production monitoring.")

    # First approved version still active
    assert reg.current_operational_aggregate() is not None


def test_registry_quarantine_on_arrival():
    """If poisoning guard trips, incoming version is immediately quarantined."""
    reg = ModelRegistry()

    # Establish a baseline approved version
    pkg_a = _med_pkg("NODE_BASE_A", "COUNTRY_A", count=30000, fi={"t1_violations_now": 0.60, "burn_inconsistency_30d": 0.40})
    pkg_b = _med_pkg("NODE_BASE_B", "COUNTRY_B", count=30000, fi={"t1_violations_now": 0.60, "burn_inconsistency_30d": 0.40})
    aggr = FederatedTrustAggregator(ResourceType.MEDICINE)
    aggr.add_package(pkg_a)
    aggr.add_package(pkg_b)
    base_agg = aggr.aggregate()
    mv_base = reg.register_available(base_agg)
    reg.transition_to_evaluated(mv_base.version_id, {}, "EVAL")
    reg.transition_to_approved(mv_base.version_id, "OFFICER", release_checklist_completed=True)

    # Now submit an aggregate with a 50pp shift — above the 30pp threshold
    pkg_c = _med_pkg("NODE_NEW_C", "COUNTRY_A", count=30000, fi={"t1_violations_now": 0.10, "burn_inconsistency_30d": 0.90})
    pkg_d = _med_pkg("NODE_NEW_D", "COUNTRY_B", count=30000, fi={"t1_violations_now": 0.10, "burn_inconsistency_30d": 0.90})
    aggr2 = FederatedTrustAggregator(ResourceType.MEDICINE)
    aggr2.add_package(pkg_c)
    aggr2.add_package(pkg_d)
    poisoned_agg = aggr2.aggregate()

    mv_new = reg.register_available(poisoned_agg, registered_by="COORD")
    assert mv_new.state == ModelLifecycleState.QUARANTINED
    assert mv_new.quarantine_reason is not None
    assert "Poisoning guard tripped" in mv_new.quarantine_reason


# ---------------------------------------------------------------------------
# FederatedTrustAggregator guard tests
# ---------------------------------------------------------------------------

def test_aggregator_rejects_unverified_package():
    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    pkg = _med_pkg("NODE_X", "COUNTRY_X")
    pkg.sanitization_verified = False  # manually corrupt

    with pytest.raises(ValueError, match="UNVERIFIED_PACKAGE"):
        agg.add_package(pkg)


def test_aggregator_rejects_resource_type_mismatch():
    agg = FederatedTrustAggregator(ResourceType.BED)
    pkg = _med_pkg("NODE_X", "COUNTRY_X")  # medicine, not bed

    with pytest.raises(ValueError, match="Resource type mismatch"):
        agg.add_package(pkg)


def test_aggregator_rejects_duplicate_district():
    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(_med_pkg("NODE_A", "COUNTRY_A"))

    with pytest.raises(ValueError, match="DUPLICATE_SUBMISSION"):
        agg.add_package(_med_pkg("NODE_A", "COUNTRY_B"))


def test_aggregator_quorum_not_met():
    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(_med_pkg("NODE_A", "COUNTRY_A", count=30000))

    with pytest.raises(ValueError, match="QUORUM_NOT_MET"):
        agg.aggregate()


def test_aggregator_min_observations_guard():
    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(_med_pkg("NODE_A", "COUNTRY_A", count=10))
    agg.add_package(_med_pkg("NODE_B", "COUNTRY_B", count=10))

    with pytest.raises(ValueError, match="INSUFFICIENT_OBSERVATIONS"):
        agg.aggregate()


def test_aggregator_country_boundary_raw_data_not_accessible():
    """No cross-node raw facility data is accessible through the aggregator.
    The only data available after aggregation is the normalized weight dict.
    The sovereignty_assurance field mentions 'patient' in its disclosure text —
    that is correct and expected; it is not raw patient data."""
    from tathyon.federation import _feature_name_violates_gate
    pkg_a = _med_pkg("NODE_A", "COUNTRY_A", count=30000)
    pkg_b = _med_pkg("NODE_B", "COUNTRY_B", count=30000)

    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(pkg_a)
    agg.add_package(pkg_b)
    result = agg.aggregate()

    # Structural check: only expected mathematical keys at top level
    expected_keys = {
        "status", "is_demo", "demo_label",
        "participating_districts", "participating_countries",
        "participating_countries_count",
        "total_federated_observations", "mean_node_pr_auc",
        "aggregated_feature_importances",
        "consensus_timestamp", "resource_type",
        "sovereignty_assurance", "limitations",
    }
    unexpected_keys = set(result.keys()) - expected_keys
    assert not unexpected_keys, f"Unexpected keys in aggregate: {unexpected_keys}"

    # Feature importances must be normalized floats; no entity-id tokens
    fi = result["aggregated_feature_importances"]
    assert isinstance(fi, dict)
    for k, v in fi.items():
        assert isinstance(k, str), f"Feature key not a string: {k!r}"
        assert isinstance(v, float), f"Feature value not a float: {v!r}"
        assert _feature_name_violates_gate(k) is None, (
            f"Aggregate contains forbidden feature token: {k!r}"
        )

    # No raw-data row objects should appear
    assert "facility_id" not in str(result)
    assert "patient_id" not in str(result)




# ---------------------------------------------------------------------------
# Demo harness tests
# ---------------------------------------------------------------------------

def test_synthetic_demo_runs_end_to_end():
    result = run_synthetic_federation_demo(resource_type=ResourceType.MEDICINE, seed=42)
    assert result.provenance == "SYNTHETIC_DEMO_ONLY"
    assert "SYNTHETIC" in result.demo_label
    assert result.aggregate["is_demo"] is True
    assert "[SYNTHETIC DEMO" in result.aggregate["demo_label"]


def test_synthetic_demo_lifecycle_reaches_approved():
    result = run_synthetic_federation_demo()
    final_state = result.lifecycle_summary[-1]["state"]
    assert final_state == ModelLifecycleState.APPROVED.value


def test_synthetic_demo_deterministic():
    r1 = run_synthetic_federation_demo(seed=99)
    r2 = run_synthetic_federation_demo(seed=99)
    assert r1.aggregate["total_federated_observations"] == r2.aggregate["total_federated_observations"]
    assert r1.aggregate["mean_node_pr_auc"] == r2.aggregate["mean_node_pr_auc"]


def test_synthetic_demo_is_demo_flags_set():
    result = run_synthetic_federation_demo()
    for pkg_dict in result.node_packages:
        assert pkg_dict["is_demo"] is True, "Every demo node package must carry is_demo=True"


def test_synthetic_demo_no_real_country_names():
    """Demo node names must not use real country or district codes."""
    result = run_synthetic_federation_demo()
    real_country_codes = {"INDIA", "SOUTH_AFRICA", "BRAZIL", "CHINA", "RUSSIA"}
    for country in result.aggregate.get("participating_countries", []):
        assert country not in real_country_codes, (
            f"Demo node used real country code {country!r}. "
            "Use DEMO_COUNTRY_* prefixed names instead."
        )


# ---------------------------------------------------------------------------
# Schema-version enforcement
# ---------------------------------------------------------------------------

def test_schema_version_is_recorded():
    pkg = _med_pkg()
    assert pkg.schema_version == "v1"


def test_aggregation_includes_sovereignty_assurance():
    pkg_a = _med_pkg("NODE_A", "COUNTRY_A", count=30000)
    pkg_b = _med_pkg("NODE_B", "COUNTRY_B", count=30000)
    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(pkg_a)
    agg.add_package(pkg_b)
    result = agg.aggregate()
    assert "sovereignty_assurance" in result
    assert "ZERO" in result["sovereignty_assurance"]


def test_aggregation_includes_limitations_disclosure():
    """The aggregate must explicitly disclose what privacy protections are NOT present."""
    pkg_a = _med_pkg("NODE_A", "COUNTRY_A", count=30000)
    pkg_b = _med_pkg("NODE_B", "COUNTRY_B", count=30000)
    agg = FederatedTrustAggregator(ResourceType.MEDICINE)
    agg.add_package(pkg_a)
    agg.add_package(pkg_b)
    result = agg.aggregate()
    assert "limitations" in result
    assert "No secure aggregation" in result["limitations"]
    assert "No differential privacy" in result["limitations"]
