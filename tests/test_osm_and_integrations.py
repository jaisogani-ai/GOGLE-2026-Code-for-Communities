"""Real OSM facility adapter + honest integration matrix. No network access in tests."""
import json
import pytest

from tathyon.integrations import (
    CATALOGUE, LIVE_EXTERNAL, NOT_CONFIGURED, PROVENANCE_VALUES, SYNTHETIC,
    capability_state, integration_matrix,
)
from tathyon.osm_facilities import (
    FETCH_OK, FETCH_PARTIAL, FETCH_UNAVAILABLE, build_query, fetch_osm_facilities,
    parse_elements, tile_bbox,
)

AT = "2026-09-21T08:00:00+00:00"
BASTAR = (19.0, 81.9, 19.2, 82.1)

# Verbatim shape of a real Overpass response (this record was actually returned).
REAL_PAYLOAD = json.dumps({"elements": [
    {"type": "node", "id": 7522069244, "lat": 19.0845103, "lon": 82.0236900,
     "tags": {"amenity": "hospital", "name": "Maharani Hospital", "addr:district": "Jagdalpur"}},
    {"type": "way", "id": 123, "center": {"lat": 19.05054, "lon": 81.94831},
     "tags": {"amenity": "hospital", "name": "Government Medical College Hospital, Bastar"}},
    {"type": "node", "id": 999, "lat": 19.1, "lon": 82.0, "tags": {"amenity": "clinic"}},  # unnamed
]})


def ok(payload=REAL_PAYLOAD):
    return lambda q, e, t: payload


def boom(q, e, t):
    raise TimeoutError("HTTP 504")


# ---------------------------------------------------------------- tiling
def test_large_bbox_is_split_because_overpass_504s_on_wide_areas():
    assert len(tile_bbox(18.7, 81.4, 19.6, 82.3, step=0.3)) == 9
    assert len(tile_bbox(19.0, 81.9, 19.2, 82.1, step=0.5)) == 1


def test_tiles_cover_the_box_without_exceeding_it():
    for s, w, n, e in tile_bbox(18.7, 81.4, 19.6, 82.3, step=0.4):
        assert 18.7 <= s < n <= 19.6 and 81.4 <= w < e <= 82.3


@pytest.mark.parametrize("bad", [(19.2, 81.9, 19.0, 82.1), (19.0, 82.1, 19.2, 81.9), (-91, 0, 10, 10)])
def test_invalid_bbox_rejected(bad):
    with pytest.raises(ValueError):
        tile_bbox(*bad)


def test_query_targets_healthcare_amenities_in_the_bbox():
    q = build_query((19.0, 81.9, 19.2, 82.1))
    assert "hospital|clinic" in q and "19.0,81.9,19.2,82.1" in q and "out:json" in q


# ---------------------------------------------------------------- parsing
def test_parses_real_payload_keeping_identity_and_provenance():
    fs = parse_elements(REAL_PAYLOAD, AT)
    assert len(fs) == 3
    m = next(f for f in fs if f.osm_id == 7522069244)
    assert m.name == "Maharani Hospital" and m.amenity == "hospital"
    assert (round(m.lat, 4), round(m.lon, 4)) == (19.0845, 82.0237)
    assert m.source == "OPENSTREETMAP" and m.provenance == LIVE_EXTERNAL
    assert m.osm_url == "https://www.openstreetmap.org/node/7522069244"
    assert m.fetched_at == AT


def test_way_uses_center_coordinates():
    w = next(f for f in parse_elements(REAL_PAYLOAD, AT) if f.osm_type == "way")
    assert (w.lat, w.lon) == (19.05054, 81.94831)


def test_elements_without_coordinates_or_amenity_are_skipped_not_guessed():
    payload = json.dumps({"elements": [
        {"type": "node", "id": 1, "tags": {"amenity": "hospital"}},              # no coords
        {"type": "node", "id": 2, "lat": 19.0, "lon": 82.0, "tags": {}},         # no amenity
        {"type": "node", "id": 3, "lat": 19.0, "lon": 82.0, "tags": {"amenity": "clinic"}},
    ]})
    assert [f.osm_id for f in parse_elements(payload, AT)] == [3]


