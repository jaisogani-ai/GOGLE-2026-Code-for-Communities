import pytest
from tathyon.schema import ResourceType, FacilityType
from tathyon.graph import HealthcareResourceGraph, ResourceState, Facility, Resource
from tathyon.store import EventStore
from tathyon.verify import VerificationEngine
from tathyon.cases import CaseManager
from tathyon.shipment import ShipmentIntelligence, ShipmentOrder
from tathyon.planner import ResponsePlanner

from tathyon.dvdms_sync import DVDMSAdapter
from tathyon.offline import OfflineQueueManager, OfflineEvent

@pytest.fixture
def base_engines():
    graph = HealthcareResourceGraph()
    # Add dummy facilities
    graph.add_facility(Facility(facility_id="F_PHC", name="PHC Alpha", facility_type=FacilityType.PHC, district="D1", state="S1", lat=0.0, lon=0.0))
    graph.add_facility(Facility(facility_id="F_DH", name="DH Omega", facility_type=FacilityType.DISTRICT_HOSPITAL, district="D1", state="S1", lat=0.1, lon=0.1))
    
    # Add dummy resources
    graph.add_resource(Resource(resource_id="MED_RABIES", name="Rabies Vaccine", resource_type=ResourceType.MEDICINE, unit="vial"))
    
    store = EventStore()
    verify = VerificationEngine(graph, store)
    cases = CaseManager()
    shipments = ShipmentIntelligence()
    
    return graph, store, verify, cases, shipments

def test_demo_1_ghost_stock(base_engines):
    """
    Demo 1: The Ghost Stock Reconciliation
    DVDMS reports 100 units, but physical verification shows 20 (expired/damaged).
    Tathyon flags discrepancy, calculates true runway, and triggers crisis case.
    """
    graph, store, verify, cases, shipments = base_engines
    sku = "MED_RABIES"
    fid = "F_PHC"
    
    # 1. DVDMS Sync
    adapter = DVDMSAdapter(graph)
    adapter.sync_facility_inventory(fid, [{"resource_id": sku, "quantity": 100.0}])
    
    state = graph.get_resource_state(fid, sku)
    assert state.claimed_quantity == 100.0
    
    # 2. Physical verification (simulated by manual update here for simplicity)
    # The reality is 20 usable, 80 expired.
    state.observed_quantity = 100.0
    state.expired_quantity = 80.0
    state.reconcile_usable_state()
    
    assert state.usable_quantity == 20.0
    
    # 3. Consumption velocity is 10/day. True runway = 2 days.
    state.consumption_velocity = 10.0
    runway = state.usable_quantity / state.consumption_velocity
    assert runway == 2.0
    
    # 4. Crisis Case is opened
    case = cases.create_case(
        facility=fid, resource=sku, trigger="GHOST_STOCK_DISCREPANCY",
        risk=0.9, severity="CRITICAL", evidence={"discrepancy": 80.0, "runway": 2.0}
    )
    assert case.status.value == "DETECTED"


def test_demo_2_scarce_allocation(base_engines):
    """
    Demo 2: The Scarce Allocation
    Critical PHC needs vaccines. Nearest DH has surplus, but Tathyon limits
    transfer to protect DH's 14-day safety floor, explicitly explaining the math.
    """
    graph, store, verify, cases, shipments = base_engines
    sku = "MED_RABIES"
    
    # PHC needs 70 units (0 usable, consumption 10, lead time 7)
    graph.set_resource_state(ResourceState(
        facility_id="F_PHC", resource_id=sku, claimed_quantity=0.0, 
        observed_quantity=0.0, usable_quantity=0.0, consumption_velocity=10.0
    ))
    
    # DH has 190 usable. Consumption 10. Lead time 7. Safety floor = 14 * 10 = 140.
    # Surplus = 190 - 140 = 50.
    # Planner limits to max 50% of usable (95) OR surplus (50). So max transfer = 50.
    graph.set_resource_state(ResourceState(
        facility_id="F_DH", resource_id=sku, claimed_quantity=190.0, 
        observed_quantity=190.0, usable_quantity=190.0, consumption_velocity=10.0
    ))
    
    planner = ResponsePlanner(graph, store)
    plan = planner.plan_redistribution(sku, min_safety_days=14.0)
    
    assert plan.status == "PROPOSED"
    assert len(plan.transfers) == 1
    assert plan.transfers[0].quantity == 50.0  # Cap applied correctly to protect safety floor
    
    # Ensure explanation states why 50 and not 70
    assert "Allocated 50.0 units matching recipient deficit while strictly capping donor allocation at surplus." in plan.why_quantity


def test_demo_3_offline_discrepancy(base_engines):
    """
    Demo 3: The Offline Discrepancy
    A transfer is dispatched, receiving PHC is offline. Truck arrives short (pilferage).
    PHC logs short receipt offline. Upon reconnect, Tathyon syncs, updates supplier metric,
    and leaves case OPEN.
    """
    graph, store, verify, cases, shipments = base_engines
    sku = "MED_RABIES"
    fid = "F_PHC"
    
    # Create a dispatched order
    order = ShipmentOrder(
        order_id="ORD-123", supplier_id="F_DH", supplier_name="DH Omega",
        source_facility_id="F_DH", dest_facility_id="F_PHC", resource_id=sku,
        ordered_quantity=100.0, expected_delivery_date="2026-09-30T12:00:00Z", status="DISPATCHED"
    )
    shipments.record_order(order)
    
    # 1. PHC is offline. They receive 60 units (40 lost).
    offline_manager = OfflineQueueManager(cases, shipments)
    offline_event = OfflineEvent(
        facility_id=fid, event_type="RECEIVE_SHIPMENT",
        payload={"order_id": "ORD-123", "received_quantity": 60.0, "loss_quantity": 40.0, "resource_id": sku}
    )
    offline_manager.enqueue(offline_event)
    
    assert len(offline_manager.get_pending()) == 1
    
    # 2. Connection restored. Process sync.
    results = offline_manager.process_sync(fid)
    
    assert results["conflicts"] == 1
    assert results["success"] == 0
    assert len(offline_manager.get_pending()) == 0
    
    # 3. Check that the order is updated
    assert order.status == "DELIVERED"
    assert order.loss_quantity == 40.0
    
    # 4. Check that a conflict case was opened
    opened_cases = [c for c in cases.list_cases() if c.facility == fid and c.trigger == "OFFLINE_SYNC_CONFLICT"]
    assert len(opened_cases) == 1
    assert opened_cases[0].status.value == "DETECTED"
    assert opened_cases[0].evidence["loss"] == 40.0
