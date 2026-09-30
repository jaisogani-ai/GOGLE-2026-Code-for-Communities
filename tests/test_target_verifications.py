"""
target_verifications: the product's core decision, tested in isolation.

value = P(materially wrong) x hidden stockout-days x essentiality, and an exact
0/1 knapsack picks the most value that fits the monthly visit budget.
"""
from __future__ import annotations

import dataclasses
import itertools

import numpy as np
import pandas as pd
import pytest

from tathyon.schema import FORBIDDEN_STORED_SCORE_FIELDS, VerificationVisit, VisitStatus
from tathyon.trust_features import TRUST_FEATURES
from tathyon.optimize import knapsack
from tathyon.verify import (
    RANKING_INPUTS, VerificationQueue, stockout_days, target_verifications, visit_cost_slots,
)

AS_OF = pd.Timestamp("2026-08-28")


def _cands(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(RANKING_INPUTS))


# ---------------------------------------------------------------------------
# knapsack
# ---------------------------------------------------------------------------

def _brute_force(values, costs, cap):
    best, best_set = 0.0, ()
    for r in range(len(values) + 1):
        for combo in itertools.combinations(range(len(values)), r):
            if sum(costs[i] for i in combo) <= cap:
                v = sum(values[i] for i in combo)
                if v > best + 1e-12:
                    best, best_set = v, combo
    return best, best_set


@pytest.mark.parametrize("seed", range(25))
def test_knapsack_matches_brute_force(seed):
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 9))
    values = list(rng.uniform(0, 10, n).round(3))
    costs = list(rng.integers(1, 4, n))
    cap = int(rng.integers(0, 9))
    chosen = knapsack(values, costs, cap)
    assert sum(costs[i] for i in chosen) <= cap
    assert sum(values[i] for i in chosen) == pytest.approx(_brute_force(values, costs, cap)[0])


def test_knapsack_prefers_value_density_over_greedy_top_value():
    # greedy-by-value takes the 10 (cost 3) and stops; optimum is 7 + 6 (cost 2 + 1)
    assert sorted(knapsack([10.0, 7.0, 6.0], [3, 2, 1], 3)) == [1, 2]


def test_knapsack_takes_zero_cost_items_at_zero_capacity():
    assert knapsack([6.42, 3.0], [0, 1], 0) == [0]


def test_knapsack_never_picks_zero_value_items():
    assert knapsack([0.0, 0.0, -1.0], [1, 1, 1], 3) == []


# ---------------------------------------------------------------------------
# target_verifications as a pure function
# ---------------------------------------------------------------------------

def test_budget_is_respected_and_queue_is_value_ordered():
    c = _cands([
        ("F1", "AMX250", 0.9, 10.0, 3.0, 1),   # 27
        ("F2", "PCM500", 0.9, 10.0, 1.0, 1),   #  9
        ("F3", "ORS01", 0.2, 20.0, 3.0, 2),    # 12
        ("F4", "SALB", 0.01, 5.0, 3.0, 1),     #  0.15
    ])
    q = target_verifications(3, c, as_of=AS_OF)
    assert isinstance(q, VerificationQueue) and isinstance(q, list)
    assert q.budget_used <= q.budget == 3
    assert [(v.facility_id, v.sku_id) for v in q] == [("F1", "AMX250"), ("F3", "ORS01")]
    assert all(isinstance(v, VerificationVisit) and v.status == VisitStatus.SCHEDULED for v in q)


def test_consequence_can_outrank_probability():
    """A likely-wrong report with nothing at stake loses to a less likely one
    that would hide a vital-medicine stockout."""
    c = _cands([("F1", "PCM500", 0.95, 0.0, 1.0, 1),
                ("F2", "INJDEX", 0.30, 12.0, 3.0, 1)])
    q = target_verifications(1, c, as_of=AS_OF)
    assert [v.facility_id for v in q] == ["F2"]


def test_zero_budget_gives_an_empty_queue_with_full_ranking():
    c = _cands([("F1", "AMX250", 0.9, 10.0, 3.0, 1)])
    q = target_verifications(0, c, as_of=AS_OF)
    assert list(q) == [] and len(q.ranking) == 1 and not q.ranking["selected"].any()


@pytest.mark.parametrize("bad", [-1, 2.5, True, "20", None])
def test_malformed_budget_fails_fast(bad):
    with pytest.raises(ValueError):
        target_verifications(bad, _cands([("F1", "AMX250", 0.9, 1.0, 1.0, 1)]), as_of=AS_OF)


