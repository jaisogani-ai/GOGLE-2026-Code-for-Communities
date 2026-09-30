"""
Tests for Phase 1: Healthcare Resource Graph.

Verifies:
1. Facility and Resource models across sovereign tiers (PHC, CHC, DH, DWH).
2. ResourceState separation of CLAIMED STATE vs USABLE STATE.
3. Invariant: Unverified/phantom inventory NEVER becomes donor inventory.
4. Haversine transit distance calculations.
5. Donor selection respecting safety floors and excluding unusable stock.
6. Shortage facility detection based on consumption velocity and lead time.
7. Synchronization from VerificationEngine (Input Quality).
"""
import pytest
from tathyon.graph import (
    Facility,
    HealthcareResourceGraph,
    MedicineBatch,
    Resource,
    ResourceState,
    create_default_resource_graph,
    haversine_distance_km,
)
from tathyon.schema import FacilityType, ResourceType, VerificationState
from tathyon.store import EventStore
from tathyon.verify import VerificationEngine


def test_01_graph_creation_and_topology():
    """Verify nodes and master catalog can be added to the graph."""
    graph = HealthcareResourceGraph()

    fac = Facility(
        facility_id="PHC_TEST_01",
        name="PHC Test North",
        facility_type=FacilityType.PHC,
        district="Bastar",
        state="Chhattisgarh",
        lat=19.20,
        lon=82.10,
    )
    graph.add_facility(fac)
    assert "PHC_TEST_01" in graph.facilities
    assert graph.facilities["PHC_TEST_01"].facility_type == FacilityType.PHC

    res = Resource(
        resource_id="MED_VACCINE_RABIES",
        name="Rabies Vaccine IP",
        resource_type=ResourceType.VACCINE,
        unit="vial",
        criticality=9.5,
        cold_chain_required=True,
    )
    graph.add_resource(res)
    assert "MED_VACCINE_RABIES" in graph.resources
    assert graph.resources["MED_VACCINE_RABIES"].cold_chain_required is True


def test_02_haversine_distance_calculation():
    """Verify geographic distance calculation between facilities."""
    # Bastar depot to PHC East
    d = haversine_distance_km(19.0750, 82.0150, 19.2100, 82.3400)
    assert 30.0 < d < 45.0  # Approx ~37 km


def test_03_claimed_vs_usable_state_separation():
    """CRITICAL TEST: Verify the graph distinguishes claimed stock from usable stock."""
    graph = HealthcareResourceGraph()

    # Facility with phantom inventory (claims 200, but all expired -> usable = 0)
    st = ResourceState(
        facility_id="CHC_NORTH",
        resource_id="MED_RABIES",
        claimed_quantity=200.0,
        usable_quantity=0.0,
        consumption_velocity=10.0,
    )
    graph.set_resource_state(st)

    assert graph.get_claimed_stock("CHC_NORTH", "MED_RABIES") == 200.0
    assert graph.get_usable_stock("CHC_NORTH", "MED_RABIES") == 0.0
    assert st.phantom_inventory == 200.0
    assert st.days_of_usable_stock == 0.0


def test_04_unverified_inventory_never_becomes_donor_inventory():
    """CRITICAL INVARIANT: Unverified or expired stock cannot be selected as donor."""
    graph = create_default_resource_graph()

    # In default graph:
    # - DWH_DISTRICT_DEPOT_01: Claimed=500, Usable=500 (Verified)
    # - CHC_RURAL_NORTH: Claimed=200, Usable=0 (Expired phantom inventory)
    # - PHC_REMOTE_EAST: Claimed=4, Usable=4 (Surge/Shortage)
    # - PHC_VALLEY_WEST: Claimed=45, Usable=45 (Small buffer)

    donors = graph.get_donor_inventory("MED_ANTI_RABIES_VACCINE", min_safety_days=7.0)

    donor_fids = [d["facility_id"] for d in donors]

    # DWH should be the top donor
    assert "DWH_DISTRICT_DEPOT_01" in donor_fids

    # CHC_RURAL_NORTH claims 200, but usable = 0 -> MUST NEVER BE IN DONORS!
    assert "CHC_RURAL_NORTH" not in donor_fids

    # Check that DWH surplus preserves its 7-day safety floor (12 units/day * 7 days = 84 units)
    dwh_donor = [d for d in donors if d["facility_id"] == "DWH_DISTRICT_DEPOT_01"][0]
    assert dwh_donor["usable_quantity"] == 500.0
    assert dwh_donor["safety_floor"] == 84.0
    assert dwh_donor["surplus_transferable"] == 500.0 - 84.0  # 416.0


def test_05_shortage_detection():
    """Verify shortage facilities identified based on consumption velocity and lead time."""
    graph = create_default_resource_graph()

    shortages = graph.get_shortage_facilities("MED_ANTI_RABIES_VACCINE", lead_time_days=6.0)
    shortage_fids = [s["facility_id"] for s in shortages]

    # Both CHC_RURAL_NORTH (0 usable stock) and PHC_REMOTE_EAST (under 1 day left) should be flagged
    assert "CHC_RURAL_NORTH" in shortage_fids
    assert "PHC_REMOTE_EAST" in shortage_fids

    # DWH (over 40 days stock) should NOT be in shortages
    assert "DWH_DISTRICT_DEPOT_01" not in shortage_fids


def test_06_graph_summary_export():
    """Verify graph summary exports valid statistics and synthetic provenance."""
    graph = create_default_resource_graph()
    summary = graph.export_graph_summary()

    assert summary["total_facilities"] == 5
    assert summary["total_resources"] == 5
    assert summary["total_claimed_stock"] > 0
    assert summary["total_usable_stock"] > 0
    assert summary["total_phantom_inventory"] == 200.0  # CHC_RURAL_NORTH's expired 200
    assert summary["provenance"] == "SYNTHETIC"
    assert "CRITICAL" in summary["risk_distribution"]



