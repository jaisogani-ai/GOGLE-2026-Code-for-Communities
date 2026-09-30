"""
TATHYON - PHASE 5 CORE PRODUCT LOOP TEST
Implements the 16-step execution loop of the verified product.
Ensures that a judge can mutate physical stock, demand, supplier delay, etc.
and watch the system dynamically recalculate.
"""

import pytest
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from tathyon.graph import HealthcareResourceGraph, Facility, FacilityType, ResourceState
from tathyon.store import EventStore
from tathyon.schema import Attestation, Evidence, Claim, ResourceType, new_id, now, Provenance
from tathyon.verify import VerificationEngine
from tathyon.forecast import fit_tsb, early_warning
from tathyon.cases import CaseManager, CaseStatus
from tathyon.planner import ResponsePlanner
from tathyon.shipment import ShipmentIntelligence, ShipmentOrder


def run_core_loop(
    physical_usable_stock_donor: float,
    physical_usable_stock_recipient: float,
    demand_multiplier: float,
    shipment_quantity: float,
    delivered_quantity: float,
    donor_consumption_velocity: float = 10.0,
    recipient_consumption_velocity: float = 2.0,
):
    """
    Executes the 16-step Tathyon Core Loop deterministically.
    Returns a dictionary of the final computed states for assertions.
    """
    # Initialize Core Engines
    graph = HealthcareResourceGraph()
    store = EventStore()
    verify = VerificationEngine(store)
    cases = CaseManager()
    shipments = ShipmentIntelligence()

    # Setup Facilities
    graph.add_facility(Facility("F_RECIPIENT", "PHC Recipient", FacilityType.PHC, "Bastar", "CG", 0.0, 0.0))
    graph.add_facility(Facility("F_DONOR", "DH Donor", FacilityType.DISTRICT_HOSPITAL, "Bastar", "CG", 0.0, 0.0))
    
    sku = "MED_ANTI_RABIES_VACCINE"
    
    # ---------------------------------------------------------
    # 1. FACILITY OBSERVES RESOURCE & 2. SYSTEM RECONCILES RESOURCE REALITY
    # ---------------------------------------------------------
    # Simulating digital ledger baseline
    claim_rec = Claim(new_id("clm"), "F_RECIPIENT", ResourceType.MEDICINE, sku, {"reported_stock": 200.0}, "DVDMS", "system", now(), now(), Provenance.SYNTHETIC)
    store.put_claim(claim_rec)
    
    # Field physical evidence overrides digital claim for recipient
    att_rec = Attestation(new_id("att"), claim_rec.claim_id, [], {"usable_qty": physical_usable_stock_recipient}, now(), "usr_1", "pharmacist", "dlg_1", False, 120.0, "sig")
    store.put_attestation(att_rec)
    
    state_rec = verify.state_for("F_RECIPIENT", ResourceType.MEDICINE, sku, as_of=now())
    verified_stock_rec = state_rec.verified_usable_qty if state_rec.verified_usable_qty is not None else 0.0
    
    # Field physical evidence for donor
    claim_don = Claim(new_id("clm"), "F_DONOR", ResourceType.MEDICINE, sku, {"reported_stock": 1000.0}, "DVDMS", "system", now(), now(), Provenance.SYNTHETIC)
    store.put_claim(claim_don)
    att_don = Attestation(new_id("att"), claim_don.claim_id, [], {"usable_qty": physical_usable_stock_donor}, now(), "usr_2", "pharmacist", "dlg_2", False, 120.0, "sig")
    store.put_attestation(att_don)
    state_don = verify.state_for("F_DONOR", ResourceType.MEDICINE, sku, as_of=now())
    verified_stock_don = state_don.verified_usable_qty if state_don.verified_usable_qty is not None else 0.0

    # Sync to Graph
    graph.set_resource_state(ResourceState(facility_id="F_RECIPIENT", resource_id=sku, claimed_quantity=200.0, observed_quantity=verified_stock_rec, usable_quantity=verified_stock_rec, consumption_velocity=recipient_consumption_velocity))
    graph.set_resource_state(ResourceState(facility_id="F_DONOR", resource_id=sku, claimed_quantity=1000.0, observed_quantity=verified_stock_don, usable_quantity=verified_stock_don, consumption_velocity=donor_consumption_velocity))

    # ---------------------------------------------------------
    # 3. DEMAND/CONSUMPTION IS UPDATED
    # ---------------------------------------------------------
    base_history = np.array([5.0, 6.0, 5.0, 4.0, 6.0, 5.0, 5.0, 4.0, 7.0, 5.0])
    demand_history = base_history * demand_multiplier
    fit = fit_tsb(demand_history)
    
    # ---------------------------------------------------------
    # 4. RISK IS CALCULATED
    # ---------------------------------------------------------
    warning = early_warning(verified_stock_rec, fit, lead_time_days=14.0, criticality=1.5, basis="verified")
    
    # ---------------------------------------------------------
    # 5. SHORTAGE CASE IS CREATED
    # ---------------------------------------------------------
    if warning.level in ("CRITICAL", "HIGH"):
        case = cases.create_case("F_RECIPIENT", sku, "STOCKOUT_PREDICTED", warning.p_stockout_lead_time, warning.level)
    else:
        case = cases.create_case("F_RECIPIENT", sku, "ROUTINE_REPLENISHMENT", 0.1, "LOW")
        
    # ---------------------------------------------------------
    # 6. SYSTEM FINDS FEASIBLE SOURCES & 7. OPTIMIZER CALCULATES PLAN & 8. EXPLAINS REJECTIONS
    # ---------------------------------------------------------
    planner = ResponsePlanner(graph, store)
    plan_result = planner.plan_redistribution(sku, min_safety_days=14.0)
    
    case.attach_candidate_plans([plan_result.to_dict()])
    if plan_result.transfers:
        case.select_plan(plan_result.plan_id)
    
    # ---------------------------------------------------------
    # 9. HUMAN APPROVES
    # ---------------------------------------------------------
    case.record_approval({"officer": "CMO_BASTAR", "approved": True})
    
    # ---------------------------------------------------------
    # 10. SHIPMENT IS CREATED
    # ---------------------------------------------------------
    order_id = new_id("ORD")
    order = ShipmentOrder(order_id, "F_DONOR", "Donor Warehouse", "F_DONOR", "F_RECIPIENT", sku, shipment_quantity, now())
    shipments.record_order(order)
    
    # ---------------------------------------------------------
    # 11. DELIVERY IS TRACKED (Time passes)
    # ---------------------------------------------------------
    # ... Wait 2 days ...
    
    # ---------------------------------------------------------
    # 12. RECEIPT IS VERIFIED & 13. DISCREPANCY IS RECORDED
    # ---------------------------------------------------------
    receipt = shipments.record_delivery(order_id, delivered_quantity, actual_delivery_date=now())
    
    # ---------------------------------------------------------
    # 14. INVENTORY IS UPDATED
    # ---------------------------------------------------------
    new_stock = verified_stock_rec + delivered_quantity
    graph.set_resource_state(ResourceState(facility_id="F_RECIPIENT", resource_id=sku, claimed_quantity=new_stock, observed_quantity=new_stock, usable_quantity=new_stock, consumption_velocity=recipient_consumption_velocity))
    
    # ---------------------------------------------------------
    # 15. RISK IS RECALCULATED
    # ---------------------------------------------------------
    new_warning = early_warning(new_stock, fit, lead_time_days=14.0, criticality=1.5, basis="verified")
    
    # ---------------------------------------------------------
    # 16. CASE IS MEASURED/CLOSED
    # ---------------------------------------------------------
    case.record_outcome({"new_risk_level": new_warning.level, "fill_rate": receipt.fill_rate})
    case.transition(CaseStatus.CLOSED)

    return {
        "initial_stock_rec": verified_stock_rec,
        "initial_stock_don": verified_stock_don,
        "demand_rate": fit.rate,
        "initial_warning_level": warning.level,
        "initial_stockout_prob": warning.p_stockout_lead_time,
        "plan_generated": bool(plan_result.transfers),
        "plan_rejections": plan_result.rejected_donors,
        "receipt_fill_rate": receipt.fill_rate,
        "final_stock_rec": new_stock,
        "final_warning_level": new_warning.level,
        "final_stockout_prob": new_warning.p_stockout_lead_time,
        "case_status": case.status.value,
    }


