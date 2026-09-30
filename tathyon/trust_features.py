"""
Observed-only features for the trust scorer.

The scorer answers one narrow question per facility x SKU observation: how
likely is it that the stock figure the system of record shows today is
materially wrong? It is TRIAGE for a limited verification budget, never a
truth machine: a high score earns a physical count, it never changes a number.

ANTI-LEAKAGE CONTRACT (enforced by tests/test_trust_scorer.py):
  1. Features are computed from the observed ledger plus the attestation log
     (human counts), never from truth columns, pathology flags or the label.
  2. Features are CAUSAL. A snapshot on day t sees ledger rows dated <= t and
     human counts dated strictly < t. detect.hard_violations is reused for the
     row-local tier-1 flags, but its batch/expiry check looks at a batch's whole
     history (future rows included), so a causal equivalent is computed here.
  3. Train and test are split BY SEED: no generated world contributes to both.

Every feature is listed in FEATURE_DOCS with what it measures and why.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import detect
from .verify import ConsumptionPrior, posterior_unobserved

WINDOW_DAYS = 30                 # trailing window for counts and burn checks
PRIOR_DAYS = 28                  # consumption history that fits the Gamma prior
STALENESS_QUANTILE = 0.90        # upper quantile of unobserved consumption
QTY_Z_OUTLIER = 3.0
ROUND_NUMBER = 50
RETRO_ENTRY_DAYS = 20
NEVER_ATTESTED_DAYS = 365.0      # staleness assigned to a series never counted

KEYS = ["facility_id", "sku"]

# Row-local (causal) tier-1 flags taken unchanged from detect.hard_violations.
CAUSAL_TIER1 = [
    "v_arithmetic", "v_negative_consumption", "v_issue_against_expired",
    "v_absurd_unit_value", "v_missing_expiry", "v_negative_qty",
    "v_nonfinite_qty", "v_future_dated", "v_impossible_chronology",
]

FEATURE_DOCS = {
    # -- tier 1: hard violations (the record contradicts itself) ------------
    "t1_violations_now": "Number of tier-1 hard violations on today's record "
                         "(arithmetic, negative consumption, issue against expired batch, "
                         "absurd unit value, missing expiry, corrupt quantity, chronology, "
                         "causal batch/expiry conflict).",
    "t1_violation_days_30d": "Days in the last 30 with at least one tier-1 violation.",
    "t1_arithmetic_days_30d": "Days in the last 30 where closing != opening + receipts - issues.",
    "t1_expiry_integrity_now": "1 if today's batch has been recorded with two expiry dates so far, "
                               "or today's record has no expiry date.",
    # -- tier 2: weak signals (suspicious, not dispositive) -----------------
    "t2_weak_signal_days_30d": "Days in the last 30 carrying a weak signal: quantity outlier "
                               "against the series' own trailing history, a round-number balance, "
                               "or retroactive bulk entry.",
    "t2_qty_z_now": "Today's reported stock as a z-score against the series' own trailing "
                    "28-day mean and spread (facility-relative, so a big hospital is not 'odd').",
    "t2_entry_lag_log": "log(1 + days between the event and its ledger entry).",
    "t2_expired_but_stocked": "1 if the recorded expiry is already past and stock is still reported.",
    # -- Gamma-Poisson staleness (verify.posterior_unobserved) --------------
    "gp_days_since_attestation": "Days since the last human count strictly before today "
                                 f"({NEVER_ATTESTED_DAYS:.0f} if never counted).",
    "gp_unobserved_ratio": "Expected consumption since the last count, from the Gamma-Poisson "
                           "posterior over the trailing 28 days, divided by reported stock.",
    "gp_uncertainty_ratio": f"Spread of that posterior (q{int(STALENESS_QUANTILE * 100)} minus mean) "
                            "divided by reported stock: how much could have left unobserved.",
    # -- attestation history -------------------------------------------------
    "att_count": "Number of human counts of this series before today.",
    "att_never": "1 if this series has never been physically counted.",
    "att_last_record_gap": "At the last count: |reported - counted present| / counted present. "
                           "Was the record wrong the last time anyone looked?",
    "att_last_unusable_share": "At the last count: share of present stock that was not usable "
                               "(expired or damaged stock found on the shelf).",
    # -- burn-rate inconsistency (issues vs receipts) ------------------------
    "burn_inconsistency_30d": "|change in reported stock over 30 days - (receipts - issues)| / "
                              "reported stock: stock that moved without a transaction.",
    "burn_recon_gap_since_count": "|reported today - (last count + receipts - issues since)| / "
                                  "reported stock (0 if never counted).",
    "burn_issue_rate_ratio": "Mean issues over the last 7 days / mean over the last 30 days.",
}
TRUST_FEATURES = list(FEATURE_DOCS)
TIER1_FEATURES = [f for f in TRUST_FEATURES if f.startswith("t1_")]


# ---------------------------------------------------------------------------
# Row-level causal columns
# ---------------------------------------------------------------------------

def _causal_expiry_conflict(d: pd.DataFrame) -> pd.Series:
    """True from the first day a batch has been recorded with a second expiry."""
    first = d.groupby(KEYS + ["batch"], sort=False)["obs_expiry"].transform("first")
    differs = d["obs_expiry"].notna() & first.notna() & (d["obs_expiry"] != first)
    return differs.groupby([d[k] for k in KEYS + ["batch"]], sort=False).cummax().astype(bool)


def _rolling(g, col: str, window: int, how: str, min_periods: int = 1) -> pd.Series:
    r = g[col].rolling(window, min_periods=min_periods)
    return getattr(r, how)().reset_index(level=[0, 1], drop=True)


def _row_columns(observed: pd.DataFrame) -> pd.DataFrame:
    d = detect.hard_violations(observed).sort_values(KEYS + ["date"]).reset_index(drop=True)
    d["t1_expiry_integrity_now"] = (
        _causal_expiry_conflict(d) | d["v_missing_expiry"].astype(bool)).astype(float)
    d["t1_violations_now"] = (d[CAUSAL_TIER1].astype(bool).sum(axis=1)
                              + d["t1_expiry_integrity_now"] - d["v_missing_expiry"].astype(float))
    d["_any_t1"] = (d["t1_violations_now"] > 0).astype(float)

    g = d.groupby(KEYS, sort=False)
    mu = _rolling(g, "reported_stock", PRIOR_DAYS, "mean", 5)
    sd = _rolling(g, "reported_stock", PRIOR_DAYS, "std", 5)
    d["t2_qty_z_now"] = ((d["reported_stock"] - mu) / sd.replace(0, np.nan)).abs().fillna(0.0)
    d["t2_entry_lag_log"] = np.log1p(d["entry_lag_days"].astype(float))
    d["t2_expired_but_stocked"] = (d["obs_expiry"].notna() & (d["date"] > d["obs_expiry"])
                                   & (d["reported_stock"] > 0)).astype(float)
    round_bal = (d["reported_stock"] > 0) & (d["reported_stock"] % ROUND_NUMBER == 0)
    d["_weak"] = ((d["t2_qty_z_now"] > QTY_Z_OUTLIER) | round_bal
                  | (d["entry_lag_days"] > RETRO_ENTRY_DAYS)).astype(float)

    g = d.groupby(KEYS, sort=False)
    d["t1_violation_days_30d"] = _rolling(g, "_any_t1", WINDOW_DAYS, "sum")
    d["t1_arithmetic_days_30d"] = _rolling(g, "v_arithmetic", WINDOW_DAYS, "sum")
    d["t2_weak_signal_days_30d"] = _rolling(g, "_weak", WINDOW_DAYS, "sum")
    d["_issued_30"] = _rolling(g, "issued", WINDOW_DAYS, "sum")
    d["_receipt_30"] = _rolling(g, "receipt", WINDOW_DAYS, "sum")
    d["_issued_7_mean"] = _rolling(g, "issued", 7, "mean")
    d["_issued_30_mean"] = _rolling(g, "issued", WINDOW_DAYS, "mean")
    d["_issued_prior_sum"] = _rolling(g, "issued", PRIOR_DAYS, "sum")
    d["_reported_lag30"] = g["reported_stock"].shift(WINDOW_DAYS)
    d["_cum_issued"] = g["issued"].cumsum()
    d["_cum_receipt"] = g["receipt"].cumsum()
    return d


def _attach_attestations(d: pd.DataFrame, attestations: pd.DataFrame) -> pd.DataFrame:
    """For every row, the last human count STRICTLY before that row's date."""
    att = attestations.rename(columns={"counted_present": "_att_present",
                                       "counted_usable": "_att_usable"})
    d = d.merge(att, on=["date"] + KEYS, how="left")
    has = d["_att_present"].notna()
    d["_att_date"] = d["date"].where(has)
    d["_att_reported"] = d["reported_stock"].where(has)
    d["_att_cum_issued"] = d["_cum_issued"].where(has)
    d["_att_cum_receipt"] = d["_cum_receipt"].where(has)
    d["_att_flag"] = has.astype(float)
    g = d.groupby(KEYS, sort=False)
    carry = ["_att_date", "_att_present", "_att_usable", "_att_reported",
             "_att_cum_issued", "_att_cum_receipt"]
    # shift(1) then ffill: a count on day t is known from day t+1 onwards.
    d[carry] = g[carry].shift(1)
    d[carry] = d.groupby(KEYS, sort=False)[carry].ffill()
    d["att_count"] = g["_att_flag"].cumsum() - d["_att_flag"]
    return d


