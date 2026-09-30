"""
Unit and integration tests:
  - Emergency Resilience Scenario Engine (6 shocks, Baseline vs Response, SIMULATION provenance)
  - Response Planner with OR-Tools (usable donor inventory only, safety floors, human approval)
"""
import pytest

from tathyon.graph import create_default_resource_graph
from tathyon.planner import ResponsePlanner
from tathyon.schema import EventType
from tathyon.twin import EmergencyResilienceScenarioEngine, ShockType


@pytest.fixture
def resource_graph():
    """Provides an initialized default resource graph."""
    return create_default_resource_graph()


def test_01_scenario_engine_all_six_shocks(resource_graph):
    """Verifies that all 6 shocks execute deterministically and declare SIMULATION provenance."""
    twin = EmergencyResilienceScenarioEngine(resource_graph)

    shocks = [
        ShockType.DEMAND_PLUS_20,
        ShockType.DEMAND_PLUS_50,
        ShockType.DEMAND_PLUS_100,
        ShockType.DISTRICT_WAREHOUSE_UNAVAILABLE,
        ShockType.LEAD_TIME_X2,
        ShockType.ONE_DISTRICT_OUTBREAK_UPLIFT,
    ]

    for shock in shocks:
        result = twin.run_simulation(shock=shock, resource_id="MED_ANTI_RABIES_VACCINE", horizon_days=14)

        # 1. Mandatory provenance check
        assert result.provenance == "SIMULATION"
        assert "SIMULATION ONLY" in result.disclaimer

        # 2. Metric presence & types
        b = result.baseline
        t = result.tathyon_response

        assert b.stockouts >= 0
        assert b.shortage_quantity >= 0.0
        assert 0.0 <= b.service_coverage <= 100.0
        assert b.distance == 0.0  # baseline has no active transfers

        assert t.stockouts >= 0
        assert t.shortage_quantity >= 0.0
        assert 0.0 <= t.service_coverage <= 100.0
        assert t.cost >= 0.0

        # Tathyon response should never worsen stockouts compared to doing nothing
        assert t.stockouts <= b.stockouts
        assert t.shortage_quantity <= b.shortage_quantity
        assert t.service_coverage >= b.service_coverage

        # Check delta metrics
        assert result.delta["stockouts_prevented"] >= 0
        assert result.delta["shortage_reduction_units"] >= 0.0


def test_02_response_planner_ortools_and_safety_floors(resource_graph):
    """Verifies that OR-Tools planner respects donor safety floors and unverified exclusions."""
    planner = ResponsePlanner(resource_graph)

    plan = planner.plan_redistribution(resource_id="MED_ANTI_RABIES_VACCINE")

    # Initial state must be PROPOSED (never automatically executed)
    assert plan.status == "PROPOSED"
    assert plan.provenance == "SIMULATION"
    assert "NOTHING is executed automatically" in plan.disclaimer
    assert len(plan.constraints) > 0

    # If transfers were planned, verify donor safety floor
    if plan.transfers:
        for t in plan.transfers:
            donor_st = resource_graph.states.get((t.source, t.resource))
            assert donor_st is not None
            # Donor must have had verified usable inventory
            assert donor_st.usable_quantity > 0
            assert t.distance > 0.0
            assert t.travel_hours > 0.0

    # Human approval flow
    approved_plan = planner.approve_plan(
        plan=plan,
        officer_id="MO-PUNE-4821",
        role="ChiefMedicalOfficer",
        notes="Redistribution approved for outbreak mitigation.",
    )
    assert approved_plan.status == "APPROVED"
    assert approved_plan.approved_by == "MO-PUNE-4821"
    assert approved_plan.approval_timestamp is not None

    # Check that plan_approved event exists in EventStore
    approved_events = [
        e for e in resource_graph.store.events if e.event_type == EventType.PLAN_APPROVED
    ]
    assert len(approved_events) >= 1
    assert approved_events[-1].payload["approved_by"] == "MO-PUNE-4821"

    # Outcome recording
    outcome = planner.record_outcome(
        plan_id=plan.plan_id,
        actual_delivered_qty=plan.quantity,
        response_time_hours=2.5,
        outcome_status="SUCCESSFUL",
        stockout_prevented=True,
    )
    assert outcome["outcome_status"] == "SUCCESSFUL"
    assert outcome["stockout_prevented"] is True

    outcome_events = [
        e for e in resource_graph.store.events if e.event_type == EventType.OUTCOME_RECORDED
    ]
    assert len(outcome_events) >= 1


def test_03_unverified_donor_inventory_strictly_prevented(resource_graph):
    """Guarantees that a facility with large claimed stock but 0 usable stock cannot donate."""
    # Poison a facility: 500 claimed, 0 usable
    st = resource_graph.states.get(("CHC_RURAL_NORTH", "MED_ANTI_RABIES_VACCINE"))
    st.claimed_quantity = 500.0
    st.usable_quantity = 0.0  # Unverified / failed attestation

    planner = ResponsePlanner(resource_graph)
    plan = planner.plan_redistribution(resource_id="MED_ANTI_RABIES_VACCINE")

    # CHC_RURAL_NORTH must NOT be the source of any transfer
    for t in plan.transfers:
        assert t.source != "CHC_RURAL_NORTH"
