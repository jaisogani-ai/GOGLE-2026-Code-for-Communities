"""
Adversarial input. The system must fail SAFE, never silently.

The standard applied in every test below is deliberately narrow and hard to
wriggle out of: for each corrupt input, EITHER a specific, named reason code
fires, OR a clean, typed exception is raised. What is not acceptable is the
third outcome -- a corrupt record passing through the detector with an empty
reason list and a clean bill of health -- because that is the failure mode that
puts a fabricated number in front of a district officer with no warning
attached to it.

Several of these cases were silent passes before this file existed; the fixes
are in tathyon/detect.py and tathyon/optimize.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tathyon import detect
from tathyon.optimize import Facility, Need, SourceStock, optimise
from tathyon.schema import REASONS, VerificationState

N = 8
DAY0 = pd.Timestamp("2026-01-01")


def base(n: int = N) -> pd.DataFrame:
    """An internally CONSISTENT ledger: closing = opening + receipts - issues,
    one batch, one expiry, sane unit value. Any reason code that fires on a
    corrupted copy of this frame is attributable to the corruption."""
    dates = pd.date_range(DAY0, periods=n)
    return pd.DataFrame({
        "date": dates,
        "facility_id": ["FAC001"] * n,
        "sku": ["AMX250"] * n,
        "reported_stock": [1000.0 - 10.0 * i for i in range(n)],
        "issued": [10.0] * n,
        "receipt": [0.0] * n,
        "batch": ["B1234"] * n,
        "obs_expiry": [pd.Timestamp("2027-06-01")] * n,
        "unit_value": [8.0] * n,
        "entered_at": dates,
        "entry_lag_days": [0] * n,
        "physically_verified": [True] * n,
        "opd": [180.0] * n,
    })


def codes_for(df: pd.DataFrame) -> set[str]:
    """Every reason code the detector raises anywhere in the frame.

    Any exception escaping here fails the test loudly rather than being
    swallowed -- a crash is a different finding from a silent pass, and the
    individual tests below distinguish them.
    """
    built = detect.build(df)
    out: set[str] = set()
    for _, row in built.iterrows():
        out |= set(detect.reason_codes(row))
    return out


# ---------------------------------------------------------------------------
# control: the clean frame must be quiet, or every test below is meaningless
# ---------------------------------------------------------------------------

def test_00_control_clean_data_raises_nothing():
    assert codes_for(base()) == set(), (
        "the control frame is not clean -- every assertion in this file that "
        "says 'this code fired because of the corruption' is then a lie")


def test_00b_every_reason_code_the_detector_emits_is_documented():
    """A code with no entry in REASONS renders as a bare identifier in the UI
    and cannot be read aloud in a hearing."""
    emitted = set()
    for mutate in (
        lambda d: d.assign(unit_value=1_110_111.20),
        lambda d: d.assign(obs_expiry=pd.NaT),
        lambda d: d.assign(entry_lag_days=947),
        lambda d: d.assign(reported_stock=-5.0),
        lambda d: d.assign(reported_stock=np.nan),
        lambda d: d.assign(entered_at=DAY0 - pd.Timedelta(days=30)),
        lambda d: d.assign(date=pd.date_range("2099-01-01", periods=N)),
    ):
        emitted |= codes_for(mutate(base()))
    undocumented = sorted(emitted - set(REASONS))
    assert not undocumented, f"reason codes with no human text: {undocumented}"


# ---------------------------------------------------------------------------
# 1. duplicate batch numbers with different expiries  (CAG Punjab 2.1.7.2(ii))
# ---------------------------------------------------------------------------

def test_01_same_batch_two_expiries():
    d = base()
    d.loc[4:, "obs_expiry"] = pd.Timestamp("2028-11-30")
    assert "BATCH_EXPIRY_CONFLICT" in codes_for(d)


# ---------------------------------------------------------------------------
# 2. negative quantities
# ---------------------------------------------------------------------------

def test_02_negative_quantity_is_flagged():
    d = base()
    d.loc[3, "reported_stock"] = -450.0
    codes = codes_for(d)
    assert "NEGATIVE_QUANTITY" in codes, (
        f"a negative physical stock passed as clean; got {sorted(codes)}")


def test_02b_negative_issue_is_flagged():
    d = base()
    d.loc[3, "issued"] = -40.0
    assert "NEGATIVE_QUANTITY" in codes_for(d)


# ---------------------------------------------------------------------------
# 3. future-dated timestamps
# ---------------------------------------------------------------------------

def test_03_future_dated_record_is_flagged():
    d = base()
    future = pd.Timestamp.utcnow().tz_localize(None) + pd.Timedelta(days=400)
    d["date"] = pd.date_range(future, periods=N)
    d["entered_at"] = d["date"]
    assert "FUTURE_DATED_RECORD" in codes_for(d)


def test_03b_tz_aware_input_does_not_crash_the_detector():
    """The API path hands the detector tz-aware timestamps and the parquet path
    hands it naive ones. Comparing the two raises TypeError in pandas, so both
    must be handled."""
    d = base()
    d["date"] = d["date"].dt.tz_localize("UTC")
    d["entered_at"] = d["entered_at"].dt.tz_localize("UTC")
    d["obs_expiry"] = d["obs_expiry"].dt.tz_localize("UTC")
    assert codes_for(d) == set()


# ---------------------------------------------------------------------------
# 4. the real CAG Maharashtra figure  (Para 2.4.8.12)
# ---------------------------------------------------------------------------

def test_04_absurd_unit_value_1110111_20():
    d = base()
    d.loc[2, "unit_value"] = 1_110_111.20
    assert "ABSURD_UNIT_VALUE" in codes_for(d)


def test_04b_plausible_unit_value_is_not_flagged():
    d = base()
    d.loc[2, "unit_value"] = 4_500.0        # an expensive but real injectable
    assert "ABSURD_UNIT_VALUE" not in codes_for(d)


# ---------------------------------------------------------------------------
# 5. missing expiry
# ---------------------------------------------------------------------------

def test_05_missing_expiry_is_reported_not_just_flagged():
    """v_missing_expiry existed as a hard violation but emitted no reason code,
    so it could never be surfaced to an officer. A violation nobody can read is
    not a control."""
    d = base()
    d.loc[3, "obs_expiry"] = pd.NaT
    built = detect.build(d)
    assert built["v_missing_expiry"].any()
    assert "MISSING_EXPIRY" in codes_for(d)


# ---------------------------------------------------------------------------
# 6. impossible delivery chronology: recorded before it happened
# ---------------------------------------------------------------------------

def test_06_receipt_entered_before_the_event_it_describes():
    d = base()
    d.loc[5, "receipt"] = 400.0
    d.loc[5, "reported_stock"] = d.loc[4, "reported_stock"] + 400.0 - 10.0
    d.loc[5, "entered_at"] = DAY0 - pd.Timedelta(days=60)   # before dispatch
    codes = codes_for(d)
    assert "IMPOSSIBLE_CHRONOLOGY" in codes, (
        f"a receipt recorded two months before it happened passed; {sorted(codes)}")


# ---------------------------------------------------------------------------
# 7. the 947-day entry lag  (CAG Punjab Para 2.1.7.6(vii))
# ---------------------------------------------------------------------------

def test_07_947_day_entry_lag():
    d = base()
    d["entry_lag_days"] = 947
    d["entered_at"] = d["date"] + pd.Timedelta(days=947)
    codes = codes_for(d)
    assert "RETROACTIVE_BULK_ENTRY" in codes
    assert "IMPOSSIBLE_CHRONOLOGY" not in codes, (
        "a LATE entry is not a chronologically impossible one; conflating them "
        "would make the rarer, harder signal useless")


# ---------------------------------------------------------------------------
# 8. NaN quantities
# ---------------------------------------------------------------------------

def test_08_nan_quantity_is_flagged_not_silently_swallowed():
    """NaN compares False against every threshold in the detector, so before
    this check a record with no quantity at all looked exactly like a clean
    one. That is the definition of a silent pass."""
    d = base()
    d.loc[4, "reported_stock"] = np.nan
    codes = codes_for(d)
    assert "NON_FINITE_QUANTITY" in codes, f"NaN passed as clean; {sorted(codes)}"


def test_08b_infinite_quantity_is_flagged():
    d = base()
    d.loc[4, "issued"] = np.inf
    assert "NON_FINITE_QUANTITY" in codes_for(d)


def test_08c_non_numeric_quantity_is_flagged():
    d = base()
    d["reported_stock"] = d["reported_stock"].astype(object)
    d.loc[4, "reported_stock"] = "NOT_AVAILABLE"
    assert "NON_FINITE_QUANTITY" in codes_for(d)


# ---------------------------------------------------------------------------
# 9. a facility id that does not exist
# ---------------------------------------------------------------------------

def test_09_unknown_facility_raises_a_named_error():
    """Not a KeyError from inside the distance function, and emphatically not a
    silent drop: silently dropping the source renders as NO_SAFE_SOURCE, which
    is a plausible-looking refusal hiding an integration bug."""
    facilities = {"FAC001": Facility("FAC001", "CHC", 0.0, 0.0)}
    src = SourceStock("FAC_DOES_NOT_EXIST", "AMX250", 300.0,
                      VerificationState.VERIFIED, q_alpha=300.0)
    needs = [Need("FAC001", "AMX250", 100.0, 2.0)]
    with pytest.raises(ValueError, match="UNKNOWN_FACILITY"):
        optimise(facilities, [src], needs)


def test_09b_unknown_recipient_facility_raises():
    facilities = {"FAC001": Facility("FAC001", "CHC", 0.0, 0.0)}
    src = SourceStock("FAC001", "AMX250", 300.0,
                      VerificationState.VERIFIED, q_alpha=300.0)
    with pytest.raises(ValueError, match="UNKNOWN_FACILITY"):
        optimise(facilities, [src], [Need("GHOST", "AMX250", 100.0, 2.0)])


# ---------------------------------------------------------------------------
# 10/11. degenerate frames
# ---------------------------------------------------------------------------

def test_10_empty_dataframe_returns_an_empty_frame_not_a_crash():
    """An empty ledger is a legitimate input: a new facility, or a filtered
    query. It previously raised
    'TypeError: Invalid comparison between dtype=datetime64[us] and ndarray'
    from the FEFO check."""
    built = detect.build(base(0))
    assert len(built) == 0
    for col in detect.VIOLATION_COLUMNS:
        assert col in built.columns, f"empty frame lost the {col} column"
    for f in detect.FEATURES:
        assert f in built.columns, f"empty frame lost the {f} feature"
    assert codes_for(base(0)) == set()


def test_11_single_row_dataframe_does_not_crash_or_false_positive():
    """One row has no previous row, so the ledger-arithmetic identity is
    UNDEFINED, not violated. It used to substitute the closing balance for the
    missing opening one, which made the first row of every single series in the
    corpus a guaranteed false positive."""
    built = detect.build(base(1))
    assert len(built) == 1
    assert not bool(built["v_arithmetic"].iloc[0]), (
        "the first row of a series cannot violate an identity that needs an "
        "opening balance it does not have")
    assert detect.reason_codes(built.iloc[0]) == []
    for f in detect.FEATURES:
        assert np.isfinite(float(built[f].iloc[0])), f"{f} is not finite on n=1"


def test_11b_two_rows_can_still_violate_arithmetic():
    """The n=1 guard must not disable the check outright."""
    d = base(2)
    d.loc[1, "reported_stock"] = 4000.0
    built = detect.build(d)
    assert bool(built["v_arithmetic"].any())
    assert "LEDGER_ARITHMETIC" in codes_for(d)


# ---------------------------------------------------------------------------
# the corpus-level guarantee
# ---------------------------------------------------------------------------

def test_corrupt_rows_never_pass_through_with_an_empty_reason_list():
    """One frame, every corruption at once, one row each. Every corrupted row
    must carry at least one reason code."""
    d = base(12)
    corrupt = {
        2: lambda r: r.__setitem__("unit_value", 1_110_111.20),
        3: lambda r: r.__setitem__("obs_expiry", pd.NaT),
        4: lambda r: r.__setitem__("reported_stock", -1.0),
        5: lambda r: r.__setitem__("issued", np.nan),
        6: lambda r: r.__setitem__("entered_at", DAY0 - pd.Timedelta(days=90)),
        7: lambda r: r.__setitem__("obs_expiry", pd.Timestamp("2030-01-01")),
    }
    for i, fn in corrupt.items():
        row = d.loc[i]
        fn(row)
        d.loc[i] = row
    built = detect.build(d).sort_values("date").reset_index(drop=True)
    silent = [i for i in corrupt
              if not detect.reason_codes(built.iloc[i])]
    assert not silent, f"corrupt rows passed with no reason code: rows {silent}"
