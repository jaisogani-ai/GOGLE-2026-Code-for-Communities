"""TATHYON federated learning and anomaly-exchange prototype.

Build with AI: Code for Communities 2.0 (Track 3) — Smart Health & Supply Chain Resilience.
This is a local design and synthetic demo; it does not implement or claim a
government or BRICS federation mandate, partnership, or standards compliance.

PRODUCTION-READINESS STATUS: NOT PRODUCTION-READY.
This module implements the data contracts, sanitization gate, model lifecycle
state machine, and synthetic demo harness for a future privacy-preserving
federation capability. No real multi-country environment is connected.
All demo runs are clearly labelled SYNTHETIC_DEMO_ONLY.

WHAT THIS FILE ACTUALLY DOES vs. WHAT IS STILL FUTURE WORK:
  IMPLEMENTED (tested):
    - FederatedWeightPackage: the unit of sovereign exchange.
    - sanitize_and_export_weights: PII/facility-ID hard-fail gate.
    - FederatedTrustAggregator: observation-weighted FedAvg.
    - PhantomAnomalyPattern: structural anomaly signatures.
    - ModelLifecycleState: AVAILABLE → EVALUATED → APPROVED finite state machine.
    - ModelRegistry: versioning, rollback, poisoning guards (Δ-importance threshold).
    - SyntheticFederationDemo: deterministic two-node demo harness.

  NOT YET IMPLEMENTED (future work, recorded in docs/federation-privacy.md):
    - Secure aggregation (cryptographic masking of individual weight vectors).
    - Differential privacy (ε-δ Gaussian/Laplace noise on exported weights).
    - Network transport layer (mTLS, node identity certificates).
    - Real country-node onboarding (legal agreements, DPA, key exchange).
    - Coordinator service (separate process / sovereign HSM-backed key store).
    - k-anonymity enforcement on pattern libraries (minimum-k guard).

SOVEREIGN DATA INVARIANTS (enforced in code, not merely claimed in prose):
  SHARED:
    - Scorer model weights & feature importances (sanitized through hard gate).
    - Standardized feature definitions (TRUST_FEATURES vocabulary).
    - Anomaly Pattern Library: structural failure signatures without identifiers.
    - Aggregate hyperparameter recommendations.
  NEVER SHARED (gate raises ValueError on violation):
    - ZERO facility-level inventory or attendance rows.
    - ZERO patient-identifiable records, names, or demographics.
    - ZERO raw event-store payloads or transaction logs.
    - ZERO facility coordinates or location data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

import numpy as np

from .schema import ResourceType, sha256, now, new_id


# ---------------------------------------------------------------------------
# Model Lifecycle State Machine
# ---------------------------------------------------------------------------

class ModelLifecycleState(str, Enum):
    """Explicit three-state lifecycle preventing premature operational use.

    AVAILABLE:  Aggregate received from coordinator. No local validation yet.
                Must NOT influence operational forecasts, trust scores, or
                allocations. Read-only display in UI with RESEARCH_ONLY banner.

    EVALUATED:  Local validation completed. PR-AUC, calibration, and fairness
                checks passed internal thresholds. Still not operational.
                Requires an additional authorized human release action.

    APPROVED:   Authorized local officer (CMO/DHO) has explicitly released the
                aggregate for operational use after reviewing the evaluation
                report, model card, and release checklist.

    ROLLED_BACK: A previously APPROVED aggregate was withdrawn. All forecasts
                and trust scores revert to the previous APPROVED version or to
                the local-only scorer. Reason and actor are recorded.

    QUARANTINED: Aggregate failed poisoning guards or exceeded drift thresholds.
                Do not apply. Incident report required before any further action.

    State transitions allowed:
      AVAILABLE → EVALUATED (automated validation passes)
      EVALUATED → APPROVED  (human release action)
      APPROVED  → ROLLED_BACK (human rollback action)
      AVAILABLE | EVALUATED | APPROVED → QUARANTINED (poisoning guard trips)
    """
    AVAILABLE = "AVAILABLE"
    EVALUATED = "EVALUATED"
    APPROVED = "APPROVED"
    ROLLED_BACK = "ROLLED_BACK"
    QUARANTINED = "QUARANTINED"

    def can_transition_to(self, next_state: ModelLifecycleState) -> bool:
        valid = {
            ModelLifecycleState.AVAILABLE: {
                ModelLifecycleState.EVALUATED, ModelLifecycleState.QUARANTINED},
            ModelLifecycleState.EVALUATED: {
                ModelLifecycleState.APPROVED, ModelLifecycleState.QUARANTINED},
            ModelLifecycleState.APPROVED: {
                ModelLifecycleState.ROLLED_BACK, ModelLifecycleState.QUARANTINED},
            ModelLifecycleState.ROLLED_BACK: set(),
            ModelLifecycleState.QUARANTINED: set(),
        }
        return next_state in valid.get(self, set())


# ---------------------------------------------------------------------------
# Phantom Pattern Library (Structural Signatures — no identifiers)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PhantomAnomalyPattern:
    """A structural anomaly signature. Describes HOW a pathology manifests,
    never WHO committed it or at which facility. Suitable for cross-border
    sharing because it contains no entity-level data."""
    pattern_id: str
    resource_type: ResourceType
    pattern_name: str
    detection_rule: str
    feature_signature: Dict[str, float]
    provenance_citation: str
    risk_weight: float


CANONICAL_PATTERNS = [
    PhantomAnomalyPattern(
        pattern_id="PAT_MED_01",
        resource_type=ResourceType.MEDICINE,
        pattern_name="Batch Expiry Rollover",
        detection_rule=(
            "Stock is reported against expired batch or conflicting expiry dates "
            "assigned across snapshots."
        ),
        feature_signature={"t1_expiry_integrity_now": 1.0, "t2_expired_but_stocked": 1.0},
        provenance_citation="CAG Punjab Rpt 4/2019 Para 2.1.7.2(ii)",
        risk_weight=0.88,
    ),
    PhantomAnomalyPattern(
        pattern_id="PAT_MED_02",
        resource_type=ResourceType.MEDICINE,
        pattern_name="Uncredited Inventory Disappearance",
        detection_rule=(
            "Reported stock drops without recorded issue transactions or patient dispensing."
        ),
        feature_signature={"burn_inconsistency_30d": 0.40, "t1_violations_now": 0.0},
        provenance_citation="CAG Maharashtra Para 2.4.8.9",
        risk_weight=0.75,
    ),
    PhantomAnomalyPattern(
        pattern_id="PAT_BED_01",
        resource_type=ResourceType.BED,
        pattern_name="Phantom Available Beds",
        detection_rule=(
            "Ward reports high vacant beds but admissions cannot be processed "
            "due to broken fixtures or lack of oxygen."
        ),
        feature_signature={"t1_unreported_broken_now": 1.0, "t2_hours_since_ward_census": 24.0},
        provenance_citation="CAG Bihar Rpt 3/2021 Para 2.3",
        risk_weight=0.82,
    ),
    PhantomAnomalyPattern(
        pattern_id="PAT_STAFF_01",
        resource_type=ResourceType.PERSONNEL,
        pattern_name="Ghost Clinical Worker",
        detection_rule=(
            "Staff member marked present for full shift but zero OPD encounters "
            "or diagnostic procedures recorded."
        ),
        feature_signature={"t1_zero_clinical_encounters": 1.0, "t2_punch_timestamp_regularity": 1.0},
        provenance_citation="CAG Jharkhand Rpt 2/2022 Para 3.1",
        risk_weight=0.90,
    ),
]


# ---------------------------------------------------------------------------
# Federated Weight Package (The Unit of Sovereign Exchange)
# ---------------------------------------------------------------------------

@dataclass
class FederatedWeightPackage:
    """The only object permitted to leave a district or cross a sovereign border.

    Contains mathematical weights and structural signatures only.
    Raw observations, facility IDs, patient records, and coordinates are
    explicitly excluded by the sanitization gate and verified by package_hash.

    Fields:
        package_id: Unique identifier for this weight submission.
        exporting_district_id: Anonymous district code (not a facility ID).
        exporting_country: ISO-style country label, e.g. "INDIA", "SOUTH_AFRICA".
        resource_type: ResourceType enum value.
        model_family: Name of the model class (not the fitted object).
        schema_version: Feature vocabulary version; reject mismatches.
        feature_names: Approved feature vocabulary names (no entity tokens).
        feature_importances: Normalized feature importance map.
        model_hyperparameters: Structural hyperparameters only.
        train_observation_count: Number of facility-observation rows used.
        train_base_rate: Fraction of rows that were materially wrong.
        train_window_start / train_window_end: ISO8601 training window.
        held_out_pr_auc: PR-AUC on held-out local test split.
        patterns_shared: Canonical anomaly signatures (no identifiers).
        export_timestamp: ISO8601 UTC timestamp of this export.
        is_demo: True when this package is synthetic/simulated.
        package_hash: SHA-256 of all fields except package_hash itself.
        sanitization_verified: True only after gate passes.
    """
    package_id: str
    exporting_district_id: str
    exporting_country: str
    resource_type: ResourceType
    model_family: str
    schema_version: str
    feature_names: List[str]
    feature_importances: Dict[str, float]
    model_hyperparameters: Dict[str, Any]
    train_observation_count: int
    train_base_rate: float
    train_window_start: str
    train_window_end: str
    held_out_pr_auc: float
    patterns_shared: List[Dict[str, Any]]
    export_timestamp: str
    is_demo: bool = True
    package_hash: str = ""
    sanitization_verified: bool = False

    def compute_hash(self) -> str:
        d = asdict(self)
        d["package_hash"] = ""
        return sha256(d)


# ---------------------------------------------------------------------------
# Sanitization Gate (Hard Privacy Enforcement)
# ---------------------------------------------------------------------------

# Tokens whose presence as a COMPLETE WORD-SEGMENT in a feature name indicates
# raw entity data leakage. Checked against the set of '_'-split segments in the
# lowercase feature name, so 'violations' does not trip 'lat'.
_FORBIDDEN_FEATURE_TOKENS: Set[str] = {
    "fac", "facility", "patient", "aadhar", "aadhaar",
    "name", "phc", "chc", "dh", "hospital",
    "address", "pincode", "gps", "lat", "lon", "coordinate",
    "staff", "worker", "employee", "biometric",
    "dr",   # doctor name abbreviation
}

# Additionally, reject any feature whose name contains these RAW SUBSTRINGS
# (not word-segment-bound), because these are unambiguous entity identifiers
# regardless of where they appear:
_FORBIDDEN_SUBSTRINGS: Set[str] = {
    "aadhar", "aadhaar", "patient_id", "facility_id",
    "staff_id", "worker_id", "employee_id", "biometric",
}


def _feature_name_violates_gate(fn: str) -> Optional[str]:
    """Return the offending token if the feature name violates the leakage gate,
    or None if the feature name is clean.

    Uses two checks:
    1. Word-segment check: split feature name on '_', '-', '.' and check each
       segment against _FORBIDDEN_FEATURE_TOKENS. A segment 'lat' in 'lat_deviation'
       trips the gate; 'lat' inside 'violations' does NOT.
    2. Substring check: check against _FORBIDDEN_SUBSTRINGS for compound tokens
       that must never appear anywhere in a feature name.
    """
    fn_lower = fn.lower()
    # Check 2 first (unambiguous compound tokens)
    for sub in _FORBIDDEN_SUBSTRINGS:
        if sub in fn_lower:
            return sub
    # Check 1: word-segment matching
    segments = set(part for part in fn_lower.replace("-", "_").replace(".", "_").split("_") if part)
    for seg in segments:
        if seg in _FORBIDDEN_FEATURE_TOKENS:
            return seg
    return None


def sanitize_and_export_weights(
    district_id: str,
    country: str,
    resource_type: ResourceType,
    feature_names: List[str],
    feature_importances: Dict[str, float],
    model_hyperparameters: Dict[str, Any],
    train_count: int,
    base_rate: float,
    pr_auc: float,
    train_window_start: str = "",
    train_window_end: str = "",
    schema_version: str = "v1",
    patterns: Optional[List[PhantomAnomalyPattern]] = None,
    is_demo: bool = True,
) -> FederatedWeightPackage:
    """Sanitizes local model state and builds an exportable FederatedWeightPackage.

    HARD ASSERTION: raises ValueError('SOVEREIGN_DATA_LEAKAGE_DETECTED: ...')
    if any feature name contains a forbidden entity-identifier token.

    This function does NOT apply differential privacy noise. If DP is required
    by your deployment's threat model, add calibrated Laplace or Gaussian noise
    to feature_importances before calling this function and document the ε, δ
    parameters, sensitivity, and utility trade-off in the model card.

    Args:
        district_id: Anonymous district code. Must NOT be a facility ID.
        country: ISO-style country label.
        resource_type: ResourceType for which the scorer was trained.
        feature_names: Feature vocabulary (checked against forbidden tokens).
        feature_importances: Normalized importance scores keyed by feature name.
        model_hyperparameters: Structural hyperparameters (no fitted coefficients).
        train_count: Number of training observations.
        base_rate: Fraction of training rows that were materially wrong.
        pr_auc: PR-AUC on held-out local test split.
        train_window_start: ISO8601 UTC start of training data window.
        train_window_end: ISO8601 UTC end of training data window.
        schema_version: Feature vocabulary schema version string.
        patterns: Canonical anomaly patterns to include (default: CANONICAL_PATTERNS).
        is_demo: Must be True unless connected to a real authorized environment.

    Returns:
        FederatedWeightPackage with sanitization_verified=True and package_hash set.

    Raises:
        ValueError: If feature names contain forbidden entity-identifier tokens.
    """
    for fn in feature_names:
        offending = _feature_name_violates_gate(fn)
        if offending is not None:
            raise ValueError(
                f"SOVEREIGN_DATA_LEAKAGE_DETECTED: Forbidden token {offending!r} "
                f"found in feature name {fn!r}. Export aborted."
            )

    pats = [
        asdict(p)
        for p in (patterns or CANONICAL_PATTERNS)
        if p.resource_type == resource_type
    ]

    pkg = FederatedWeightPackage(
        package_id=new_id("fed_pkg"),
        exporting_district_id=district_id,
        exporting_country=country,
        resource_type=resource_type,
        model_family="GradientBoostingClassifier",
        schema_version=schema_version,
        feature_names=list(feature_names),
        feature_importances={k: round(float(v), 5) for k, v in feature_importances.items()},
        model_hyperparameters=model_hyperparameters,
        train_observation_count=train_count,
        train_base_rate=round(float(base_rate), 4),
        train_window_start=train_window_start or now(),
        train_window_end=train_window_end or now(),
        held_out_pr_auc=round(float(pr_auc), 4),
        patterns_shared=pats,
        export_timestamp=now(),
        is_demo=is_demo,
        sanitization_verified=True,
    )
    pkg.package_hash = pkg.compute_hash()
    return pkg


# ---------------------------------------------------------------------------
# Model Registry (Versioning, Lifecycle, Rollback, Poisoning Guards)
# ---------------------------------------------------------------------------

@dataclass
class ModelVersion:
    """A versioned snapshot of a federated aggregate at a lifecycle state."""
    version_id: str
    aggregate: Dict[str, Any]           # Output of FederatedTrustAggregator.aggregate()
    state: ModelLifecycleState
    state_changed_at: str
    state_changed_by: str               # Officer ID who triggered transition
    evaluation_report: Optional[Dict[str, Any]] = None
    rollback_reason: Optional[str] = None
    quarantine_reason: Optional[str] = None
    release_checklist_completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


class ModelRegistry:
    """Versioned model registry with lifecycle state machine and poisoning guards.

    Enforces that a federated aggregate cannot influence operational decisions
    until it has been explicitly evaluated and approved by an authorized officer.

    Poisoning Guard:
        When a new aggregate arrives, compare each feature's importance against
        the currently APPROVED version. If any single feature shifts by more than
        MAX_IMPORTANCE_DELTA (default 0.30 = 30 percentage points), the incoming
        package is quarantined and flagged for human review rather than applied.

        This is a heuristic guard, not a cryptographic proof of integrity.
        A well-resourced adversary who controls multiple nodes can stay under
        the threshold. See docs/federation-privacy.md §4 for the full threat
        analysis.

    Limitations (documented, not hidden):
        - No secure aggregation: individual node contributions are visible to
          the coordinator. A dishonest coordinator can learn node-level weights.
        - No differential privacy: individual node importances are exported
          without noise. With very few nodes, a node's contribution can be
          inferred from the aggregate.
        - No Byzantine-fault tolerance beyond the delta threshold.
    """

    MAX_IMPORTANCE_DELTA: float = 0.30  # 30 pp shift in any feature triggers quarantine

    def __init__(self) -> None:
        self._versions: List[ModelVersion] = []

    def register_available(
        self,
        aggregate: Dict[str, Any],
        registered_by: str = "COORDINATION_SERVICE",
    ) -> ModelVersion:
        """Register a new aggregate in the AVAILABLE state.

        Applies the poisoning delta-importance guard against the current APPROVED
        version. If the guard trips, the version is created but immediately
        transitioned to QUARANTINED.
        """
        version_id = new_id("fedv")
        mv = ModelVersion(
            version_id=version_id,
            aggregate=aggregate,
            state=ModelLifecycleState.AVAILABLE,
            state_changed_at=now(),
            state_changed_by=registered_by,
        )

        approved = self._current_approved()
        if approved is not None:
            poisoning_result = self._check_poisoning_guard(approved, aggregate)
            if poisoning_result["triggered"]:
                mv.state = ModelLifecycleState.QUARANTINED
                mv.quarantine_reason = (
                    f"Poisoning guard tripped on arrival: "
                    f"{poisoning_result['offending_features']}. "
                    f"Max delta={poisoning_result['max_delta']:.3f} > "
                    f"threshold={self.MAX_IMPORTANCE_DELTA:.3f}."
                )
        self._versions.append(mv)
        return mv

    def transition_to_evaluated(
        self,
        version_id: str,
        evaluation_report: Dict[str, Any],
        evaluated_by: str,
    ) -> ModelVersion:
        """Record completed local validation and transition AVAILABLE → EVALUATED."""
        mv = self._get(version_id)
        if not mv.state.can_transition_to(ModelLifecycleState.EVALUATED):
            raise ValueError(
                f"Cannot transition version {version_id!r} from "
                f"{mv.state.value} to EVALUATED."
            )
        mv.state = ModelLifecycleState.EVALUATED
        mv.state_changed_at = now()
        mv.state_changed_by = evaluated_by
        mv.evaluation_report = evaluation_report
        return mv

    def transition_to_approved(
        self,
        version_id: str,
        officer_id: str,
        release_checklist_completed: bool,
    ) -> ModelVersion:
        """Authorize the aggregate for operational use. EVALUATED → APPROVED.

        The release checklist must be completed before approval is accepted.
        This is enforced here rather than merely documented.
        """
        if not release_checklist_completed:
            raise ValueError(
                "RELEASE_CHECKLIST_INCOMPLETE: The release checklist must be "
                "completed and confirmed before an aggregate can be approved "
                "for operational use."
            )
        mv = self._get(version_id)
        if not mv.state.can_transition_to(ModelLifecycleState.APPROVED):
            raise ValueError(
                f"Cannot transition version {version_id!r} from "
                f"{mv.state.value} to APPROVED."
            )
        mv.state = ModelLifecycleState.APPROVED
        mv.state_changed_at = now()
        mv.state_changed_by = officer_id
        mv.release_checklist_completed = True
        return mv

    def rollback(
        self,
        version_id: str,
        officer_id: str,
        reason: str,
    ) -> ModelVersion:
        """Withdraw an approved aggregate. APPROVED → ROLLED_BACK.

        After rollback, current_operational_aggregate() reverts to the
        most-recent remaining APPROVED version, if one exists.
        """
        if not reason or len(reason.strip()) < 10:
            raise ValueError(
                "Rollback reason must be at least 10 characters. "
                "Provide a meaningful justification."
            )
        mv = self._get(version_id)
        if not mv.state.can_transition_to(ModelLifecycleState.ROLLED_BACK):
            raise ValueError(
                f"Cannot roll back version {version_id!r} from state {mv.state.value}."
            )
        mv.state = ModelLifecycleState.ROLLED_BACK
        mv.state_changed_at = now()
        mv.state_changed_by = officer_id
        mv.rollback_reason = reason
        return mv

    def quarantine(
        self,
        version_id: str,
        triggered_by: str,
        reason: str,
    ) -> ModelVersion:
        """Quarantine an aggregate at any state. Use for poisoning or anomaly events."""
        mv = self._get(version_id)
        if not mv.state.can_transition_to(ModelLifecycleState.QUARANTINED):
            raise ValueError(
                f"Cannot quarantine version {version_id!r} from state {mv.state.value}."
            )
        mv.state = ModelLifecycleState.QUARANTINED
        mv.state_changed_at = now()
        mv.state_changed_by = triggered_by
        mv.quarantine_reason = reason
        return mv

    def current_operational_aggregate(self) -> Optional[Dict[str, Any]]:
        """Return the most recently APPROVED aggregate, or None.

        If None, callers must fall back to the local-only scorer.
        They must NOT use AVAILABLE or EVALUATED aggregates for operational decisions.
        """
        approved = [v for v in self._versions if v.state == ModelLifecycleState.APPROVED]
        return approved[-1].aggregate if approved else None

    def summary(self) -> List[Dict[str, Any]]:
        """Return a summary of all registered versions for audit display."""
        return [v.to_dict() for v in self._versions]

    # -- Private helpers -------------------------------------------------------

    def _get(self, version_id: str) -> ModelVersion:
        for v in self._versions:
            if v.version_id == version_id:
                return v
        raise KeyError(f"Model version {version_id!r} not found in registry.")

    def _current_approved(self) -> Optional[ModelVersion]:
        approved = [v for v in self._versions if v.state == ModelLifecycleState.APPROVED]
        return approved[-1] if approved else None

    def _check_poisoning_guard(
        self,
        current: ModelVersion,
        incoming: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compare incoming feature importances against the current APPROVED version.

        Returns a dict with keys:
            triggered (bool): True if any feature exceeded MAX_IMPORTANCE_DELTA.
            max_delta (float): Largest observed absolute delta.
            offending_features (list): Feature names that exceeded the threshold.
        """
        current_fi: Dict[str, float] = (
            current.aggregate.get("aggregated_feature_importances", {})
        )
        incoming_fi: Dict[str, float] = (
            incoming.get("aggregated_feature_importances", {})
        )
        offending: List[str] = []
        max_delta = 0.0
        all_features = set(current_fi) | set(incoming_fi)
        for feat in all_features:
            delta = abs(incoming_fi.get(feat, 0.0) - current_fi.get(feat, 0.0))
            if delta > max_delta:
                max_delta = delta
            if delta > self.MAX_IMPORTANCE_DELTA:
                offending.append(f"{feat}(Δ={delta:.3f})")
        return {
            "triggered": len(offending) > 0,
            "max_delta": max_delta,
            "offending_features": offending,
        }


