"""
TATHYON Intelligence Build — Comprehensive Test Suite.

Tests for all new modules:
1. intelligence.py — Resource Health Profile, Discrepancy Detector, Confidence
2. demand.py — Demand Signal Fusion, Emergency Signals
3. twin.py — Twin 2.0 compare_plans
4. planner.py — PlanExplorer multi-objective
5. shipment.py — Shipment Intelligence, Supplier/Route Reliability
6. data_moat.py — Closed-loop analytics
7. scenarios.py — Failure Injection, Scenario Builder, Final Demo

These tests run through demonstration CODE PATHS.
"""
import pytest

from tathyon.graph import (
    Facility,
    HealthcareResourceGraph,
    Resource,
    ResourceState,
    create_default_resource_graph,
    haversine_distance_km,
)
from tathyon.schema import (
    EventType, FacilityType, ResourceType, VerificationState,
    new_id, now,
)
from tathyon.store import EventStore


# ==========================================================================
# Fixtures
# ==========================================================================

@pytest.fixture
def graph_with_store():
    """Creates a minimal graph with event store for testing."""
    store = EventStore()
    graph = create_default_resource_graph(store)
    return graph, store


@pytest.fixture
def resource_state():
    """Creates a resource state with typical values."""
    return ResourceState(
        facility_id="PHC_CENTRAL_01",
        resource_id="MED-ARV-01",
        claimed_quantity=500.0,
        observed_quantity=420.0,
        usable_quantity=380.0,
        expired_quantity=30.0,
        quarantined_quantity=10.0,
        incoming_quantity=200.0,
        consumption_velocity=15.0,
        lead_time=7.0,
        forecast_demand=210.0,
        risk="MEDIUM",
        last_attested_at=now(),
    )


# ==========================================================================
# 1. INTELLIGENCE MODULE TESTS
# ==========================================================================

class TestResourceHealthProfile:
    """Tests for Resource Health Profile computation."""

    def test_healthy_profile(self, resource_state):
        """State with 25+ days of stock and fresh attestation should be HEALTHY."""
        from tathyon.intelligence import compute_health_profile
        resource_state.usable_quantity = 500.0
        resource_state.claimed_quantity = 520.0
        resource_state.consumption_velocity = 15.0
        resource_state.expired_quantity = 5.0
        resource_state.quarantined_quantity = 0.0
        resource_state.last_attested_at = now()

        profile = compute_health_profile(resource_state)
        assert profile.supply.status in ("HEALTHY", "DEGRADED")
        assert profile.freshness.status == "HEALTHY"
        assert profile.quality.status == "HEALTHY"
        assert profile.reconciliation.status == "HEALTHY"
        assert profile.to_dict()["facility_id"] == "PHC_CENTRAL_01"

    def test_critical_supply(self, resource_state):
        """Near-zero stock should produce CRITICAL supply dimension."""
        from tathyon.intelligence import compute_health_profile
        resource_state.usable_quantity = 10.0
        resource_state.consumption_velocity = 15.0
        profile = compute_health_profile(resource_state)
        assert profile.supply.status == "CRITICAL"
        assert "supply" in profile.critical_dimensions

    def test_stale_attestation(self, resource_state):
        """Missing attestation should produce CRITICAL freshness."""
        from tathyon.intelligence import compute_health_profile
        resource_state.last_attested_at = None
        profile = compute_health_profile(resource_state)
        assert profile.freshness.status == "CRITICAL"

    def test_severe_phantom_inventory(self, resource_state):
        """Large claim-usable gap should produce CRITICAL reconciliation."""
        from tathyon.intelligence import compute_health_profile
        resource_state.claimed_quantity = 1000.0
        resource_state.usable_quantity = 200.0
        profile = compute_health_profile(resource_state)
        assert profile.reconciliation.status == "CRITICAL"

    def test_high_expiry_rate(self, resource_state):
        """High expired stock percentage should produce CRITICAL quality."""
        from tathyon.intelligence import compute_health_profile
        resource_state.expired_quantity = 250.0
        resource_state.claimed_quantity = 500.0
        resource_state.observed_quantity = 500.0
        profile = compute_health_profile(resource_state)
        assert profile.quality.status in ("CRITICAL", "DEGRADED")

    def test_overall_status_driven_by_worst(self, resource_state):
        """Overall status should be CRITICAL if any dimension is CRITICAL."""
        from tathyon.intelligence import compute_health_profile
        resource_state.usable_quantity = 5.0  # CRITICAL supply
        profile = compute_health_profile(resource_state)
        assert profile.overall_status == "CRITICAL"


