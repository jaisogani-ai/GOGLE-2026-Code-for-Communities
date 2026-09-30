"""Telemetry honesty, OSRM parsing, and the route provider hook feeding the planner."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from tathyon.integrations import ESTIMATED, LIVE_EXTERNAL, NOT_CONFIGURED, SIMULATION, STALE
from tathyon.osrm import (
    OSRMClient, SOURCE_OSRM, SOURCE_STRAIGHT_LINE, haversine_km, parse_osrm_route, straight_line_route,
)
from tathyon.routing import RouteIntelligenceAdapter
from tathyon.telemetry import (
    TelemetryPoint, TraccarAdapter, classify, estimated_position, simulate_along_route, simulated_report,
)

T0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
GEOM = ((81.9, 19.0), (82.0, 19.0), (82.0, 19.1))          # (lon, lat)

REAL_OSRM = json.dumps({"code": "Ok", "routes": [{
    "distance": 37595.6, "duration": 2741.3,
    "geometry": {"type": "LineString", "coordinates": [[81.9479, 19.0505], [81.85, 19.1], [81.7554, 19.2264]]}}]})


# ---------------------------------------------------------------- OSRM
def test_osrm_response_becomes_a_real_road_route():
    r = parse_osrm_route(REAL_OSRM, "A", "B", "2026-09-21T09:00:00+00:00")
    assert r.source == SOURCE_OSRM and r.is_real_road_route
    assert r.distance_km == pytest.approx(37.596, abs=0.001) and r.duration_hours == pytest.approx(0.7614, abs=0.001)
    assert len(r.geometry) == 3 and r.traffic_aware is False
    assert "not a truck" in r.vehicle_profile


def test_osrm_error_codes_raise():
    with pytest.raises(ValueError):
        parse_osrm_route(json.dumps({"code": "NoRoute"}), "A", "B", "t")


def test_client_failure_raises_never_fabricates():
    def bad(url, t):
        raise TimeoutError("down")
    with pytest.raises(RuntimeError, match="OSRM unavailable"):
        OSRMClient(get=bad, retries=1, sleep=lambda s: None).route("A", "B", (19.0, 82.0), (19.1, 82.1), "t")


def test_client_builds_lon_lat_url_and_retries():
    seen, n = [], {"n": 0}

    def get(url, t):
        seen.append(url)
        n["n"] += 1
        if n["n"] == 1:
            raise TimeoutError("blip")
        return REAL_OSRM
    r = OSRMClient(get=get, retries=1, sleep=lambda s: None).route("A", "B", (19.0505, 81.9479), (19.2264, 81.7554), "t")
    assert r.is_real_road_route and "81.9479,19.0505;81.7554,19.2264" in seen[0] and len(seen) == 2


def test_straight_line_fallback_is_labelled_synthetic():
    r = straight_line_route("A", "B", (19.0, 82.0), (19.1, 82.0), "t")
    assert r.source == SOURCE_STRAIGHT_LINE and not r.is_real_road_route
    assert r.distance_km == pytest.approx(haversine_km(19.0, 82.0, 19.1, 82.0), abs=0.01)
    assert "not a road" in r.vehicle_profile


def test_route_round_trips_through_dict():
    r = parse_osrm_route(REAL_OSRM, "A", "B", "t")
    from tathyon.osrm import RoadRoute
    assert RoadRoute.from_dict(r.to_dict()) == r


# ---------------------------------------------------------------- router hook -> planner
def test_provider_eta_reaches_the_router_and_is_labelled():
    ra = RouteIntelligenceAdapter()
    ra.provider = lambda o, d, a, b, c, e: (37.6, 0.76, SOURCE_OSRM)
    info = ra.get_route_intelligence("A", "B", 19.0, 82.0, 19.1, 82.1)
    assert info.eta_hours == 0.76 and info.data_source == SOURCE_OSRM and info.traffic_aware is False


def test_override_beats_provider_and_no_provider_means_haversine():
    ra = RouteIntelligenceAdapter()
    assert ra.get_route_intelligence("A", "B", 19.0, 82.0, 19.1, 82.1).data_source == "SYNTHETIC_HAVERSINE_ESTIMATE"
    ra.provider = lambda *a: (1, 1, SOURCE_OSRM)
    ra.set_synthetic_override("A", "B", eta_hours=9.0)
    assert ra.get_route_intelligence("A", "B", 19.0, 82.0, 19.1, 82.1).eta_hours == 9.0


def test_provider_returning_none_falls_back_to_estimate():
    ra = RouteIntelligenceAdapter()
    ra.provider = lambda *a: None
    assert ra.get_route_intelligence("A", "B", 19.0, 82.0, 19.1, 82.1).data_source == "SYNTHETIC_HAVERSINE_ESTIMATE"


# ---------------------------------------------------------------- telemetry honesty
def pt(minutes_ago):
    return TelemetryPoint((T0 - timedelta(minutes=minutes_ago)).isoformat(), 19.0, 82.0, LIVE_EXTERNAL, "TRACCAR")


def test_no_points_means_not_available_never_a_position():
    r = classify([], T0)
    assert r.state == NOT_CONFIGURED and r.points == [] and "NOT AVAILABLE" in r.label


def test_recent_real_points_are_live_and_old_ones_are_stale_with_age():
    assert classify([pt(5)], T0).state == LIVE_EXTERNAL
    stale = classify([pt(45), pt(50)], T0)
    assert stale.state == STALE and "45 min" in stale.label and stale.age_minutes == 45.0


def test_stale_threshold_is_configurable():
    assert classify([pt(45)], T0, stale_after_minutes=60).state == LIVE_EXTERNAL


def test_traccar_unconfigured_returns_nothing():
    a = TraccarAdapter(None)
    assert not a.configured and a.positions(1, T0, T0) == []


def test_traccar_parses_real_shape_and_skips_incomplete_fixes():
    payload = json.dumps([
        {"fixTime": "2026-09-21T09:00:00.000+00:00", "latitude": 19.05, "longitude": 81.95, "speed": 10.0,
         "attributes": {"temp1": 4.5, "batteryLevel": 88}},
        {"fixTime": "2026-09-21T09:15:00.000+00:00", "latitude": None, "longitude": 81.96},      # no fix
        {"fixTime": "2026-09-21T09:30:00.000+00:00", "latitude": 19.07, "longitude": 81.97, "attributes": {}},
    ])
    seen = {}

    def get(url, headers):
        seen["url"], seen["h"] = url, headers
        return payload
    pts = TraccarAdapter("http://traccar.local/", "u", "p", get=get).positions(7, T0, T0 + timedelta(hours=1))
    assert len(pts) == 2 and pts[0].provenance == LIVE_EXTERNAL and pts[0].source == "TRACCAR"
    assert pts[0].speed_kmh == 18.5 and pts[0].temperature_c == 4.5 and pts[0].battery_pct == 88
    assert pts[1].temperature_c is None                       # never invented
    assert "deviceId=7" in seen["url"] and seen["h"]["Authorization"].startswith("Basic ")


def test_simulated_points_are_labelled_simulation_never_live():
    pts = simulate_along_route(GEOM, T0, 2.0, T0 + timedelta(hours=1), step_minutes=15)
    assert len(pts) == 5 and all(p.provenance == SIMULATION and p.source == "SIMULATED TELEMETRY" for p in pts)
    rep = simulated_report(pts)
    assert rep.state == SIMULATION and rep.label == "SIMULATED TELEMETRY"
    assert LIVE_EXTERNAL not in (rep.state, rep.label)


def test_simulation_moves_along_the_route_and_reaches_the_end():
    pts = simulate_along_route(GEOM, T0, 2.0, T0 + timedelta(hours=5), step_minutes=30)
    assert (pts[0].lon, pts[0].lat) == GEOM[0]
    last = pts[-1]
    assert (round(last.lon, 3), round(last.lat, 3)) == (82.0, 19.1)      # clamped at destination


def test_no_simulated_points_before_dispatch():
    assert simulate_along_route(GEOM, T0, 2.0, T0 - timedelta(minutes=1)) == []
    assert simulated_report([]).state == NOT_CONFIGURED


def test_estimated_position_is_labelled_estimated():
    p = estimated_position(GEOM, T0, 2.0, T0 + timedelta(hours=1))
    assert p.provenance == ESTIMATED and "no tracker" in p.source
    assert estimated_position(GEOM, T0, 2.0, T0 - timedelta(hours=1)) is None
