"""
Anti-leakage enforcement.

`tathyon/generator.py` and `docs/07_DATA_PROVENANCE.md` both claim that this
file exists and enforces the discipline. Until now it did not, which made the
claim a code-review convention dressed up as a test. This file makes the claim
true.

What leakage would look like here, concretely: the generator's `observed`
frame is a MERGE of the observed ledger with the truth table, so
`true_stock`, `true_usable`, `true_demand`, `rel_err` and `label_wrong` are
live columns sitting next to the features. Nothing in `detect.py` stops a
future contributor from appending `"rel_err"` to FEATURES; the only thing
standing between that and a 1.00 AUC headline is an allow-list and whoever is
reviewing the diff at 2am.

Three independent guards, because any one of them can be argued around:
  1. NAME -- no feature is named after a ground-truth or pathology column.
  2. CONSTRUCTION -- every feature is computable with the truth columns
     physically deleted from the input frame.
  3. BEHAVIOUR -- no single feature separates the label well enough to be a
     disguised copy of it.
"""
from __future__ import annotations

import pandas as pd
import pytest
from sklearn.metrics import mutual_info_score, roc_auc_score

from tathyon import detect
from tathyon.generator import CAG_ANCHORS, EQUIPMENT_ANCHORS

DATA = "data/observed.parquet"

# Columns produced by comparing truth to observation. A feature derived from
# any of these is the label wearing a moustache.
GROUND_TRUTH_COLUMNS = {
    "true_stock", "true_usable", "true_demand", "true_status",
    "rel_err", "label_wrong", "expiry",
}

# Names of the injected pathologies. A flag saying "this series was corrupted
# on purpose" is not a signal, it is the answer key.
PATHOLOGY_FLAGS = set(CAG_ANCHORS) | set(EQUIPMENT_ANCHORS)

# Tokens that must not appear inside any feature name.
FORBIDDEN_TOKENS = {"true_", "truth", "label", "rel_err", "wrong", "pathology"}

AUC_CEILING = 0.95          # above this, a single feature IS the label
MI_CEILING = 0.25           # nats; a near-deterministic map to the label


@pytest.fixture(scope="module")
def observed() -> pd.DataFrame:
    return pd.read_parquet(DATA)


@pytest.fixture(scope="module")
def observed_only(observed: pd.DataFrame) -> pd.DataFrame:
    """The ledger with every ground-truth column physically removed. If a
    feature cannot be built from this, it is not an observed-data feature."""
    drop = [c for c in observed.columns if c in GROUND_TRUTH_COLUMNS]
    assert drop, (
        "the generated ledger no longer carries any ground-truth column -- "
        "either the generator changed or the fixture is pointing at the wrong "
        "file; this test is worthless unless the risk it guards against is "
        "actually present in the input"
    )
    return observed.drop(columns=drop)


@pytest.fixture(scope="module")
def features_and_label(observed: pd.DataFrame, observed_only: pd.DataFrame):
    """Build features from observed-only data, then re-attach the label BY KEY.

    Deliberately not by index: hard_violations() does a merge internally, which
    resets the index, so an index-aligned join would silently mis-pair labels
    and quietly make this whole file meaningless.
    """
    built = detect.build(observed_only)
    keys = ["date", "facility_id", "sku"]
    merged = built.merge(observed[keys + ["label_wrong"]], on=keys, how="left")
    assert len(merged) == len(built)
    assert merged["label_wrong"].notna().all()
    assert 0.0 < merged["label_wrong"].mean() < 1.0, "degenerate label"
    return merged


# ---------------------------------------------------------------------------
# 1. NAME
# ---------------------------------------------------------------------------

def test_no_feature_is_named_after_ground_truth():
    overlap = set(detect.FEATURES) & GROUND_TRUTH_COLUMNS
    assert not overlap, f"ground-truth columns used as features: {sorted(overlap)}"


def test_no_feature_is_named_after_an_injected_pathology():
    bad = {f for f in detect.FEATURES
           if any(p in f for p in PATHOLOGY_FLAGS) or f.lstrip("f_") in PATHOLOGY_FLAGS}
    assert not bad, f"pathology flags used as features: {sorted(bad)}"


def test_no_feature_name_contains_a_forbidden_token():
    bad = {f: t for f in detect.FEATURES for t in FORBIDDEN_TOKENS if t in f.lower()}
    assert not bad, f"feature names referencing ground truth: {bad}"


def test_hard_violation_flags_are_not_features():
    """v_* columns are free LABELS (tier 1). Using a label as a feature in the
    tier-2 model would be circular: the ensemble would be predicting its own
    supervision signal."""
    leaked = [f for f in detect.FEATURES if f.startswith("v_")]
    assert not leaked, f"hard-violation flags in FEATURES: {leaked}"
    assert not set(detect.FEATURES) & set(detect.VIOLATION_COLUMNS)
    assert not set(detect.FEATURES) & {"hard_violation", "n_hard_violations"}


