"""
The two decision-loop events: VerificationVisit and OutcomeMeasurement.

Doctrines under test:
  - both are immutable; a status change is a new event, never an update;
  - a queue-ordering score is never persisted (no field for it, and the store
    refuses a payload that carries one);
  - STALE is a read-time projection, never a stored state.
"""
from __future__ import annotations

import dataclasses

import pytest

from tathyon.persist import SQLiteStore
from tathyon.schema import (
    FORBIDDEN_STORED_SCORE_FIELDS, EventType, OutcomeMeasurement, VerificationState,
    VerificationVisit, VisitStatus,
)
from tathyon.store import EventStore

DUE = "2026-10-01T00:00:00+00:00"


def _visit(**kw) -> VerificationVisit:
    base = dict(visit_id="vv_1", facility_id="FAC001", sku_id="AMX250",
                assignee="block-verifier-3", due_date=DUE)
    return VerificationVisit(**{**base, **kw})


def test_verification_visit_has_exactly_the_specified_fields():
    names = [f.name for f in dataclasses.fields(VerificationVisit)]
    assert names == ["visit_id", "facility_id", "sku_id", "assignee", "due_date", "status"]


def test_outcome_measurement_has_exactly_the_specified_fields():
    names = [f.name for f in dataclasses.fields(OutcomeMeasurement)]
    assert names == ["plan_id", "delivered_qty", "variance", "measured_at"]


def test_no_decision_event_can_carry_a_score():
    for cls in (VerificationVisit, OutcomeMeasurement):
        leaked = {f.name for f in dataclasses.fields(cls)} & FORBIDDEN_STORED_SCORE_FIELDS
        assert not leaked, f"{cls.__name__} can persist a score: {leaked}"


def test_events_are_immutable():
    v = _visit()
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.status = VisitStatus.COMPLETED
    m = OutcomeMeasurement("PLAN-1", 40.0, -10.0, DUE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        m.delivered_qty = 50.0


def test_status_change_returns_a_new_record_and_leaves_the_original():
    v = _visit()
    done = v.with_status(VisitStatus.COMPLETED)
    assert v.status == VisitStatus.SCHEDULED
    assert done.status == VisitStatus.COMPLETED and done.visit_id == v.visit_id


@pytest.mark.parametrize("bad", [
    {"visit_id": ""}, {"facility_id": "  "}, {"assignee": ""}, {"due_date": "next tuesday"},
    {"status": "LOST"},
])
def test_invalid_visit_is_refused_at_construction(bad):
    with pytest.raises(ValueError):
        _visit(**bad)


@pytest.mark.parametrize("bad", [
    dict(plan_id="", delivered_qty=1.0, variance=0.0, measured_at=DUE),
    dict(plan_id="P", delivered_qty=-1.0, variance=0.0, measured_at=DUE),
    dict(plan_id="P", delivered_qty=float("nan"), variance=0.0, measured_at=DUE),
    dict(plan_id="P", delivered_qty=1.0, variance=float("inf"), measured_at=DUE),
    dict(plan_id="P", delivered_qty=True, variance=0.0, measured_at=DUE),
    dict(plan_id="P", delivered_qty=1.0, variance=0.0, measured_at="yesterday"),
])
def test_invalid_outcome_is_refused_at_construction(bad):
    with pytest.raises(ValueError):
        OutcomeMeasurement(**bad)


def test_store_appends_visits_to_the_hash_chain_and_projects_latest_status():
    store = EventStore()
    v = _visit()
    store.put_verification_visit(v, actor="ddw:officer")
    store.put_verification_visit(v.with_status(VisitStatus.COMPLETED), actor="block-verifier-3")
    assert [e.event_type for e in store.events] == [EventType.VERIFICATION_VISIT] * 2
    assert store.verification_visits()["vv_1"].status == VisitStatus.COMPLETED
    ok, broken_at = store.verify_chain()
    assert ok and broken_at is None


def test_replaying_the_same_visit_status_is_a_no_op():
    store = EventStore()
    v = _visit()
    assert store.put_verification_visit(v, actor="ddw") is not None
    assert store.put_verification_visit(v, actor="ddw") is None
    assert len(store.events) == 1


def test_store_refuses_a_payload_carrying_a_score():
    with pytest.raises(ValueError, match="SCORE_NOT_PERSISTABLE"):
        EventStore._refuse_scores({"visit_id": "vv", "priority": 0.9})


def test_outcome_measurement_round_trips_through_the_store():
    store = EventStore()
    m = OutcomeMeasurement(plan_id="PLAN-7", delivered_qty=72.0, variance=-8.0, measured_at=DUE)
    ev = store.put_outcome_measurement(m, facility_id="FAC003", resource_key="ORS01", actor="dho")
    assert ev.event_type == EventType.OUTCOME_MEASURED
    assert store.outcome_measurements() == [m]


def test_decision_events_survive_a_durable_store_reload(tmp_path):
    db = str(tmp_path / "t.sqlite3")
    s1 = SQLiteStore(db)
    s1.put_verification_visit(_visit(), actor="ddw")
    s1.put_outcome_measurement(OutcomeMeasurement("PLAN-1", 10.0, 0.0, DUE), "FAC001", "AMX250", "dho")
    s1.close()
    s2 = SQLiteStore(db)
    assert s2.verification_visits()["vv_1"].status == VisitStatus.SCHEDULED
    assert s2.outcome_measurements()[0].plan_id == "PLAN-1"
    s2.close()


def test_stale_is_never_a_stored_state():
    assert "STALE" not in {s.value for s in VerificationState}
    assert "STALE" not in {s.value for s in VisitStatus}
