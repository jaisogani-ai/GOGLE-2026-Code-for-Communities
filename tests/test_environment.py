"""Open-Meteo precipitation layer: honest state handling, no network."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tathyon.environment import (
    age_hours, fetch_precipitation, load_cache, mark_stale, parse_forecast, save_cache, state_of,
)
from tathyon.integrations import LIVE_EXTERNAL, NOT_CONFIGURED, STALE

AT = "2026-09-21T06:00:00+00:00"
NOW = datetime(2026, 9, 21, 6, 30, tzinfo=timezone.utc)
POINTS = [("A", 19.01, 81.87), ("B", 19.05, 81.95)]


def hourly(vals):
    return {"hourly": {"time": [f"2026-09-21T{h:02d}:00" for h in range(len(vals))], "precipitation": vals}}


def test_parses_multi_point_response_and_computes_from_data_only():
    payload = json.dumps([hourly([0, 0.5, 2.7, 0.1]), hourly([0, 0, 0, 0])])
    a, b = parse_forecast(payload, POINTS, AT)
    assert (a.total_mm, a.max_mm_per_hour, a.peak_hour_utc) == (3.3, 2.7, "2026-09-21T02:00")
    assert b.total_mm == 0 and b.peak_hour_utc is None
    assert a.state == LIVE_EXTERNAL and a.source == "OPEN-METEO" and a.used_in_decisions is False
    assert "not an observation" in a.kind and "CC BY" in a.attribution


def test_single_location_response_dict_is_accepted():
    (a,) = parse_forecast(json.dumps(hourly([1.0, 1.0])), POINTS[:1], AT)
    assert a.total_mm == 2.0 and a.horizon_hours == 2


def test_null_hours_are_dropped_never_treated_as_no_rain():
    (a,) = parse_forecast(json.dumps(hourly([None, 4.0, None])), POINTS[:1], AT)
    assert a.horizon_hours == 1 and a.max_mm_per_hour == 4.0


def test_all_null_or_missing_data_raises():
    with pytest.raises(ValueError):
        parse_forecast(json.dumps(hourly([None, None])), POINTS[:1], AT)
    with pytest.raises(ValueError):
        parse_forecast(json.dumps({"hourly": {}}), POINTS[:1], AT)


def test_location_count_mismatch_raises():
    with pytest.raises(ValueError, match="2 locations for 1"):
        parse_forecast(json.dumps([hourly([1]), hourly([1])]), POINTS[:1], AT)


def test_fetch_builds_one_multi_point_request_and_failure_raises():
    seen = []

    def get(url, t):
        seen.append(url)
        return json.dumps([hourly([1.0]), hourly([2.0])])
    out = fetch_precipitation(POINTS, AT, get=get)
    assert len(seen) == 1 and "latitude=19.0100%2C19.0500" in seen[0] and "hourly=precipitation" in seen[0]
    assert [o.total_mm for o in out] == [1.0, 2.0]

    def boom(url, t):
        raise TimeoutError("down")
    with pytest.raises(RuntimeError, match="Open-Meteo unavailable"):
        fetch_precipitation(POINTS, AT, get=boom)
    assert fetch_precipitation([], AT, get=boom) == []


def test_old_cache_becomes_stale_with_the_same_numbers():
    items = parse_forecast(json.dumps([hourly([1.0]), hourly([2.0])]), POINTS, AT)
    fresh = mark_stale(items, NOW)
    assert state_of(fresh) == LIVE_EXTERNAL
    later = mark_stale(items, NOW + timedelta(hours=8))
    assert state_of(later) == STALE and later[0].total_mm == items[0].total_mm
    assert mark_stale(items, NOW + timedelta(hours=8), stale_after_hours=24)[0].state == LIVE_EXTERNAL


def test_no_data_is_not_configured():
    assert state_of([]) == NOT_CONFIGURED
    assert age_hours(AT, NOW) == pytest.approx(0.5)


def test_cache_round_trip(tmp_path):
    items = parse_forecast(json.dumps([hourly([1.0, 0.0]), hourly([2.0, 3.0])]), POINTS, AT)
    p = str(tmp_path / "env.json")
    save_cache(items, p)
    assert load_cache(p) == items and load_cache(str(tmp_path / "none.json")) == []
