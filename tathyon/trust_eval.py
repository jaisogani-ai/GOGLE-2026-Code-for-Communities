"""
Held-out evaluation of the trust scorer and the verification queue.

Run:  python -m tathyon.trust_eval      (writes artifacts/trust_scorer_report.json)

Rules this file follows, because the easiest way to fake a result is here:
  - Train on TRAIN_SEEDS, report on TEST_SEEDS. No world appears in both.
  - Hyper-parameters are fixed in verify.GBC_PARAMS and never tuned on TEST_SEEDS.
  - Every method is compared against baselines that need no ML at all: random,
    oldest-count-first, and the tier-1 hard-violation rule. If the model does
    not beat the tier-1 rule, the report says so.
  - Failure cases (confident false alarms, missed wrong reports) are listed.

All data is SYNTHETIC (CAG-anchored generator). These numbers describe how the
scorer behaves on that generator's failure modes, not on a real ledger.
"""
from __future__ import annotations

import json
import os
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

from .forecast import select_model
from .trust_features import FEATURE_DOCS, TIER1_FEATURES, TRUST_FEATURES
from .verify import (
    COUNT_HOURS, CONSEQUENCE_HORIZON_DAYS, DDW_XY, GBC_PARAMS, SKU_VEN, SLOT_HOURS,
    TEST_SEEDS, TRAIN_SEEDS, VEN_WEIGHTS, DEFAULT_VEN, TrustScorer, _generated,
    labelled_snapshots, stockout_days, target_verifications, train_trust_scorer,
    visit_cost_slots,
)

VISIT_BUDGET = 20              # counts (or slots) per district per month
HONEST_BAR = 2.2               # lift over random that a triage scorer should clear
RANDOM_DRAWS = 200
RNG_SEED = 20260926
REPORT_PATH = os.path.join("artifacts", "trust_scorer_report.json")
LABEL = "is_materially_wrong"


def _test_frame(scorer: TrustScorer) -> pd.DataFrame:
    frame = pd.concat([labelled_snapshots(s) for s in TEST_SEEDS], ignore_index=True)
    return frame.assign(p_wrong=scorer.p_wrong(frame))


def _months(frame: pd.DataFrame):
    return frame.groupby(["seed", "date"], sort=True)


def _topk_hits(frame: pd.DataFrame, order: Callable[[pd.DataFrame], pd.DataFrame],
               k: int) -> dict:
    """Hit rate and recall when the top-k reports of each month are counted."""
    hit, recall = [], []
    for _, month in _months(frame):
        chosen = order(month).head(k)
        hit.append(chosen[LABEL].mean())
        total = month[LABEL].sum()
        recall.append(chosen[LABEL].sum() / total if total else np.nan)
    return {"hit_rate": float(np.mean(hit)), "recall": float(np.nanmean(recall))}


def _random_hits(frame: pd.DataFrame, k: int, rng: np.random.Generator) -> dict:
    hit, recall = [], []
    for _, month in _months(frame):
        y = month[LABEL].to_numpy()
        for _ in range(RANDOM_DRAWS):
            pick = rng.choice(len(y), size=min(k, len(y)), replace=False)
            hit.append(y[pick].mean())
            recall.append(y[pick].sum() / y.sum() if y.sum() else np.nan)
    return {"hit_rate": float(np.mean(hit)), "recall": float(np.nanmean(recall))}


ORDERINGS = {
    "trained_scorer": lambda m: m.sort_values(["p_wrong", "facility_id", "sku"], ascending=[False, True, True]),
    "oldest_count_first": lambda m: m.sort_values(["gp_days_since_attestation", "facility_id", "sku"],
                                                  ascending=[False, True, True]),
    "tier1_rule": lambda m: m.sort_values(["t1_violations_now", "t1_violation_days_30d",
                                           "gp_days_since_attestation", "facility_id", "sku"],
                                          ascending=[False, False, False, True, True]),
}


def classifier_metrics(y: np.ndarray, p: np.ndarray) -> dict:
    base = float(y.mean())
    pr_auc = float(average_precision_score(y, p))
    return {"rows": int(len(y)), "positives": int(y.sum()), "base_rate": round(base, 4),
            "pr_auc": round(pr_auc, 4), "pr_auc_over_base_rate": round(pr_auc / base, 2),
            "roc_auc": round(float(roc_auc_score(y, p)), 4),
            "brier": round(float(brier_score_loss(y, p)), 5)}