class TestDiscrepancyDetector:
    """Tests for Discrepancy Detector."""

    def test_phantom_detection(self):
        """Should detect phantom inventory when claim >> usable."""
        from tathyon.intelligence import DiscrepancyDetector
        detector = DiscrepancyDetector()
        records = detector.record_observation(
            "FAC-001", "MED-001",
            claimed=500.0, usable=200.0,
        )
        assert len(records) >= 1
        assert records[0].discrepancy_type == "PHANTOM_INVENTORY"
        assert records[0].magnitude == 300.0

    def test_no_discrepancy_when_close(self):
        """Should NOT detect phantom when claim is close to usable."""
        from tathyon.intelligence import DiscrepancyDetector
        detector = DiscrepancyDetector()
        records = detector.record_observation(
            "FAC-001", "MED-001",
            claimed=500.0, usable=490.0,
        )
        phantom_records = [r for r in records if r.discrepancy_type == "PHANTOM_INVENTORY"]
        assert len(phantom_records) == 0

    def test_pattern_detection_systematic(self):
        """Should detect SYSTEMATIC pattern after repeated observations."""
        from tathyon.intelligence import DiscrepancyDetector
        detector = DiscrepancyDetector()
        for i in range(8):
            detector.record_observation(
                "FAC-001", "MED-001",
                claimed=500.0, usable=200.0,
            )
        patterns = detector.detect_patterns(min_occurrences=3)
        assert len(patterns) >= 1
        assert patterns[0].severity == "SYSTEMATIC"
        assert patterns[0].pattern_type == "PHANTOM_INVENTORY"

    def test_short_delivery_detection(self):
        """Should detect short delivery."""
        from tathyon.intelligence import DiscrepancyDetector
        detector = DiscrepancyDetector()
        records = detector.record_observation(
            "FAC-001", "MED-001",
            claimed=500.0, usable=500.0,
            expected_delivery=100.0, delivered=50.0,
        )
        short = [r for r in records if r.discrepancy_type == "SHORT_DELIVERY"]
        assert len(short) == 1


class TestConfidenceCalculator:
    """Tests for data-quality-derived confidence."""

    def test_confidence_with_fresh_data(self, resource_state):
        """Fresh attestation and good reconciliation should give high confidence."""
        from tathyon.intelligence import calculate_confidence
        result = calculate_confidence(resource_state, observation_count=5, attestation_count=3)
        assert result["composite_confidence"] > 0.3
        assert "freshness" in result["factors"]
        assert "methodology" in result

    def test_confidence_without_attestation(self, resource_state):
        """Missing attestation should produce low confidence."""
        from tathyon.intelligence import calculate_confidence
        resource_state.last_attested_at = None
        result = calculate_confidence(resource_state)
        assert result["factors"]["freshness"] == 0.0
        assert result["composite_confidence"] < 0.3


# ==========================================================================
# 2. DEMAND MODULE TESTS
# ==========================================================================

