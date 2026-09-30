"""Test ResponsePlanner thin orchestration pipeline.
trust-score -> target verifications -> forecast -> allocate -> approval.
"""
from datetime import datetime, timezone
import pytest

from tathyon.graph import Facility, HealthcareResourceGraph, Resource, ResourceState
from tathyon.planner import ResponsePlanner
from tathyon.schema import FacilityType, ResourceType
from tathyon.store import EventStore


def test_planner_orchestration_cycle():
    graph = HealthcareResourceGraph()
    store = EventStore()
    
    # 1 donor, 1 shortage
    f_donor = Facility("F_DONOR", "Donor Hospital", FacilityType.DISTRICT_HOSPITAL, "Bastar", "CG", 19.1, 81.1)
    f_short = Facility("F_SHORT", "Shortage PHC", FacilityType.PHC, "Bastar", "CG", 19.2, 81.2)
    sku = Resource("MED_RABIES", "Rabies Vaccine", ResourceType.MEDICINE, "vial", criticality=8.0)
    
    graph.add_facility(f_donor)
    graph.add_facility(f_short)
    graph.add_resource(sku)
    
    # Donor has 200 usable, burn 5/d -> 14d floor is 70, surplus is 130
    graph.set_resource_state(ResourceState(
        facility_id="F_DONOR",
        resource_id="MED_RABIES",
        claimed_quantity=200.0,
        usable_quantity=200.0,
        consumption_velocity=5.0,
        lead_time=7.0,
        last_attested_at=datetime.now(timezone.utc).isoformat(),
    ))
    
    # Shortage has 0 usable, burn 5/d -> needs 35 units
    graph.set_resource_state(ResourceState(
        facility_id="F_SHORT",
        resource_id="MED_RABIES",
        claimed_quantity=0.0,
        usable_quantity=0.0,
        consumption_velocity=5.0,
        lead_time=7.0,
        last_attested_at=datetime.now(timezone.utc).isoformat(),
    ))
    
    import pandas as pd
    candidates = pd.DataFrame([
        {
            "facility_id": "F_SHORT",
            "sku": "MED_RABIES",
            "p_wrong": 0.8,
            "hidden_stockout_days": 10.0,
            "essentiality": 3.0,
            "visit_cost": 2,
        }
    ])
    
    planner = ResponsePlanner(graph, store)
    result = planner.orchestrate(
        resource_id="MED_RABIES",
        district="Bastar",
        visit_budget=10,
        candidates=candidates,
        approver_id="MO_12345",
        approver_role="Chief Medical Officer",
        notes="Approved for emergency distribution",
    )
    
    assert result["resource_id"] == "MED_RABIES"
    assert result["district"] == "Bastar"
    assert "verification_queue" in result
    assert "forecasts" in result
    assert result["plan"].status == "APPROVED"
    assert result["plan"].quantity == 35.0
    assert result["plan"].fulfilled_qty == 35.0
    assert result["plan"].shortfall_qty == 0.0
    assert result["plan"].replan_required is False
    assert result["sor_payload"] is not None
    assert result["sor_payload"]["status"] == "READY_FOR_SYSTEM_OF_RECORD"
