"""
Anomaly detection: hard violations + weak-supervision rule ensemble.

Day one we have ZERO human-verified labels. Pretending otherwise would be the
single most dishonest thing in this repo. So we do not ship a fake classifier.

Instead:
  TIER 1 - HARD VIOLATIONS. Provably wrong with no human time at all, because
           they are internally inconsistent. Free labels.
  TIER 2 - WEAK SIGNALS. Suspicious but not dispositive. Calibrated against
           tier-1 labels so the output is a probability, not a vibe.

The named failure mode we actively defend against: a model that learns
"unusual" (big hospital, new SKU, remote PHC with lumpy supply) instead of
"wrong". Detectors for that live in eval.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .schema import REASONS

ABSURD_UNIT_VALUE = 50_000.0     # no single dose costs this much
STALE_DAYS = 30

# The complete, ordered set of hard-violation columns. Declared here rather
# than discovered by prefix so that an EMPTY frame has the same schema as a
# full one, and so tests can assert none of them leaks into FEATURES.
VIOLATION_COLUMNS = [
    "v_arithmetic", "v_negative_consumption", "v_batch_expiry_conflict",
    "v_issue_against_expired", "v_absurd_unit_value", "v_missing_expiry",
    "v_negative_qty", "v_nonfinite_qty", "v_future_dated",
    "v_impossible_chronology",
]


# ---------------------------------------------------------------------------
# Tier 1 -- hard violations. These are FREE LABELS.
# ---------------------------------------------------------------------------

def hard_violations(df: pd.DataFrame) -> pd.DataFrame:
    """Internally-inconsistent records. No ground truth needed to know these
    are wrong -- the record contradicts itself."""
    d = df.copy().sort_values(["facility_id", "sku", "date"])
    if d.empty:
        # An empty ledger is a legitimate input (a new facility, a filtered
        # query). It must produce an empty, correctly-shaped frame -- not a
        # TypeError from comparing an empty datetime column to an ndarray.
        for c in VIOLATION_COLUMNS:
            d[c] = pd.Series(dtype=bool)
        d["prev_stock"] = pd.Series(dtype=float)
        d["implied_consumption"] = pd.Series(dtype=float)
        d["n_expiry"] = pd.Series(dtype=float)
        d["hard_violation"] = pd.Series(dtype=bool)
        d["n_hard_violations"] = pd.Series(dtype=int)
        return d
    # --- coerce quantity columns BEFORE any arithmetic -----------------------
    # A string in a quantity column previously raised
    #   TypeError: can only concatenate str (not "float") to str
    # deep inside a pandas binary op. That is a CRASH, not a flag, and a crash
    # is the one failure mode this system must never have: an ingestion batch
    # containing one corrupt cell would take down the detector for every other
    # record in it. Garbage in a quantity column is itself a finding, so we
    # coerce to NaN, record what was lost, and let v_nonfinite_qty report it.
    QTY_COLS = ("reported_stock", "issued", "receipt", "unit_value")
    d["_coercion_loss"] = False
    for c in QTY_COLS:
        if c in d.columns:
            raw = d[c]
            num = pd.to_numeric(raw, errors="coerce")
            # notna on the original but na after coercion == unparseable value
            d["_coercion_loss"] |= raw.notna() & num.isna()
            d[c] = num

    g = d.groupby(["facility_id", "sku"], sort=False)

    d["prev_stock"] = g["reported_stock"].shift(1)
    # closing != opening + receipts - issues
    expected = d["prev_stock"] + d["receipt"] - d["issued"]
    # The FIRST row of a series has no opening balance, so the identity
    # "closing == opening + receipts - issues" is undefined there. The previous
    # code substituted the closing balance for the missing opening one, which
    # made every series' seed row violate by exactly (receipts - issues) -- a
    # guaranteed false positive on every (facility, sku) pair in the corpus.
    d["v_arithmetic"] = d["prev_stock"].notna() & (
        (d["reported_stock"] - expected).abs() > np.maximum(
            1.0, 0.02 * d["reported_stock"].abs()))

    # implied consumption negative
    d["implied_consumption"] = d["prev_stock"] + d["receipt"] - d["reported_stock"]
    d["v_negative_consumption"] = d["implied_consumption"] < -1.0

    # same batch, different expiry  (CAG Punjab Para 2.1.7.2(ii))
    bx = d.dropna(subset=["obs_expiry"]).groupby(["facility_id", "sku", "batch"])[
        "obs_expiry"].nunique().rename("n_expiry").reset_index()
    d = d.merge(bx, on=["facility_id", "sku", "batch"], how="left")
    d["v_batch_expiry_conflict"] = d["n_expiry"].fillna(1) > 1

    # issue against an already-expired batch  (FEFO / Delhi Ch.4)
    d["v_issue_against_expired"] = (
        (d["issued"] > 0) & d["obs_expiry"].notna() & (d["date"] > d["obs_expiry"]))

    # absurd unit value  (CAG Maharashtra Para 2.4.8.12)
    d["v_absurd_unit_value"] = d["unit_value"] > ABSURD_UNIT_VALUE

    # missing expiry on a dated commodity
    d["v_missing_expiry"] = d["obs_expiry"].isna()

    # --- corrupt-input violations -------------------------------------------
    # These exist because the alternative is a SILENT PASS. Garbage in the
    # quantity columns previously produced no violation and no reason code at
    # all: a NaN compares False against every threshold above, so a record with
    # no quantity looked exactly like a clean one. Failing safe means saying so.
    qty_cols = [c for c in ("reported_stock", "issued", "receipt") if c in d.columns]
    qty = d[qty_cols].apply(pd.to_numeric, errors="coerce")
    d["v_negative_qty"] = (qty < 0).any(axis=1)
    # A value that could not be parsed at all is non-finite for our purposes:
    # we refuse to treat an unreadable quantity as if it were absent.
    d["v_nonfinite_qty"] = (
        ~np.isfinite(qty.to_numpy(dtype=float)).all(axis=1)) | d["_coercion_loss"]

    # utc=True so this works whether the caller's timestamps are naive (the
    # parquet path) or tz-aware (the API path). Mixing the two raises.
    dt = pd.to_datetime(d["date"], errors="coerce", utc=True)
    d["v_future_dated"] = dt > pd.Timestamp.now(tz="UTC")
    if "entered_at" in d.columns:
        entered = pd.to_datetime(d["entered_at"], errors="coerce", utc=True)
        # a record cannot be entered into the ledger before the event happened
        d["v_impossible_chronology"] = (entered.notna() & dt.notna()
                                        & (entered < dt))
    else:
        d["v_impossible_chronology"] = False

    vcols = [c for c in d.columns if c.startswith("v_")]
    d["hard_violation"] = d[vcols].any(axis=1)
    d["n_hard_violations"] = d[vcols].sum(axis=1)
    return d


# ---------------------------------------------------------------------------
# Tier 2 -- weak signals, computed from OBSERVED data only
# ---------------------------------------------------------------------------

def weak_features(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy().sort_values(["facility_id", "sku", "date"])
    g = d.groupby(["facility_id", "sku"], sort=False)

    # staleness: days since this series was physically verified
    d["days_since_verified"] = (
        g["physically_verified"].transform(
            lambda s: (~s).groupby(s.cumsum()).cumcount()))

    # entry lag (retroactive bulk entry -- CAG Punjab Para 2.1.7.6(vii))
    d["f_entry_lag"] = np.log1p(d["entry_lag_days"])

    # round-number density: the classic fabrication tell
    d["f_round_number"] = (d["reported_stock"] % 50 == 0).astype(float)

    # facility-RELATIVE quantity deviation. Facility-relative on purpose:
    # normalising globally is exactly how a model learns "big hospital" = "wrong".
    mu = g["reported_stock"].transform(lambda s: s.rolling(28, min_periods=5).mean())
    sd = g["reported_stock"].transform(lambda s: s.rolling(28, min_periods=5).std())
    d["f_qty_z"] = ((d["reported_stock"] - mu) / sd.replace(0, np.nan)).abs().fillna(0)

    # consumption per unit OPD, against the facility's own history
    cons = d["issued"] / d["opd"].replace(0, np.nan)
    d["f_cons_per_opd_z"] = (
        cons - cons.groupby([d["facility_id"], d["sku"]]).transform("mean")
    ).abs() / cons.groupby([d["facility_id"], d["sku"]]).transform("std").replace(0, np.nan)
    d["f_cons_per_opd_z"] = d["f_cons_per_opd_z"].fillna(0)

    # inter-arrival regularity: real consumption is bursty, fabricated is smooth
    d["f_smoothness"] = 1.0 / (1.0 + g["issued"].transform(
        lambda s: s.rolling(14, min_periods=4).std()).fillna(0))

    # expiry proximity
    d["f_days_to_expiry"] = (
        pd.to_datetime(d["obs_expiry"]) - pd.to_datetime(d["date"])
    ).dt.days.fillna(-999)
    d["f_expired_but_stocked"] = (
        (d["f_days_to_expiry"] < 0) & (d["reported_stock"] > 0)).astype(float)

    d["f_stale"] = (d["days_since_verified"] > STALE_DAYS).astype(float)
    d["f_days_since_verified"] = np.log1p(d["days_since_verified"])
    return d


FEATURES = [
    "f_entry_lag", "f_round_number", "f_qty_z", "f_cons_per_opd_z",
    "f_smoothness", "f_expired_but_stocked", "f_stale", "f_days_since_verified",
]


def build(df: pd.DataFrame) -> pd.DataFrame:
    return weak_features(hard_violations(df))


def reason_codes(row) -> list[str]:
    """Machine-readable reasons. Never a bare score. An officer must be able to
    read the reason aloud in a hearing."""
    r = []
    if row.get("v_batch_expiry_conflict"):
        r.append("BATCH_EXPIRY_CONFLICT")
    if row.get("v_arithmetic"):
        r.append("LEDGER_ARITHMETIC")
    if row.get("v_negative_consumption"):
        r.append("NEGATIVE_IMPLIED_CONSUMPTION")
    if row.get("v_issue_against_expired"):
        r.append("ISSUE_AGAINST_EXPIRED")
    if row.get("v_absurd_unit_value"):
        r.append("ABSURD_UNIT_VALUE")
    # v_missing_expiry has existed as a hard violation since the first commit
    # but had no reason code, so it could never be shown to an officer. A
    # violation nobody can read is not a control.
    if row.get("v_missing_expiry"):
        r.append("MISSING_EXPIRY")
    if row.get("v_negative_qty"):
        r.append("NEGATIVE_QUANTITY")
    if row.get("v_nonfinite_qty"):
        r.append("NON_FINITE_QUANTITY")
    if row.get("v_future_dated"):
        r.append("FUTURE_DATED_RECORD")
    if row.get("v_impossible_chronology"):
        r.append("IMPOSSIBLE_CHRONOLOGY")
    if row.get("f_qty_z", 0) > 3:
        r.append("QTY_OUTLIER")
    if row.get("entry_lag_days", 0) > 20:
        r.append("RETROACTIVE_BULK_ENTRY")
    if row.get("days_since_verified", 0) > STALE_DAYS:
        r.append("ATTESTATION_STALE")
    return r


def explain(codes: list[str]) -> list[dict]:
    return [{"code": c, "text": REASONS.get(c, c)} for c in codes]
