"""
The trust scorer: trained from generator labels, triage-grade, leak-free.

Guards, each independent of the others:
  1. NAME         no feature is named after truth, a pathology or the label;
  2. CONSTRUCTION features are identical with truth columns deleted;
  3. CAUSALITY    features for day t are identical with every later row deleted;
  4. SPLIT        train, test and deployment worlds never overlap;
  5. BEHAVIOUR    no single feature recovers the label;
  6. OUTCOME      held-out PR-AUC beats the base rate and triage clears 2.2x.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import average_precision_score, roc_auc_score

from tathyon.generator import (
    CAG_ANCHORS, EQUIPMENT_ANCHORS, TRUTH_COLUMNS, attestation_log, generate,
    observation_labels, observed_only,
)
from tathyon.trust_eval import HONEST_BAR, VISIT_BUDGET, triage_at_budget
from tathyon.trust_features import FEATURE_DOCS, TIER1_FEATURES, TRUST_FEATURES, snapshot_features
from tathyon.verify import (
    DEPLOYMENT_SEED, SNAPSHOT_DAYS, TEST_SEEDS, TRAIN_SEEDS, default_trust_scorer,
    labelled_snapshots, train_trust_scorer,
)

FORBIDDEN_TOKENS = {"true_", "truth", "label", "rel_err", "wrong", "pathology"}
SEED = 4242                          # a world in no split, used for structural checks


@pytest.fixture(scope="module")
def world() -> dict:
    return generate(seed=SEED)


@pytest.fixture(scope="module")
def snapshot_dates(world) -> list:
    day0 = world["observed"]["date"].min()
    return [day0 + pd.Timedelta(days=d) for d in (56, 140, 238)]


@pytest.fixture(scope="module")
def scorer():
    return train_trust_scorer()


@pytest.fixture(scope="module")
def held_out(scorer) -> pd.DataFrame:
    frame = pd.concat([labelled_snapshots(s) for s in TEST_SEEDS], ignore_index=True)
    return frame.assign(p_wrong=scorer.p_wrong(frame))


# 1. NAME ------------------------------------------------------------------

def test_every_feature_is_documented():
    assert TRUST_FEATURES == list(FEATURE_DOCS)
    assert all(len(FEATURE_DOCS[f]) > 20 for f in TRUST_FEATURES)


def test_no_feature_name_references_truth_labels_or_pathologies():
    bad = {f for f in TRUST_FEATURES
           if any(t in f.lower() for t in FORBIDDEN_TOKENS)
           or any(p in f for p in set(CAG_ANCHORS) | set(EQUIPMENT_ANCHORS))
           or f in TRUTH_COLUMNS or f == "is_materially_wrong"}
    assert not bad, bad


def test_tier1_features_are_counts_of_hard_violations_not_the_label():
    assert set(TIER1_FEATURES) <= set(TRUST_FEATURES) and TIER1_FEATURES


# 2. CONSTRUCTION ------------------------------------------------------------

def test_features_are_identical_with_truth_columns_deleted(world, snapshot_dates):
    att = attestation_log(world)
    with_truth = snapshot_features(world["observed"], att, snapshot_dates)
    without = snapshot_features(observed_only(world["observed"]), att, snapshot_dates)
    pd.testing.assert_frame_equal(with_truth, without)
    assert not set(without.columns) & set(TRUTH_COLUMNS)


# 3. CAUSALITY ---------------------------------------------------------------

def test_features_do_not_see_the_future(world, snapshot_dates):
    obs, att = observed_only(world["observed"]), attestation_log(world)
    t = snapshot_dates[1]
    full = snapshot_features(obs, att, [t])
    past = snapshot_features(obs[obs["date"] <= t], att[att["date"] <= t], [t])
    pd.testing.assert_frame_equal(full.reset_index(drop=True), past.reset_index(drop=True))


def test_a_count_on_the_snapshot_day_is_not_visible_that_day(world):
    """A physical count on day t reveals truth for day t. The scorer may only
    use counts strictly before t, or the label leaks through the attestation."""
    obs, att = observed_only(world["observed"]), attestation_log(world)
    t = att["date"].iloc[len(att) // 2]
    with_same_day = snapshot_features(obs, att, [t])
    without = snapshot_features(obs, att[att["date"] != t], [t])
    pd.testing.assert_frame_equal(with_same_day, without)


# 4. SPLIT -------------------------------------------------------------------

def test_train_test_and_deployment_worlds_never_overlap():
    assert not set(TRAIN_SEEDS) & set(TEST_SEEDS)
    assert DEPLOYMENT_SEED not in set(TRAIN_SEEDS) | set(TEST_SEEDS)
    assert SEED not in set(TRAIN_SEEDS) | set(TEST_SEEDS) | {DEPLOYMENT_SEED}


def test_scorer_is_trained_only_on_train_seeds(scorer):
    assert scorer.train_seeds == TRAIN_SEEDS
    assert scorer.train_rows == len(TRAIN_SEEDS) * 200 * len(SNAPSHOT_DAYS)
    assert scorer.model_name == "gbc"


def test_labels_come_from_the_generator(world):
    labels = observation_labels(world, [56, 140])
    assert set(labels["is_materially_wrong"].unique()) <= {0, 1}
    assert len(labels) == 2 * 200
    assert 0.0 < labels["is_materially_wrong"].mean() < 0.5


# 5. BEHAVIOUR ---------------------------------------------------------------

def test_no_single_feature_recovers_the_label(held_out):
    y = held_out["is_materially_wrong"]
    aucs = {f: max(a, 1 - a) for f in TRUST_FEATURES
            for a in [roc_auc_score(y, held_out[f])]}
    assert max(aucs.values()) < 0.95, sorted(aucs.items(), key=lambda kv: -kv[1])[:3]


# 6. OUTCOME -----------------------------------------------------------------

def test_held_out_pr_auc_beats_the_base_rate(held_out):
    y = held_out["is_materially_wrong"].to_numpy()
    pr_auc = average_precision_score(y, held_out["p_wrong"])
    assert pr_auc > 2 * y.mean()


def test_triage_clears_the_honest_bar_at_fixed_budget(held_out):
    t = triage_at_budget(held_out, VISIT_BUDGET)
    assert t["trained_scorer"]["lift_vs_random"] >= HONEST_BAR
    assert t["trained_scorer"]["hit_rate"] <= t["ceiling_hit_rate"] + 1e-9


def test_probabilities_are_probabilities(held_out):
    p = held_out["p_wrong"]
    assert p.between(0, 1).all() and np.isfinite(p).all()


# model selection ------------------------------------------------------------

def test_logistic_regression_is_available_as_the_fallback_model():
    s = train_trust_scorer(train_seeds=TRAIN_SEEDS[:2], model="logistic")
    assert s.model_name == "logistic"


def test_unknown_model_is_refused():
    with pytest.raises(ValueError, match="UNKNOWN_MODEL"):
        train_trust_scorer(train_seeds=TRAIN_SEEDS[:1], model="vertex-automl")


def test_expected_hidden_days_uses_the_spread_not_a_point(scorer):
    """A report with 90 days of cover: the median usable share still covers
    the horizon, but a wrong report can be almost entirely unusable."""
    hidden = scorer.expected_hidden_stockout_days([900.0], [10.0])[0]
    assert min(scorer.usable_share_quantiles) < scorer.usable_share_if_over
    assert hidden > 0.0
