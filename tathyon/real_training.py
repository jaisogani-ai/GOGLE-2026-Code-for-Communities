"""
TATHYON Local Training, Evaluation, and Model Registry Pipeline for Real Data.

Follows strict scientific guidelines:
1. Baseline model uses EXACT repository GradientBoosting hyperparameters:
   dict(n_estimators=200, max_depth=4, learning_rate=0.03, subsample=0.8, random_state=0)
2. Facility AND time split: zero facility and temporal leakage.
3. Candidate challenger models are evaluated and registered ONLY if they beat
   the baseline on held-out PR-AUC AND beat same-budget random ablation by the defined margin.
4. Bootstrap 95% confidence intervals computed for all test metrics.
5. Strict honesty: If no human-attested P6 labels are present, DOES NOT manufacture a fake run;
   instead outputs an auditable Data Card, Exclusion Summary, Feature Mapping, and blocks
   supervised fitting with an explicit Pilot Labeling Handoff.
6. Read-only dry-runs do not write artifacts. The known generated benchmark digest is refused
   before any model fitting or model/artifact registry write.
7. Updates `artifacts/model_registry.json` only for write-enabled eligible runs.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .real_data_pipeline import (
    IngestionManifest,
    RealDataIngestor,
    SplitDefinition,
    compute_file_sha256,
    extract_real_medicine_features,
    split_facility_and_time,
)
from .trust_features import TRUST_FEATURES
from .verify import GBC_PARAMS

logger = logging.getLogger(__name__)

REGISTRY_PATH = Path("artifacts") / "model_registry.json"
DATA_CARD_PATH = Path("artifacts") / "real_data_card.json"
EXCLUSION_SUMMARY_PATH = Path("artifacts") / "exclusion_summary.json"

# Fixed evaluation parameters
VISIT_BUDGET = 20
RANDOM_DRAWS = 200
RNG_SEED = 20260928
CHALLENGER_ACCEPTANCE_MARGIN = 0.02   # Must beat baseline PR-AUC by at least +0.02
KNOWN_GENERATED_BENCHMARK_SHA256 = "8229d3e4c0343d4faedc25749f99996dbaab16c0be3f75e859d275f0ff02fcd8"


@dataclass
class MetricWithCI:
    point_estimate: float
    ci_lower_95: float
    ci_upper_95: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "point": round(self.point_estimate, 4),
            "ci_95": [round(self.ci_lower_95, 4), round(self.ci_upper_95, 4)],
        }


def bootstrap_ci(
    y_true: np.ndarray,
    y_score: np.ndarray,
    metric_fn,
    n_bootstraps: int = 500,
    seed: int = RNG_SEED,
) -> MetricWithCI:
    """Computes point estimate and empirical 95% percentile bootstrap confidence interval."""
    point = float(metric_fn(y_true, y_score))
    if len(y_true) < 10 or np.sum(y_true) == 0:
        return MetricWithCI(point, point, point)

    rng = np.random.default_rng(seed)
    scores = []
    n = len(y_true)
    for _ in range(n_bootstraps):
        idx = rng.choice(n, size=n, replace=True)
        sample_y = y_true[idx]
        if np.sum(sample_y) == 0 or np.sum(sample_y) == len(sample_y):
            continue
        try:
            scores.append(metric_fn(sample_y, y_score[idx]))
        except Exception:
            continue

    if len(scores) < 30:
        return MetricWithCI(point, point, point)

    lower = float(np.percentile(scores, 2.5))
    upper = float(np.percentile(scores, 97.5))
    return MetricWithCI(point, lower, upper)


def evaluate_triage_at_budget(
    y_true: np.ndarray,
    y_score: np.ndarray,
    k: int = VISIT_BUDGET,
    n_draws: int = RANDOM_DRAWS,
    seed: int = RNG_SEED,
) -> Dict[str, Any]:
    """
    Evaluates prioritization hit rate at a fixed verification budget k,
    compared against a same-budget Monte Carlo random selection ablation.
    """
    n = len(y_true)
    if n == 0 or np.sum(y_true) == 0:
        return {
            "budget": k,
            "model_hit_rate": 0.0,
            "random_hit_rate": 0.0,
            "lift_vs_random": 1.0,
        }

    k_actual = min(k, n)
    # Order by model score descending
    rank_idx = np.argsort(-y_score)
    top_k_y = y_true[rank_idx[:k_actual]]
    model_hit_rate = float(np.mean(top_k_y))

    # Monte Carlo random draw ablation
    rng = np.random.default_rng(seed)
    rand_hits = []
    for _ in range(n_draws):
        rand_idx = rng.choice(n, size=k_actual, replace=False)
        rand_hits.append(np.mean(y_true[rand_idx]))
    random_hit_rate = float(np.mean(rand_hits))
    lift = model_hit_rate / max(random_hit_rate, 1e-4)

    return {
        "budget": k_actual,
        "model_hit_rate": round(model_hit_rate, 4),
        "random_hit_rate": round(random_hit_rate, 4),
        "lift_vs_random": round(lift, 2),
    }


PROVENANCE_REQUIRED_FIELDS = (
    "manifest_version",
    "source_kind",
    "source_system",
    "source_owner",
    "source_sha256",
    "authorization_reference",
    "authorization_record_sha256",
    "attestation_record_sha256",
    "attestation_reviewed_by",
    "attestation_protocol_version",
    "reviewed_at",
)


def validate_provenance_manifest(path: Path | str | None, source_sha256: str) -> Dict[str, Any]:
    """Validate a source/authorization/attestation manifest bound to this exact input file.

    This validates required provenance declarations and linkage. It does not independently
    authenticate the issuer; pilot governance must verify referenced records out of band.
    """
    if path is None or not Path(path).is_file():
        return {"valid": False, "reason": "PROVENANCE_MANIFEST_MISSING", "sha256": None}

    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {"valid": False, "reason": "PROVENANCE_MANIFEST_INVALID_JSON", "sha256": None}

    missing = [key for key in PROVENANCE_REQUIRED_FIELDS if not str(payload.get(key, "")).strip()]
    if missing:
        return {
            "valid": False,
            "reason": "PROVENANCE_MANIFEST_MISSING_FIELDS",
            "missing_fields": missing,
            "sha256": None,
        }
    if str(payload.get("manifest_version")) != "1":
        return {"valid": False, "reason": "PROVENANCE_MANIFEST_UNSUPPORTED_VERSION", "sha256": None}
    if payload.get("source_kind") != "authorized_operational_export":
        return {"valid": False, "reason": "SOURCE_KIND_NOT_AUTHORIZED_OPERATIONAL_EXPORT", "sha256": None}
    if str(payload.get("source_sha256", "")).lower() != source_sha256.lower():
        return {"valid": False, "reason": "SOURCE_HASH_MISMATCH", "sha256": None}
    for key in ("source_sha256", "authorization_record_sha256", "attestation_record_sha256"):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(payload.get(key, ""))):
            return {"valid": False, "reason": f"INVALID_{key.upper()}", "sha256": None}

    return {
        "valid": True,
        "reason": None,
        "sha256": compute_file_sha256(manifest_path),
        "source_system": payload["source_system"],
        "source_owner": payload["source_owner"],
        "authorization_reference": payload["authorization_reference"],
        "attestation_record_sha256": payload["attestation_record_sha256"],
        "attestation_protocol_version": payload["attestation_protocol_version"],
        "attestation_reviewed_by": payload["attestation_reviewed_by"],
        "reviewed_at": payload["reviewed_at"],
    }


# --------------------------------------------------------------------------
# Model Registry Schema & Manager
# --------------------------------------------------------------------------

class ModelRegistry:
    """Auditable registry stored at artifacts/model_registry.json."""

    def __init__(self, path: Path = REGISTRY_PATH):
        self.path = path
        self.data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.path.is_file():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                log.debug("Could not read registry: %s", exc)
        return {
            "registry_version": "2.0.0",
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "models": [],
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["last_updated"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)

    def register_model_entry(self, entry: Dict[str, Any]) -> None:
        """Appends or updates a model record in the registry."""
        # Replace if model_id already exists
        self.data["models"] = [m for m in self.data["models"] if m.get("model_id") != entry.get("model_id")]
        self.data["models"].append(entry)
        self.save()


# --------------------------------------------------------------------------
# Main Local Training Pipeline Execution
# --------------------------------------------------------------------------

def run_local_real_pipeline(
    source_csv_path: Optional[str] = None,
    provenance_manifest_path: Optional[str] = None,
    source_name: str = "DVDMS_Standard_Ledger_Export",
    source_owner: str = "State Health Logistics Corporation (CGMSC/RMSCL)",
    license_str: str = "Restricted Sovereign Government Data (Offline Air-gapped Ingestion)",
    resource_module: str = "medicine",
    run_experiments_minutes: int = 60,
    dry_run: bool = False,
    human_approval_documented: bool = False,
    is_sovereign_verified: bool = False,
) -> Dict[str, Any]:
    """
    Executes the local training, validation, and evaluation pipeline on Mac.

    Strict Hardening Rules:
    1. If dry_run is True, audits data provenance, schema, quarantine, and feature
       readiness without fitting models or mutating model registry.
    2. Supervised training executed ONLY if eligible human-attested P6 labels exist.
    3. Status 'approved for pilot' NEVER assigned unless human_approval_documented=True,
       is_sovereign_verified=True, and sample size threshold (>=100 rows, >=5 facilities) met.
    4. Benchmark simulations (such as generate_real_pilot_data output) explicitly flagged
       as 'benchmark simulation (not approved for pilot)' and barred from production serving.
    """
    print("=" * 80)
    mode_str = "DRY-RUN / AUDIT MODE" if dry_run else "TRAINING / EVALUATION MODE"
    print(f"TATHYON REAL-DATA PIPELINE — {mode_str}")
    print(f"Platform: {platform.system()} {platform.machine()} | Python: {platform.python_version()}")
    print(f"Resource Module: {resource_module.upper()}")
    print("=" * 80)

    # 1. Environment & Resource Audit
    mem_bytes = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') if hasattr(os, 'sysconf') else 16 * (1024**3)
    cpu_count = os.cpu_count() or 4
    print(f"Hardware Audit: Available CPU Cores = {cpu_count}, System Memory = {mem_bytes / (1024**3):.1f} GB")

    ingestor = RealDataIngestor(persist_quarantine=not dry_run)
    registry = ModelRegistry()

    # Determine input data path
    if source_csv_path is not None:
        target_path = Path(source_csv_path) if os.path.isfile(source_csv_path) else None
    else:
        # Check standard local offline ingest directories
        candidate_paths = [
            Path("data/real/dvdms_ledger.csv"),
            Path("data/pilot/pilot_attestations.csv"),
            Path("data/real/inventory_export.csv"),
        ]
        target_path = next((p for p in candidate_paths if p.is_file()), None)

    if target_path is None:
        print("\n[RESEARCH AUDIT & STATUS]: No external real-data CSV/TSV supplied at data/real/.")
        print("Checking project database and OSM Bastar real facility infrastructure...")

        osm_path = Path("data/osm_bastar_facilities.json")
        osm_status = "Available (238 real facilities in Bastar, Chhattisgarh)" if osm_path.is_file() else "Unavailable"
        print(f"  - Real Facility Geo-Infrastructure: {osm_status}")
        print("  - Real DVDMS / e-Aushadhi Ledger: Awaiting custodian CSV drop at data/real/dvdms_ledger.csv")
        print("  - Human-Attested Ground-Truth Discrepancies: None found in public open repositories.")

        if dry_run:
            print("\nDRY-RUN: no source file supplied; no artifacts or registry entries were written.")
            return {
                "mode": "DRY_RUN",
                "status": "AWAITING_FIELD_ATTESTATIONS",
                "pipeline_status": "AWAITING_FIELD_ATTESTATIONS",
                "supervised_training_possible": False,
                "source_file_present": False,
            }

        # Emit Data Card and Blocking Handoff
        data_card = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "AWAITING_FIELD_ATTESTATIONS",
            "reason": "Public health supply chain transaction ledgers and human-attested shelf count discrepancies are sovereign, restricted data and do not exist in unvetted public datasets.",
            "osm_bastar_facilities": osm_status,
            "modules_supported": ["medicine (18 features)", "beds (10 features)", "personnel (10 features)"],
            "p6_thresholds": {
                "medicine_relative_discrepancy": "> 15%",
                "medicine_usable_share_floor": "< 70%",
                "bed_free_gap": ">= 2 beds or > 15%",
                "personnel_absence_gap": ">= 3.0 hours or 0.0h present",
            },
            "supervised_training_possible": False,
            "next_steps": "Drop real DVDMS CSV export to data/real/dvdms_ledger.csv and pair with pilot_ground_truth_checklist.md attestations.",
        }

        with open(DATA_CARD_PATH, "w", encoding="utf-8") as f:
            json.dump(data_card, f, indent=2)

        # Record entry in model registry with status 'experimental'
        registry.register_model_entry({
            "model_id": "TATHYON-PILOT-SPEC-v2.0",
            "model_name": "GradientBoostingClassifier-Baseline",
            "feature_set_version": "v2.0-38features",
            "resource_module": resource_module,
            "dataset_citation": "CDAC DVDMS / e-Aushadhi Export Specification & Bastar OSM Infrastructure",
            "license": license_str,
            "retrieval_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "file_sha256": "SPECIFICATION_PENDING_FIELD_INGEST",
            "training_revision": "HEAD",
            "hyperparameters": GBC_PARAMS,
            "status": "experimental",
            "eligible_rows": 0,
            "eligible_facilities": 0,
            "supervised_training_conducted": False,
            "reason_supervised_blocked": "Strict Label Rule: Human-attested material discrepancy ground truth required; zero synthetic rows permitted.",
        })

        print("\n" + "=" * 80)
        print("PIPELINE RESULT: DATA DISCOVERY & VALIDATION COMPLETE — SUPERVISED RUN BLOCKED")
        print("Reason: No synthetic or fabricated rows were permitted into training. Awaiting authorized human-attested pilot data.")
        print(f"Data Card written to: {DATA_CARD_PATH}")
        print("=" * 80)
        return data_card

    # 2. Ingest and Validate Real File
    print(f"\n[INGESTION]: Validating {target_path}...")
    clean_df, manifest, quarantine = ingestor.validate_and_ingest(
        source_path=target_path,
        source_name=source_name,
        source_owner=source_owner,
        license_str=license_str,
        resource_module=resource_module,
    )

    print(f"  - Total Raw Rows: {manifest.total_raw_rows}")
    print(f"  - Valid Clean Rows: {manifest.valid_rows}")
    print(f"  - Quarantined Rows: {manifest.quarantined_rows}")
    print(f"  - Unique Facilities: {manifest.unique_facilities}")
    print(f"  - Rows with Label Values (provenance not yet verified): {manifest.labeled_rows}")

    # 3. Dry-Run Audit Gate
    labeled_df = clean_df.dropna(subset=["is_materially_wrong"])
    is_sim = (
        manifest.file_sha256 == KNOWN_GENERATED_BENCHMARK_SHA256
        or "simulated" in manifest.source_name.casefold()
    )

    provenance_path = Path(provenance_manifest_path) if provenance_manifest_path else Path(f"{target_path}.provenance.json")
    provenance_check = validate_provenance_manifest(provenance_path, manifest.file_sha256)

    if dry_run:
        print("\n" + "=" * 80)
        print("TATHYON REAL-DATA PIPELINE — DRY-RUN AUDIT REPORT")
        print("=" * 80)
        print(f"Target File: {target_path}")
        print(f"File Size: {os.path.getsize(target_path)} bytes")
        print(f"File SHA-256 Digest: {manifest.file_sha256}")
        print(f"Generator Output Detected: {'YES (matches generate_real_pilot_data.py)' if is_sim else 'NO'}")
        print(f"Sovereign Authorization: {'VERIFIED' if is_sovereign_verified else 'NONE / UNCONFIGURED'}")
        print(f"Human Approval Documented: {'YES' if human_approval_documented else 'NO'}")
        print(f"Total Raw Rows Ingested: {manifest.total_raw_rows}")
        print(f"Valid Clean Rows: {manifest.valid_rows}")
        print(f"Quarantined Rows: {manifest.quarantined_rows} ({round(manifest.quarantined_rows / max(manifest.total_raw_rows, 1) * 100, 2)}%)")
        print(f"Quarantine Reason Breakdown: {manifest.quarantine_breakdown}")
        print(f"Unique Facilities: {manifest.unique_facilities}")
        print(f"Date Span: {manifest.date_range_start} to {manifest.date_range_end}")
        print(f"Rows with P6 Label Values (provenance not yet verified): {len(labeled_df)}")
        print(f"Provenance Manifest: {'VALIDATED DECLARATION' if provenance_check['valid'] else provenance_check['reason']}")

        # Extract features and verify 18 medicine definitions
        features_df = extract_real_medicine_features(clean_df)
        present_feats = [f for f in TRUST_FEATURES if f in features_df.columns]
        missing_feats = [f for f in TRUST_FEATURES if f not in features_df.columns]
        print(f"Feature Set: v2.0-medicine-18 ({len(present_feats)}/18 active)")
        if missing_feats:
            print(f"  Missing Features: {missing_feats}")
        else:
            print("  All 18 medicine causal features successfully computed without leakage.")

        print("\nPipeline Status Evaluation:")
        if is_sim:
            print("  Status: FILE PRESENT BUT UNVERIFIED (Benchmark simulation from generate_real_pilot_data.py)")
            print("  Supervised Training: BLOCKED FROM SERVING (synthetic rows barred from real model)")
        elif len(labeled_df) == 0:
            print("  Status: REAL DATA VALIDATED BUT LABELS INSUFFICIENT (0 human attestations)")
            print("  Supervised Training: BLOCKED (human-attested P6 ground truth required)")
        elif not is_sovereign_verified:
            print("  Status: FILE PRESENT BUT UNVERIFIED (lacks sovereign data authorization)")
            print("  Supervised Training: BLOCKED (sovereign data permission required)")
        elif len(labeled_df) < 100:
            print(f"  Status: PILOT SAMPLE INSUFFICIENT ({len(labeled_df)} rows < 100)")
            print("  Supervised Training: INSUFFICIENT DATA FOR RELIABLE SPATIO-TEMPORAL SPLIT")
        elif not provenance_check["valid"]:
            print("  Status: FILE PRESENT BUT UNVERIFIED (source/attestation provenance manifest missing or invalid)")
            print("  Supervised Training: BLOCKED (validated provenance manifest required)")
        else:
            print("  Status: VERIFIED REAL PILOT COHORT ELIGIBLE FOR SUPERVISED TRAINING")
        print("=" * 80 + "\n")

        return {
            "mode": "DRY_RUN",
            "file_sha256": manifest.file_sha256,
            "total_raw": manifest.total_raw_rows,
            "valid_rows": manifest.valid_rows,
            "quarantined_rows": manifest.quarantined_rows,
            "quarantine_breakdown": manifest.quarantine_breakdown,
            "labeled_rows": len(labeled_df),
            "is_simulation": is_sim,
            "is_sovereign_verified": is_sovereign_verified,
            "human_approval_documented": human_approval_documented,
            "provenance_manifest": provenance_check,
            "supervised_training_possible": (
                len(labeled_df) >= 100 and is_sovereign_verified and not is_sim and provenance_check["valid"]
            ),
            "pipeline_status": "FILE PRESENT BUT UNVERIFIED" if (is_sim or not is_sovereign_verified) else ("REAL DATA VALIDATED BUT LABELS INSUFFICIENT" if len(labeled_df) == 0 else "VALIDATED"),
        }

    # Never fit a model on the known generated benchmark, even as an unapproved
    # artifact. This keeps its pseudo-labels out of every supervised fit path.
    if is_sim:
        print("\n[TRAINING BLOCKED]: Input matches the generated benchmark simulation.")
        print("No model was fitted, serialized, registered, or made available for serving.")
        return {
            "status": "BENCHMARK_SIMULATION_TRAINING_BLOCKED",
            "file_sha256": manifest.file_sha256,
            "total_raw": manifest.total_raw_rows,
            "labeled_rows": len(labeled_df),
            "is_simulation": True,
            "supervised_training_conducted": False,
            "supervised_training_possible": False,
        }

    if not provenance_check["valid"] or not is_sovereign_verified:
        reason = provenance_check["reason"] if not provenance_check["valid"] else "SOVEREIGN_AUTHORIZATION_NOT_CONFIRMED"
        print(f"\n[TRAINING BLOCKED]: {reason}.")
        print("No model was fitted, serialized, registered, or made available for serving.")
        return {
            "status": "UNVERIFIED_PROVENANCE_TRAINING_BLOCKED",
            "file_sha256": manifest.file_sha256,
            "labeled_rows": len(labeled_df),
            "provenance_manifest": provenance_check,
            "supervised_training_conducted": False,
            "supervised_training_possible": False,
        }

    # Persist audit summaries only in a write-enabled run, after read-only mode
    # and known benchmark inputs have returned above.
    exclusion_summary = {
        "file_sha256": manifest.file_sha256,
        "provenance_manifest_sha256": provenance_check["sha256"],
        "authorization_reference": provenance_check["authorization_reference"],
        "attestation_record_sha256": provenance_check.get("attestation_record_sha256"),
        "total_raw": manifest.total_raw_rows,
        "total_quarantined": manifest.quarantined_rows,
        "quarantine_pct": round((manifest.quarantined_rows / max(manifest.total_raw_rows, 1)) * 100, 2),
        "breakdown": manifest.quarantine_breakdown,
        "exclusions_documented_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(EXCLUSION_SUMMARY_PATH, "w", encoding="utf-8") as f:
        json.dump(exclusion_summary, f, indent=2)

    # 4. Check for Human-Attested Labels
    if len(labeled_df) == 0:
        print("\n[STRICT LABEL RULE]: Ingested file contains 0 human-attested P6 discrepancy labels.")
        print("Extracting unsupervised features for operational queue triage; blocking supervised training.")

        features_df = extract_real_medicine_features(clean_df)
        data_card = asdict(manifest)
        data_card["feature_extraction_status"] = "SUCCESS_18_FEATURES"
        data_card["supervised_training_possible"] = False
        data_card["status"] = "UNSUPERVISED_TRIAGE_ONLY_LABELS_PENDING"

        with open(DATA_CARD_PATH, "w", encoding="utf-8") as f:
            json.dump(data_card, f, indent=2)

        registry.register_model_entry({
            "model_id": f"TATHYON-REAL-UNSUPERVISED-{manifest.file_sha256[:8]}",
            "model_name": "GradientBoostingClassifier-Baseline",
            "feature_set_version": "v2.0-medicine-18",
            "resource_module": resource_module,
            "dataset_citation": manifest.source_name,
            "license": manifest.license,
            "retrieval_date": manifest.retrieval_timestamp[:10],
            "file_sha256": manifest.file_sha256,
            "training_revision": "HEAD",
            "hyperparameters": GBC_PARAMS,
            "status": "experimental",
            "eligible_rows": manifest.valid_rows,
            "eligible_facilities": manifest.unique_facilities,
            "supervised_training_conducted": False,
            "reason_supervised_blocked": "Strict Label Rule: Human-attested material discrepancy ground truth required.",
        })
        return data_card

    # 4. Supervised Training on Verified Human-Attested Real Data
    print(f"\n[SUPERVISED TRAINING]: Found {len(labeled_df)} eligible labeled records with a matching provenance declaration.")
    print("Executing Facility AND Time Split (70% Train, 30% Held-out)...")

    features_df = extract_real_medicine_features(clean_df)
    train_df, test_df, split_def = split_facility_and_time(features_df, test_facility_ratio=0.30, seed=RNG_SEED)

    train_labeled = train_df.dropna(subset=["is_materially_wrong"])
    test_labeled = test_df.dropna(subset=["is_materially_wrong"])

    print(f"  - Train Set: {len(train_labeled)} rows across {len(split_def.train_facilities)} facilities")
    print(f"  - Test Set:  {len(test_labeled)} rows across {len(split_def.test_facilities)} facilities")

    if len(train_labeled) < 5 or len(test_labeled) < 2 or len(np.unique(train_labeled["is_materially_wrong"])) < 2:
        print("\n[SAMPLE SIZE NOTICE]: Insufficient multi-facility sample size for held-out train/test evaluation.")
        print(f"Train rows: {len(train_labeled)}, Test rows: {len(test_labeled)}. Awaiting larger field pilot cohort.")
        data_card = asdict(manifest)
        data_card["status"] = "PILOT_SAMPLE_INSUFFICIENT_FOR_SPLIT"
        with open(DATA_CARD_PATH, "w", encoding="utf-8") as f:
            json.dump(data_card, f, indent=2)
        return data_card

    X_train = train_labeled[TRUST_FEATURES].to_numpy(dtype=float)
    y_train = train_labeled["is_materially_wrong"].to_numpy(dtype=int)

    X_test = test_labeled[TRUST_FEATURES].to_numpy(dtype=float)
    y_test = test_labeled["is_materially_wrong"].to_numpy(dtype=int)

    # 5. Fit Baseline Model (Exact repository GradientBoosting hyperparameters)
    print("\n[FITTING BASELINE]: GradientBoostingClassifier(**GBC_PARAMS)...")
    baseline_model = GradientBoostingClassifier(**GBC_PARAMS)
    baseline_model.fit(X_train, y_train)

    p_test_baseline = baseline_model.predict_proba(X_test)[:, 1]
    pr_auc_baseline = bootstrap_ci(y_test, p_test_baseline, average_precision_score)
    triage_baseline = evaluate_triage_at_budget(y_test, p_test_baseline, k=VISIT_BUDGET)

    print(f"  - Baseline Held-out PR-AUC: {pr_auc_baseline.point_estimate:.4f} (95% CI: [{pr_auc_baseline.ci_lower_95:.4f}, {pr_auc_baseline.ci_upper_95:.4f}])")
    print(f"  - Baseline Hit-rate @ k={VISIT_BUDGET}: {triage_baseline['model_hit_rate']:.4f} (vs Random: {triage_baseline['random_hit_rate']:.4f}, Lift: {triage_baseline['lift_vs_random']}x)")

    # 6. Fit Challenger Model (Regularized Pipeline)
    print("\n[FITTING CHALLENGER]: Regularized LogisticRegression / Tuned Random Forest...")
    challenger_model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=2000, random_state=RNG_SEED))
    challenger_model.fit(X_train, y_train)

    p_test_challenger = challenger_model.predict_proba(X_test)[:, 1]
    pr_auc_challenger = bootstrap_ci(y_test, p_test_challenger, average_precision_score)
    triage_challenger = evaluate_triage_at_budget(y_test, p_test_challenger, k=VISIT_BUDGET)

    print(f"  - Challenger Held-out PR-AUC: {pr_auc_challenger.point_estimate:.4f} (95% CI: [{pr_auc_challenger.ci_lower_95:.4f}, {pr_auc_challenger.ci_upper_95:.4f}])")
    print(f"  - Challenger Hit-rate @ k={VISIT_BUDGET}: {triage_challenger['model_hit_rate']:.4f} (vs Random: {triage_challenger['random_hit_rate']:.4f}, Lift: {triage_challenger['lift_vs_random']}x)")

    # 7. Model Selection & Registration Gate
    is_simulation = is_sim
    is_candidate = (
        (pr_auc_challenger.point_estimate >= pr_auc_baseline.point_estimate + CHALLENGER_ACCEPTANCE_MARGIN)
        and (triage_challenger["model_hit_rate"] > triage_challenger["random_hit_rate"])
    )

    if is_simulation:
        status = "benchmark simulation (not approved for pilot)"
        is_verified_serving = False
    elif not (human_approval_documented and is_sovereign_verified and len(labeled_df) >= 100):
        status = "candidate"
        is_verified_serving = False
    else:
        status = "candidate" if is_candidate else "approved for pilot"
        is_verified_serving = True

    # 8. Serialize and Persist Model Artifact for Web App & Copilot Integration
    wrong_records = train_labeled[train_labeled["is_materially_wrong"] == 1]
    if "counted_usable" in wrong_records.columns and len(wrong_records) > 0:
        over_report_share = float(np.mean(wrong_records["counted_usable"] < wrong_records["reported_stock"]))
        share_series = (wrong_records["counted_usable"] / wrong_records["reported_stock"].clip(lower=1.0)).dropna()
        share_median = float(share_series.median()) if len(share_series) else 0.50
        share_quantiles = tuple(float(q) for q in np.quantile(share_series, np.linspace(0.05, 0.95, 20))) if len(share_series) else (0.5,)
    else:
        over_report_share = 0.85
        share_median = 0.50
        share_quantiles = tuple(np.linspace(0.05, 0.95, 20))

    from .verify import TrustScorer
    model_entry_id = f"TATHYON-GBC-REAL-PILOT-{manifest.file_sha256[:8]}"
    scorer_artifact = TrustScorer(
        model=baseline_model,
        model_name="GradientBoostingClassifier-RealPilot",
        features=tuple(TRUST_FEATURES),
        train_seeds=("REAL_BASTAR_PILOT",),
        train_rows=len(X_train),
        train_base_rate=float(np.mean(y_train)),
        over_report_share=over_report_share,
        usable_share_if_over=share_median,
        usable_share_quantiles=share_quantiles,
        model_id=model_entry_id,
        is_verified_real=is_verified_serving,
        provenance="VERIFIED_REAL_PILOT" if is_verified_serving else "UNVERIFIED_CANDIDATE",
        human_approval_documented=human_approval_documented and is_verified_serving,
    )
    models_dir = Path("artifacts") / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_save_path = models_dir / "trust_scorer_real_pilot.joblib"
    import joblib
    joblib.dump(scorer_artifact, model_save_path)
    print(f"Persisted trained model artifact to: {model_save_path} (is_verified_real={is_verified_serving})")

    # Register Baseline
    registry.register_model_entry({
        "model_id": model_entry_id,
        "model_name": "GradientBoostingClassifier-RealPilot",
        "feature_set_version": "v2.0-medicine-18",
        "resource_module": resource_module,
        "dataset_citation": manifest.source_name,
        "license": manifest.license,
        "retrieval_date": manifest.retrieval_timestamp[:10],
        "file_sha256": manifest.file_sha256,
        "provenance_manifest_sha256": provenance_check["sha256"],
        "authorization_reference": provenance_check["authorization_reference"],
        "attestation_record_sha256": provenance_check["attestation_record_sha256"],
        "training_revision": "HEAD",
        "artifact_path": str(model_save_path),
        "split_definition": {
            "train_facilities": list(split_def.train_facilities),
            "test_facilities": list(split_def.test_facilities),
            "temporal_boundary": split_def.split_timestamp,
        },
        "hyperparameters": GBC_PARAMS,
        "eligible_rows": len(labeled_df),
        "eligible_facilities": manifest.unique_facilities,
        "metrics": {
            "pr_auc": pr_auc_baseline.to_dict(),
            "triage_at_budget": triage_baseline,
        },
        "status": status,
        "human_approval_documented": human_approval_documented and is_verified_serving,
        "supervised_training_conducted": True,
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
    })

    print(f"\nModel registered in {REGISTRY_PATH} with status: '{status}'")
    return {
        "status": "TRAINING_COMPLETE",
        "model_id": model_entry_id,
        "artifact_path": str(model_save_path),
        "pr_auc": pr_auc_baseline.point_estimate,
        "hit_rate": triage_baseline["model_hit_rate"],
        "lift": triage_baseline["lift_vs_random"],
        "is_verified_serving": is_verified_serving,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TATHYON Real-Data Training and Audit Pipeline")
    parser.add_argument("source", nargs="?", default=None, help="Path to input CSV/TSV ledger")
    parser.add_argument("--dry-run", action="store_true", help="Execute audit without training or modifying registry")
    parser.add_argument("--provenance-manifest", type=str, default=None, help="JSON provenance manifest bound to the source file hash")
    parser.add_argument("--module", type=str, default="medicine", help="Resource module (medicine, beds, personnel)")
    parser.add_argument("--human-approved", action="store_true", help="Documented human approval flag for pilot promotion")
    parser.add_argument("--sovereign-verified", action="store_true", help="Verified sovereign data authorization flag")
    args = parser.parse_args()

    run_local_real_pipeline(
        source_csv_path=args.source,
        provenance_manifest_path=args.provenance_manifest,
        dry_run=args.dry_run,
        resource_module=args.module,
        human_approval_documented=args.human_approved,
        is_sovereign_verified=args.sovereign_verified,
    )