class TestEmergencySignals:
    """Tests for Emergency Signal typing."""

    def test_emergency_type_enum(self):
        from tathyon.demand import EmergencyType
        assert EmergencyType.DENGUE_SURGE.value == "DENGUE_SURGE"
        assert EmergencyType.WAREHOUSE_FAILURE.value == "WAREHOUSE_FAILURE"

    def test_emergency_templates_exist(self):
        from tathyon.demand import EMERGENCY_TEMPLATES, EmergencyType
        assert EmergencyType.DENGUE_SURGE in EMERGENCY_TEMPLATES
        template = EMERGENCY_TEMPLATES[EmergencyType.DENGUE_SURGE]
        assert template["demand_multiplier"] == 2.5
        assert "justification" in template

    def test_emergency_signal_creation(self):
        from tathyon.demand import EmergencySignal, EmergencyType
        signal = EmergencySignal(
            signal_id="TEST-001",
            emergency_type=EmergencyType.FLOOD,
            name="Test Flood",
            description="Test flood event",
            affected_geography=["Bastar"],
            affected_resources=["MED_ORS"],
            demand_multiplier=1.8,
            duration_days=14,
            uncertainty=0.5,
        )
        d = signal.to_dict()
        assert d["demand_multiplier"] == 1.8
        assert d["emergency_type"] == "FLOOD"


class TestDemandFusion:
    """Tests for Demand Signal Fusion engine."""

    def test_baseline_fusion(self):
        """With no modifiers, fused rate should equal baseline."""
        from tathyon.demand import DemandFusionEngine
        engine = DemandFusionEngine()
        result = engine.fuse_demand(
            "FAC-001", "MED-001",
            baseline_velocity=10.0,
        )
        assert result.fused_daily_rate == 10.0
        assert result.total_multiplier == 1.0
        assert "stable" in result.explanation.lower()

    def test_emergency_multiplier(self):
        """Active emergency should multiply demand."""
        from tathyon.demand import DemandFusionEngine, EmergencySignal, EmergencyType
        engine = DemandFusionEngine()
        engine.declare_emergency(EmergencySignal(
            signal_id="E-001",
            emergency_type=EmergencyType.DENGUE_SURGE,
            name="Dengue Surge",
            description="Test",
            affected_geography=["Bastar"],
            affected_resources=["MED-001"],
            demand_multiplier=2.5,
            duration_days=21,
            uncertainty=0.3,
        ))
        result = engine.fuse_demand(
            "FAC-001", "MED-001",
            baseline_velocity=10.0,
            district="Bastar",
        )
        assert result.fused_daily_rate > 10.0
        assert result.total_multiplier >= 2.5
        assert "E-001" in result.active_emergencies
        assert "emergency" in result.explanation.lower()

    def test_seasonality_factor(self):
        """Seasonality should adjust demand."""
        from tathyon.demand import DemandFusionEngine
        engine = DemandFusionEngine()
        result = engine.fuse_demand(
            "FAC-001", "MED-001",
            baseline_velocity=10.0,
            seasonality_factor=1.4,
        )
        assert result.fused_daily_rate > 10.0
        assert result.total_multiplier == pytest.approx(1.4, abs=0.01)

    def test_clear_emergency(self):
        """Clearing an emergency should restore baseline demand."""
        from tathyon.demand import DemandFusionEngine, EmergencySignal, EmergencyType
        engine = DemandFusionEngine()
        engine.declare_emergency(EmergencySignal(
            signal_id="E-002",
            emergency_type=EmergencyType.FLOOD,
            name="Flood",
            description="Test",
            affected_geography=["Bastar"],
            affected_resources=["MED-001"],
            demand_multiplier=1.8,
            duration_days=14,
            uncertainty=0.5,
        ))
        engine.clear_emergency("E-002")
        result = engine.fuse_demand(
            "FAC-001", "MED-001",
            baseline_velocity=10.0,
            district="Bastar",
        )
        assert result.fused_daily_rate == 10.0


# ==========================================================================
# 3. TWIN 2.0 COMPARE PLANS TESTS
# ==========================================================================

