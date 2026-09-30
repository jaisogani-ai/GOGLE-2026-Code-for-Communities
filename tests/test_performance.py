"""
Performance and Scalability Benchmark Test Suite.

Section 24 of Sovereign Control Plane Specification:
Test:
  - 100 facilities
  - 1,000 resources
  - 10,000 observations
  - Large event history
  - Assert responsive latency and memory efficiency.
"""
import time
import pytest

from tathyon.graph import (
    Facility,
    HealthcareResourceGraph,
    Resource,
    ResourceState,
)
from tathyon.schema import (
    EventType,
    FacilityType,
    Provenance,
    ResourceType,
    now,
)
from tathyon.store import EventStore


class TestScaleAndPerformance:
    """Stress tests graph and event store with 100 facilities, 1,000 resources, and 10,000 observations."""

    def test_100_facilities_1000_resources_10000_observations(self):
        t0 = time.perf_counter()
        store = EventStore()
        graph = HealthcareResourceGraph(store=store)

        # 1. Register 100 Facilities
        for i in range(100):
            fid = f"FAC_PERF_{i:03d}"
            ftype = (
                FacilityType.DISTRICT_WAREHOUSE if i == 0 else
                FacilityType.DISTRICT_HOSPITAL if i < 5 else
                FacilityType.CHC if i < 25 else
                FacilityType.PHC
            )
            graph.add_facility(Facility(
                facility_id=fid,
                name=f"Performance Health Facility {i}",
                facility_type=ftype,
                district=f"District_{i % 5}",
                state="Chhattisgarh",
                lat=19.0 + (i * 0.01),
                lon=81.0 + (i * 0.01),
                catchment_population=10_000 + (i * 500),
            ))

        assert len(graph.facilities) == 100
        t_facs = time.perf_counter()

        # 2. Register 1,000 Resources
        for j in range(1000):
            rid = f"RES_PERF_{j:04d}"
            rtype = (
                ResourceType.VACCINE if j % 10 == 0 else
                ResourceType.BED if j % 15 == 0 else
                ResourceType.EQUIPMENT if j % 20 == 0 else
                ResourceType.PERSONNEL if j % 25 == 0 else
                ResourceType.MEDICINE
            )
            graph.add_resource(Resource(
                resource_id=rid,
                name=f"Resource Item {j}",
                resource_type=rtype,
                unit="vial" if rtype == ResourceType.VACCINE else "tablet",
                criticality=1.0 + ((j % 10) * 0.9),
                cold_chain_required=(rtype == ResourceType.VACCINE),
            ))

        assert len(graph.resources) == 1000
        t_res = time.perf_counter()

        # 3. Ingest and Reconcile 10,000 Observations
        # Distributed across the 100 facilities and first 100 resources (100 x 100 = 10,000 states)
        for i in range(100):
            fid = f"FAC_PERF_{i:03d}"
            for k in range(100):
                rid = f"RES_PERF_{k:04d}"
                claimed = 100.0 + (i + k) * 5.0
                observed = claimed * (0.85 if (i + k) % 3 == 0 else 1.0)
                expired = 10.0 if (i + k) % 7 == 0 else 0.0
                quarantined = 5.0 if (i + k) % 11 == 0 else 0.0

                st = ResourceState(
                    facility_id=fid,
                    resource_id=rid,
                    claimed_quantity=claimed,
                    observed_quantity=observed,
                    expired_quantity=expired,
                    quarantined_quantity=quarantined,
                    consumption_velocity=5.0 + (k % 10),
                    lead_time=7.0,
                    last_attested_at=now(),
                )
                graph.set_resource_state(st)

        assert len(graph.states) == 10_000
        t_states = time.perf_counter()

        # 4. Ingest 1,000 audit events into EventStore
        for ev_idx in range(1000):
            store.append(
                event_type=EventType.ATTESTED if ev_idx % 2 == 0 else EventType.CLAIM_INGESTED,
                facility_id=f"FAC_PERF_{ev_idx % 100:03d}",
                resource_type=ResourceType.MEDICINE,
                resource_key=f"RES_PERF_{ev_idx % 100:04d}",
                payload={"sample_metric": ev_idx, "batch": f"B-{ev_idx}"},
                actor="PerfBenchmarkActor",
            )

        assert len(store.events) == 1000
        t_events = time.perf_counter()

        # 5. Query responsiveness test: query 50 random states and shortage evaluations
        t_query_start = time.perf_counter()
        for q_idx in range(50):
            st = graph.get_resource_state(f"FAC_PERF_{q_idx:03d}", f"RES_PERF_{q_idx:04d}")
            assert st is not None
            assert st.usable_quantity <= st.claimed_quantity
            assert st.phantom_quantity >= 0.0
            assert st.usable_ratio <= 1.0
            assert st.reconciliation_gap >= 0.0

        shortages = graph.get_shortage_facilities("RES_PERF_0001", lead_time_days=14.0)
        donors = graph.get_donor_inventory("RES_PERF_0001", min_safety_days=7.0)
        t_query_end = time.perf_counter()

        total_time = t_query_end - t0
        query_latency_ms = ((t_query_end - t_query_start) / 52.0) * 1000.0

        # Assertions
        assert total_time < 5.0, f"Full 10,000 state matrix setup took {total_time:.2f}s (must be < 5.0s)"
        assert query_latency_ms < 10.0, f"Per-query latency {query_latency_ms:.2f}ms (must be < 10ms)"
