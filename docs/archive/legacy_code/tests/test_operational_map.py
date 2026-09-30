"""Comprehensive unit and integration tests for TATHYON Operational Map.

Validates:
1. GET /map/facilities: structure, trust status, multi-physics resources, stats, and filtering.
2. GET /map/operational-routes: road geometries for approved transfers, diversions, redeployments.
3. Privacy invariance: Staff redeployments expose role-level routes only (no PII).
4. GET /map/district-boundary: valid Bastar district polygon GeoJSON.
5. Live state invalidation: Submitting an attestation via /verify/attest immediately reflects
   in /map/facilities (transitions to VERIFIED, sets EVT- ID, updates ground truth).
6. Consistency: Trust Queue knapsack results visibly match map verification targets.
"""
import pytest
from fastapi.testclient import TestClient

from api.main import app, Registry

client = TestClient(app)


def setup_function():
    Registry.reset()


def test_get_map_facilities_structure():
    """Verify /map/facilities returns the operational view of registry facilities."""
    res = client.get("/map/facilities")
    assert res.status_code == 200
    data = res.json()

    assert "facilities" in data
    assert "count" in data
    assert "stats" in data
    assert data["count"] > 0
    assert len(data["facilities"]) == data["count"]

    stats = data["stats"]
    assert "verified" in stats
    assert "high_risk" in stats
    assert "unverified" in stats
    assert "targets" in stats

    valid_statuses = {"VERIFIED", "HIGH_RISK", "UNVERIFIED", "STALE"}

    # Check first few facilities
    for f in data["facilities"][:10]:
        assert "facility_id" in f
        assert "name" in f
        assert "lat" in f and "lon" in f
        assert 17.0 <= f["lat"] <= 21.0
        assert 80.0 <= f["lon"] <= 83.5
        assert f["trust_status"] in valid_statuses
        assert "resources" in f
        assert "is_target" in f
        assert isinstance(f["is_target"], bool)


def test_get_map_facilities_resource_and_tier_filtering():
    """Verify resource and facility type query parameters work."""
    # Filter by resource_type
    res_med = client.get("/map/facilities?resource_type=medicines")
    assert res_med.status_code == 200
    data_med = res_med.json()
    for f in data_med["facilities"]:
        assert "medicines" in f["resources"]

    res_beds = client.get("/map/facilities?resource_type=beds")
    assert res_beds.status_code == 200
    data_beds = res_beds.json()
    for f in data_beds["facilities"]:
        assert "beds" in f["resources"]

    # Filter by facility_type
    res_dh = client.get("/map/facilities?facility_type=DH")
    assert res_dh.status_code == 200
    data_dh = res_dh.json()
    for f in data_dh["facilities"]:
        assert f["facility_type"] == "DH"


def test_get_map_operational_routes():
    """Verify demo route geometry is explicitly non-routed and non-authoritative."""
    res = client.get("/map/operational-routes")
    assert res.status_code == 200
    data = res.json()

    assert "routes" in data
    assert "count" in data
    assert data["count"] >= 3
    assert data["data_environment"] == "SYNTHETIC_DEMO"
    assert "NOT AUTHORIZED OR EXECUTABLE" in data["routes_provenance"]

    route_types = {r["type"] for r in data["routes"]}
    assert "MEDICINE_TRANSFER" in route_types
    assert "PATIENT_DIVERSION" in route_types
    assert "STAFF_REDEPLOYMENT" in route_types

    for r in data["routes"]:
        assert "route_id" in r
        assert "from_facility_id" in r
        assert "to_facility_id" in r
        assert "geometry" in r
        assert len(r["geometry"]) >= 2
        # Geometry must be [lon, lat] coordinates
        for pt in r["geometry"]:
            assert len(pt) == 2
            assert 80.0 <= pt[0] <= 83.5  # lon
            assert 17.0 <= pt[1] <= 21.0  # lat
        assert r["distance_km"] > 0
        assert r["duration_hours"] > 0
        assert r["approval_status"] == "DEMO_ONLY"
        assert r["is_real_road_route"] is False
        assert r["route_geometry_status"] == "GENERATED_DEMO_GEOMETRY_NOT_ROUTED"


