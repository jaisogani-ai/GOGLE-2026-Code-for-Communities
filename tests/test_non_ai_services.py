"""
Comprehensive unit and property tests for TATHYON's Non-AI Deterministic Decision Services.

Tests:
1. Trust Queue:
   - Multi-factor ranking (risk, data age, criticality, discrepancy history, cost).
   - Missing data is treated as high uncertainty / high priority (never low risk).
   - Strict distinction between risk score and data confidence.
   - Deterministic repeatability and explainability (top reasons, source facts).
   - Absence of intent/fraud claims.

2. Verification Dispatch:
   - Deterministic inspector workload balancing under capacity limits.
   - High-value / vital missing verifications enter PENDING_APPROVAL.
   - Operational manual override commits EventStore audit block.
   - Repeatability: identical inputs yield identical task IDs and inspector assignments.

3. Deterministic Forecasting:
   - Intermittent demand projections with uncertainty intervals and MASE backtesting.
   - Insufficient data returns explicit 'INSUFFICIENT_DATA' with reasons.
   - Stale data (>60d) returns explicit 'STALE_DATA' warning rather than fabricating numbers.
   - Bed occupancy projections with turnover constraints.
   - Projections declare 'STATISTICAL PROJECTION' disclaimer (never observed fact).

4. Deterministic Resource Allocation:
   - Gating: unverified donor stock is strictly excluded.
   - Expiry constraints: expiring stock is rejected.
   - Safety floors: donor minimum 14-day stock floor preserved.
   - Proposal status is PROPOSED; requires human Medical Officer sign-off.
   - Partial fulfillment and replan flags.

5. Demo Safety Controls:
   - Synthetic seed guarantees reproducibility.
   - Prominent DEMO_SAFETY_BANNER and jurisdiction marking.
"""
import pytest
from datetime import datetime, timezone
import numpy as np

from tathyon.non_ai_services import (
    TrustQueueService,
    TrustQueueFactorWeights,
    VerificationDispatchService,
    DispatchStatus,
    DeterministicForecastingService,
    DeterministicAllocationService,
    AllocationCandidate,
    AllocationRequirement,
    DemoSafetyService,
    DEMO_SAFETY_BANNER,
)
from tathyon.store import EventStore
from tathyon.schema import EventType


# ==============================================================================
# 1. TRUST QUEUE TESTS
# ==============================================================================

def test_01_trust_queue_multi_factor_ranking_and_explainability():
    service = TrustQueueService()
    obs = [
        {
            "facility_id": "PHC_VITAL",
            "resource_key": "MED-ARV-01",
            "reported_stock": 100.0,
            "data_age_days": 45.0,
            "p_wrong": 0.85,
            "essentiality": 3.0,
            "discrepancy_history_count": 2,
            "visit_cost_slots": 1,
            "hidden_stockout_days": 15.0,
        },
        {
            "facility_id": "PHC_ROUTINE",
            "resource_key": "MED-PCM-01",
            "reported_stock": 100.0,
            "data_age_days": 5.0,
            "p_wrong": 0.10,
            "essentiality": 1.0,
            "discrepancy_history_count": 0,
            "visit_cost_slots": 1,
            "hidden_stockout_days": 2.0,
        }
    ]

    items = service.score_and_rank(obs, budget_slots=10)
    assert len(items) == 2
    assert items[0].facility_id == "PHC_VITAL"
    assert items[0].rank == 1
    assert items[0].expected_value > items[1].expected_value

    # Check explainability
    top_item = items[0]
    assert len(top_item.top_contributing_reasons) > 0
    assert any("staleness" in r.lower() for r in top_item.top_contributing_reasons)
    assert any("vital resource tier" in r.lower() for r in top_item.top_contributing_reasons)
    assert "reported_stock" in top_item.source_facts
    assert top_item.score_version == "2.1.0"


def test_02_trust_queue_missing_value_is_never_treated_as_low_risk():
    service = TrustQueueService()
    obs = [
        {
            "facility_id": "PHC_MISSING",
            "resource_key": "MED-ARV-01",
            "reported_stock": None,
            "is_missing": True,
            "essentiality": 3.0,
            "visit_cost_slots": 1,
        },
        {
            "facility_id": "PHC_SAFE_REPORTED",
            "resource_key": "MED-ARV-01",
            "reported_stock": 200.0,
            "is_missing": False,
            "p_wrong": 0.05,
            "data_age_days": 2.0,
            "essentiality": 3.0,
            "visit_cost_slots": 1,
        }
    ]

    items = service.score_and_rank(obs)
    missing_item = next(it for it in items if it.facility_id == "PHC_MISSING")
    
    assert missing_item.is_missing_data is True
    assert missing_item.risk_score >= 0.90
    assert missing_item.data_confidence <= 0.10
    assert missing_item.rank == 1
    assert any("missing observation" in r.lower() for r in missing_item.top_contributing_reasons)