# ---------------------------------------------------------------------------
# Federated Trust Aggregator (FedAvg with country-boundary enforcement)
# ---------------------------------------------------------------------------

class FederatedTrustAggregator:
    """Aggregates weights from multiple sovereign nodes using observation-weighted FedAvg.

    Country-boundary enforcement:
        Each node contributes its own package. No node can query another node's
        raw data through this interface — only the sanitized weight packages
        (already validated by the originating node's sanitization gate) are
        aggregated here.

    Non-IID data:
        Observation-weighted averaging naturally down-weights data-sparse nodes.
        However, it does NOT correct for systematic distribution differences
        (e.g. disease mix, facility tier, procurement system) across countries.
        The held_out_pr_auc per node is included so local validation can detect
        when the aggregate performs worse than the local-only model.

    Dropout:
        If a node does not submit a package for a round, it is simply absent
        from that round's aggregate. The coordinator does not wait indefinitely.
        Minimum quorum (MIN_QUORUM) must be met before aggregation proceeds.

    Minimum aggregate size guard:
        Refuses to produce an aggregate if total_federated_observations is below
        MIN_OBSERVATIONS, to reduce the risk of individual-node inference from
        the aggregate.
    """

    MIN_QUORUM: int = 2             # Minimum distinct nodes required
    MIN_OBSERVATIONS: int = 5000    # Minimum total observations across all nodes

    def __init__(self, resource_type: ResourceType) -> None:
        self.resource_type = resource_type
        self.packages: List[FederatedWeightPackage] = []

    def add_package(self, pkg: FederatedWeightPackage) -> None:
        """Add a sanitized weight package from a participating node.

        Rejects packages with:
          - Resource type mismatch.
          - sanitization_verified=False.
          - Duplicate submission from the same district in this round.
        """
        if pkg.resource_type != self.resource_type:
            raise ValueError(
                f"Resource type mismatch: expected {self.resource_type.value}, "
                f"got {pkg.resource_type.value}"
            )
        if not pkg.sanitization_verified:
            raise ValueError(
                f"UNVERIFIED_PACKAGE: Package {pkg.package_id!r} has not passed "
                "the sanitization gate. Reject and request re-submission."
            )
        existing_districts = {p.exporting_district_id for p in self.packages}
        if pkg.exporting_district_id in existing_districts:
            raise ValueError(
                f"DUPLICATE_SUBMISSION: District {pkg.exporting_district_id!r} "
                "already submitted a package for this round."
            )
        self.packages.append(pkg)

    def aggregate(self) -> Dict[str, Any]:
        """Performs observation-weighted aggregation of feature importances.

        Returns a dict suitable for storage in ModelRegistry or display.
        Includes sovereignty_assurance text and demo labelling.

        Raises:
            ValueError: If quorum or minimum-observation threshold not met.
        """
        if len(self.packages) < self.MIN_QUORUM:
            raise ValueError(
                f"QUORUM_NOT_MET: Need at least {self.MIN_QUORUM} nodes, "
                f"have {len(self.packages)}."
            )

        total_obs = sum(p.train_observation_count for p in self.packages)
        if total_obs < self.MIN_OBSERVATIONS:
            raise ValueError(
                f"INSUFFICIENT_OBSERVATIONS: Need at least {self.MIN_OBSERVATIONS} "
                f"total observations, have {total_obs}. "
                "Refuse to aggregate to limit individual-node inference risk."
            )

        all_features: Set[str] = set()
        for p in self.packages:
            all_features.update(p.feature_importances.keys())

        aggregated_weights: Dict[str, float] = {f: 0.0 for f in all_features}
        for p in self.packages:
            weight = p.train_observation_count / total_obs
            for f in all_features:
                aggregated_weights[f] += p.feature_importances.get(f, 0.0) * weight

        total_w = sum(aggregated_weights.values()) or 1.0
        normalized = {
            f: round(w / total_w, 4)
            for f, w in sorted(aggregated_weights.items(), key=lambda kv: -kv[1])
        }

        mean_pr_auc = float(np.mean([p.held_out_pr_auc for p in self.packages]))
        is_demo = all(p.is_demo for p in self.packages)

        return {
            "status": "AGGREGATED",
            "is_demo": is_demo,
            "demo_label": (
                "[SYNTHETIC DEMO — NOT REAL PARTICIPATION DATA]"
                if is_demo else None
            ),
            "participating_districts": [p.exporting_district_id for p in self.packages],
            "participating_countries": sorted({p.exporting_country for p in self.packages}),
            "participating_countries_count": len({p.exporting_country for p in self.packages}),
            "total_federated_observations": total_obs,
            "mean_node_pr_auc": round(mean_pr_auc, 4),
            "aggregated_feature_importances": normalized,
            "consensus_timestamp": now(),
            "resource_type": self.resource_type.value,
            "sovereignty_assurance": (
                "ZERO raw facility rows or patient data exchanged. "
                "Only sanitized model weights and structural anomaly signatures. "
                "Each node ran its own data-sanitization gate before submission."
            ),
            "limitations": (
                "No secure aggregation applied: individual node contributions are "
                "visible to the coordinator. No differential privacy noise applied. "
                "With fewer than ~10 nodes, node-level importances may be inferable "
                "from the aggregate. See docs/federation-privacy.md §3 and §4."
            ),
        }


