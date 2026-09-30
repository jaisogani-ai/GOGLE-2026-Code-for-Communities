"""
SQLite persistence for the event store.

An in-process EventStore is fine for tests and for a single API worker, but the
directive is explicit about a real database and about survival across restarts.
So this file backs the store with SQLite -- append-only WAL, one table for the
hash-chained events plus three tables for the immutable payloads. On startup
the process replays every event and reconstructs the projection deterministically,
which is precisely what the store's design already guarantees.

SQLite is deliberate. Postgres would be defensible for a real deployment; for a
prototype that must run from a clean clone with zero infra, a single file with
crash-safe WAL and journaled durability is the right call. The persistence
interface is small enough that a Postgres backend is a mechanical swap.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict
from typing import Optional

from .schema import (
    Attestation, Claim, Evidence, EventType, Provenance, ResourceType,
    StateEvent, sha256,
)
from .store import EventStore, GENESIS


SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    offset       INTEGER PRIMARY KEY,
    event_id     TEXT NOT NULL UNIQUE,
    event_type   TEXT NOT NULL,
    facility_id  TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at  TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    actor        TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    hash         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_fac_res
    ON events(facility_id, resource_key);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);

CREATE TABLE IF NOT EXISTS claims (
    claim_id     TEXT PRIMARY KEY,
    body_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id  TEXT PRIMARY KEY,
    body_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS attestations (
    attestation_id TEXT PRIMARY KEY,
    body_json    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS client_ids (
    client_event_id TEXT PRIMARY KEY
);
"""


class SQLiteStore(EventStore):
    """EventStore variant that mirrors every write to SQLite.

    Preserves the in-process invariants -- append-only, hash-chained,
    idempotent -- and adds durability. Reads still hit memory, so the projection
    remains a pure function of the log."""

    def __init__(self, path: str = "data/tathyon.sqlite3"):
        super().__init__(path=path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False,
                                     isolation_level=None)   # autocommit
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.executescript(SCHEMA)
        self._load()

    # -- loading ------------------------------------------------------------

    def _load(self) -> None:
        cur = self._conn.execute("SELECT body_json FROM claims")
        for (b,) in cur.fetchall():
            c = self._claim_from_json(json.loads(b))
            self._claims[c.claim_id] = c
        cur = self._conn.execute("SELECT body_json FROM evidence")
        for (b,) in cur.fetchall():
            e = self._evidence_from_json(json.loads(b))
            self._evidence[e.evidence_id] = e
        cur = self._conn.execute("SELECT body_json FROM attestations")
        for (b,) in cur.fetchall():
            a = self._att_from_json(json.loads(b))
            self._attestations[a.attestation_id] = a
        cur = self._conn.execute("SELECT client_event_id FROM client_ids")
        self._client_ids = {row[0] for row in cur.fetchall()}
        cur = self._conn.execute(
            "SELECT offset,event_id,event_type,facility_id,resource_type,"
            "resource_key,payload_json,occurred_at,recorded_at,actor,"
            "prev_hash,hash FROM events ORDER BY offset")
        for row in cur.fetchall():
            ev = StateEvent(
                event_id=row[1], offset=row[0],
                event_type=EventType(row[2]), facility_id=row[3],
                resource_type=ResourceType(row[4]), resource_key=row[5],
                payload=json.loads(row[6]),
                occurred_at=row[7], recorded_at=row[8], actor=row[9],
                prev_hash=row[10], hash=row[11])
            self._events.append(ev)

    @staticmethod
    def _claim_from_json(d: dict) -> Claim:
        return Claim(
            claim_id=d["claim_id"], facility_id=d["facility_id"],
            resource_type=ResourceType(d["resource_type"]),
            resource_key=d["resource_key"], state=d["state"],
            source_system=d["source_system"], source_actor=d["source_actor"],
            effective_at=d["effective_at"], ingested_at=d["ingested_at"],
            provenance=Provenance(d["provenance"]),
            supersedes=d.get("supersedes"))

    @staticmethod
    def _evidence_from_json(d: dict) -> Evidence:
        return Evidence(
            evidence_id=d["evidence_id"], claim_id=d["claim_id"],
            facility_id=d["facility_id"], kind=d["kind"],
            captured_at=d["captured_at"], device_id=d["device_id"],
            nonce=d["nonce"], nonce_issued_at=d["nonce_issued_at"],
            frame_count=d["frame_count"], artifact_hash=d["artifact_hash"],
            perceptual_hash=d["perceptual_hash"],
            extraction=d.get("extraction"),
            extraction_model=d.get("extraction_model"),
            extraction_confidence=d.get("extraction_confidence"),
            extraction_abstained=d.get("extraction_abstained", False),
            provenance=Provenance(d.get("provenance", "SYNTHETIC")))

    @staticmethod
    def _att_from_json(d: dict) -> Attestation:
        return Attestation(
            attestation_id=d["attestation_id"], claim_id=d["claim_id"],
            evidence_refs=d["evidence_refs"], observed=d["observed"],
            observed_at=d["observed_at"], attestor_id=d["attestor_id"],
            attestor_role=d["attestor_role"], delegation_id=d["delegation_id"],
            is_custodian=d["is_custodian"], seconds_spent=d["seconds_spent"],
            signature=d["signature"],
            extraction_agreement=d.get("extraction_agreement"))

    # -- writes -- override each so persistence is atomic with memory --------

    def append(self, event_type, facility_id, resource_type, resource_key,
               payload, actor, occurred_at=None, client_event_id=None):
        with self._lock:
            if client_event_id and client_event_id in self._client_ids:
                return None
            ev = super().append(event_type, facility_id, resource_type,
                                resource_key, payload, actor, occurred_at,
                                client_event_id)
            if ev is None:
                return None
            self._conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ev.offset, ev.event_id, ev.event_type.value, ev.facility_id,
                 ev.resource_type.value, ev.resource_key,
                 json.dumps(ev.payload, default=str),
                 ev.occurred_at, ev.recorded_at, ev.actor,
                 ev.prev_hash, ev.hash))
            if client_event_id:
                self._conn.execute(
                    "INSERT OR IGNORE INTO client_ids VALUES (?)",
                    (client_event_id,))
            return ev

    def put_claim(self, claim: Claim):
        d = asdict(claim)
        d["resource_type"] = claim.resource_type.value
        d["provenance"] = claim.provenance.value
        self._conn.execute(
            "INSERT OR REPLACE INTO claims VALUES (?,?)",
            (claim.claim_id, json.dumps(d, default=str)))
        return super().put_claim(claim)

    def put_evidence(self, ev: Evidence):
        d = asdict(ev)
        d["provenance"] = ev.provenance.value
        self._conn.execute(
            "INSERT OR REPLACE INTO evidence VALUES (?,?)",
            (ev.evidence_id, json.dumps(d, default=str)))
        return super().put_evidence(ev)

    def put_attestation(self, att: Attestation):
        d = asdict(att)
        self._conn.execute(
            "INSERT OR REPLACE INTO attestations VALUES (?,?)",
            (att.attestation_id, json.dumps(d, default=str)))
        return super().put_attestation(att)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