def triage_at_budget(frame: pd.DataFrame, k: int = VISIT_BUDGET) -> dict:
    rng = np.random.default_rng(RNG_SEED)
    rand = _random_hits(frame, k, rng)
    out = {"budget_counts_per_month": k, "months": int(_months(frame).ngroups),
           "wrong_reports_per_month": round(float(_months(frame)[LABEL].sum().mean()), 2),
           "random": {k2: round(v, 4) for k2, v in rand.items()}}
    ceiling = _months(frame)[LABEL].sum().clip(upper=k).mean() / k
    out["ceiling_hit_rate"] = round(float(ceiling), 4)
    for name, order in ORDERINGS.items():
        r = _topk_hits(frame, order, k)
        out[name] = {"hit_rate": round(r["hit_rate"], 4), "recall": round(r["recall"], 4),
                     "lift_vs_random": round(r["hit_rate"] / rand["hit_rate"], 2)}
    return out


def silent_errors(frame: pd.DataFrame, k: int = VISIT_BUDGET) -> dict:
    """Wrong reports with NO tier-1 violation today: the hard case, where the
    record is internally consistent and only weak signals can find it."""
    pos = frame[frame[LABEL] == 1]
    silent = pos[pos["t1_violations_now"] == 0]
    caught = 0
    for _, month in _months(frame):
        top = ORDERINGS["trained_scorer"](month).head(k)
        caught += int(((top[LABEL] == 1) & (top["t1_violations_now"] == 0)).sum())
    per_month = frame.groupby(["seed", "date"]).size().mean()
    return {"wrong_reports": int(len(pos)), "silent_wrong_reports": int(len(silent)),
            "silent_share": round(len(silent) / max(len(pos), 1), 4),
            "silent_caught_in_top_k": caught,
            "silent_recall_at_budget": round(caught / max(len(silent), 1), 4),
            "random_expected_recall": round(k / per_month, 4)}


def failure_cases(frame: pd.DataFrame, n: int = 5) -> dict:
    cols = ["seed", "date", "facility_id", "sku", "reported_stock", "p_wrong",
            "t1_violations_now", "burn_inconsistency_30d", "gp_days_since_attestation"]

    def rows(df):
        out = df[cols].copy()
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        return out.round(4).to_dict(orient="records")

    neg = frame[frame[LABEL] == 0].nlargest(n, "p_wrong")
    pos = frame[frame[LABEL] == 1].nsmallest(n, "p_wrong")
    return {"confident_false_alarms": rows(neg), "missed_wrong_reports": rows(pos)}


# ---------------------------------------------------------------------------
# The decision itself: the knapsack queue, scored against truth
# ---------------------------------------------------------------------------

def _month_candidates(month: pd.DataFrame, scorer: TrustScorer) -> pd.DataFrame:
    seed, date = int(month["seed"].iloc[0]), month["date"].iloc[0]
    g = _generated(seed)
    hist = g["observed"][g["observed"]["date"] <= date]
    rates = {k: select_model(h["issued"].to_numpy(dtype=float))[0].rate
             for k, h in hist.groupby(["facility_id", "sku"], sort=False)}
    fac = g["facilities"].set_index("facility_id")
    c = month.copy()
    c["daily_rate"] = [rates[(f, s)] for f, s in zip(c["facility_id"], c["sku"])]
    right = stockout_days(c["reported_stock"], c["daily_rate"])
    c["hidden_stockout_days"] = scorer.expected_hidden_stockout_days(c["reported_stock"], c["daily_rate"])
    c["essentiality"] = c["sku"].map(SKU_VEN).fillna(DEFAULT_VEN).map(VEN_WEIGHTS)
    c["visit_cost"] = [visit_cost_slots(fac.at[f, "x"], fac.at[f, "y"]) for f in c["facility_id"]]
    # Truth, for scoring the decision only: essential stockout-days that trusting
    # this report would actually have hidden.
    c["true_hidden"] = c["essentiality"] * (
        stockout_days(c["true_usable"], c["daily_rate"]) - right).clip(min=0)
    return c


