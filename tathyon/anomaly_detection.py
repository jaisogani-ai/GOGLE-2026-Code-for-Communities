"""History-gated, advisory outlier checks for incoming resource observations.

This module is deterministic statistics, not an AI model or fraud detector. It
uses only prior observations with the same facility, resource, metric, unit,
and provenance class; it never fills missing history with synthetic proxies.
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable


MIN_HISTORY = 8
ROBUST_Z_THRESHOLD = 3.5


def score_observation(
    value: float,
    history: Iterable[float],
    *,
    min_history: int = MIN_HISTORY,
    threshold: float = ROBUST_Z_THRESHOLD,
) -> dict[str, Any]:
    """Compare one finite value with a like-for-like historical series.

    A robust z-score is an operational review threshold, not a hypothesis-test
    p-value or a claim of statistical significance. The caller should preserve
    that distinction in UI and downstream use.
    """
    current = float(value)
    prior = [float(x) for x in history if math.isfinite(float(x))]
    if not math.isfinite(current):
        raise ValueError("observation must be finite")
    if len(prior) < min_history:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "flagged": False,
            "history_count": len(prior),
            "minimum_history": min_history,
            "reason": f"Need at least {min_history} comparable prior observations; found {len(prior)}.",
        }

    center = median(prior)
    abs_dev = [abs(x - center) for x in prior]
    mad = median(abs_dev)
    # MAD is robust to extreme historical points. When it is zero (common for
    # integer bed/staff counts), use IQR as a spread estimate. If the whole
    # history is constant, use one unit as a conservative measurement floor.
    if mad > 0:
        scale = 1.4826 * mad
        scale_method = "MAD_NORMALIZED"
    else:
        ordered = sorted(prior)
        q1 = _quantile(ordered, 0.25)
        q3 = _quantile(ordered, 0.75)
        iqr = q3 - q1
        if iqr > 0:
            scale = iqr / 1.349
            scale_method = "IQR_FALLBACK"
        else:
            scale = max(1.0, abs(center) * 0.01)
            scale_method = "CONSTANT_HISTORY_FLOOR"

    robust_z = (current - center) / scale
    flagged = abs(robust_z) >= threshold
    direction = "ABOVE" if current > center else "BELOW" if current < center else "AT"
    return {
        "status": "FLAGGED_FOR_REVIEW" if flagged else "WITHIN_EXPECTED_RANGE",
        "flagged": flagged,
        "history_count": len(prior),
        "minimum_history": min_history,
        "historical_median": round(center, 6),
        "historical_mad": round(mad, 6),
        "scale": round(scale, 6),
        "scale_method": scale_method,
        "robust_z": round(robust_z, 4),
        "threshold": threshold,
        "direction": direction,
        "reason": (
            f"Current value is {direction.lower()} the comparable-history median by "
            f"{abs(robust_z):.2f} robust scale units (review threshold {threshold:.1f})."
        ),
        "candidate_explanations": [
            "data entry, unit, or product/ward/role mapping difference",
            "reporting-period or timing difference",
            "a genuine operational change in supply, demand, occupancy, or staffing",
        ] if flagged else [],
        "interpretation_limit": "A statistical deviation is a review cue, not proof of an error, cause, loss, or misconduct.",
    }


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    pos = (len(values) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)