def test_missing_ranking_input_fails_fast():
    with pytest.raises(ValueError, match="CANDIDATES_MISSING_COLUMNS"):
        target_verifications(5, pd.DataFrame({"facility_id": ["F1"], "sku": ["X"]}), as_of=AS_OF)


def test_visit_ids_are_deterministic_so_replays_are_idempotent():
    c = _cands([("F1", "AMX250", 0.9, 10.0, 3.0, 1)])
    a = target_verifications(1, c, as_of=AS_OF)
    b = target_verifications(1, c, as_of=AS_OF)
    assert [v.visit_id for v in a] == [v.visit_id for v in b] == ["vv_20260828_F1_AMX250"]


def test_ranking_is_inspectable_but_never_part_of_a_visit():
    c = _cands([("F1", "AMX250", 0.9, 10.0, 3.0, 1)]).assign(t1_violations_now=2.0)
    q = target_verifications(1, c, as_of=AS_OF)
    r = q.ranking
    for col in (*RANKING_INPUTS, "value", "rank", "selected", "t1_violations_now"):
        assert col in r.columns
    assert r.loc[0, "value"] == pytest.approx(0.9 * 10.0 * 3.0)
    payload = q[0].to_payload()
    assert not set(payload) & FORBIDDEN_STORED_SCORE_FIELDS
    r.loc[0, "value"] = -1.0                      # mutating the copy ...
    assert q.ranking.loc[0, "value"] > 0          # ... never touches the queue


def test_input_frame_is_not_mutated():
    c = _cands([("F1", "AMX250", 0.9, 10.0, 3.0, 1)])
    before = c.copy()
    target_verifications(1, c, as_of=AS_OF)
    pd.testing.assert_frame_equal(c, before)


# ---------------------------------------------------------------------------
# consequence and cost primitives
# ---------------------------------------------------------------------------

def test_stockout_days_is_bounded_by_the_horizon():
    assert stockout_days([0.0], [5.0], 30.0)[0] == 30.0
    assert stockout_days([100.0], [5.0], 30.0)[0] == 10.0
    assert stockout_days([1000.0], [5.0], 30.0)[0] == 0.0
    assert stockout_days([10.0], [0.0], 30.0)[0] == 0.0      # no demand, no stockout


def test_remote_facilities_cost_more_visit_slots():
    assert visit_cost_slots(50.0, 50.0) == 1
    assert visit_cost_slots(0.0, 0.0) > visit_cost_slots(50.0, 50.0)


# ---------------------------------------------------------------------------
# acceptance: target_verifications(20) on the synthetic deployment ledger
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def deployment_queue() -> VerificationQueue:
    """Candidates built explicitly from the OFFLINE synthetic benchmark (test input only);
    the library has no implicit synthetic default."""
    from tathyon.verify import SNAPSHOT_DAYS, TRAIN_SEEDS, _generated, train_trust_scorer, verification_candidates
    scorer = train_trust_scorer()
    generated = _generated(TRAIN_SEEDS[0])
    as_of = pd.Timestamp(generated["observed"]["date"].min()) + pd.Timedelta(days=SNAPSHOT_DAYS[-1])
    return target_verifications(20, verification_candidates(generated, as_of, scorer), as_of=as_of)


def test_target_verifications_20_returns_a_ranked_budgeted_queue(deployment_queue):
    q = deployment_queue
    assert 0 < len(q) <= 20
    assert q.budget == 20 and 0 < q.budget_used <= 20
    r = q.ranking
    assert len(r) == 200                                  # 20 facilities x 10 SKUs
    selected = r[r["selected"]]
    assert selected["visit_cost"].sum() == q.budget_used
    assert [(v.facility_id, v.sku_id) for v in q] == list(zip(selected["facility_id"], selected["sku"]))
    assert list(selected["value"]) == sorted(selected["value"], reverse=True)


def test_every_ranking_feature_is_inspectable(deployment_queue):
    r = deployment_queue.ranking
    for col in (*TRUST_FEATURES, *RANKING_INPUTS, "daily_rate", "forecast_model",
                "stockout_days_if_right", "stockout_days_if_wrong", "ven", "value"):
        assert col in r.columns, col
    assert r["p_wrong"].between(0, 1).all()
    assert (r["hidden_stockout_days"] >= 0).all()