def _fill(month: pd.DataFrame, order: pd.Index, budget: int) -> pd.DataFrame:
    """Greedy fill in a given order under the slot budget."""
    picked, used = [], 0
    for i in order:
        cost = int(month.at[i, "visit_cost"])
        if used + cost <= budget:
            picked.append(i)
            used += cost
    return month.loc[picked]


def queue_decision(frame: pd.DataFrame, scorer: TrustScorer, budget: int = VISIT_BUDGET) -> dict:
    rng = np.random.default_rng(RNG_SEED)
    totals = {m: {"true_hidden": 0.0, "wrong_caught": 0} for m in
              ("tathyon_queue", "median_plugin_queue", "p_wrong_only", "oldest_count_first", "random")}
    available = 0.0
    for _, month in _months(frame):
        c = _month_candidates(month, scorer).reset_index(drop=True)
        available += float(c["true_hidden"].sum())
        q = target_verifications(budget, c)
        # The first consequence model, kept as a row so the fix stays auditable:
        # a single median usable share instead of the expectation over its spread.
        plug = c.assign(hidden_stockout_days=scorer.over_report_share * (
            stockout_days(c["reported_stock"] * scorer.usable_share_if_over, c["daily_rate"])
            - stockout_days(c["reported_stock"], c["daily_rate"])))
        qm = target_verifications(budget, plug)
        sel = {"tathyon_queue": q.ranking[q.ranking["selected"]],
               "median_plugin_queue": qm.ranking[qm.ranking["selected"]],
               "p_wrong_only": _fill(c, c.sort_values("p_wrong", ascending=False).index, budget),
               "oldest_count_first": _fill(c, c.sort_values("gp_days_since_attestation",
                                                            ascending=False).index, budget)}
        for name, s in sel.items():
            totals[name]["true_hidden"] += float(s["true_hidden"].sum())
            totals[name]["wrong_caught"] += int(s[LABEL].sum())
        draws = [_fill(c, pd.Index(rng.permutation(c.index)), budget) for _ in range(20)]
        totals["random"]["true_hidden"] += float(np.mean([d["true_hidden"].sum() for d in draws]))
        totals["random"]["wrong_caught"] += float(np.mean([d[LABEL].sum() for d in draws]))
    out = {"visit_slot_budget_per_month": budget, "months": int(_months(frame).ngroups),
           "true_hidden_essential_stockout_days_available": round(available, 1)}
    for name, t in totals.items():
        out[name] = {"true_hidden_essential_stockout_days_caught": round(t["true_hidden"], 1),
                     "wrong_reports_caught": round(float(t["wrong_caught"]), 1)}
    return out


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(include_queue: bool = True) -> dict:
    scorer = train_trust_scorer()
    frame = _test_frame(scorer)
    y = frame[LABEL].to_numpy()

    no_t1 = train_trust_scorer(features=tuple(f for f in TRUST_FEATURES if f not in TIER1_FEATURES))
    logistic = train_trust_scorer(model="logistic")
    ablated = frame.assign(p_wrong=no_t1.p_wrong(frame))
    logit = frame.assign(p_wrong=logistic.p_wrong(frame))

    triage = triage_at_budget(frame)
    ablated_triage = triage_at_budget(ablated)["trained_scorer"]
    silent = silent_errors(frame)
    lift = triage["trained_scorer"]["lift_vs_random"]
    beats_rule = triage["trained_scorer"]["hit_rate"] - triage["tier1_rule"]["hit_rate"]
    reading = (
        f"Held-out lift {lift}x over random at {VISIT_BUDGET} counts/month (bar {HONEST_BAR}x); "
        f"ceiling hit-rate {triage['ceiling_hit_rate']}. The signal is the ledger failing to "
        f"reconcile with its own transactions: a plain tier-1 rule reaches "
        f"{triage['tier1_rule']['hit_rate']} vs the scorer's {triage['trained_scorer']['hit_rate']}, "
        f"and dropping tier-1 features leaves {ablated_triage['hit_rate']} because burn-rate "
        f"inconsistency sees the same event. This generator's errors are mostly one-day spikes "
        f"that break arithmetic; only {silent['silent_share']:.0%} of wrong reports are silent "
        f"(no tier-1 flag), and those are recalled at {silent['silent_recall_at_budget']:.0%}. "
        f"Real ledgers with internally consistent phantom stock will be harder: treat these "
        f"numbers as an upper bound on this generator, not a forecast of field performance.")
    report = {
        "provenance": "SYNTHETIC",
        "question": "Which facility x SKU stock reports should get a physical count this month?",
        "split": {"train_seeds": list(TRAIN_SEEDS), "test_seeds": list(TEST_SEEDS),
                  "leakage_rule": "split by seed; features causal and truth-free"},
        "model": {"name": scorer.model_name, "params": GBC_PARAMS, "train_rows": scorer.train_rows,
                  "train_base_rate": round(scorer.train_base_rate, 4),
                  "feature_importance": {f: round(float(i), 4) for f, i in sorted(
                      zip(scorer.features, scorer.model.feature_importances_),
                      key=lambda t: -t[1])}},
        "features": FEATURE_DOCS,
        "classifier_test": classifier_metrics(y, frame["p_wrong"].to_numpy()),
        "triage_at_fixed_budget": triage,
        "ablation_without_tier1_features": {
            "classifier_test": classifier_metrics(y, ablated["p_wrong"].to_numpy()),
            "trained_scorer": ablated_triage},
        "logistic_regression_comparison": {
            "classifier_test": classifier_metrics(y, logit["p_wrong"].to_numpy()),
            "trained_scorer": triage_at_budget(logit)["trained_scorer"]},
        "silent_errors": silent,
        "failure_cases": failure_cases(frame),
        "assumptions": {
            "visit_budget": f"{VISIT_BUDGET} counts (triage) / {VISIT_BUDGET} half-day slots (queue) per month",
            "consequence_horizon_days": CONSEQUENCE_HORIZON_DAYS,
            "district_warehouse_xy": DDW_XY, "count_hours": COUNT_HOURS, "slot_hours": SLOT_HOURS,
            "over_report_share_if_wrong": round(scorer.over_report_share, 4),
            "usable_share_if_over_reported": round(scorer.usable_share_if_over, 4),
            "essentiality": "WHO/MSH VEN classes, weights V=3 E=2 N=1 (policy input)",
            "demand": "per-series backtest-selected forecaster (tathyon.forecast.select_model)",
        },
        "verdict": {
            "lift_vs_random": lift, "honest_bar": HONEST_BAR, "clears_bar": bool(lift >= HONEST_BAR),
            "hit_rate_minus_tier1_rule": round(beats_rule, 4),
            "reading": reading,
        },
    }
    if include_queue:
        qd = queue_decision(frame, scorer)
        report["queue_decision"] = qd

        def caught(name: str) -> float:
            return qd[name]["true_hidden_essential_stockout_days_caught"]
        report["verdict"]["queue_hidden_days_vs_p_wrong_only"] = round(
            caught("tathyon_queue") / max(caught("p_wrong_only"), 1e-9), 3)
        report["verdict"]["queue_hidden_days_vs_random"] = round(
            caught("tathyon_queue") / max(caught("random"), 1e-9), 2)
        report["verdict"]["queue_hidden_days_vs_median_plugin"] = round(
            caught("tathyon_queue") / max(caught("median_plugin_queue"), 1e-9), 3)
    return report


def main() -> None:
    report = build_report()
    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as fh:
        json.dump(report, fh, indent=2, default=str)
    c, t = report["classifier_test"], report["triage_at_fixed_budget"]
    lines = [
        f"SYNTHETIC evaluation, test seeds {list(TEST_SEEDS)} ({c['rows']} observations)",
        f"PR-AUC {c['pr_auc']} vs base rate {c['base_rate']} ({c['pr_auc_over_base_rate']}x)",
        f"hit-rate@{VISIT_BUDGET}: scorer {t['trained_scorer']['hit_rate']} | random {t['random']['hit_rate']} "
        f"| lift {t['trained_scorer']['lift_vs_random']}x (bar {HONEST_BAR}x) | ceiling {t['ceiling_hit_rate']}",
        f"tier-1 rule {t['tier1_rule']['hit_rate']} | oldest-count-first {t['oldest_count_first']['hit_rate']}",
        f"report written to {REPORT_PATH}",
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