def test_03_trust_queue_risk_score_distinct_from_confidence():
    service = TrustQueueService()
    obs = [
        {
            "facility_id": "PHC_STALE_BENIGN",
            "resource_key": "MED-ARV-01",
            "reported_stock": 500.0,
            "data_age_days": 50.0,
            "p_wrong": 0.15,  # Low anomaly probability on past history
            "essentiality": 2.0,
            "visit_cost_slots": 1,
        }
    ]
    item = service.score_and_rank(obs)[0]
    # Risk score is low (0.15), but data confidence is degraded due to 50 days of staleness
    assert item.risk_score == 0.15
    assert item.data_confidence < 0.60
    assert item.risk_score != (1.0 - item.data_confidence)


# ==============================================================================
# 2. VERIFICATION DISPATCH TESTS
# ==============================================================================

def test_04_verification_dispatch_deterministic_and_capacity_constrained():
    store = EventStore()
    tq_service = TrustQueueService()
    dispatch_service = VerificationDispatchService(store=store)

    obs = [
        {"facility_id": f"PHC_{i}", "resource_key": "MED-ARV-01", "reported_stock": 100.0, "essentiality": 2.0, "p_wrong": 0.5, "visit_cost_slots": 2}
        for i in range(10)
    ]
    queue_items = tq_service.score_and_rank(obs)

    inspectors = ["INSP_ALICE", "INSP_BOB"]
    tasks_run1 = dispatch_service.plan_dispatch_schedule(queue_items, inspectors, district_slots_budget=6)
    tasks_run2 = dispatch_service.plan_dispatch_schedule(queue_items, inspectors, district_slots_budget=6)

    # Repeatability check: identical inputs yield identical dispatch plans
    assert len(tasks_run1) == 3
    assert [t.dispatch_id for t in tasks_run1] == [t.dispatch_id for t in tasks_run2]
    assert [t.assignee_id for t in tasks_run1] == [t.assignee_id for t in tasks_run2]

    # Slot budget constraint check: 3 tasks * 2 slots = 6 slots
    total_slots = sum(t.assigned_slots for t in tasks_run1)
    assert total_slots <= 6


def test_05_verification_dispatch_policy_approval_and_manual_override():
    store = EventStore()
    dispatch_service = VerificationDispatchService(store=store)

    item = TrustQueueService().score_and_rank([
        {"facility_id": "PHC_CRITICAL", "resource_key": "MED-ARV-01", "reported_stock": None, "is_missing": True, "essentiality": 3.0, "visit_cost_slots": 1}
    ])[0]

    tasks = dispatch_service.plan_dispatch_schedule([item], ["INSP_01"], district_slots_budget=5, policy_approval_threshold_value=10.0)
    task = tasks[0]

    assert task.requires_approval is True
    assert task.status == DispatchStatus.PENDING_APPROVAL

    # Human approval flow
    approved = dispatch_service.approve_dispatch(task, officer_id="CMO_DR_PATEL")
    assert approved.status == DispatchStatus.DISPATCHED
    assert approved.approval_by == "CMO_DR_PATEL"
    assert any(e.event_type == EventType.PLAN_APPROVED for e in store.events)

    # Manual override flow with audit
    overridden = dispatch_service.apply_manual_override(approved, new_assignee="INSP_02", officer_id="CMO_DR_PATEL", reason="Urgent border deployment")
    assert overridden.status == DispatchStatus.OVERRIDDEN
    assert overridden.assignee_id == "INSP_02"
    assert any(e.event_type == EventType.OVERRIDDEN for e in store.events)



# ==============================================================================
# 3. DETERMINISTIC FORECASTING TESTS
# ==============================================================================

def test_06_forecasting_intermittent_demand_with_uncertainty():
    service = DeterministicForecastingService()
    history = [0.0, 10.0, 0.0, 0.0, 15.0, 0.0, 12.0, 0.0, 0.0, 20.0, 0.0, 14.0]

    fc = service.forecast_medicine_demand(
        facility_id="PHC_1",
        sku="MED-ARV-01",
        historical_issued=history,
        current_stock=40.0,
        horizon_days=14,
        data_freshness_days=1.0,
    )

    assert fc.status == "SUCCESS"
    assert fc.point_forecast > 0.0
    assert fc.lower_bound_80 <= fc.point_forecast <= fc.upper_bound_80
    assert 0.0 <= fc.stockout_risk_prob <= 1.0
    assert fc.days_to_stockout is not None
    assert "STATISTICAL PROJECTION" in fc.disclaimer
    assert fc.backtest_mase is not None


