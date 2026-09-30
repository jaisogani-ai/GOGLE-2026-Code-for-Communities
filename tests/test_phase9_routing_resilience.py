import pytest
from datetime import datetime, timezone, timedelta
from tathyon.schema import ResourceType, FacilityType
from tathyon.graph import HealthcareResourceGraph, ResourceState, Facility, Resource
from tathyon.store import EventStore
from tathyon.planner import ResponsePlanner

@pytest.fixture
def base_engines():
    graph = HealthcareResourceGraph()
    graph.add_facility(Facility(facility_id="F_RECIPIENT", name="Tokapal PHC", facility_type=FacilityType.PHC, district="Bastar", state="CG", lat=19.0, lon=81.0))
    graph.add_facility(Facility(facility_id="F_DONOR_A", name="Donor A (Far)", facility_type=FacilityType.DISTRICT_HOSPITAL, district="Bastar", state="CG", lat=19.5, lon=81.5))
    graph.add_facility(Facility(facility_id="F_DONOR_B", name="Donor B (Near)", facility_type=FacilityType.CHC, district="Bastar", state="CG", lat=19.1, lon=81.1))
    
    graph.add_resource(Resource(resource_id="MED_RABIES", name="Rabies Vaccine", resource_type=ResourceType.MEDICINE, unit="vial"))
    store = EventStore()
    return graph, store

def test_routing_resilience(base_engines):
    graph, store = base_engines
    sku = "MED_RABIES"
    
    now_ts = datetime.now(timezone.utc).isoformat()
    
    # Recipient: 1.5 days stock.
    # Let's say consumption is 10/day. 15 units = 1.5 days.
    # Shortfall is target (safety floor) - current.
    # Safety floor = 14 days * 10 = 140. Shortfall = 140 - 15 = 125.
    graph.set_resource_state(ResourceState(
        facility_id="F_RECIPIENT", resource_id=sku, claimed_quantity=15.0, 
        observed_quantity=15.0, usable_quantity=15.0, consumption_velocity=10.0,
        last_attested_at=now_ts
    ))
    
    # Donor A: 50 transferable. 
    # To get 50 transferable, usable_quantity - safety_floor = 50. 
    # Safety floor = 14 * 10 = 140. Usable = 190.
    graph.set_resource_state(ResourceState(
        facility_id="F_DONOR_A", resource_id=sku, claimed_quantity=190.0, 
        observed_quantity=190.0, usable_quantity=190.0, consumption_velocity=10.0,
        last_attested_at=now_ts
    ))

    # Donor B: 35 transferable.
    # Safety floor = 14 * 10 = 140. Usable = 175.
    graph.set_resource_state(ResourceState(
        facility_id="F_DONOR_B", resource_id=sku, claimed_quantity=175.0, 
        observed_quantity=175.0, usable_quantity=175.0, consumption_velocity=10.0,
        last_attested_at=now_ts
    ))
    
    planner = ResponsePlanner(graph, store)
    
    # Override routing adapter logic for test scenario
    # ETA Donor A = 3 days (72 hours)
    planner.router.set_synthetic_override("F_DONOR_A", "F_RECIPIENT", eta_hours=72.0)
    # ETA Donor B = 8 hours
    planner.router.set_synthetic_override("F_DONOR_B", "F_RECIPIENT", eta_hours=8.0)
    
    # SCENARIO 1: Expected Result -> Donor A cannot solve because ETA > Runway (72 > 1.5*24 = 36).
    # Donor B is selected despite having less transferable stock.
    plan1 = planner.plan_redistribution(sku, min_safety_days=14.0, max_distance_km=5000)
    
    print("Plan1 Status:", plan1.status)
    print("Plan1 Transfers:", plan1.transfers)
    print("Plan1 Rejected:", plan1.rejected_donors)
    
    assert plan1.status == "PROPOSED", f"Plan1 failed: {plan1.status}"
    # The solver should prefer Donor B
    assert any(t.source == "F_DONOR_B" for t in plan1.transfers)
    assert not any(t.source == "F_DONOR_A" for t in plan1.transfers)
    
    # Check rejection reason for A
    rej_a = [d for d in plan1.rejected_donors if d["facility_id"] == "F_DONOR_A"]
    assert len(rej_a) > 0
    assert "DELIVERY WILL ARRIVE AFTER PROJECTED SHORTAGE" in rej_a[0]["reason"]
    
    # SCENARIO 2: Elevated environmental risk on Donor B route
    planner.router.set_synthetic_override("F_DONOR_B", "F_RECIPIENT", eta_hours=8.0, risk_signal_type="POTENTIAL ACCESS DISRUPTION")
    
    # Recipient runway increases to 4 days (96 hours) so Donor A becomes feasible again.
    graph.set_resource_state(ResourceState(
        facility_id="F_RECIPIENT", resource_id=sku, claimed_quantity=40.0, 
        observed_quantity=40.0, usable_quantity=40.0, consumption_velocity=10.0,
        last_attested_at=now_ts
    ))
    
    plan2 = planner.plan_redistribution(sku, min_safety_days=14.0, max_distance_km=5000)
    
    print("Plan2 Status:", plan2.status)
    print("Plan2 Transfers:", plan2.transfers)
    
    assert plan2.status == "PROPOSED"
    # Because of the massive 50,000 W_RISK penalty, Donor B (with risk) will be heavily penalized
    # and Donor A (72 hours ETA, but runway is 96h) should be chosen since risk is Normal.
    assert any(t.source == "F_DONOR_A" for t in plan2.transfers)
    
    # Verify counterfactuals are populated
    assert len(plan2.counterfactuals) > 0
    assert any("Do nothing" in c["scenario"] for c in plan2.counterfactuals)
