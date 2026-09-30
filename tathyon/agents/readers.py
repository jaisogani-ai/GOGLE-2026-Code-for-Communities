"""Read-only projections shared by agent tools. Pure functions of the workspace."""
from __future__ import annotations

from typing import Optional

from ..observations import latest_for_facility
from ..workspace import Workspace, WorkspaceError

EVENT_EXPLANATIONS = {
    "workspace_loaded": "A workspace was loaded; this sets the data environment for every later record.",
    "facility_registered": "A facility entered the registry with its coordinates and their provenance.",
    "claim_ingested": "A stock report (claim) was ingested. Reports are UNVERIFIED until a human count.",
    "row_quarantined": "An uploaded row failed validation and was quarantined with typed reasons.",
    "attested": "A human physically counted stock (attester is never the custodian).",
    "reconciled": "The count was compared with the report; the finding is recorded.",
    "verification_requested": "A verification task was opened because a plan line depends on it.",
    "plan_proposed": "The CP-SAT planner proposed options for a human decision.",
    "plan_approved": "A district medical officer approved a plan option (line-level decisions recorded).",
    "rejected": "A human rejected a plan.",
    "overridden": "A medical officer used recorded break-glass to release an unverified line.",
    "obligation_created": "A follow-up obligation (e.g. post-hoc count) was created with a deadline.",
    "transfer_approved": "A held line was released after a count confirmed the donor.",
    "shipment_dispatched": "A shipment left the donor. Status updates only; there is no GPS tracking.",
    "shipment_delayed": "A shipment delay was reported and the ETA moved.",
    "received": "The recipient confirmed receipt, including damaged units.",
    "outcome_recorded": "Recipient runway before and after the receipt was recorded.",
    "replan_required": "The deterministic monitor found an approved plan can no longer be honoured.",
    "escalated": "Unmet need was escalated to the state warehouse as a recorded obligation.",
    "clock_advanced": "Simulated time advanced (synthetic scenario only).",
    "agent_run": "An agent run: tools used and cited evidence are recorded.",
    "agent_refused": "An agent refused a request; the refusal code is recorded.",
    "flagged": "A security or data-quality flag was raised.",
    "decision_made": "A recorded decision or scenario checkpoint.",
}


def event_row(ev) -> dict:
    return {"event_id": ev.event_id, "offset": ev.offset, "event_type": ev.event_type.value,
            "occurred_at": ev.occurred_at, "facility_id": ev.facility_id, "sku": ev.resource_key,
            "actor": ev.actor, "hash": ev.hash[:16], "payload": _trim(ev.payload)}


def _trim(payload: dict) -> dict:
    drop = {"options", "selected_lines", "constraints", "raw", "snapshot"}
    return {k: v for k, v in payload.items() if k not in drop}


def search_events(ws: Workspace, event_type: Optional[str] = None, facility_id: Optional[str] = None,
                  sku: Optional[str] = None, plan_id: Optional[str] = None, limit: int = 25) -> list[dict]:
    out = []
    for ev in reversed(ws.store.events):
        if event_type and ev.event_type.value != event_type:
            continue
        if facility_id and ev.facility_id != facility_id and ev.payload.get("from_facility") != facility_id \
                and ev.payload.get("to_facility") != facility_id:
            continue
        if sku and ev.resource_key not in (sku, "*"):
            continue
        if plan_id and ev.payload.get("plan_id") != plan_id:
            continue
        out.append(event_row(ev))
        if len(out) >= limit:
            break
    return out


def explain_event(ws: Workspace, event_id: str) -> dict:
    ev = next((e for e in ws.store.events if e.event_id == event_id), None)
    if ev is None:
        raise WorkspaceError("EVENT_NOT_FOUND", event_id, 404)
    intact, bad = ws.store.verify_chain()
    return {**event_row(ev), "prev_hash": ev.prev_hash[:16],
            "explanation": EVENT_EXPLANATIONS.get(ev.event_type.value, "Recorded event."),
            "chain_intact": intact, "first_bad_offset": bad}


def plan_view(ws: Workspace, plan_id: Optional[str] = None, sku: Optional[str] = None) -> dict:
    plans = sorted((p for p in ws.plans.values() if not sku or p["sku"] == sku), key=lambda p: p["version"])
    if not plans:
        raise WorkspaceError("NO_PLAN", "No plan has been proposed yet.", 404)
    plan = ws.plans.get(plan_id) if plan_id else plans[-1]
    if plan is None:
        raise WorkspaceError("PLAN_NOT_FOUND", str(plan_id), 404)
    return {
        "plan_id": plan["plan_id"], "sku": plan["sku"], "version": plan["version"], "status": plan["status"],
        "parent_plan_id": plan.get("parent_plan_id"), "trigger": plan["trigger"],
        "recommended_option": plan["recommended_option"], "recommendation_rule": plan["recommendation_rule"],
        "selected_option": plan.get("selected_option"),
        "options": [{k: o[k] for k in ("option", "approvable", "fulfilled_qty", "shortfall_qty",
                                       "qty_weighted_travel_hours", "why")} |
                    {"lines": [{k: l[k] for k in ("line_id", "from_facility", "to_facility", "qty", "kind", "travel_hours")}
                               for l in o["lines"]]} for o in plan["options"]],
        "lines": [{k: l.get(k) for k in ("line_id", "from_facility", "to_facility", "qty", "kind", "status",
                                         "travel_hours", "shipment_id")} for l in plan["lines"]],
        "rejected_donors": plan["rejected_donors"], "needs": plan["needs"],
        "proposed_event_id": plan["proposed_event_id"], "history_event_ids": plan["history"],
        "decision": {k: plan.get("decision", {}).get(k) for k in ("officer_id", "option", "reason")}
        if plan.get("decision") else None,
        "environment": plan["environment"],
        "data_provenance": sorted({str(ws.reports[(n["facility_id"], plan["sku"])].get("provenance"))
                                   for n in plan["needs"] if (n["facility_id"], plan["sku"]) in ws.reports}),
    }


def facility_detail(ws: Workspace, facility_id: str) -> dict:
    f = ws.facilities.get(facility_id)
    if f is None:
        raise WorkspaceError("UNKNOWN_FACILITY", facility_id, 404)
    states = [ws.resource_state(facility_id, s) for s in sorted(ws.skus)
              if (facility_id, s) in ws.reports or (facility_id, s) in ws.attestations]
    return {"facility": {k: f.get(k) for k in ("facility_id", "name", "tier", "block", "lat", "lon",
                                               "has_cold_chain", "provenance", "coordinates_provenance",
                                               "event_id")},
            "resources": states,
            "open_tasks": [t for t in ws.tasks.values() if t["facility_id"] == facility_id and t["status"] == "OPEN"],
            "shipments": [s for s in ws.shipments.values()
                          if facility_id in (s["from_facility"], s["to_facility"])],
            "beds_and_staff": [o for o in latest_for_facility(ws, facility_id) if o["resource_module"] != "MEDICINES"]}