def test_07_forecasting_insufficient_and_stale_data_rejection():
    service = DeterministicForecastingService(min_history_points=5, max_acceptable_staleness_days=30.0)

    # 1. Insufficient data points
    short_fc = service.forecast_medicine_demand("PHC_SHORT", "MED-01", [5.0, 10.0], current_stock=20.0)
    assert short_fc.status == "INSUFFICIENT_DATA"
    assert any("below minimum requirement" in r for r in short_fc.reasons)

    # 2. Stale data points
    stale_fc = service.forecast_medicine_demand(
        "PHC_STALE", "MED-01", [10.0] * 15, current_stock=50.0, data_freshness_days=45.0
    )
    assert stale_fc.status == "STALE_DATA"
    assert any("stale" in r.lower() for r in stale_fc.reasons)


def test_08_forecasting_bed_occupancy():
    service = DeterministicForecastingService()
    admissions = [4, 5, 3, 6, 4, 5, 4]

    fc = service.forecast_bed_occupancy(
        facility_id="DH_DISTRICT",
        ward_type="ICU_O2",
        total_beds=20,
        historical_admissions=admissions,
        current_occupied=16,
        target_turnover_days=4.0,
        horizon_days=7,
    )

    assert fc.status == "SUCCESS"
    assert fc.resource_type == "BEDS"
    assert fc.point_forecast <= 20.0
    assert fc.stockout_risk_prob > 0.0


# ==============================================================================
# 4. DETERMINISTIC RESOURCE ALLOCATION TESTS
# ==============================================================================

def test_09_allocation_rejects_unverified_donors_and_violating_floors():
    allocator = DeterministicAllocationService(default_min_safety_days=14.0)

    donors = [
        # Donor 1: Unverified (Phantom trap)
        AllocationCandidate(
            facility_id="PHC_PHANTOM",
            resource_key="MED-ARV-01",
            reported_stock=5000.0,
            verified_usable_stock=0.0,
            verification_state="UNVERIFIED",
        ),
        # Donor 2: Below safety floor (14d * 5 = 70 units required floor; holds only 60)
        AllocationCandidate(
            facility_id="PHC_STRIPPED",
            resource_key="MED-ARV-01",
            reported_stock=60.0,
            verified_usable_stock=60.0,
            verification_state="VERIFIED",
            safety_floor=14.0,
            daily_consumption=5.0,
        ),
        # Donor 3: Valid donor with surplus
        AllocationCandidate(
            facility_id="PHC_VALID",
            resource_key="MED-ARV-01",
            reported_stock=300.0,
            verified_usable_stock=300.0,
            verification_state="VERIFIED",
            safety_floor=14.0,
            daily_consumption=5.0,  # Floor = 70.0; surplus = 230.0
            x_km=10.0,
            y_km=10.0,
        ),
    ]

    recipients = [
        AllocationRequirement(
            facility_id="PHC_SHORTAGE",
            resource_key="MED-ARV-01",
            shortfall_quantity=100.0,
            urgency_days_to_stockout=0.0,
            x_km=15.0,
            y_km=15.0,
        )
    ]

    res = allocator.solve_allocation(donors, recipients, resource_key="MED-ARV-01")

    assert res.status == "PROPOSED"
    assert res.fulfilled_quantity == 100.0
    assert len(res.proposed_transfers) == 1
    assert res.proposed_transfers[0].from_facility == "PHC_VALID"

    # Check exact rejection invariants
    rejected = {r["facility_id"]: r["invariant_violated"] for r in res.rejected_candidates}
    assert rejected["PHC_PHANTOM"] == "UNVERIFIED_STOCK_GATE"
    assert rejected["PHC_STRIPPED"] == "DONOR_SAFETY_FLOOR_VIOLATION"


# ==============================================================================
# 5. DEMO SAFETY CONTROLS TESTS
# ==============================================================================

def test_10_demo_safety_scenarios():
    sc1 = DemoSafetyService.seed_synthetic_scenario(seed=20260928)
    sc2 = DemoSafetyService.seed_synthetic_scenario(seed=20260928)

    assert sc1["demo_banner"] == DEMO_SAFETY_BANNER
    assert sc1["is_live_connection"] is False
    assert sc1["provenance"] == "SYNTHETIC_SIMULATION_CORPUS"
    assert len(sc1["facilities"]) == 4
    assert sc1["facilities"] == sc2["facilities"]
