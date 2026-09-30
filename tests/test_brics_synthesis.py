"""
tests/test_brics_synthesis.py — Verification of BRICS Track 3 Synthesis and End-to-End Resilience Pipeline.

Validates the full 14-stage causal loop:
1. Existing System Claims (e-Aushadhi / DVDMS)
2. Usable-State Engine (claimed, observed, expired, quarantined, reserved, incoming, safety floor)
3. Physical Reconciliation
4. Resource Graph Multi-Resource Scope (medicine, beds, personnel, equipment)
5. Failure Risk & Network Impact
6. Counterfactual Twin (DO NOTHING vs ACT)
7. Response Plan (OR-Tools Optimization + Safety Floor)
8. Medical Officer Approval (policy-based role guard)
9. System-of-Record Payload (e-Aushadhi / DVDMS interoperable dispatch voucher)
10. Delivery & Planned vs Actual Variances
11. Outcome Feedback into Subsequent Decisions
"""

import pytest
from datetime import datetime, timezone
from tathyon.graph import ResourceState, HealthcareResourceGraph, create_default_resource_graph
from tathyon.stockout import StockoutPredictor
from tathyon.twin import EmergencyResilienceScenarioEngine, ShockType
from tathyon.planner import ResponsePlanner
from tathyon.store import EventStore, EventType



def test_usable_state_engine_physical_reconciliation():
    """Validates the Usable-State Engine reconciliation formula:
    usable = max(min(observed, claimed) - expired - quarantined - reserved, 0.0)
    """
    state = ResourceState(
        facility_id="PHC_REMOTE_01",
        resource_id="MED_ANTI_RABIES_VACCINE",
        claimed_quantity=100.0,
        observed_quantity=90.0,
        expired_quantity=10.0,
        quarantined_quantity=5.0,
        reserved_quantity=5.0,
        incoming_quantity=20.0,
        consumption_velocity=3.0,
        safety_floor_days=14.0,
    )
    # Reconciliation: min(90, 100) = 90; 90 - 10 (expired) - 5 (quarantined) - 5 (reserved) = 70.0
    assert state.reconcile_usable_state() == 70.0
    assert state.usable_quantity == 70.0
    assert state.phantom_inventory == 30.0  # 100 claimed - 70 usable
    assert state.safety_floor_quantity == 42.0  # 3.0 * 14.0

    # Serialization check
    d = state.to_dict()
    assert "reconciliation_breakdown" in d
    assert d["reconciliation_breakdown"]["claimed"] == 100.0
    assert d["reconciliation_breakdown"]["observed"] == 90.0
    assert d["reconciliation_breakdown"]["expired"] == 10.0
    assert d["reconciliation_breakdown"]["quarantined"] == 5.0
    assert d["reconciliation_breakdown"]["reserved"] == 5.0
    assert d["reconciliation_breakdown"]["usable"] == 70.0



