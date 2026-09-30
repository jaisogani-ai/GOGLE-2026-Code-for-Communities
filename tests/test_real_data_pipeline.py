"""
Tests for the TATHYON Auditable Real-Data Ingestion, Validation, and Training Pipeline.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score

from tathyon.real_data_pipeline import (
    AUTHORIZED_ATTESTER_ROLES,
    MIN_INSPECTION_SECONDS,
    P6_MEDICINE_REL_ERR_THRESHOLD,
    P6_MEDICINE_USABLE_SHARE_FLOOR,
    IngestionManifest,
    RealDataIngestor,
    compute_file_sha256,
    extract_real_medicine_features,
    split_facility_and_time,
)
from tathyon.real_training import (
    ModelRegistry,
    bootstrap_ci,
    evaluate_triage_at_budget,
    run_local_real_pipeline,
)
from tathyon.trust_features import TRUST_FEATURES
from tathyon.verify import GBC_PARAMS


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    """Creates a temporary sample DVDMS/e-Aushadhi export CSV with both valid and invalid rows."""
    csv_content = """FacilityCode,DrugCode,Date,AvailableStock,ReceiptQty,IssuedQty,BatchNo,ExpiryDate,is_materially_wrong,attester_role,is_custodian,seconds_spent,counted_present,counted_usable
