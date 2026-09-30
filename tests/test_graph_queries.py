"""Tests for the three named network queries on HealthcareResourceGraph:
1. donor_reachability(recipient, sku, deadline)
2. supplier_concentration(sku)
3. failure_cascade(node)
"""
import pytest
from tathyon.graph import (
    Facility,
    HealthcareResourceGraph,
    Resource,
    ResourceState,
    Supplier,
    create_default_resource_graph,
)
from tathyon.schema import FacilityType, ResourceType


def test_donor_reachability_within_deadline():
    graph = create_default_resource_graph()
    sku = "MED_ANTI_RABIES_VACCINE"
    recipient = "PHC_REMOTE_EAST"

    # With 24-hour deadline, DWH should be reachable
    reach_24h = graph.donor_reachability(recipient, sku, deadline=24.0)
    assert len(reach_24h) > 0
    donor_ids = [d["donor_facility_id"] for d in reach_24h]
    assert "DWH_DISTRICT_DEPOT_01" in donor_ids
    # Unverified / expired donor CHC_RURAL_NORTH must NEVER be returned
    assert "CHC_RURAL_NORTH" not in donor_ids

    # With a tight 0.1-hour deadline, no remote donor should be reachable
    reach_tight = graph.donor_reachability(recipient, sku, deadline=0.1)
    assert len(reach_tight) == 0


def test_donor_reachability_respects_access_risk_multiplier():
    graph = create_default_resource_graph()
    sku = "MED_ANTI_RABIES_VACCINE"
    recipient = "PHC_REMOTE_EAST"
    donor = "DWH_DISTRICT_DEPOT_01"

    # Set transfer edge with normal risk multiplier
    edge = graph.set_can_transfer(donor, recipient, eta_hours=5.0, risk_multiplier=1.0)
    res1 = graph.donor_reachability(recipient, sku, deadline=6.0)
    assert any(d["donor_facility_id"] == donor for d in res1)

    # Set transfer edge with 1.5x disruption multiplier -> effective ETA = 7.5h > 6.0h deadline
    graph.set_can_transfer(donor, recipient, eta_hours=5.0, risk_multiplier=1.5)
    res2 = graph.donor_reachability(recipient, sku, deadline=6.0)
    assert not any(d["donor_facility_id"] == donor for d in res2)


def test_supplier_concentration_hhi_calculation():
    graph = create_default_resource_graph()
    sku = "MED_ANTI_RABIES_VACCINE"

    conc = graph.supplier_concentration(sku)
    assert "hhi" in conc
    assert conc["supplier_count"] >= 2
    assert 0 <= conc["hhi"] <= 10000
    assert conc["concentration_level"] in ("HIGH", "MODERATE", "LOW")
    assert "suppliers" in conc


def test_supplier_concentration_single_source_detected():
    graph = HealthcareResourceGraph()
    sku = "MED_RARE_ANTIDOTE"
    graph.add_resource(Resource(resource_id=sku, name="Rare Antidote", resource_type=ResourceType.MEDICINE))
    graph.add_supplier(Supplier(supplier_id="MONOPOLY_LAB", name="Single Lab"))
    graph.set_supplies("MONOPOLY_LAB", sku, share_pct=1.0)

    conc = graph.supplier_concentration(sku)
    assert conc["single_source_risk"] is True
    assert conc["hhi"] == 10000.0
    assert conc["concentration_level"] == "HIGH"


def test_failure_cascade_evaluation():
    graph = create_default_resource_graph()
    cascade = graph.failure_cascade("DWH_DISTRICT_DEPOT_01")

    assert cascade["failed_facility_id"] == "DWH_DISTRICT_DEPOT_01"
    assert "direct_impact" in cascade
    assert "redistributed_demand" in cascade
    assert "secondary_shortages" in cascade
    assert "cascade_burden_score" in cascade
    assert 0.0 <= cascade["cascade_burden_score"] <= 1.0
