import pytest
from tathyon.schema import ResourceType, FacilityType
from tathyon.graph import HealthcareResourceGraph, ResourceState, Facility, Resource
from tathyon.store import EventStore
from tathyon.planner import ResponsePlanner

@pytest.fixture
def base_engines():
    graph = HealthcareResourceGraph()
    # Add dummy facilities
    graph.add_facility(Facility(facility_id="F_PHC", name="PHC Alpha", facility_type=FacilityType.PHC, district="D1", state="S1", lat=0.0, lon=0.0))
    graph.add_facility(Facility(facility_id="F_DH1", name="DH Omega 1", facility_type=FacilityType.DISTRICT_HOSPITAL, district="D1", state="S1", lat=0.1, lon=0.1))
    graph.add_facility(Facility(facility_id="F_DH2", name="DH Omega 2", facility_type=FacilityType.DISTRICT_HOSPITAL, district="D1", state="S1", lat=0.2, lon=0.2))
    
    # Add dummy resources
    graph.add_resource(Resource(resource_id="MED_RABIES", name="Rabies Vaccine", resource_type=ResourceType.MEDICINE, unit="vial"))
    
    store = EventStore()
    return graph, store

def test_no_fabricated_transfer_when_donors_cannot_cover_the_need(base_engines):
    """
    Test 4: DONORS CANNOT COVER THE NEED.
    Recipient needs 100; all donors together hold only 50 transferable units
    above their 14-day safety floors. Whatever the plan does, it must never move
    a unit that is not there: no donor below its floor, no total above 50, and
    nothing executed without a human.

    Open question for the allocator (logged in DEVIATIONS.md): whether a partial
    plan (current behaviour) or NO_FEASIBLE_PLAN is the right answer here.
    """
    graph, store = base_engines
    sku = "MED_RABIES"

    # PHC: 0 usable, consumption 10/day.
    graph.set_resource_state(ResourceState(
        facility_id="F_PHC", resource_id=sku, claimed_quantity=0.0,
        observed_quantity=0.0, usable_quantity=0.0, consumption_velocity=10.0
    ))
    # DH1: 160 usable, floor 14 x 10 = 140 -> surplus 20.
    graph.set_resource_state(ResourceState(
        facility_id="F_DH1", resource_id=sku, claimed_quantity=160.0,
        observed_quantity=160.0, usable_quantity=160.0, consumption_velocity=10.0
    ))
    # DH2: 170 usable, floor 140 -> surplus 30.
    graph.set_resource_state(ResourceState(
        facility_id="F_DH2", resource_id=sku, claimed_quantity=170.0,
        observed_quantity=170.0, usable_quantity=170.0, consumption_velocity=10.0
    ))

    plan = ResponsePlanner(graph, store).plan_redistribution(sku, min_safety_days=14.0)

    surplus = {"F_DH1": 20.0, "F_DH2": 30.0}
    moved = {}
    for t in plan.transfers:
        moved[t.source] = moved.get(t.source, 0.0) + t.quantity
    assert set(moved) <= set(surplus)
    assert all(moved[d] <= surplus[d] + 1e-6 for d in moved), moved
    assert sum(moved.values()) <= 50.0 + 1e-6
    assert plan.status == "PROPOSED"      # Prompt 2 decision: partial fulfillment allowed under PROPOSED
    assert plan.fulfilled_qty == 50.0
    assert plan.shortfall_qty == 20.0     # 70 needed (7d lead time * 10/day) - 50 fulfilled
    assert plan.replan_required is True


def test_data_freshness_configuration(base_engines):
    """
    Test 5: TEST DATA FRESHNESS.
    Verify that stale information cannot silently behave like fresh physical truth.
    Do not use arbitrary universal freshness threshold unless supported by policy.
    Make it configurable.
    """
    graph, store = base_engines
    sku = "MED_RABIES"
    
    from datetime import datetime, timezone, timedelta
    now_ts = datetime.now(timezone.utc)
    
    stale_ts = (now_ts - timedelta(hours=50)).isoformat()
    fresh_ts = (now_ts - timedelta(hours=10)).isoformat()
    
    # Stale observation (50h old)
    graph.set_resource_state(ResourceState(
        facility_id="F_DH1", resource_id=sku, claimed_quantity=200.0, 
        observed_quantity=200.0, usable_quantity=200.0, consumption_velocity=10.0,
        last_attested_at=stale_ts
    ))
    
    # Fresh observation (10h old)
    graph.set_resource_state(ResourceState(
        facility_id="F_DH2", resource_id=sku, claimed_quantity=400.0, 
        observed_quantity=400.0, usable_quantity=400.0, consumption_velocity=10.0,
        last_attested_at=fresh_ts
    ))
    
    # PHC needs 50 units.
    graph.set_resource_state(ResourceState(
        facility_id="F_PHC", resource_id=sku, claimed_quantity=0.0, 
        observed_quantity=0.0, usable_quantity=0.0, consumption_velocity=10.0
    ))
    
    planner = ResponsePlanner(graph, store)
    
    # If max_freshness = 24.0, DH1 is rejected, DH2 is used.
    plan = planner.plan_redistribution(sku, min_safety_days=14.0, max_freshness_hours=24.0)
    
    assert plan.status == "PROPOSED"
    assert len(plan.transfers) == 1
    assert plan.transfers[0].source == "F_DH2"
    
    # Check that DH1 is in rejected donors
    dh1_rejects = [d for d in plan.rejected_donors if d["facility_id"] == "F_DH1"]
    assert len(dh1_rejects) == 1
    assert "Stale physical attestation" in dh1_rejects[0]["reason"]
    assert "24.0h" in dh1_rejects[0]["reason"]

def test_policy_transparency(base_engines):
    """
    Test 6: TEST POLICY TRANSPARENCY.
    Every constraint must identify: source, jurisdiction, policy type, parameter.
    Separate: LAW / REGULATION, OFFICIAL GUIDELINE, SYSTEM POLICY, FACILITY POLICY, CONFIGURATION, DEMO ASSUMPTION.
    """
    graph, store = base_engines
    sku = "MED_RABIES"
    
    # Just need basic state to trigger planning
    graph.set_resource_state(ResourceState(
        facility_id="F_PHC", resource_id=sku, claimed_quantity=0.0, 
        observed_quantity=0.0, usable_quantity=0.0, consumption_velocity=10.0
    ))
    graph.set_resource_state(ResourceState(
        facility_id="F_DH1", resource_id=sku, claimed_quantity=200.0, 
        observed_quantity=200.0, usable_quantity=200.0, consumption_velocity=10.0
    ))
    
    planner = ResponsePlanner(graph, store)
    plan = planner.plan_redistribution(sku)
    
    # We should have a list of dicts for constraints
    for constraint in plan.constraints:
        assert isinstance(constraint, dict)
        assert "source" in constraint
        assert "jurisdiction" in constraint
        assert "policy_type" in constraint
        assert "parameter" in constraint
        assert constraint["policy_type"] in ["LAW / REGULATION", "OFFICIAL GUIDELINE", "SYSTEM POLICY", "FACILITY POLICY", "CONFIGURATION", "DEMO ASSUMPTION"]

