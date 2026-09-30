"""
The ten cases the product lives or dies on.

These are written against the CORE (store + verify + optimize), not the HTTP
surface, so that a refactor of the API cannot quietly make them vacuous. Each
test corresponds to a claim the product makes to a buyer; if one of them goes
red, the claim is false that day.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tathyon.optimize import (
    Decision, Facility, Need, SourceStock, optimise,
)
from tathyon.schema import (
    Attestation, Claim, EventType, Provenance, ResourceType,
    VerificationState, new_id,
)
from tathyon.store import EventStore
from tathyon.verify import POLICY, VerificationEngine

T0 = pd.Timestamp("2026-06-01T09:00:00+00:00")
SKU = "AMX250"
FID = "FAC001"


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def _claim(store: EventStore, reported_stock: float = 340.0,
           at: pd.Timestamp = T0) -> Claim:
    c = Claim(
        claim_id=new_id("clm"), facility_id=FID,
        resource_type=ResourceType.MEDICINE, resource_key=SKU,
        state={"reported_stock": reported_stock, "batch": "B1234"},
        source_system="e-Aushadhi", source_actor="sys:eaushadhi",
        effective_at=at.isoformat(), ingested_at=at.isoformat(),
        provenance=Provenance.SYNTHETIC,
    )
    store.put_claim(c)
    return c


def _attest(store: EventStore, claim: Claim, *, usable: float = 40.0,
            present: float | None = None, seconds: float = 180.0,
            at: pd.Timestamp = T0, is_custodian: bool = False,
            agreement: dict | None = None) -> Attestation:
    a = Attestation(
        attestation_id=new_id("att"), claim_id=claim.claim_id,
        evidence_refs=[], observed={"usable_qty": usable,
                                    "present_qty": usable if present is None else present},
        observed_at=at.isoformat(), attestor_id="user:mo_sharma",
        attestor_role="medical_officer", delegation_id="dlg_1",
        is_custodian=is_custodian, seconds_spent=seconds,
        signature="sig:test", extraction_agreement=agreement,
    )
    store.put_attestation(a)
    return a


def _state(store: EventStore, as_of: pd.Timestamp = T0):
    return VerificationEngine(store).state_for(
        FID, ResourceType.MEDICINE, SKU, as_of=as_of.isoformat())


FACILITIES = {
    FID: Facility(FID, "CHC", 0.0, 0.0),
    "FAC002": Facility("FAC002", "PHC", 10.0, 0.0),
}


def _needs(shortfall: float = 500.0) -> list[Need]:
    return [Need("FAC002", SKU, shortfall, days_to_stockout=2.0, criticality=1.0)]


# ---------------------------------------------------------------------------
# 1. verified stock -> transfer allowed
# ---------------------------------------------------------------------------

def test_01_verified_stock_is_transferable():
    store = EventStore()
    c = _claim(store)
    _attest(store, c, usable=340.0, present=340.0)
    vs = _state(store)
    assert vs.state is VerificationState.VERIFIED, vs.reasons

    src = SourceStock(FID, SKU, reported_qty=340.0,
                      verified_state=vs.state, q_alpha=vs.posterior["q_alpha"],
                      safety_stock=0.0)
    assert src.transferable > 0
    d = optimise(FACILITIES, [src], _needs())
    assert d.allowed is True
    assert d.status == "ALLOWED"
    assert d.transfers, "a verified donor with headroom must produce a transfer"
    assert all(t.from_facility == FID for t in d.transfers)


# ---------------------------------------------------------------------------
# 2. unverified stock -> blocked, with reasons
# ---------------------------------------------------------------------------

def test_02_unverified_stock_is_blocked_with_reasons():
    store = EventStore()
    _claim(store)                      # claim only: no attestation ever
    vs = _state(store)
    assert vs.state is VerificationState.UNVERIFIED
    assert "NO_ATTESTATION" in vs.reasons

    src = SourceStock(FID, SKU, reported_qty=340.0,
                      verified_state=vs.state, q_alpha=None, reasons=vs.reasons)
    d = optimise(FACILITIES, [src], _needs())

    assert d.allowed is False
    assert d.status == "BLOCKED_VERIFICATION_REQUIRED"
    assert d.reasons, "a refusal with no machine-readable reason is unauditable"
    assert "NO_ATTESTATION" in d.reasons
    assert not d.transfers
    assert d.blocked_sources and d.blocked_sources[0]["facility_id"] == FID
    assert d.remedy
    # the refusal is a typed decision, never an exception
    assert isinstance(d, Decision)


# ---------------------------------------------------------------------------
# 3. expired/unusable stock is excluded from the solver's arithmetic
# ---------------------------------------------------------------------------

def test_03_solver_moves_against_verified_usable_never_reported():
    """Reported 340, physically present 340, verified USABLE only 40.
    The ceiling is max_donor_fraction x (40 - safety_stock). Nothing in the
    decision may be derived from 340."""
    store = EventStore()
    c = _claim(store, reported_stock=340.0)
    _attest(store, c, usable=40.0, present=340.0)
    vs = _state(store)

    assert vs.state is VerificationState.VERIFIED
    assert vs.reported_qty == 340.0
    assert vs.verified_usable_qty == 40.0
    assert vs.unusable_qty == 300.0
    assert "EXPIRED_STOCK_COUNTED_LIVE" in vs.reasons

    safety, frac = 5.0, 0.40
    # anchor on the verified usable figure; the posterior can only shrink it
    q_alpha = vs.posterior["q_alpha"]
    assert q_alpha <= 40.0, "posterior must not exceed the verified anchor"

    src = SourceStock(FID, SKU, reported_qty=340.0, verified_state=vs.state,
                      q_alpha=q_alpha, unusable_qty=vs.unusable_qty,
                      safety_stock=safety)
    d = optimise(FACILITIES, [src], _needs(shortfall=500.0),
                 max_donor_fraction=frac)

    ceiling = frac * max(q_alpha - safety, 0.0)
    moved = sum(t.qty for t in d.transfers)
    assert moved <= ceiling + 1e-6, (
        f"moved {moved} against a verified ceiling of {ceiling}")
    assert moved <= frac * (40.0 - safety) + 1e-6
    # and emphatically nothing anchored on the reported figure
    assert moved < frac * (340.0 - safety), (
        "the solver moved a quantity only reachable from the REPORTED stock")


# ---------------------------------------------------------------------------
# 4. conflicting evidence -> CONFLICTED
# ---------------------------------------------------------------------------

def test_04_extraction_disagreement_is_conflicted_not_verified():
    store = EventStore()
    c = _claim(store)
    _attest(store, c, usable=340.0, agreement={"delta_pct": 8.0})
    vs = _state(store)
    assert vs.state is VerificationState.CONFLICTED
    assert vs.state is not VerificationState.VERIFIED
    assert "EXTRACTION_DISAGREEMENT" in vs.reasons

    src = SourceStock(FID, SKU, 340.0, vs.state, q_alpha=340.0, reasons=vs.reasons)
    assert src.transferable == 0.0, "conflicted stock must not be transferable"


def test_04b_small_disagreement_stays_verified():
    """The boundary matters: a 5% threshold that rejects everything is not a
    threshold, it is an outage."""
    store = EventStore()
    c = _claim(store)
    _attest(store, c, usable=340.0, agreement={"delta_pct": 2.0})
    assert _state(store).state is VerificationState.VERIFIED


# ---------------------------------------------------------------------------
# 5. rubber-stamped attestation
# ---------------------------------------------------------------------------

def test_05_attestation_faster_than_minimum_is_rubber_stamp_and_conflicted():
    store = EventStore()
    c = _claim(store)
    fast = POLICY["min_attestation_seconds"] - 1.0
    _attest(store, c, usable=340.0, seconds=fast)
    vs = _state(store)
    assert "RUBBER_STAMP_SUSPECTED" in vs.reasons
    assert vs.state is VerificationState.CONFLICTED
    assert SourceStock(FID, SKU, 340.0, vs.state, 340.0).transferable == 0.0


# ---------------------------------------------------------------------------
# 6. separation of duties is a WRITE-time constraint
# ---------------------------------------------------------------------------

def test_06_custodian_may_not_attest():
    store = EventStore()
    c = _claim(store)
    with pytest.raises(ValueError, match="ATTESTOR_IS_CUSTODIAN"):
        _attest(store, c, is_custodian=True)
    # and nothing was written: a rejected write leaves no trace in the ledger
    assert not [e for e in store.events if e.event_type is EventType.ATTESTED]
    assert _state(store).state is VerificationState.UNVERIFIED


# ---------------------------------------------------------------------------
# 7. idempotency
# ---------------------------------------------------------------------------

def test_07_replayed_client_event_id_appends_exactly_once():
    store = EventStore()
    cid = "client-evt-42"
    first = store.append(
        EventType.CONSUMED, FID, ResourceType.MEDICINE, SKU,
        {"qty": 10}, actor="device:tab1", client_event_id=cid)
    second = store.append(
        EventType.CONSUMED, FID, ResourceType.MEDICINE, SKU,
        {"qty": 10}, actor="device:tab1", client_event_id=cid)

    assert first is not None
    assert second is None, "a replayed client_event_id must be a no-op"
    matching = [e for e in store.events if e.payload.get("qty") == 10]
    assert len(matching) == 1
    assert len(store.events) == 1
    ok, bad = store.verify_chain()
    assert ok and bad is None


# ---------------------------------------------------------------------------
# 8. hash chain detects tampering
# ---------------------------------------------------------------------------

def test_08_mutating_a_payload_breaks_the_chain_at_that_offset():
    store = EventStore()
    c = _claim(store)
    _attest(store, c, usable=40.0)
    store.append(EventType.CONSUMED, FID, ResourceType.MEDICINE, SKU,
                 {"qty": 3}, actor="device:tab1")

    ok, bad = store.verify_chain()
    assert ok is True and bad is None

    target = 1
    store.events  # snapshot copy; mutate the live event instead
    store._events[target].payload["observed"] = {"usable_qty": 999999.0}

    ok, bad = store.verify_chain()
    assert ok is False
    assert bad == target, f"chain named offset {bad}, tampering was at {target}"


def test_08b_chain_detects_a_deleted_event():
    store = EventStore()
    for i in range(4):
        store.append(EventType.CONSUMED, FID, ResourceType.MEDICINE, SKU,
                     {"qty": i}, actor="a")
    assert store.verify_chain() == (True, None)
    del store._events[1]
    ok, bad = store.verify_chain()
    assert ok is False


# ---------------------------------------------------------------------------
# 9. staleness is a read-time projection
# ---------------------------------------------------------------------------

def test_09_attestation_beyond_the_policy_window_is_unverified_and_stale():
    store = EventStore()
    c = _claim(store)
    _attest(store, c, usable=40.0, at=T0)

    max_age = POLICY["medicine"]["max_attestation_age_days"]
    fresh = _state(store, as_of=T0 + pd.Timedelta(days=max_age - 1))
    assert fresh.state is VerificationState.VERIFIED

    stale = _state(store, as_of=T0 + pd.Timedelta(days=max_age + 1))
    assert stale.state is VerificationState.UNVERIFIED
    assert "ATTESTATION_STALE" in stale.reasons
    assert stale.staleness_s > max_age * 86400

    # and the same log, re-read at the earlier instant, is still VERIFIED:
    # staleness must be a projection, not a stored mutation
    assert _state(store, as_of=T0 + pd.Timedelta(days=1)).state \
        is VerificationState.VERIFIED

    src = SourceStock(FID, SKU, 340.0, stale.state, q_alpha=40.0,
                      reasons=stale.reasons)
    assert src.transferable == 0.0
    d = optimise(FACILITIES, [src], _needs())
    assert d.allowed is False
    assert "ATTESTATION_STALE" in d.reasons


# ---------------------------------------------------------------------------
# 10. break-glass is recorded, with a name and a reason
# ---------------------------------------------------------------------------

def test_10_break_glass_override_is_an_event_with_an_actor_and_a_reason():
    store = EventStore()
    _claim(store)
    vs = _state(store)
    assert vs.state is VerificationState.UNVERIFIED

    d = optimise(FACILITIES,
                 [SourceStock(FID, SKU, 340.0, vs.state, None, reasons=vs.reasons)],
                 _needs())
    assert d.allowed is False
    assert d.override["eligible"] is True
    assert d.override["required_role"]

    before = len(store.events)
    ev = store.append(
        EventType.OVERRIDDEN, FID, ResourceType.MEDICINE, SKU,
        payload={"override_id": new_id("ovr"),
                 "reason_code": "CLINICAL_EMERGENCY",
                 "reason_text": "paediatric ward out of stock, 4h to next supply",
                 "blocked_reasons": vs.reasons,
                 "obligation": "PHYSICAL_VERIFICATION_REQUIRED"},
        actor="user:mo_sharma")

    assert ev is not None
    assert len(store.events) == before + 1
    assert ev.event_type is EventType.OVERRIDDEN
    assert ev.actor and ev.actor != "system", "an override must name a human"
    assert ev.payload["reason_code"]
    assert ev.payload["reason_text"]
    assert ev.payload["obligation"] == "PHYSICAL_VERIFICATION_REQUIRED"
    assert ev.hash and ev.prev_hash
    assert store.verify_chain() == (True, None)

    tl = store.timeline(FID, SKU)
    assert tl[-1]["type"] == EventType.OVERRIDDEN.value
    assert tl[-1]["actor"] == "user:mo_sharma"