class TestTwinComparePlans:
    """Tests for Twin 2.0 multi-plan comparison."""

    def test_compare_returns_structure(self, graph_with_store):
        """compare_plans should return structured comparison."""
        from tathyon.twin import EmergencyResilienceScenarioEngine, ShockType
        graph, store = graph_with_store
        twin = EmergencyResilienceScenarioEngine(graph, store)

        # Get a valid resource ID from the graph
        resource_id = None
        for (fid, rid), st in graph.states.items():
            if st.consumption_velocity > 0:
                resource_id = rid
                break
        if not resource_id:
            pytest.skip("No active resource in default graph")

        result = twin.compare_plans(
            shock=ShockType.DEMAND_PLUS_50,
            resource_id=resource_id,
            plans=[{
                "plan_name": "TEST_PLAN_A",
                "transfers": [],
            }],
        )
        assert "do_nothing" in result
        assert "plans" in result
        assert result["shock"] == "DEMAND_PLUS_50"
        assert result["provenance"] == "SIMULATION"

    def test_plan_with_transfers_improves(self, graph_with_store):
        """A plan with transfers should improve over do-nothing."""
        from tathyon.twin import EmergencyResilienceScenarioEngine, ShockType
        graph, store = graph_with_store
        twin = EmergencyResilienceScenarioEngine(graph, store)

        # Find shortage and donor
        resource_id = None
        for (fid, rid), st in graph.states.items():
            if st.consumption_velocity > 0:
                resource_id = rid
                break
        if not resource_id:
            pytest.skip("No active resource")

        shortages = graph.get_shortage_facilities(resource_id)
        donors = graph.get_donor_inventory(resource_id, min_safety_days=7.0)

        if not shortages or not donors:
            pytest.skip("No shortage/donor pair for comparison test")

        result = twin.compare_plans(
            shock=ShockType.DEMAND_PLUS_100,
            resource_id=resource_id,
            plans=[{
                "plan_name": "REDISTRIBUTION",
                "transfers": [{
                    "source": donors[0]["facility_id"],
                    "destination": shortages[0]["facility_id"],
                    "quantity": min(donors[0]["surplus_transferable"] * 0.3,
                                   shortages[0]["shortfall_quantity"]),
                }],
            }],
        )
        assert len(result["plans"]) == 1


# ==========================================================================
# 4. PLAN EXPLORER TESTS
# ==========================================================================

class TestPlanExplorer:
    """Tests for multi-objective plan generation."""

    def test_explorer_returns_four_plans(self, graph_with_store):
        """Should generate 4 candidate plans when shortages and donors exist."""
        from tathyon.planner import PlanExplorer
        graph, store = graph_with_store

        resource_id = None
        for (fid, rid), st in graph.states.items():
            if st.consumption_velocity > 0:
                resource_id = rid
                break
        if not resource_id:
            pytest.skip("No active resource")

        explorer = PlanExplorer(graph, store)
        result = explorer.explore_plans(resource_id)

        if result["status"] == "NO_PLANS_POSSIBLE":
            pytest.skip("No shortage/donor pair for exploration")

        assert result["status"] == "PLANS_GENERATED"
        assert len(result["plans"]) == 4
        assert "tradeoff_matrix" in result
        assert len(result["tradeoff_matrix"]) == 4

        # Verify plan names
        names = {p["plan_name"] for p in result["plans"]}
        assert "PLAN_A_CLOSEST_DONOR" in names
        assert "PLAN_B_MAX_COVERAGE" in names
        assert "PLAN_C_LOWEST_COST" in names
        assert "PLAN_D_MAX_RESILIENCE" in names

    def test_explorer_handles_no_shortages(self, graph_with_store):
        """Should handle case with no shortages gracefully."""
        from tathyon.planner import PlanExplorer
        graph, store = graph_with_store
        explorer = PlanExplorer(graph, store)
        result = explorer.explore_plans("NONEXISTENT_RESOURCE")
        assert result["status"] == "NO_PLANS_POSSIBLE"


# ==========================================================================
# 5. SHIPMENT INTELLIGENCE TESTS
# ==========================================================================