def test_staff_redeployment_privacy_no_pii():
    """Verify staff redeployment routes only expose role titles and strictly zero personal data."""
    res = client.get("/map/operational-routes")
    assert res.status_code == 200
    routes = res.json()["routes"]

    staff_routes = [r for r in routes if r["type"] == "STAFF_REDEPLOYMENT"]
    assert len(staff_routes) > 0

    for sr in staff_routes:
        assert "staff_role" in sr
        assert "staff_count" in sr
        assert sr["staff_count"] > 0
        # Ensure no individual names or PII fields exist
        assert "person_name" not in sr
        assert "staff_names" not in sr
        assert "phone" not in sr
        assert "email" not in sr
        assert "aadhaar" not in sr


def test_get_map_district_boundary():
    """Verify /map/district-boundary returns valid Bastar polygon GeoJSON."""
    res = client.get("/map/district-boundary")
    assert res.status_code == 200
    geojson = res.json()

    assert geojson.get("type") == "FeatureCollection"
    assert len(geojson.get("features", [])) == 1
    feat = geojson["features"][0]
    assert feat["geometry"]["type"] == "Polygon"
    coords = feat["geometry"]["coordinates"][0]
    assert len(coords) >= 4
    # Closed polygon
    assert coords[0] == coords[-1]
    assert feat["properties"]["district"] == "Bastar"


def test_attestation_state_invalidation_updates_map():
    """Verify that posting a field verification attestation immediately updates the facility's map status."""
    target_facility = "PHC_X"

    # Initial state
    res_before = client.get("/map/facilities")
    assert res_before.status_code == 200
    fac_before = next(f for f in res_before.json()["facilities"] if f["facility_id"] == target_facility)
    assert fac_before["trust_status"] in {"HIGH_RISK", "UNVERIFIED"}
    assert fac_before["last_attestation_id"] is None

    # Submit valid non-custodian attestation
    attest_payload = {
        "facility_id": target_facility,
        "resource_key": "MED-ARV-01",
        "present_quantity": 450.0,
        "usable_quantity": 420.0,
        "damaged_quantity": 30.0,
        "expired_quantity": 0.0,
        "attester_id": "field_officer_anita",
        "is_custodian": False,
        "nonce": "TEST-NONCE-MAP-01"
    }
    attest_res = client.post("/verify/attest", json=attest_payload)
    assert attest_res.status_code == 200
    attest_data = attest_res.json()
    evt_id = attest_data["event_id"]
    assert evt_id.startswith("evt_") or evt_id.startswith("EVT-")

    # Fetch facilities again from the operational map endpoint
    res_after = client.get("/map/facilities")
    assert res_after.status_code == 200
    fac_after = next(f for f in res_after.json()["facilities"] if f["facility_id"] == target_facility)

    # Marker must now reflect VERIFIED trust status, low p_wrong, and the new hash-chain EVT- ID
    assert fac_after["trust_status"] == "VERIFIED"
    assert fac_after["p_wrong"] == 0.0
    assert fac_after["last_attestation_id"] == evt_id
    assert fac_after["verified_usable_stock"] == 420.0


def test_trust_queue_knapsack_consistency_with_map_targets():
    """Verify facilities flagged as map targets match the Trust Queue knapsack ranking."""
    queue_res = client.get("/trust/queue?limit=5&budget_slots=6")
    assert queue_res.status_code == 200
    queue_targets = queue_res.json()["targeted_verifications"]
    queue_facility_ids = {t["facility_id"] for t in queue_targets}

    map_res = client.get("/map/facilities")
    assert map_res.status_code == 200
    map_targets = [f for f in map_res.json()["facilities"] if f["is_target"]]
    map_target_ids = {f["facility_id"] for f in map_targets}

    # Every queue target must be marked as target on the operational map
    for q_id in queue_facility_ids:
        assert q_id in map_target_ids, f"Queue target {q_id} should be flagged as target on operational map"

    queue_lookup = {t["facility_id"]: t for t in queue_targets}
    for mf in map_targets:
        if mf["facility_id"] in queue_lookup:
            qt = queue_lookup[mf["facility_id"]]
            assert mf["queue_score"] > 0
            assert qt["expected_value"] > 0