# ---------------------------------------------------------------- fetch
def test_successful_fetch_reports_real_named_facilities():
    r = fetch_osm_facilities(BASTAR, AT, transport=ok())
    assert r.fetch_status == FETCH_OK and r.tiles_failed == 0
    assert len(r.facilities) == 3 and len(r.named) == 2
    assert r.attribution == "© OpenStreetMap contributors, ODbL"
    assert r.to_dict()["provenance"] == LIVE_EXTERNAL


def test_duplicate_elements_across_tiles_are_deduplicated():
    r = fetch_osm_facilities((18.7, 81.4, 19.7, 82.4), AT, transport=ok(), step=0.5)
    assert r.tiles_requested == 4 and len(r.facilities) == 3    # same 3 ids from every tile


def test_total_failure_is_unavailable_and_returns_nothing():
    r = fetch_osm_facilities(BASTAR, AT, transport=boom, retries=0)
    assert r.fetch_status == FETCH_UNAVAILABLE
    assert r.facilities == [] and r.tiles_failed == r.tiles_requested
    assert r.to_dict()["provenance"] == NOT_CONFIGURED          # never claims live with no data
    assert "504" in r.failures[0]


def test_partial_failure_is_reported_not_hidden():
    """A failed tile must be distinguishable from 'no facilities mapped there'."""
    calls = {"n": 0}

    def flaky(q, e, t):
        calls["n"] += 1
        if calls["n"] % 2 == 0:
            raise TimeoutError("HTTP 504")
        return REAL_PAYLOAD

    r = fetch_osm_facilities((18.7, 81.4, 19.7, 82.4), AT, transport=flaky, step=0.5, retries=0)
    assert r.fetch_status == FETCH_PARTIAL
    assert 0 < r.tiles_failed < r.tiles_requested and r.failures