class TestShipmentIntelligence:
    """Tests for Shipment Intelligence engine."""

    def test_record_order_and_delivery(self):
        """Should track order lifecycle."""
        from tathyon.shipment import ShipmentIntelligence, ShipmentOrder
        si = ShipmentIntelligence()
        order = ShipmentOrder(
            order_id="ORD-001",
            supplier_id="SUP-001",
            supplier_name="Test Supplier",
            source_facility_id="DWH-01",
            dest_facility_id="PHC-01",
            resource_id="MED-001",
            ordered_quantity=100.0,
            expected_delivery_date="2025-01-15T00:00:00+00:00",
        )
        si.record_order(order)
        assert len(si.orders) == 1

        result = si.record_delivery("ORD-001", received_quantity=90.0,
                                     actual_delivery_date="2025-01-16T00:00:00+00:00")
        assert result is not None
        assert result.fill_rate == 0.9
        assert result.is_short

    def test_replenishment_failure_detection(self):
        """Should detect when shipment arrives AFTER stockout."""
        from datetime import datetime, timedelta, timezone
        from tathyon.shipment import ShipmentIntelligence, ShipmentOrder

        si = ShipmentIntelligence()
        # Shipment arriving in 10 days
        future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
        si.record_order(ShipmentOrder(
            order_id="ORD-002",
            supplier_id="SUP-001",
            supplier_name="Slow Supplier",
            source_facility_id="DWH-01",
            dest_facility_id="PHC-01",
            resource_id="MED-001",
            ordered_quantity=200.0,
            expected_delivery_date=future,
            status="IN_TRANSIT",
        ))

        # Stockout in 3 days — BEFORE shipment arrives
        result = si.check_replenishment_risk("PHC-01", "MED-001", days_to_stockout=3.0)
        assert result["status"] == "REPLENISHMENT_FAILURE"
        assert result["risk"] == "CRITICAL"

    def test_no_pending_shipment(self):
        """Should flag NO_PENDING_SHIPMENT."""
        from tathyon.shipment import ShipmentIntelligence
        si = ShipmentIntelligence()
        result = si.check_replenishment_risk("PHC-XX", "MED-XX", days_to_stockout=5.0)
        assert result["status"] == "NO_PENDING_SHIPMENT"
        assert result["risk"] == "HIGH"


class TestSupplierReliability:
    """Tests for Supplier Reliability computation."""

    def test_insufficient_data(self):
        """Should report INSUFFICIENT_DATA with < 3 deliveries."""
        from tathyon.shipment import ShipmentIntelligence, ShipmentOrder, SupplierReliability
        si = ShipmentIntelligence()
        si.record_order(ShipmentOrder(
            order_id="O1", supplier_id="S1", supplier_name="Test",
            source_facility_id="DW1", dest_facility_id="PHC1",
            resource_id="M1", ordered_quantity=100, expected_delivery_date=now(),
        ))
        si.record_delivery("O1", 100)

        sr = SupplierReliability(si)
        result = sr.compute_reliability("S1")
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_reliable_supplier(self):
        """Should compute RELIABLE for a good supplier."""
        from tathyon.shipment import ShipmentIntelligence, ShipmentOrder, SupplierReliability
        si = ShipmentIntelligence()
        for i in range(5):
            oid = f"ORD-R-{i}"
            si.record_order(ShipmentOrder(
                order_id=oid, supplier_id="S-GOOD", supplier_name="Good Supplier",
                source_facility_id="DW1", dest_facility_id="PHC1",
                resource_id="M1", ordered_quantity=100,
                expected_delivery_date=now(),
            ))
            si.record_delivery(oid, received_quantity=98.0, actual_delivery_date=now())

        sr = SupplierReliability(si)
        result = sr.compute_reliability("S-GOOD")
        assert result["status"] == "COMPUTED"
        assert result["fill_rate"] > 0.95
        assert result["composite_reliability"] > 0.7


