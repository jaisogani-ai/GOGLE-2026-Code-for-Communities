"""Bed occupancy, staff attendance and stock observations (non-stock-export resources).

No bed-management or attendance system is connected. Observations arrive from
uploaded reports (USER_SUPPLIED_UNVERIFIED) or the opt-in SAMPLE dataset, are
ledgered as OBSERVATION events, and get a history-gated robust-z advisory
(anomaly_detection.py). An advisory is a review cue, never a finding of fault,
and never changes verified state.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Iterable, Optional

from .anomaly_detection import score_observation
from .schema import EventType, ResourceType
from .workspace import Workspace, WorkspaceError

MODULE_TYPES = {"MEDICINES": ResourceType.MEDICINE, "BEDS": ResourceType.BED, "PERSONNEL": ResourceType.PERSONNEL}
ADVISORY_LABEL = "Statistical anomaly signal — human decides"


def _parse_at(value: str) -> datetime:
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkspaceError("INVALID_OBSERVED_AT", value, 422) from exc
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def record_observations(ws: Workspace, source_report_id: str, observations: Iterable[dict], *,
                        provenance: str, actor: str) -> list[dict]:
    """Validate all rows first (all-or-nothing), then ledger each with its advisory."""
    parsed, seen = [], set()
    for o in observations:
        if o["resource_module"] not in MODULE_TYPES:
            raise WorkspaceError("INVALID_MODULE", str(o["resource_module"]), 422)
        value = float(o["value"])
        if not math.isfinite(value) or value < 0:
            raise WorkspaceError("INVALID_VALUE", f"{o['facility_id']}/{o['metric']} must be >= 0", 422)
        key = (o["facility_id"], o["resource_module"], o["resource_key"], o["metric"], o["unit"])
        if key in seen:
            raise WorkspaceError("DUPLICATE_OBSERVATION", str(key), 422)
        seen.add(key)
        parsed.append((o, value, _parse_at(o["observed_at"])))
    results = []
    with ws.lock:
        for o, value, at in parsed:
            history = history_for(ws, o, before=at.isoformat())
            score = score_observation(value, [h["value"] for h in history])
            rtype = MODULE_TYPES[o["resource_module"]]
            ev = ws.store.append(EventType.OBSERVATION, o["facility_id"], rtype, o["resource_key"], {
                "kind": "resource_report_observation", "source_class": provenance,
                "source_report_id": source_report_id, "resource_module": o["resource_module"],
                "metric": o["metric"], "value": value, "unit": o["unit"], "observed_at": at.isoformat(),
                "anomaly_status": score["status"]}, actor, occurred_at=at.isoformat(),
                client_event_id=f"rr:{source_report_id}:{o['facility_id']}:{o['resource_key']}:{o['metric']}")
            row = {"facility_id": o["facility_id"], "resource_module": o["resource_module"], "metric": o["metric"],
                   "value": value, "event_id": ev.event_id if ev else None, "duplicate": ev is None, **score}
            if ev and score["flagged"]:
                adv = ws.store.append(EventType.DECISION_MADE, o["facility_id"], rtype, o["resource_key"], {
                    "category": "STATISTICAL_ANOMALY_ADVISORY", "advisory_only": True, "source_class": provenance,
                    "observation_event_id": ev.event_id, "resource_module": o["resource_module"],
                    "metric": o["metric"], "value": value, "robust_z": score["robust_z"],
                    "reason": score["reason"]}, "tathyon_statistical_advisor", occurred_at=at.isoformat())
                row["advisory_event_id"] = adv.event_id if adv else None
            results.append(row)
    return results


def history_for(ws: Workspace, o: dict, before: Optional[str] = None) -> list[dict]:
    return [{"value": float(e.payload["value"]), "observed_at": e.payload["observed_at"], "event_id": e.event_id}
            for e in ws.store.events
            if e.event_type == EventType.OBSERVATION and e.facility_id == o["facility_id"]
            and e.resource_key == o["resource_key"] and e.payload.get("metric") == o["metric"]
            and e.payload.get("unit") == o["unit"] and e.payload.get("resource_module") == o["resource_module"]
            and (before is None or e.payload.get("observed_at", "") < before)]


def latest_for_facility(ws: Workspace, facility_id: str) -> list[dict]:
    """Latest value per (module, resource, metric) with provenance and any advisory, for the facility drawer."""
    latest: dict[tuple, dict] = {}
    advisories = {e.payload.get("observation_event_id"): e.event_id for e in ws.store.events
                  if e.event_type == EventType.DECISION_MADE
                  and e.payload.get("category") == "STATISTICAL_ANOMALY_ADVISORY" and e.facility_id == facility_id}
    for e in ws.store.events:
        if e.event_type != EventType.OBSERVATION or e.facility_id != facility_id:
            continue
        p = e.payload
        key = (p.get("resource_module"), e.resource_key, p.get("metric"))
        if key not in latest or p.get("observed_at", "") >= latest[key]["observed_at"]:
            latest[key] = {"resource_module": key[0], "resource_key": key[1], "metric": key[2], "value": p["value"],
                           "unit": p.get("unit"), "observed_at": p.get("observed_at"),
                           "provenance": p.get("source_class"), "anomaly_status": p.get("anomaly_status"),
                           "event_id": e.event_id, "advisory_event_id": advisories.get(e.event_id)}
    return sorted(latest.values(), key=lambda r: (r["resource_module"], r["resource_key"], r["metric"]))