def test_transient_504_is_retried_and_recovers():
    calls = {"n": 0}

    def once_bad(q, e, t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("HTTP 504")
        return REAL_PAYLOAD

    slept = []
    r = fetch_osm_facilities(BASTAR, AT, transport=once_bad, retries=2, sleep=slept.append)
    assert r.fetch_status == FETCH_OK and len(r.facilities) == 3 and slept == [2.0]


def test_empty_area_is_ok_with_zero_facilities():
    r = fetch_osm_facilities(BASTAR, AT, transport=ok(json.dumps({"elements": []})))
    assert r.fetch_status == FETCH_OK and r.facilities == []


# ---------------------------------------------------------------- integration matrix
def test_every_capability_has_a_legal_provenance_state():
    for row in integration_matrix(env={}):
        assert row["state"] in PROVENANCE_VALUES


def test_uncredentialed_integrations_report_not_configured():
    for cap in ("google_routes", "shipment_gps_telemetry",
                "cold_chain_temperature", "environmental_access_signal",
                "government_stock_system_of_record"):
        assert capability_state(cap, env={}) == NOT_CONFIGURED


def test_credentials_alone_never_promote_an_unimplemented_capability_to_live():
    env = {"GOOGLE_MAPS_API_KEY": "AIza-fake", "TRACCAR_URL": "http://x", "COPERNICUS_TOKEN": "t"}
    rows = {r["capability"]: r for r in integration_matrix(env=env)}
    assert rows["google_routes"]["state"] == NOT_CONFIGURED
    assert rows["google_routes"]["credentials_present"] is True
    assert rows["shipment_gps_telemetry"]["state"] == NOT_CONFIGURED


def test_only_keyless_open_sources_are_live_external():
    rows = [r for r in integration_matrix(env={}) if r["state"] == LIVE_EXTERNAL]
    assert sorted(r["capability"] for r in rows) == ["facility_coordinates", "road_routes_osrm", "weather_forecast"]
    assert all(not r["auth_required"] for r in rows)


def test_stock_is_declared_synthetic():
    assert capability_state("stock_levels", env={}) == SYNTHETIC


def test_satellite_vehicle_tracking_is_declared_impossible_and_unimplemented():
    row = next(r for r in integration_matrix(env={}) if r["capability"] == "satellite_vehicle_tracking")
    assert row["implemented"] is False and row["state"] == NOT_CONFIGURED
    assert "NOT TECHNICALLY POSSIBLE" in row["notes"]


def test_live_capability_must_carry_attribution():
    for row in integration_matrix(env={}):
        if row["state"] == LIVE_EXTERNAL:
            assert row["attribution"]


def test_unknown_capability_raises():
    with pytest.raises(KeyError):
        capability_state("teleportation", env={})


# ---------------------------------------------------------------- real building footprints
from tathyon.osm_facilities import fetch_osm_buildings, parse_buildings, save_cache, load_cache

BUILDINGS = json.dumps({"elements": [
    {"type": "way", "id": 735410641, "tags": {"building": "yes", "building:levels": "2"},
     "geometry": [{"lat": 19.0, "lon": 82.0}, {"lat": 19.0, "lon": 82.001}, {"lat": 19.001, "lon": 82.001},
                  {"lat": 19.0, "lon": 82.0}]},
    {"type": "way", "id": 2, "tags": {"building": "yes", "height": "12 m", "name": "Ward"},
     "geometry": [{"lat": 19.0, "lon": 82.0}, {"lat": 19.0, "lon": 82.002}, {"lat": 19.002, "lon": 82.002},
                  {"lat": 19.0, "lon": 82.0}]},
    {"type": "way", "id": 3, "tags": {"building": "yes"}, "geometry": [{"lat": 19.0, "lon": 82.0}]},   # degenerate
]})


def test_building_footprints_keep_real_geometry_and_only_mapped_heights():
    b = parse_buildings(BUILDINGS)
    assert [x.osm_id for x in b] == [735410641, 2]                 # degenerate ring skipped
    assert b[0].levels == 2.0 and b[0].height_m is None
    assert b[1].height_m == 12.0 and b[1].name == "Ward"
    assert b[0].ring[0] == (82.0, 19.0)                            # (lon, lat)


def test_unmapped_height_stays_unknown_not_guessed():
    assert parse_buildings(json.dumps({"elements": [
        {"type": "way", "id": 9, "tags": {"building": "yes"},
         "geometry": [{"lat": 1, "lon": 1}, {"lat": 1, "lon": 2}, {"lat": 2, "lon": 2}, {"lat": 1, "lon": 1}]}]}))[0].levels is None


def test_building_fetch_failure_raises_instead_of_returning_empty():
    with pytest.raises(RuntimeError, match="unavailable"):
        fetch_osm_buildings((19.0, 82.0, 19.01, 82.01), transport=boom, retries=1, sleep=lambda s: None)


def test_building_bbox_size_limited_to_protect_overpass():
    with pytest.raises(ValueError):
        fetch_osm_buildings((19.0, 82.0, 19.5, 82.5), transport=ok(BUILDINGS))


def test_cache_round_trip_preserves_fetch_time(tmp_path):
    r = fetch_osm_facilities(BASTAR, AT, transport=ok())
    path = str(tmp_path / "osm.json")
    save_cache(r, path, AT)
    c = load_cache(path)
    assert c["fetched_at"] == AT and len(c["facilities"]) == 3
    assert load_cache(str(tmp_path / "missing.json")) is None


# Real record (fetched 2026-09-21): a CHC with NO amenity tag, only healthcare=centre.
CHC_TOKAPAL = json.dumps({"elements": [
    {"type": "node", "id": 7148125127, "lat": 19.012307, "lon": 81.8769098,
     "tags": {"healthcare": "centre", "name": "CHC TOKAPAL", "description": "Community Health Center",
              "addr:district": "Bastar", "source": "OpenGovernmentData"}}]})


def test_healthcare_tagged_centre_without_amenity_is_found():
    f = parse_elements(CHC_TOKAPAL, AT)[0]
    assert f.name == "CHC TOKAPAL" and f.amenity == "centre" and f.osm_id == 7148125127
    assert build_query((19.0, 81.8, 19.1, 81.9)).count('["healthcare"') == 2


def test_classification_comes_only_from_osm_text():
    from tathyon.osm_facilities import classify_facility
    chc = parse_elements(CHC_TOKAPAL, AT)[0]
    assert classify_facility(chc) == "CHC"
    hosp, _, clinic = parse_elements(REAL_PAYLOAD, AT)
    assert classify_facility(hosp) == "HOSPITAL" and classify_facility(clinic) == "CLINIC"