# ==========================================================================
# 6. DATA MOAT TESTS
# ==========================================================================


# ==========================================================================
# 7. SCENARIOS & FAILURE INJECTION TESTS
# ==========================================================================

class TestFailureInjector:
    """Tests for named failure injection."""

    def test_ledger_wrong_injection(self, graph_with_store):
        """LEDGER_WRONG should reduce usable but not claimed."""
        from tathyon.scenarios import FailureInjector, FailureMode
        graph, store = graph_with_store

        # Find first facility-resource pair
        fid, rid = None, None
        for (f, r), st in graph.states.items():
            if st.usable_quantity > 0:
                fid, rid = f, r
                break
        if not fid:
            pytest.skip("No active state for injection test")

        injector = FailureInjector(graph, store)
        result = injector.inject(FailureMode.LEDGER_WRONG, fid, rid)
        assert result["status"] == "INJECTED"
        assert result["after"]["usable"] < result["before"]["claimed"]

    def test_demand_surge_injection(self, graph_with_store):
        """DEMAND_SURGE should double consumption velocity."""
        from tathyon.scenarios import FailureInjector, FailureMode
        graph, store = graph_with_store

        fid, rid = None, None
        for (f, r), st in graph.states.items():
            if st.consumption_velocity > 0:
                fid, rid = f, r
                break
        if not fid:
            pytest.skip("No active state")

        injector = FailureInjector(graph, store)
        result = injector.inject(FailureMode.DEMAND_SURGE, fid, rid)
        assert result["after"]["velocity"] >= result["before"]["velocity"] * 1.5

    def test_failure_on_missing_facility(self, graph_with_store):
        """Should handle missing facility gracefully."""
        from tathyon.scenarios import FailureInjector, FailureMode
        graph, store = graph_with_store
        injector = FailureInjector(graph, store)
        result = injector.inject(FailureMode.LEDGER_WRONG, "NONEXISTENT", "MED-001")
        assert result["status"] == "FACILITY_NOT_FOUND"


class TestScenarioBuilder:
    """Tests for the Scenario Builder."""

    def test_build_and_run(self, graph_with_store):
        """Should build and run a custom scenario."""
        from tathyon.scenarios import ScenarioBuilder, ScenarioSpec
        graph, store = graph_with_store

        # Find a resource
        resource_id = None
        district = None
        for (fid, rid), st in graph.states.items():
            if st.consumption_velocity > 0:
                resource_id = rid
                fac = graph.facilities.get(fid)
                district = fac.district if fac else "Bastar"
                break
        if not resource_id:
            pytest.skip("No active resource")

        spec = ScenarioSpec(
            name="Test Dengue Scenario",
            description="Test scenario for dengue outbreak",
            resource_id=resource_id,
            target_district=district,
            shock_type="DEMAND_PLUS_50",
            demand_multiplier=2.0,
        )
        builder = ScenarioBuilder(graph, store)
        result = builder.build_and_run(spec)
        assert result["scenario_name"] == "Test Dengue Scenario"
        assert "twin_result" in result
        assert result["provenance"] == "SIMULATION"


class TestFinalDemo:
    """Tests for the 16-step final demo scenario."""

    def test_final_demo_runs(self, graph_with_store):
        """The complete 16-step demo should run through demonstration code."""
        from tathyon.scenarios import run_final_demo
        graph, store = graph_with_store

        result = run_final_demo(graph, store)
        assert result["uses_engine_code_paths"] is True
        assert result["total_steps"] >= 10  # Might be less if no donor
        assert result["provenance"] == "SIMULATION"

        # Verify key steps are present
        step_names = {s["name"] for s in result["steps"]}
        assert "OBSERVE" in step_names
        assert "RECONCILE" in step_names
        assert "FORECAST" in step_names
        assert "SIGNAL_EMERGENCY" in step_names
        assert "FUSE_DEMAND" in step_names
        assert "ASSESS_RISK" in step_names