# ---------------------------------------------------------------------------
# Snapshot features
# ---------------------------------------------------------------------------

def _staleness(histories: list, gaps: np.ndarray, reported: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Expected and upper-quantile unobserved consumption over each gap, from
    the existing Gamma-Poisson posterior, as shares of reported stock."""
    fits = [ConsumptionPrior.fit(h) for h in histories]
    prior = ConsumptionPrior(a0=np.array([f.a0 for f in fits]), b0=np.array([f.b0 for f in fits]))
    nb = posterior_unobserved(prior,
                              observed_days=np.array([float(len(h)) for h in histories]),
                              observed_count=np.array([float(np.nansum(h)) for h in histories]),
                              gap_days=np.asarray(gaps, dtype=float))
    mean = np.asarray(nb.mean(), dtype=float)
    upper = np.asarray(nb.ppf(STALENESS_QUANTILE), dtype=float)
    denom = np.maximum(np.asarray(reported, dtype=float), 1.0)
    return mean / denom, np.maximum(upper - mean, 0.0) / denom


def snapshot_features(observed: pd.DataFrame, attestations: pd.DataFrame,
                      snapshot_dates) -> pd.DataFrame:
    """One row per facility x SKU per snapshot date: keys + TRUST_FEATURES.

    `observed` must be the ledger WITHOUT truth columns (they are ignored if
    present, and tests assert the output is identical when they are removed).
    """
    d = _attach_attestations(_row_columns(observed), attestations)
    snaps = pd.to_datetime(pd.Series(sorted(set(snapshot_dates))))
    issued_by_series = {k: g["issued"].to_numpy(dtype=float)
                        for k, g in d.groupby(KEYS, sort=False)}
    pos_in_series = d.groupby(KEYS, sort=False).cumcount().to_numpy()
    d["_pos"] = pos_in_series
    s = d[d["date"].isin(snaps)].copy()

    reported = s["reported_stock"].astype(float)
    denom = reported.clip(lower=1.0)
    never = s["_att_date"].isna()
    s["att_never"] = never.astype(float)
    s["gp_days_since_attestation"] = np.where(
        never, NEVER_ATTESTED_DAYS, (s["date"] - s["_att_date"]).dt.days.astype(float))
    s["att_last_record_gap"] = ((s["_att_reported"] - s["_att_present"]).abs()
                                / s["_att_present"].clip(lower=1.0)).fillna(0.0)
    s["att_last_unusable_share"] = ((s["_att_present"] - s["_att_usable"])
                                    / s["_att_present"].clip(lower=1.0)).fillna(0.0).clip(0, 1)
    moved = (reported - s["_reported_lag30"]).fillna(0.0)
    s["burn_inconsistency_30d"] = ((moved - (s["_receipt_30"] - s["_issued_30"])).abs()
                                   / denom).where(s["_reported_lag30"].notna(), 0.0)
    expected_now = (s["_att_present"] + (s["_cum_receipt"] - s["_att_cum_receipt"])
                    - (s["_cum_issued"] - s["_att_cum_issued"]))
    s["burn_recon_gap_since_count"] = ((reported - expected_now).abs() / denom).fillna(0.0)
    s["burn_issue_rate_ratio"] = (s["_issued_7_mean"]
                                  / s["_issued_30_mean"].replace(0, np.nan)).fillna(1.0)

    histories = [issued_by_series[(f, k)][max(p - PRIOR_DAYS + 1, 0): p + 1]
                 for f, k, p in zip(s["facility_id"], s["sku"], s["_pos"])]
    s["gp_unobserved_ratio"], s["gp_uncertainty_ratio"] = _staleness(
        histories, s["gp_days_since_attestation"].to_numpy(), reported.to_numpy())

    out = s[["date"] + KEYS + ["reported_stock"] + TRUST_FEATURES].copy()
    out[TRUST_FEATURES] = out[TRUST_FEATURES].astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out.reset_index(drop=True)
