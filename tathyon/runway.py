"""
Data-age-aware runway, indent-lag learning and the pending-indent check.

Pure functions. No clock reads: every function takes `now` explicitly, so results are
reproducible and testable.

Two questions this module answers:
  1. "How long will this stock last, given how old the observation is?"
     A count is only true at the moment it was taken. Consumption since then is unrecorded,
     so the estimate becomes a RANGE that widens with age. Missing stock is never invented:
     the upper bound never exceeds the observed quantity.
  2. "Will the supply already on order arrive before the facility fails?"
     Lead time comes from this facility's own indent -> receipt history, or the stated
     expected date. There is no built-in lead time.

Every threshold is configuration (FreshnessPolicy / LagPolicy). None is a legal limit.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

FRESH = "FRESH"
AGING = "AGING"
STALE = "STALE"
VERIFY_REQUIRED = "VERIFY_REQUIRED"

LEAD_TIME_INSUFFICIENT_DATA = "LEAD_TIME_INSUFFICIENT_DATA"
LEAD_TIME_LEARNED = "LEARNED_FROM_HISTORY"

NO_PENDING_INDENT = "NO_PENDING_INDENT"
PENDING_SUPPLY_SUFFICIENT = "PENDING_SUPPLY_SUFFICIENT"
PENDING_INDENT_TOO_LATE = "PENDING_INDENT_TOO_LATE"
PENDING_ARRIVAL_UNKNOWN = "PENDING_ARRIVAL_UNKNOWN"


@dataclass(frozen=True)
class FreshnessPolicy:
    """CONFIGURATION, not law. Defaults are demo assumptions and must be set per deployment."""
    fresh_hours: float = 24.0
    aging_hours: float = 72.0
    stale_hours: float = 168.0
    # Unrecorded consumption since the observation is modelled as rate * age * (1 +/- this).
    unrecorded_consumption_uncertainty: float = 0.25

    def validate(self) -> None:
        if not 0 < self.fresh_hours <= self.aging_hours <= self.stale_hours:
            raise ValueError("freshness thresholds must satisfy 0 < fresh <= aging <= stale")
        if not 0 <= self.unrecorded_consumption_uncertainty <= 1:
            raise ValueError("unrecorded_consumption_uncertainty must be within [0, 1]")


@dataclass(frozen=True)
class LagPolicy:
    min_samples: int = 3            # below this: LEAD_TIME_INSUFFICIENT_DATA


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class ObservationRunway:
    observed_quantity: float
    observed_at: str
    observation_age_hours: float
    freshness_status: str
    stock_low: float
    stock_estimate: float
    stock_high: float
    runway_hours_low: Optional[float]
    runway_hours_estimate: Optional[float]
    runway_hours_high: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


def age_adjusted_runway(
    observed_quantity: float,
    observed_at: str,
    consumption_per_day: float,
    now: datetime,
    policy: FreshnessPolicy = FreshnessPolicy(),
) -> ObservationRunway:
    """Stock and runway range as of `now`, from a count taken at `observed_at`."""
    policy.validate()
    if observed_quantity < 0 or consumption_per_day < 0:
        raise ValueError("observed_quantity and consumption_per_day must be >= 0")
    age_h = (now - parse_ts(observed_at)).total_seconds() / 3600.0
    if age_h < -1e-6:
        raise ValueError("observed_at is in the future relative to now")
    age_h = max(age_h, 0.0)

    rate_h = consumption_per_day / 24.0
    u = policy.unrecorded_consumption_uncertainty
    consumed_best = rate_h * age_h
    consumed_lo, consumed_hi = consumed_best * (1 - u), consumed_best * (1 + u)

    best = max(observed_quantity - consumed_best, 0.0)
    high = min(max(observed_quantity - consumed_lo, 0.0), observed_quantity)   # never above the count
    low = max(observed_quantity - consumed_hi, 0.0)

    if age_h <= policy.fresh_hours:
        status = FRESH
    elif age_h <= policy.aging_hours:
        status = AGING
    elif age_h <= policy.stale_hours:
        status = STALE
    else:
        status = VERIFY_REQUIRED

    def hrs(q: float) -> Optional[float]:
        return None if rate_h <= 0 else round(q / rate_h, 2)

    return ObservationRunway(
        observed_quantity=observed_quantity, observed_at=observed_at,
        observation_age_hours=round(age_h, 2), freshness_status=status,
        stock_low=round(low, 2), stock_estimate=round(best, 2), stock_high=round(high, 2),
        runway_hours_low=hrs(low), runway_hours_estimate=hrs(best), runway_hours_high=hrs(high),
    )


# ---------------------------------------------------------------- indent lag
@dataclass
class IndentLag:
    status: str
    sample_count: int
    median_days: Optional[float]
    min_days: Optional[float]
    max_days: Optional[float]
    last_observed_days: Optional[float]

    def to_dict(self) -> dict:
        return asdict(self)


def learn_indent_lag(
    records: Iterable[tuple[str, str]],
    policy: LagPolicy = LagPolicy(),
) -> IndentLag:
    """Empirical indent -> receipt duration from (indent_date, receipt_date) pairs."""
    pairs = []
    for indent, receipt in records:
        i, r = parse_ts(indent), parse_ts(receipt)
        if r >= i:
            pairs.append((r, (r - i).total_seconds() / 86400.0))
    if len(pairs) < policy.min_samples:
        return IndentLag(LEAD_TIME_INSUFFICIENT_DATA, len(pairs), None, None, None,
                         round(max(pairs)[1], 2) if pairs else None)
    days = [d for _, d in pairs]
    return IndentLag(
        LEAD_TIME_LEARNED, len(pairs), round(statistics.median(days), 2),
        round(min(days), 2), round(max(days), 2), round(max(pairs)[1], 2),
    )


@dataclass
class PendingIndent:
    order_id: str
    quantity: float
    indent_date: str
    expected_arrival: Optional[str] = None      # stated by the supplier / system of record


@dataclass
class PendingIndentResult:
    status: str
    details: list[dict]
    earliest_late_arrival_hours: Optional[float]     # the "bridge" target if the indent is too late
    on_time_quantity: float

    def to_dict(self) -> dict:
        return asdict(self)


def check_pending_indents(
    pending: list[PendingIndent],
    lag: IndentLag,
    now: datetime,
    failure_hours: Optional[float],
) -> PendingIndentResult:
    """Does the supply already on order land before failure?

    Arrival used = the LATER of the stated expected date and indent_date + learned median lag.
    With insufficient history only the stated date is used (and reported as such).
    Supply arriving after `failure_hours` is NOT counted as a rescue.
    """
    if not pending:
        return PendingIndentResult(NO_PENDING_INDENT, [], None, 0.0)

    details, late_arrivals, on_time_qty = [], [], 0.0
    for p in pending:
        candidates, basis = [], []
        if p.expected_arrival:
            candidates.append((parse_ts(p.expected_arrival) - now).total_seconds() / 3600.0)
            basis.append("STATED_EXPECTED_DATE")
        if lag.median_days is not None:
            candidates.append((parse_ts(p.indent_date) - now).total_seconds() / 3600.0 + lag.median_days * 24.0)
            basis.append("INDENT_DATE_PLUS_LEARNED_MEDIAN_LAG")
        if not candidates:
            details.append({"order_id": p.order_id, "quantity": p.quantity, "arrival_hours": None,
                            "status": "ARRIVAL_UNKNOWN", "basis": [LEAD_TIME_INSUFFICIENT_DATA]})
            continue
        arrival_h = max(max(candidates), 0.0)
        on_time = failure_hours is None or arrival_h < failure_hours
        details.append({"order_id": p.order_id, "quantity": p.quantity, "arrival_hours": round(arrival_h, 2),
                        "status": "ARRIVES_BEFORE_FAILURE" if on_time else PENDING_INDENT_TOO_LATE,
                        "basis": basis})
        if on_time:
            on_time_qty += p.quantity
        else:
            late_arrivals.append(arrival_h)

    if on_time_qty > 0:
        status = PENDING_SUPPLY_SUFFICIENT
    elif not late_arrivals:
        status = PENDING_ARRIVAL_UNKNOWN          # no stated date and no history: do not guess
    else:
        status = PENDING_INDENT_TOO_LATE
    return PendingIndentResult(
        status, details, round(min(late_arrivals), 2) if late_arrivals else None, on_time_qty,
    )
