"""
Coverage for the two additions in the final build pass:

  1. SQLite persistence -- events, claims and idempotency survive a restart.
  2. The live UI path -- /ui/ serves the SPA and the endpoints it depends on
     return the shapes it expects.

We do NOT enable TATHYON_DB globally in the suite because the other tests
exercise the in-process store. Persistence is exercised here directly.
"""
from __future__ import annotations

import os

from fastapi.testclient import TestClient


def _fresh_db(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            os.remove(p)


def test_sqlite_persistence_survives_restart(tmp_path):
    from tathyon.persist import SQLiteStore
    from tathyon.schema import (
        Claim, EventType, Provenance, ResourceType, new_id, now,
    )

    db = str(tmp_path / "t.sqlite3")
    _fresh_db(db)

    s1 = SQLiteStore(db)
    c = Claim(claim_id=new_id("clm"), facility_id="FACX",
              resource_type=ResourceType.MEDICINE, resource_key="AMX250",
              state={"reported_stock": 100},
              source_system="test", source_actor="clerk",
              effective_at=now(), ingested_at=now(),
              provenance=Provenance.SYNTHETIC)
    s1.put_claim(c)
    s1.append(EventType.FLAGGED, "FACX", ResourceType.MEDICINE, "AMX250",
              {"why": "unit-test"}, actor="detector",
              client_event_id="ce_persist_test_A")
    n_before = len(s1.events)
    last_hash = s1.events[-1].hash
    s1.close()

    # -- reopen: the store must reconstruct the exact chain from disk ------
    s2 = SQLiteStore(db)
    ok, bad = s2.verify_chain()
    assert ok is True, f"chain broken at offset {bad} after restart"
    assert len(s2.events) == n_before, "event count changed across restart"
    assert s2.events[-1].hash == last_hash, "hash chain changed across restart"
    assert s2.claim(c.claim_id).claim_id == c.claim_id, "claim not durable"

    # -- idempotency must survive the restart too --------------------------
    dup = s2.append(EventType.FLAGGED, "FACX", ResourceType.MEDICINE, "AMX250",
                    {"why": "unit-test"}, actor="detector",
                    client_event_id="ce_persist_test_A")
    assert dup is None, "idempotency ledger not restored -- duplicate accepted"
    s2.close()


# --------------------------------------------------------------------------
# UI + scenario endpoint
# --------------------------------------------------------------------------

def test_ui_index_is_served(api_client):
    r = api_client.get("/")
    assert r.status_code == 200 and "TATHYON" in r.text
    assert api_client.get("/static/app.js").status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_custodian_refusal_carries_reason_code_the_ui_can_render(api_client):
    """Facility staff cannot count their own stock; the refusal has a stable code and message."""
    r = api_client.post("/api/verify/attest", headers=api_client.login("custodian_x"),
                        json={"facility_id": "PHC-X", "sku": "OXY-10", "present_qty": 1, "usable_qty": 1})
    assert r.status_code == 403
    assert r.json()["error"] == "ATTESTOR_IS_CUSTODIAN" and r.json()["message"]


def test_events_shape_matches_audit_view(api_client):
    d = api_client.get("/api/events?limit=5", headers=api_client.login("auditor")).json()
    assert d["chain_intact"] is True and d["events"]
    for k in ("offset", "event_id", "event_type", "occurred_at", "facility_id", "sku", "actor", "hash"):
        assert k in d["events"][0], f"/api/events row missing {k!r}"


def test_map_layers_come_only_from_the_registry(api_client):
    hdr = api_client.login("dmo")
    assert api_client.get("/api/map/layers", headers=hdr).json()["markers"] == []
    r = api_client.post("/api/intake/osm-registry", headers=hdr).json()
    assert r["provenance"] == "REAL_PUBLIC_OSM" and r["added"] > 100
    layers = api_client.get("/api/map/layers", headers=hdr).json()
    assert len(layers["markers"]) == r["added"]
    m = layers["markers"][0]
    for k in ("facility_id", "name", "lat", "lon", "tier", "coordinates_provenance", "registry_event_id", "resources"):
        assert k in m
    assert all(x["coordinates_provenance"] == "REAL_PUBLIC_OSM" for x in layers["markers"])
    assert layers["maps"]["routes_provider"] == "NOT_CONFIGURED"