def test_every_feature_uses_the_f_prefix_convention():
    """The allow-list is only auditable if the naming convention holds: an
    auditor should be able to see at a glance that no v_* or true_* column is
    in the model."""
    assert all(f.startswith("f_") for f in detect.FEATURES), detect.FEATURES


# ---------------------------------------------------------------------------
# 2. CONSTRUCTION
# ---------------------------------------------------------------------------

def test_features_are_computable_without_any_ground_truth_column(observed_only):
    """The strongest structural guard: delete truth from the input entirely and
    the feature builder must still produce every declared feature."""
    built = detect.build(observed_only)
    missing = [f for f in detect.FEATURES if f not in built.columns]
    assert not missing, f"features unbuildable from observed data alone: {missing}"
    for f in detect.FEATURES:
        assert built[f].notna().all(), f"{f} is NaN on observed-only input"


def test_ground_truth_does_not_reappear_in_the_built_frame(observed_only):
    built = detect.build(observed_only)
    leaked = set(built.columns) & GROUND_TRUTH_COLUMNS
    assert not leaked, f"detect.build() reintroduced ground truth: {sorted(leaked)}"


# ---------------------------------------------------------------------------
# 3. BEHAVIOUR
# ---------------------------------------------------------------------------

def _auc(x: pd.Series, y: pd.Series) -> float:
    """Direction-free AUC: a perfectly INVERTED copy of the label leaks just as
    badly as a direct one."""
    a = roc_auc_score(y, x.astype(float).fillna(0.0))
    return max(a, 1.0 - a)


def test_no_single_feature_recovers_the_label(features_and_label):
    m = features_and_label
    aucs = {f: _auc(m[f], m["label_wrong"]) for f in detect.FEATURES}
    over = {f: round(a, 4) for f, a in aucs.items() if a > AUC_CEILING}
    assert not over, (
        f"feature(s) separate the label almost perfectly -- this is leakage, "
        f"not signal: {over}\nall AUCs: "
        f"{ {k: round(v, 4) for k, v in aucs.items()} }"
    )


def test_no_single_feature_has_near_deterministic_mutual_information(features_and_label):
    m = features_and_label
    y = m["label_wrong"]
    mis = {}
    for f in detect.FEATURES:
        # bin to 20 quantiles; a leaked continuous copy of the label still maps
        # near-deterministically after binning
        binned = pd.qcut(m[f].astype(float).rank(method="first"), 20,
                         labels=False, duplicates="drop")
        mis[f] = float(mutual_info_score(y, binned))
    over = {f: round(v, 4) for f, v in mis.items() if v > MI_CEILING}
    assert not over, (
        f"feature(s) carry near-deterministic information about the label: "
        f"{over}\nall MI (nats): { {k: round(v, 4) for k, v in mis.items()} }"
    )


def test_the_leakage_detector_itself_catches_a_planted_leak(features_and_label):
    """A guard that has never fired is a guard nobody has tested. Plant the
    label as a feature and assert the AUC check would reject it."""
    m = features_and_label
    assert _auc(m["label_wrong"], m["label_wrong"]) > AUC_CEILING
    noisy = m["label_wrong"].astype(float) + 0.01 * m["f_qty_z"]
    assert _auc(noisy, m["label_wrong"]) > AUC_CEILING


def test_ground_truth_columns_really_are_separable_from_the_label(features_and_label,
                                                                 observed):
    """Sanity: the truth columns ARE a leak by construction -- they are the
    literal terms of the label definition. This proves the guard above is
    aimed at a real hazard and not at an empty room.

    Note that `rel_err` ALONE only reaches ~0.71 AUC, because the label is an
    OR of two terms and the usable-stock term dominates. That is exactly why a
    single-feature AUC ceiling is necessary but not sufficient, and why the
    NAME and CONSTRUCTION guards above are not redundant with it.
    """
    m = observed.dropna(subset=["rel_err", "true_usable"])
    assert _auc(m["rel_err"], m["label_wrong"]) > 0.6, (
        "rel_err no longer carries label information at all -- the generator's "
        "label definition has changed; re-derive the thresholds in this file"
    )
    reconstructed = (
        (m["rel_err"] > 0.15) | (m["true_usable"] < m["reported_stock"] * 0.7)
    ).astype(float)
    assert _auc(reconstructed, m["label_wrong"]) > AUC_CEILING, (
        "the truth columns no longer reconstruct the label; the generator's "
        "label definition has changed and this file's ceilings are stale"
    )