FAC_DH_01,MED_INJDEX,2026-08-01,1000.0,200.0,50.0,BATCH-A1,2027-01-01,,,,,,
FAC_DH_01,MED_INJDEX,2026-08-02,950.0,0.0,50.0,BATCH-A1,2027-01-01,,,,,,
FAC_DH_01,MED_INJDEX,2026-08-03,900.0,0.0,50.0,BATCH-A1,2027-01-01,1,district_drug_inspector,false,120.0,700.0,700.0
FAC_DH_01,MED_ORS01,2026-08-01,500.0,0.0,20.0,BATCH-B1,2027-06-01,0,quality_medical_officer,false,60.0,500.0,500.0
FAC_CHC_02,MED_INJDEX,2026-08-01,200.0,0.0,10.0,BATCH-C1,2027-01-01,,,,,,
FAC_CHC_02,MED_INJDEX,2026-08-02,190.0,0.0,10.0,BATCH-C1,2027-01-01,1,block_health_officer,false,45.0,200.0,100.0
FAC_CHC_02,MED_ORS01,2026-08-01,300.0,0.0,15.0,BATCH-D1,2027-06-01,,,,,,
FAC_BAD_01,MED_BAD,2026-08-01,-50.0,0.0,0.0,BATCH-X,2027-01-01,,,,,,
FAC_BAD_02,MED_BAD,2026-08-01,NaN,0.0,0.0,BATCH-X,2027-01-01,,,,,,
,MED_NO_FAC,2026-08-01,100.0,0.0,0.0,BATCH-X,2027-01-01,,,,,,
FAC_BAD_03,,2026-08-01,100.0,0.0,0.0,BATCH-X,2027-01-01,,,,,,
FAC_BAD_04,MED_BAD,,100.0,0.0,0.0,BATCH-X,2027-01-01,,,,,,
FAC_BAD_05,MED_BAD,2099-01-01,100.0,0.0,0.0,BATCH-X,2027-01-01,,,,,,
FAC_BAD_06,MED_BAD,2026-08-01,100.0,0.0,0.0,BATCH-X,2027-01-01,1,storekeeper,true,60.0,50.0,50.0
FAC_BAD_07,MED_BAD,2026-08-01,100.0,0.0,0.0,BATCH-X,2027-01-01,1,district_drug_inspector,false,10.0,50.0,50.0
FAC_BAD_08,MED_BAD,2026-08-01,100.0,0.0,0.0,BATCH-X,2027-01-01,1,unauthorized_intern,false,60.0,50.0,50.0
FAC_BAD_09,MED_BAD,2026-08-01,100.0,0.0,0.0,BATCH-X,2027-01-01,0,district_drug_inspector,false,60.0,50.0,50.0
"""
    file_path = tmp_path / "test_dvdms.csv"
    file_path.write_text(csv_content, encoding="utf-8")
    return file_path


def test_ingestion_and_quarantine(sample_csv: Path, tmp_path: Path):
    """Verifies that valid rows are ingested and invalid rows are properly quarantined with reasons."""
    quarantine_dir = tmp_path / "quarantine"
    ingestor = RealDataIngestor(quarantine_dir=quarantine_dir)

    clean_df, manifest, quarantine = ingestor.validate_and_ingest(
        source_path=sample_csv,
        source_name="Test_DVDMS",
        source_owner="Test_Health_Dept",
        license_str="Test_License",
    )

    assert manifest.total_raw_rows == 17
    # Exactly 7 valid rows in sample_csv
    assert manifest.valid_rows == 7
    # 10 invalid rows must be quarantined
    assert manifest.quarantined_rows == 10
    assert len(quarantine) == 10

    # Verify specific quarantine error types
    quarantine_types = {q.error_type for q in quarantine}
    assert "NEGATIVE_QUANTITY" in quarantine_types
    assert "NON_FINITE_QTY" in quarantine_types
    assert "MISSING_FACILITY_ID" in quarantine_types
    assert "MISSING_RESOURCE_KEY" in quarantine_types
    assert "MISSING_DATE" in quarantine_types
    assert "FUTURE_DATED" in quarantine_types
    assert "CUSTODIAN_SELF_ATTESTATION" in quarantine_types
    assert "RUBBER_STAMP_UNDER_TIME" in quarantine_types
    assert "UNAUTHORIZED_ATTESTER_ROLE" in quarantine_types
    assert "P6_THRESHOLD_CONTRADICTION" in quarantine_types


def test_label_provenance_enforcement(sample_csv: Path, tmp_path: Path):
    """Verifies that only authorized human attestations adhering to P6 are accepted as labels."""
    ingestor = RealDataIngestor(quarantine_dir=tmp_path / "quarantine")
    clean_df, manifest, _ = ingestor.validate_and_ingest(
        source_path=sample_csv,
        source_name="Test_DVDMS",
        source_owner="Test_Health_Dept",
        license_str="Test_License",
    )

    # Clean df must have exactly 3 human-attested labels (FAC_DH_01 MED_INJDEX 2026-08-03, FAC_DH_01 MED_ORS01 2026-08-01, FAC_CHC_02 MED_INJDEX 2026-08-02)
    labeled = clean_df.dropna(subset=["is_materially_wrong"])
    assert len(labeled) == 3
    assert set(labeled["is_materially_wrong"].unique()) <= {0.0, 1.0}

    # Verify P6 rel error match: FAC_DH_01 row has reported=900, present=700 -> rel_err = 200/700 = 28.5% > 15% -> label=1
    row1 = labeled[labeled["facility_id"] == "FAC_DH_01"].iloc[0]
    assert row1["is_materially_wrong"] == 1.0


def test_facility_and_time_split(sample_csv: Path, tmp_path: Path):
    """Verifies that facility and time split strictly prevents facility and future leakage."""
    ingestor = RealDataIngestor(quarantine_dir=tmp_path / "quarantine")
    clean_df, _, _ = ingestor.validate_and_ingest(
        source_path=sample_csv,
        source_name="Test_DVDMS",
        source_owner="Test_Health_Dept",
        license_str="Test_License",
    )

    feats = extract_real_medicine_features(clean_df)
    train_df, test_df, split_def = split_facility_and_time(feats, test_facility_ratio=0.50, seed=42)

    # Facility disjointness
    train_facs = set(train_df["facility_id"].unique())
    test_facs = set(test_df["facility_id"].unique())
    assert train_facs.isdisjoint(test_facs)
    assert len(train_facs) > 0
    assert len(test_facs) > 0


def test_causal_features_exact_18(sample_csv: Path, tmp_path: Path):
    """Verifies that all 18 TRUST_FEATURES are extracted without truth leakage."""
    ingestor = RealDataIngestor(quarantine_dir=tmp_path / "quarantine")
    clean_df, _, _ = ingestor.validate_and_ingest(
        source_path=sample_csv,
        source_name="Test_DVDMS",
        source_owner="Test_Health_Dept",
        license_str="Test_License",
    )

    feats = extract_real_medicine_features(clean_df)
    for col in TRUST_FEATURES:
        assert col in feats.columns
    assert len(TRUST_FEATURES) == 18

    # Ensure no truth column exists in feature matrix
    for forbidden in ["true_stock", "true_usable", "rel_err", "label_wrong"]:
        assert forbidden not in feats.columns


def test_bootstrap_ci_and_triage_metrics():
    """Verifies bootstrap confidence interval and triage evaluation calculations."""
    y_true = np.array([0, 1, 0, 1, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 1])
    y_score = np.array([0.1, 0.9, 0.2, 0.8, 0.3, 0.1, 0.85, 0.4, 0.75, 0.2, 0.65, 0.15, 0.05, 0.35, 0.7])

    ci = bootstrap_ci(y_true, y_score, average_precision_score, n_bootstraps=100)
    assert 0.0 <= ci.ci_lower_95 <= ci.point_estimate <= ci.ci_upper_95 <= 1.0

    triage = evaluate_triage_at_budget(y_true, y_score, k=5, n_draws=50)
    assert "model_hit_rate" in triage
    assert "random_hit_rate" in triage
    assert "lift_vs_random" in triage
    assert triage["model_hit_rate"] >= triage["random_hit_rate"]


def test_model_registry_serialization(tmp_path: Path):
    """Verifies that model registry saves and loads according to required schema."""
    reg_file = tmp_path / "model_registry.json"
    registry = ModelRegistry(path=reg_file)

    entry = {
        "model_id": "TEST-MODEL-001",
        "model_name": "GradientBoostingClassifier-Baseline",
        "feature_set_version": "v2.0-medicine-18",
        "resource_module": "medicine",
        "dataset_citation": "Test Citation",
        "license": "Test License",
        "retrieval_date": "2026-09-28",
        "file_sha256": "abcdef123456",
        "hyperparameters": GBC_PARAMS,
        "status": "approved for pilot",
        "eligible_rows": 100,
        "eligible_facilities": 10,
        "supervised_training_conducted": True,
    }
    registry.register_model_entry(entry)

    # Re-read
    reloaded = ModelRegistry(path=reg_file)
    assert len(reloaded.data["models"]) == 1
    m = reloaded.data["models"][0]
    assert m["model_id"] == "TEST-MODEL-001"
    assert m["status"] == "approved for pilot"
    assert m["hyperparameters"]["n_estimators"] == 200


def test_blocking_condition_when_unlabeled():
    """Verifies that running on real data with 0 labels gracefully blocks supervised fitting."""
    res = run_local_real_pipeline(source_csv_path="non_existent_file.csv")
    assert res["status"] in ["AWAITING_FIELD_ATTESTATIONS", "UNSUPERVISED_TRIAGE_ONLY_LABELS_PENDING"]
    assert res["supervised_training_possible"] is False


def test_unverified_test_fixture_cannot_enter_supervised_training(tmp_path: Path):
    """Structurally valid fixture labels are insufficient without provenance evidence."""
    rows = [
        "FacilityCode,DrugCode,Date,AvailableStock,ReceiptQty,IssuedQty,BatchNo,ExpiryDate,is_materially_wrong,attester_role,is_custodian,seconds_spent,counted_present,counted_usable"
    ]
    # 4 facilities, 2 SKUs, 20 days
    facilities = ["FAC_PILOT_01", "FAC_PILOT_02", "FAC_PILOT_03", "FAC_PILOT_04"]
    skus = ["MED_INJDEX", "MED_ORS01"]

    for fac_idx, fac in enumerate(facilities):
        for s_idx, sku in enumerate(skus):
            stock = 500.0 + fac_idx * 50
            for day in range(1, 21):
                date_str = f"2026-08-{day:02d}"
                issued = 10.0 + (day % 5)
                receipt = 50.0 if day in [5, 15] else 0.0
                stock = max(0.0, stock + receipt - issued)

                # Add physical verification attestations on days 10 and 20
                if day in [10, 20]:
                    # Create some true discrepancies (>15% error or <70% usable)
                    is_disc = (fac_idx + day) % 2 == 0
                    if is_disc:
                        pres = stock * 0.70 # 30% deficit -> P6 discrepancy = 1
                        use = pres
                        lbl = 1
                    else:
                        pres = stock # 0% deficit -> P6 clean = 0
                        use = pres
                        lbl = 0
                    rows.append(
                        f"{fac},{sku},{date_str},{stock},{receipt},{issued},BATCH-P1,2027-06-01,{lbl},district_drug_inspector,false,120.0,{pres},{use}"
                    )
                else:
                    rows.append(f"{fac},{sku},{date_str},{stock},{receipt},{issued},BATCH-P1,2027-06-01,,,,,,")

    csv_path = tmp_path / "pilot_multi_facility.csv"
    csv_path.write_text("\n".join(rows), encoding="utf-8")

    tracked_paths = [
        Path("artifacts/model_registry.json"),
        Path("artifacts/exclusion_summary.json"),
        Path("artifacts/models/trust_scorer_real_pilot.joblib"),
    ]
    before = {path: path.read_bytes() if path.exists() else None for path in tracked_paths}

    result = run_local_real_pipeline(
        source_csv_path=str(csv_path),
        source_name="Pilot_District_Field_Attestations",
        source_owner="District_Drug_Inspection_Directorate",
        license_str="Restricted Sovereign Data",
    )

    assert result["status"] == "UNVERIFIED_PROVENANCE_TRAINING_BLOCKED"
    assert result["supervised_training_conducted"] is False
    assert result["provenance_manifest"]["reason"] == "PROVENANCE_MANIFEST_MISSING"
    assert before == {path: path.read_bytes() if path.exists() else None for path in tracked_paths}