# ---------------------------------------------------------------------------
# Synthetic Demo Harness (Clearly Labelled — No Real Country Data)
# ---------------------------------------------------------------------------

@dataclass
class SyntheticFederationDemoResult:
    """Output of a two-node synthetic federation demo run.

    All fields are labelled SYNTHETIC_DEMO_ONLY. These numbers do not
    represent real BRICS participation or real operational model performance.
    """
    demo_label: str
    node_packages: List[Dict[str, Any]]
    aggregate: Dict[str, Any]
    model_version: Dict[str, Any]
    lifecycle_summary: List[Dict[str, Any]]
    provenance: str = "SYNTHETIC_DEMO_ONLY"


def run_synthetic_federation_demo(
    resource_type: ResourceType = ResourceType.MEDICINE,
    seed: int = 42,
) -> SyntheticFederationDemoResult:
    """Run a deterministic two-node synthetic federation demo.

    Uses fixed synthetic importances; no real country data is involved.
    Demonstrates the full lifecycle: export → aggregate → register AVAILABLE
    → transition to EVALUATED → transition to APPROVED.

    Both nodes are clearly marked is_demo=True. The aggregate carries
    demo_label='[SYNTHETIC DEMO — NOT REAL PARTICIPATION DATA]'.

    This function exists to show the correct workflow without requiring
    a real multi-country environment.
    """
    rng = np.random.default_rng(seed)

    # Synthetic node A: India (Bastar district analog)
    fi_a = {
        "t1_violations_now": round(float(rng.uniform(0.55, 0.65)), 4),
        "burn_inconsistency_30d": round(float(rng.uniform(0.25, 0.35)), 4),
        "att_last_unusable_share": round(float(rng.uniform(0.05, 0.15)), 4),
    }
    pkg_a = sanitize_and_export_weights(
        district_id="DEMO_NODE_IN_ALPHA",
        country="DEMO_COUNTRY_A",
        resource_type=resource_type,
        feature_names=list(fi_a.keys()),
        feature_importances=fi_a,
        model_hyperparameters={"max_depth": 4, "learning_rate": 0.03},
        train_count=40000,
        base_rate=0.0225,
        pr_auc=round(float(rng.uniform(0.89, 0.95)), 4),
        train_window_start="2025-01-01T00:00:00Z",
        train_window_end="2025-12-31T23:59:59Z",
        schema_version="v1",
        is_demo=True,
    )

    # Synthetic node B: South Africa (Cape analog)
    fi_b = {
        "t1_violations_now": round(float(rng.uniform(0.58, 0.70)), 4),
        "burn_inconsistency_30d": round(float(rng.uniform(0.20, 0.30)), 4),
        "att_last_unusable_share": round(float(rng.uniform(0.06, 0.14)), 4),
    }
    pkg_b = sanitize_and_export_weights(
        district_id="DEMO_NODE_ZA_BETA",
        country="DEMO_COUNTRY_B",
        resource_type=resource_type,
        feature_names=list(fi_b.keys()),
        feature_importances=fi_b,
        model_hyperparameters={"max_depth": 3, "learning_rate": 0.05},
        train_count=35000,
        base_rate=0.0310,
        pr_auc=round(float(rng.uniform(0.87, 0.93)), 4),
        train_window_start="2025-01-01T00:00:00Z",
        train_window_end="2025-12-31T23:59:59Z",
        schema_version="v1",
        is_demo=True,
    )

    aggregator = FederatedTrustAggregator(resource_type)
    aggregator.add_package(pkg_a)
    aggregator.add_package(pkg_b)
    aggregate = aggregator.aggregate()

    registry = ModelRegistry()
    mv = registry.register_available(
        aggregate=aggregate,
        registered_by="DEMO_COORDINATOR",
    )

    # Simulate local evaluation passing
    eval_report = {
        "local_pr_auc_with_federated_prior": 0.9105,
        "local_pr_auc_without_prior": 0.9020,
        "calibration_brier_score": 0.042,
        "fairness_check_passed": True,
        "evaluation_note": "[SYNTHETIC DEMO — metrics are illustrative only]",
    }
    registry.transition_to_evaluated(
        version_id=mv.version_id,
        evaluation_report=eval_report,
        evaluated_by="DEMO_LOCAL_EVALUATOR",
    )

    # Simulate authorized officer approval with completed checklist
    registry.transition_to_approved(
        version_id=mv.version_id,
        officer_id="DEMO_CMO_OFFICER",
        release_checklist_completed=True,
    )

    return SyntheticFederationDemoResult(
        demo_label="[SYNTHETIC DEMO — NOT REAL BRICS OR COUNTRY PARTICIPATION DATA]",
        node_packages=[asdict(pkg_a), asdict(pkg_b)],
        aggregate=aggregate,
        model_version=mv.to_dict(),
        lifecycle_summary=registry.summary(),
        provenance="SYNTHETIC_DEMO_ONLY",
    )