def test_core_loop_scenario_a_normal_transfer():
    """Judge configures normal demand, donor has stock, full delivery."""
    result = run_core_loop(
        physical_usable_stock_donor=500.0,
        physical_usable_stock_recipient=10.0,
        demand_multiplier=1.0,  # ~5/day demand
        shipment_quantity=100.0,
        delivered_quantity=100.0
    )
    
    # Assert Risk Triggered
    assert result["initial_warning_level"] in ("HIGH", "CRITICAL")
    assert result["initial_stockout_prob"] > 0.8
    
    # Assert Plan
    print(f"REJECTIONS: {result['plan_rejections']}")
    assert result["plan_generated"] is True
    
    # Assert Delivery
    assert result["receipt_fill_rate"] == 1.0
    
    # Assert Recalculation
    assert result["final_stock_rec"] == 110.0
    assert result["final_warning_level"] in ("LOW", "WATCH")
    assert result["final_stockout_prob"] < 0.1
    assert result["case_status"] == "CLOSED"


def test_core_loop_scenario_b_demand_surge_short_delivery():
    """Judge configures 3x demand, donor is low on stock, partial delivery."""
    result = run_core_loop(
        physical_usable_stock_donor=80.0, # Not enough to spare after safety
        physical_usable_stock_recipient=10.0,
        demand_multiplier=3.0,  # ~15/day demand
        shipment_quantity=50.0,
        delivered_quantity=20.0
    )
    
    assert result["demand_rate"] > 14.0
    assert result["initial_warning_level"] == "CRITICAL"
    
    # Donor has 80. Demand is 15. Safety days is 14. Donor needs 210. 
    # Therefore donor is BLOCKED from transferring.
    assert result["plan_generated"] is False
    assert len(result["plan_rejections"]) > 0
    assert "safety floor" in result["plan_rejections"][0]["reason"].lower()
    
    # Delivery is short
    assert result["receipt_fill_rate"] == 0.4  # 20 / 50
    
    # Risk recalculation (10 + 20 = 30 stock. Demand is ~15/day. Runway is 2 days.)
    assert result["final_stock_rec"] == 30.0
    assert result["final_warning_level"] == "CRITICAL"
    assert result["final_stockout_prob"] > 0.9
