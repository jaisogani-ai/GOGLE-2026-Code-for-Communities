"""TATHYON operational workspace: the event-sourced core loop.

    INGEST -> OBSERVE -> VALIDATE -> ESTIMATE TRUST -> ASSESS NEED
    -> GENERATE OPTIONS -> OPTIMIZE -> HUMAN APPROVAL -> ACTION -> RECEIPT
    -> RECONCILIATION -> OUTCOME -> REPLAN

Every mutation follows one path: validate -> append a hash-chained event ->
apply the event to in-memory projections. `Workspace.replay(store)` rebuilds
every projection from the ledger alone, so a restart with the SQLite store
loses nothing and there is no state that is not on the audit chain.

Provenance is carried per record. A workspace loaded from the Phantom Trap
scenario is SYNTHETIC end to end; rows a user uploads are
USER_SUPPLIED_UNVERIFIED; counts entered by a human are HUMAN_ATTESTED.
"""
from __future__ import annotations

import math
import threading
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .intake_pipeline import StockRow
from .optimize import Facility, Need, SourceStock, knapsack, travel_hours
from .planning import PlanningPolicy, generate_options, project_xy
from .schema import (
    Attestation, Claim, EventType, Provenance, ResourceType, StateEvent,
    VerificationState, new_id,
)
from .store import EventStore

ENV_EMPTY = "EMPTY"
ENV_REAL = "REAL_DATA"            # public registry + user-supplied exports + human counts
ENV_SYNTHETIC = "SYNTHETIC_DEMO"  # test fixtures only; never loaded by the application

APPROVER_ROLES = ("district_medical_officer",)
VERIFIER_ROLES = ("field_verifier", "block_supervisor")
RECEIVER_ROLES = ("facility_incharge", "pharmacist")
DISPATCH_ROLES = ("district_medical_officer", "logistics_officer")

ATTESTATION_FRESH_DAYS = 7.0
ATTESTATION_STALE_DAYS = 30.0
REPORT_STALE_DAYS = 7.0
SURPLUS_OUTLIER_RUNWAY_DAYS = 90.0
VARIANCE_TOLERANCE_FRACTION = 0.05
VARIANCE_TOLERANCE_UNITS = 5.0
VISIT_COUNT_HOURS = 1.0
DEFAULT_VERIFIER_HOURS = 6
BREAK_GLASS_OBLIGATION_HOURS = 24.0

BREAK_GLASS_REASONS = {
    "CLINICAL_EMERGENCY": "Immediate clinical need; no verified alternative in window.",
    "DISASTER_RESPONSE": "Declared disaster or mass-casualty response.",
    "EPIDEMIC_SURGE": "Notified outbreak surge above planned consumption.",
    "COLD_CHAIN_FAILURE": "Cold-chain failure requires immediate relocation of stock.",
}
_OPEN_LINE = ("APPROVED", "APPROVED_CONTINGENT", "RELEASED_BREAK_GLASS", "DISPATCHED", "DELAYED")
_COMMITTED_OUT = ("APPROVED", "RELEASED_BREAK_GLASS")


class WorkspaceError(Exception):
    """Typed refusal. `code` is stable and rendered by the API/UI."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


def _dt(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat()


class Workspace:
    def __init__(self, store: Optional[EventStore] = None):
        self.store = store or EventStore()
        self.lock = threading.RLock()
        self._reset_projections()

    # ------------------------------------------------------------------ state
    def _reset_projections(self) -> None:
        self.environment = ENV_EMPTY
        self.scenario: Optional[str] = None
        self.policy = PlanningPolicy()
        self.clock_override: Optional[datetime] = None
        self.hq: tuple[float, float] = (19.07, 82.03)
        self.facilities: dict[str, dict] = {}
        self.skus: dict[str, dict] = {}
        self.reports: dict[tuple[str, str], dict] = {}
        self.attestations: dict[tuple[str, str], dict] = {}
        self.tasks: dict[str, dict] = {}
        self.plans: dict[str, dict] = {}
        self.shipments: dict[str, dict] = {}
        self.outcomes: list[dict] = []
        self.quarantine: list[dict] = []
        self.escalations: list[dict] = []
        self.obligations: list[dict] = []
        self.replans: list[dict] = []
        self.agent_runs: list[dict] = []
        self.baseline: dict = {}
        self.is_sandbox = False

    @classmethod
    def replay(cls, store: EventStore) -> "Workspace":
        ws = cls(store)
        for ev in store.events:
            ws._apply(ev)
        return ws

    def sandbox(self) -> "Workspace":
        """A what-if copy: same projections, writes go to a private in-memory ledger only."""
        shadow = EventStore()
        shadow._events = list(self.store.events)
        shadow._claims = dict(self.store._claims)
        shadow._attestations = dict(self.store._attestations)
        shadow._client_ids = set(self.store._client_ids)
        sb = Workspace(shadow)
        for ev in shadow.events:
            sb._apply(ev)
        sb.is_sandbox = True
        return sb

    @property
    def clock(self) -> datetime:
        return self.clock_override or datetime.now(timezone.utc)

    def _emit(self, event_type: EventType, facility_id: str, sku: str, payload: dict, actor: str,
              client_event_id: Optional[str] = None) -> StateEvent:
        ev = self.store.append(event_type, facility_id, ResourceType.MEDICINE, sku, payload, actor,
                               occurred_at=_iso(self.clock), client_event_id=client_event_id)
        if ev is None:
            raise WorkspaceError("DUPLICATE_EVENT", "This action was already recorded (idempotency key replayed).")
        self._apply(ev)
        return ev

    # ------------------------------------------------------------ projector
    def _apply(self, ev: StateEvent) -> None:  # noqa: C901 - one dispatch table, kept flat
        p, et = ev.payload, ev.event_type
        key = (ev.facility_id, ev.resource_key)
        if et == EventType.WORKSPACE_LOADED:
            self._reset_projections()
            self.environment, self.scenario = p["environment"], p.get("scenario")
            self.clock_override = _dt(p["clock"]) if p.get("clock") else None
            self.policy = PlanningPolicy(**p.get("policy", {}))
            self.hq = tuple(p.get("hq", self.hq))  # type: ignore[assignment]
            self.skus = dict(p.get("skus", {}))
        elif et == EventType.CLOCK_ADVANCED:
            self.clock_override = _dt(p["to"])
        elif et == EventType.SKU_REGISTERED:
            self.skus[ev.resource_key] = dict(p["sku"])
        elif et == EventType.FACILITY_REGISTERED:
            self.facilities[ev.facility_id] = {**p["facility"], "event_id": ev.event_id}
        elif et == EventType.CLAIM_INGESTED and "reported_qty" in p.get("state", {}):
            s = p["state"]
            self.reports[key] = {
                "claim_id": p["claim_id"], "reported_qty": s["reported_qty"],
                "daily_consumption": s["daily_consumption"], "report_date": s["report_date"],
                "batch_no": s.get("batch_no"), "expiry_date": s.get("expiry_date"),
                "provenance": p.get("provenance"), "source": p.get("source"), "event_id": ev.event_id,
            }
        elif et == EventType.ROW_QUARANTINED:
            self.quarantine.append({"event_id": ev.event_id, **p})
        elif et == EventType.ATTESTED:
            obs = p["observed"]
            self.attestations[key] = {
                "attestation_id": p["attestation_id"], "event_id": ev.event_id,
                "present_qty": obs.get("present_quantity"), "usable_qty": obs.get("usable_quantity"),
                "expired_qty": obs.get("expired_quantity"), "observed_at": ev.occurred_at,
                "attester_id": p["attestor"], "attester_role": p["role"],
                "provenance": obs.get("provenance", "HUMAN_ATTESTED"), "finding": None,
            }
        elif et == EventType.RECONCILED:
            if key in self.attestations:
                self.attestations[key] = {**self.attestations[key], "finding": p["finding"],
                                          "variance_units": p["variance_units"],
                                          "reported_qty_at_count": p["reported_qty"]}
            task = self.tasks.get(p.get("task_id") or "")
            if task:
                self.tasks[task["task_id"]] = {**task, "status": "COMPLETED", "finding": p["finding"],
                                               "completed_event_id": ev.event_id}
        elif et == EventType.VERIFICATION_REQUESTED:
            self.tasks[p["task_id"]] = {**p, "status": "OPEN", "requested_event_id": ev.event_id}
        elif et == EventType.PLAN_PROPOSED:
            self.plans[p["plan_id"]] = {**p, "status": "PROPOSED", "proposed_event_id": ev.event_id,
                                        "history": [ev.event_id]}
        elif et == EventType.PLAN_APPROVED:
            self._apply_approval(ev)
        elif et == EventType.REJECTED and p.get("kind") == "PLAN_REJECTED":
            self._plan_update(p["plan_id"], ev, status="REJECTED", decision=p)
        elif et == EventType.OVERRIDDEN:
            self._line_update(p["plan_id"], p["line_id"], ev, status="RELEASED_BREAK_GLASS")
        elif et == EventType.TRANSFER_APPROVED and "line_id" in p:
            self._line_update(p["plan_id"], p["line_id"], ev, status="APPROVED")
        elif et == EventType.DECISION_MADE and p.get("category") == "BASELINE_SNAPSHOT":
            self.baseline = dict(p["snapshot"])
        elif et == EventType.OBLIGATION_CREATED:
            self.obligations.append({"event_id": ev.event_id, **p})
        elif et == EventType.SHIPMENT_DISPATCHED:
            self.shipments[p["shipment_id"]] = {**p, "status": "IN_TRANSIT", "dispatch_event_id": ev.event_id,
                                                "events": [ev.event_id]}
            self._line_update(p["plan_id"], p["line_id"], ev, status="DISPATCHED", shipment_id=p["shipment_id"])
        elif et == EventType.SHIPMENT_DELAYED:
            sh = self.shipments[p["shipment_id"]]
            self.shipments[p["shipment_id"]] = {**sh, "status": "DELAYED", "eta": p["new_eta"],
                                                "delay_reason": p["reason"], "events": sh["events"] + [ev.event_id]}
            self._line_update(sh["plan_id"], sh["line_id"], ev, status="DELAYED")
        elif et == EventType.RECEIVED:
            sh = self.shipments[p["shipment_id"]]
            self.shipments[p["shipment_id"]] = {**sh, "status": p["status"], "received_qty": p["received_qty"],
                                                "damaged_qty": p["damaged_qty"], "received_at": ev.occurred_at,
                                                "events": sh["events"] + [ev.event_id]}
            self._line_update(sh["plan_id"], sh["line_id"], ev, status="RECEIVED")
        elif et == EventType.OUTCOME_RECORDED:
            self.outcomes.append({"event_id": ev.event_id, **p})
        elif et == EventType.REPLAN_REQUIRED:
            self.replans.append({"event_id": ev.event_id, **p})
            for line_id in p.get("invalidated_lines", []):
                self._line_update(p["plan_id"], line_id, ev, status="INVALIDATED")
            self._plan_update(p["plan_id"], ev, status="REPLAN_REQUIRED")
        elif et == EventType.ESCALATED:
            self.escalations.append({"event_id": ev.event_id, **p})
        elif et in (EventType.AGENT_RUN, EventType.AGENT_REFUSED):
            self.agent_runs.append({"event_id": ev.event_id, "event_type": et.value, "at": ev.occurred_at, **p})

    def _plan_update(self, plan_id: str, ev: StateEvent, **fields: Any) -> None:
        plan = self.plans.get(plan_id)
        if plan:
            self.plans[plan_id] = {**plan, **fields, "history": plan["history"] + [ev.event_id]}

    def _line_update(self, plan_id: str, line_id: str, ev: StateEvent, **fields: Any) -> None:
        plan = self.plans.get(plan_id)
        if not plan:
            return
        lines = [{**l, **fields} if l["line_id"] == line_id else l for l in plan["lines"]]
        self.plans[plan_id] = {**plan, "lines": lines, "history": plan["history"] + [ev.event_id]}

    def _apply_approval(self, ev: StateEvent) -> None:
        p = ev.payload
        plan = self.plans[p["plan_id"]]
        lines = [{**l, "status": p["line_decisions"].get(l["line_id"], l["status"])}
                 for l in p.get("selected_lines", plan["lines"])]
        self.plans[p["plan_id"]] = {**plan, "status": "APPROVED", "selected_option": p["option"], "lines": lines,
                                    "decision": {k: v for k, v in p.items() if k != "selected_lines"},
                                    "history": plan["history"] + [ev.event_id]}
        parent = plan.get("parent_plan_id")
        if parent and parent in self.plans:
            self._plan_update(parent, ev, status="SUPERSEDED", superseded_by=p["plan_id"])

    # ------------------------------------------------------------ load / ingest
    def load(self, environment: str, facilities: list[dict], skus: dict[str, dict], *,
             scenario: Optional[str] = None, clock: Optional[datetime] = None,
             hq: tuple[float, float] = (19.07, 82.03), policy: Optional[dict] = None,
             actor: str = "system") -> StateEvent:
        with self.lock:
            if clock is not None:
                self.clock_override = clock
            ev = self._emit(EventType.WORKSPACE_LOADED, "DISTRICT", "*", {
                "environment": environment, "scenario": scenario,
                "clock": _iso(clock) if clock else None, "hq": list(hq),
                "policy": policy or {}, "skus": skus,
                "notice": "SYNTHETIC scenario: no government system is connected." if environment == ENV_SYNTHETIC
                else "Empty workspace: upload a facility registry and stock export to begin.",
            }, actor)
            for f in facilities:
                self.register_facility(f, actor)
            return ev

    def register_facility(self, f: dict, actor: str) -> StateEvent:
        required = ("facility_id", "name", "tier", "lat", "lon", "provenance")
        missing = [k for k in required if k not in f]
        if missing:
            raise WorkspaceError("INVALID_FACILITY", f"missing fields {missing}", 422)
        x, y = project_xy(float(f["lat"]), float(f["lon"]), *self.hq)
        record = {"has_cold_chain": True, "custodian_id": None, "block": None,
                  "coordinates_provenance": f["provenance"], **f, "x_km": x, "y_km": y}
        return self._emit(EventType.FACILITY_REGISTERED, f["facility_id"], "*", {"facility": record}, actor)

    def register_sku(self, row: StockRow, source: str, provenance: str, actor: str) -> StateEvent:
        """A SKU first seen in an export. Unknown attributes stay unknown (None), never assumed."""
        ved_weight = {"V": 3, "E": 2, "N": 1}.get(row.ved or "", 2)
        record = {"name": row.sku_name or row.sku, "unit": None, "essentiality": ved_weight,
                  "ved": row.ved, "cold_chain": bool(row.cold_chain) if row.cold_chain is not None else False,
                  "cold_chain_known": row.cold_chain is not None, "provenance": provenance, "source": source}
        return self._emit(EventType.SKU_REGISTERED, "DISTRICT", row.sku, {"sku": record}, actor)

    def ingest_rows(self, rows: list[StockRow], quarantined: list[dict], *, source: str,
                    provenance: str, actor: str) -> dict:
        """Accepted rows become claims (UNVERIFIED); quarantined rows are ledgered with reasons."""
        with self.lock:
            prov = {"SYNTHETIC": Provenance.SYNTHETIC, "SAMPLE": Provenance.SAMPLE}.get(
                provenance, Provenance.REAL_USER_PROVIDED)
            accepted = []
            for r in rows:
                if r.sku not in self.skus:
                    self.register_sku(r, source, provenance, actor)
                claim = Claim(
                    claim_id=new_id("clm"), facility_id=r.facility_id, resource_type=ResourceType.MEDICINE,
                    resource_key=r.sku,
                    state={"reported_qty": r.reported_qty, "daily_consumption": r.daily_consumption,
                           "report_date": r.report_date, "batch_no": r.batch_no, "expiry_date": r.expiry_date},
                    source_system=source, source_actor=actor, effective_at=_iso(self.clock),
                    ingested_at=_iso(self.clock), provenance=prov)
                ev = self.store.put_claim(claim)
                self._apply(ev)
                accepted.append({"facility_id": r.facility_id, "sku": r.sku, "event_id": ev.event_id})
            for q in quarantined:
                raw = q.get("raw", {})
                self._emit(EventType.ROW_QUARANTINED, str(raw.get("facility_id") or "UNKNOWN")[:60], "*",
                           {"source": source, "row_number": q["row_number"], "reasons": q["reasons"], "raw": raw},
                           actor)
            return {"accepted": accepted, "quarantined": len(quarantined)}

    def advance_clock(self, hours: float, actor: str, reason: str) -> StateEvent:
        if self.environment != ENV_SYNTHETIC:
            raise WorkspaceError("CLOCK_IS_REAL_TIME", "Simulated time only exists in the synthetic scenario.", 409)
        if not 0 < hours <= 24 * 30:
            raise WorkspaceError("INVALID_DURATION", "hours must be in (0, 720].", 422)
        to = self.clock + timedelta(hours=hours)
        return self._emit(EventType.CLOCK_ADVANCED, "DISTRICT", "*",
                          {"from": _iso(self.clock), "to": _iso(to), "hours": hours, "reason": reason,
                           "provenance": "SIMULATED"}, actor)

    # ------------------------------------------------------------ resource state
    def _outflow_since(self, key: tuple[str, str], since: datetime) -> float:
        return sum(s["qty"] for s in self.shipments.values()
                   if (s["from_facility"], s["sku"]) == key and _dt(s["dispatched_at"]) >= since)

    def _inflow_since(self, key: tuple[str, str], since: datetime) -> float:
        return sum(max(s.get("received_qty", 0.0) - s.get("damaged_qty", 0.0), 0.0) for s in self.shipments.values()
                   if (s["to_facility"], s["sku"]) == key and s.get("received_at") and _dt(s["received_at"]) >= since)

    def _inbound(self, key: tuple[str, str]) -> float:
        return sum(s["qty"] for s in self.shipments.values()
                   if (s["to_facility"], s["sku"]) == key and s["status"] in ("IN_TRANSIT", "DELAYED"))

    def _committed(self, facility_id: str, sku: str, direction: str) -> float:
        field_ = "from_facility" if direction == "out" else "to_facility"
        statuses = _COMMITTED_OUT if direction == "out" else ("APPROVED", "APPROVED_CONTINGENT", "RELEASED_BREAK_GLASS")
        return sum(l["qty"] for p in self.plans.values() if p["status"] == "APPROVED"
                   for l in p["lines"] if l[field_] == facility_id and l["sku"] == sku and l["status"] in statuses)

    def resource_state(self, facility_id: str, sku: str) -> dict:
        key = (facility_id, sku)
        rep, att = self.reports.get(key), self.attestations.get(key)
        if rep is None and att is None:
            raise WorkspaceError("UNKNOWN_RESOURCE", f"No report for {facility_id}/{sku}", 404)
        now_ = self.clock
        daily = float(rep["daily_consumption"]) if rep else 0.0
        att_age = (now_ - _dt(att["observed_at"])).total_seconds() / 86400 if att else None
        rep_age = (now_ - _dt(rep["report_date"])).total_seconds() / 86400 if rep else None

        def deplete(qty: float, since: datetime) -> float:
            elapsed = max((now_ - since).total_seconds() / 86400, 0.0)
            return max(qty - daily * elapsed - self._outflow_since(key, since) + self._inflow_since(key, since), 0.0)

        reported_now = deplete(float(rep["reported_qty"]), _dt(rep["report_date"])) if rep else None
        verified_now = deplete(float(att["usable_qty"]), _dt(att["observed_at"])) if att else None
        if att and att_age is not None and att_age <= ATTESTATION_FRESH_DAYS:
            state, basis, estimate = VerificationState.VERIFIED.value, "HUMAN_COUNT", verified_now
        else:
            state = VerificationState.UNVERIFIED.value
            basis, estimate = "REPORTED", reported_now if reported_now is not None else verified_now
        runway = (estimate or 0.0) / daily if daily > 0 else None
        floor = daily * self.policy.donor_floor_days
        return {
            "facility_id": facility_id, "sku": sku,
            "facility_name": self.facilities.get(facility_id, {}).get("name", facility_id),
            "reported_qty": rep["reported_qty"] if rep else None,
            "reported_now": None if reported_now is None else round(reported_now, 1),
            "report_date": rep["report_date"] if rep else None,
            "report_age_days": None if rep_age is None else round(rep_age, 1),
            "report_provenance": rep["provenance"] if rep else None,
            "daily_consumption": daily,
            "verification_state": state,
            "estimate_basis": basis,
            "usable_estimate": None if estimate is None else round(estimate, 1),
            "verified_usable_now": None if verified_now is None else round(verified_now, 1),
            "last_attested_at": att["observed_at"] if att else None,
            "attestation_age_days": None if att_age is None else round(att_age, 1),
            "last_finding": att.get("finding") if att else None,
            "last_attestation_event_id": att["event_id"] if att else None,
            "report_event_id": rep["event_id"] if rep else None,
            "runway_days": None if runway is None else round(runway, 2),
            "projected_failure_at": None if runway is None else _iso(now_ + timedelta(days=runway)),
            "safety_floor_qty": round(floor, 1),
            "inbound_qty": round(self._inbound(key), 1),
            "evidence_confidence": ("HIGH" if state == "VERIFIED" else
                                    "MEDIUM" if att_age is not None and att_age <= ATTESTATION_STALE_DAYS else "LOW"),
        }

    def all_states(self, sku: Optional[str] = None) -> list[dict]:
        keys = sorted(set(self.reports) | set(self.attestations))
        return [self.resource_state(f, s) for f, s in keys if sku is None or s == sku]

    # ------------------------------------------------------------ need + trust queue
    def needs(self, sku: str) -> list[dict]:
        out = []
        for st in self.all_states(sku):
            if st["runway_days"] is None:
                continue
            # Dispatched lines are counted once, as inbound; approved-not-dispatched lines as committed.
            covered = st["usable_estimate"] + st["inbound_qty"] + self._committed(st["facility_id"], sku, "in")
            runway_with_supply = covered / st["daily_consumption"]
            if st["runway_days"] >= self.policy.alert_runway_days or runway_with_supply >= self.policy.horizon_days:
                continue
            shortfall = st["daily_consumption"] * self.policy.horizon_days - covered
            if shortfall <= 0.5:
                continue
            out.append({"facility_id": st["facility_id"], "sku": sku, "shortfall": round(shortfall, 1),
                        "runway_days": st["runway_days"], "daily_consumption": st["daily_consumption"],
                        "essentiality": self.skus.get(sku, {}).get("essentiality", 2),
                        "cold_chain": self.skus.get(sku, {}).get("cold_chain", False)})
        return out

    def _signals(self, st: dict, sku_runways: list[float]) -> list[str]:
        sig = []
        if st["last_attested_at"] is None:
            sig.append("NEVER_PHYSICALLY_COUNTED")
        elif st["attestation_age_days"] > ATTESTATION_STALE_DAYS:
            sig.append("LAST_COUNT_STALE")
        elif st["attestation_age_days"] > ATTESTATION_FRESH_DAYS:
            sig.append("LAST_COUNT_AGING")
        if st["last_finding"] in ("PHANTOM_STOCK", "UNDER_REPORTED"):
            sig.append(f"LAST_COUNT_FOUND_{st['last_finding']}")
        if st["report_age_days"] is not None and st["report_age_days"] > REPORT_STALE_DAYS:
            sig.append("REPORT_STALE")
        if st["reported_now"] and st["daily_consumption"] > 0:
            rep_runway = st["reported_now"] / st["daily_consumption"]
            median = sorted(sku_runways)[len(sku_runways) // 2] if sku_runways else 0
            if rep_runway > SURPLUS_OUTLIER_RUNWAY_DAYS and rep_runway > 3 * max(median, 1):
                sig.append("SURPLUS_OUTLIER_VS_DISTRICT")
        if st["reported_now"] and st["daily_consumption"] == 0:
            sig.append("STOCK_WITHOUT_CONSUMPTION")
        return sig

    def district_centre(self, facility_id: str) -> tuple[float, float]:
        """Verifiers are based in the facility's own district: use the centroid of that district's facilities."""
        f = self.facilities[facility_id]
        peers = [g for g in self.facilities.values() if g.get("district") and g.get("district") == f.get("district")]
        if not peers:
            return self.hq
        return (sum(g["lat"] for g in peers) / len(peers), sum(g["lon"] for g in peers) / len(peers))

    def visit_hours(self, facility_id: str) -> float:
        f = self.facilities.get(facility_id)
        if not f:
            return 99.0
        clat, clon = self.district_centre(facility_id)
        x, y = project_xy(f["lat"], f["lon"], clat, clon)
        hq = Facility("HQ", "HQ", 0.0, 0.0)
        return round(2 * travel_hours(hq, Facility(facility_id, f["tier"], x, y)) + VISIT_COUNT_HOURS, 2)

    def trust_queue(self, sku: Optional[str] = None, verifier_hours: int = DEFAULT_VERIFIER_HOURS) -> dict:
        """Which counts would change a decision? Value = units of planned or candidate supply
        whose existence rests on an unverified report. No probability is invented."""
        skus = [sku] if sku else sorted(self.skus)
        rows = []
        for s in skus:
            states = self.all_states(s)
            need_total = sum(n["shortfall"] for n in self.needs(s))
            need_ids = {n["facility_id"] for n in self.needs(s)}
            runways = [st["reported_now"] / st["daily_consumption"] for st in states
                       if st["reported_now"] and st["daily_consumption"] > 0]
            planned_from = self._planned_from(s)
            for st in states:
                fid = st["facility_id"]
                signals = self._signals(st, runways)
                open_task = next((t for t in self.tasks.values() if t["facility_id"] == fid and t["sku"] == s
                                  and t["status"] == "OPEN"), None)
                surplus = max((st["reported_now"] or 0.0) - st["safety_floor_qty"], 0.0)
                stake = 0.0
                if st["verification_state"] != "VERIFIED" and fid not in need_ids:
                    stake = max(planned_from.get(fid, 0.0), min(surplus, need_total))
                rows.append({**st, "signals": signals, "units_at_stake": round(stake, 1),
                             "visit_hours": self.visit_hours(fid), "is_recipient": fid in need_ids,
                             "open_task_id": open_task["task_id"] if open_task else None})
        chosen = self._select_visits(rows, verifier_hours)
        for i, r in enumerate(rows):
            r["recommended_action"], r["reason"] = self._row_action(r, i in chosen)
        rows.sort(key=lambda r: (-{"VERIFY": 3, "TRANSFER": 2, "ESCALATE": 2, "WAIT": 1, "MONITOR": 0}
                                 [r["recommended_action"]], -r["units_at_stake"], r["runway_days"] or 1e9))
        return {
            "as_of": _iso(self.clock), "environment": self.environment, "verifier_hours": verifier_hours,
            "hours_used": round(sum(r["visit_hours"] for r in rows
                                    if r["recommended_action"] == "VERIFY" and not r["open_task_id"]), 2),
            "method": "0/1 knapsack over units-at-stake / verifier-hours (tathyon.optimize.knapsack). "
                      "Units at stake = planned or candidate transfer quantity that rests on an unverified report.",
            "rows": rows,
        }

    def _planned_from(self, sku: str) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.plans.values():
            if p["sku"] != sku or p["status"] not in ("PROPOSED", "APPROVED"):
                continue
            for l in p["lines"]:
                if l["kind"] == "CONTINGENT_ON_VERIFICATION" and l["status"] in ("PROPOSED", "APPROVED_CONTINGENT"):
                    out[l["from_facility"]] = out.get(l["from_facility"], 0.0) + l["qty"]
        return out

    @staticmethod
    def _select_visits(rows: list[dict], hours: int) -> set[int]:
        idx = [i for i, r in enumerate(rows) if r["units_at_stake"] > 0 and not r["open_task_id"]]
        picked = knapsack([rows[i]["units_at_stake"] for i in idx],
                          [max(int(math.ceil(rows[i]["visit_hours"])), 1) for i in idx], hours)
        return {idx[k] for k in picked} | {i for i, r in enumerate(rows) if r["open_task_id"]}

    @staticmethod
    def _row_action(r: dict, selected: bool) -> tuple[str, str]:
        if selected:
            if r["open_task_id"]:
                return "VERIFY", f"Verification task {r['open_task_id']} is open; count pending."
            return "VERIFY", (f"{r['units_at_stake']:g} units of candidate supply rest on an unverified report "
                              f"({', '.join(r['signals']) or 'no recent count'}). A {r['visit_hours']:g} h visit "
                              "decides whether they exist.")
        if r["is_recipient"]:
            return "TRANSFER", (f"Runway {r['runway_days']:g} d is below the alert runway. "
                                "Generate a verified-donor plan.")
        if r["units_at_stake"] > 0:
            return "WAIT", "Unverified, but other visits protect more units within this cycle's verifier hours."
        return "MONITOR", "No decision currently depends on this record."

    # ------------------------------------------------------------ planning
    def _optimizer_inputs(self, sku: str) -> tuple[dict[str, Facility], list[SourceStock], list[Need], Optional[float]]:
        needs_ = self.needs(sku)
        need_ids = {n["facility_id"] for n in needs_}
        facilities = {fid: Facility(fid, f["tier"], f["x_km"], f["y_km"], has_cold_chain=f["has_cold_chain"],
                                    lat=f["lat"], lon=f["lon"]) for fid, f in self.facilities.items()}
        sources = []
        for st in self.all_states(sku):
            fid = st["facility_id"]
            if fid in need_ids or fid not in facilities:
                continue
            committed = self._committed(fid, sku, "out")
            verified = st["verification_state"] == "VERIFIED"
            # An unverified donor is only a CONTINGENT candidate while its report is fresh.
            stale_report = st["report_age_days"] is None or st["report_age_days"] > REPORT_STALE_DAYS
            sources.append(SourceStock(
                facility_id=fid, resource_key=sku,
                reported_qty=max((st["reported_now"] or 0.0) - committed, 0.0),
                verified_state=VerificationState.VERIFIED if verified else VerificationState.UNVERIFIED,
                q_alpha=max(st["usable_estimate"] - committed, 0.0) if verified else None,
                safety_stock=st["safety_floor_qty"],
                available=verified or not stale_report))
        needs = [Need(n["facility_id"], sku, n["shortfall"], n["runway_days"], essentiality=n["essentiality"],
                      cold_chain_required=n["cold_chain"]) for n in needs_]
        runway_h = min((n["runway_days"] * 24 for n in needs_), default=None)
        return facilities, sources, needs, runway_h

    def propose_plan(self, sku: str, actor: str, parent_plan_id: Optional[str] = None,
                     trigger: str = "OPERATOR_REQUEST") -> dict:
        with self.lock:
            if sku not in self.skus:
                raise WorkspaceError("UNKNOWN_SKU", f"{sku} is not in the catalogue", 404)
            open_ = [p for p in self.plans.values() if p["sku"] == sku and p["status"] == "PROPOSED"]
            if open_:
                raise WorkspaceError("PLAN_ALREADY_PENDING",
                                     f"Plan {open_[0]['plan_id']} is awaiting a decision for {sku}.", 409)
            facilities, sources, needs, runway_h = self._optimizer_inputs(sku)
            result = generate_options(sku, facilities, sources, needs, self.policy, runway_h)
            plan_id = new_id("plan")
            rec = next(o for o in result["options"] if o["option"] == result["recommended"])
            lines = [{**l, "line_id": f"{plan_id[-6:]}-{l['line_id']}"} for l in rec["lines"]]
            options = [{**o, "lines": [{**l, "line_id": f"{plan_id[-6:]}-{l['line_id']}"} for l in o["lines"]]}
                       for o in result["options"]]
            version = 1 + sum(1 for p in self.plans.values() if p["sku"] == sku)
            payload = {
                "plan_id": plan_id, "sku": sku, "version": version, "parent_plan_id": parent_plan_id,
                "trigger": trigger, "created_at": _iso(self.clock),
                "recommended_option": result["recommended"], "recommendation_rule": result["rule"],
                "options": options, "lines": lines,
                "needs": [asdict(n) for n in needs], "total_need": result["total_need"],
                "rejected_donors": result["rejected_donors"],
                "verification_targets": sorted({l["from_facility"] for l in lines
                                                if l["kind"] == "CONTINGENT_ON_VERIFICATION"}),
                "constraints": {k: v for k, v in asdict(self.policy).items()},
                "environment": self.environment,
                "solver": "OR-Tools CP-SAT (tathyon.optimize.optimise), deterministic single worker",
            }
            ev = self._emit(EventType.PLAN_PROPOSED, "DISTRICT", sku, payload, actor)
            return self.plans[payload["plan_id"]] | {"event_id": ev.event_id}

    # ------------------------------------------------------------ human decision
    @staticmethod
    def _require_role(role: str, allowed: tuple[str, ...], action: str) -> None:
        if role not in allowed:
            raise WorkspaceError("ROLE_NOT_AUTHORISED", f"{action} requires one of {list(allowed)}; got '{role}'.", 403)

    def decide_plan(self, plan_id: str, *, officer_id: str, role: str, decision: str,
                    option: Optional[str] = None, rejected_lines: Optional[list[str]] = None,
                    reason: str = "") -> dict:
        with self.lock:
            plan = self.plans.get(plan_id)
            if plan is None:
                raise WorkspaceError("PLAN_NOT_FOUND", plan_id, 404)
            if role not in APPROVER_ROLES:
                self._emit(EventType.FLAGGED, "DISTRICT", plan["sku"],
                           {"violation": "UNAUTHORISED_APPROVAL_ATTEMPT", "plan_id": plan_id, "role": role}, officer_id)
                self._require_role(role, APPROVER_ROLES, "Plan approval")
            if plan["status"] != "PROPOSED":
                raise WorkspaceError("PLAN_NOT_PENDING", f"Plan is {plan['status']}; decisions are final.", 409)
            if not reason.strip():
                raise WorkspaceError("REASON_REQUIRED", "A recorded reason is required for every decision.", 422)
            if decision == "REJECT":
                self._emit(EventType.REJECTED, "DISTRICT", plan["sku"],
                           {"kind": "PLAN_REJECTED", "plan_id": plan_id, "officer_id": officer_id, "role": role,
                            "reason": reason}, officer_id)
                return self.plans[plan_id]
            if decision != "APPROVE":
                raise WorkspaceError("INVALID_DECISION", "decision must be APPROVE or REJECT", 422)
            chosen = option or plan["recommended_option"]
            opt = next((o for o in plan["options"] if o["option"] == chosen), None)
            if opt is None or not opt["approvable"]:
                raise WorkspaceError("OPTION_NOT_APPROVABLE", f"{chosen} cannot be approved.", 422)
            rejected = set(rejected_lines or [])
            unknown = rejected - {l["line_id"] for l in opt["lines"]}
            if unknown:
                raise WorkspaceError("UNKNOWN_LINE", f"{sorted(unknown)}", 422)
            decisions = {l["line_id"]: ("REJECTED_BY_OFFICER" if l["line_id"] in rejected else
                                        "APPROVED_CONTINGENT" if l["kind"] == "CONTINGENT_ON_VERIFICATION"
                                        else "APPROVED") for l in opt["lines"]}
            self._emit(EventType.PLAN_APPROVED, "DISTRICT", plan["sku"], {
                "plan_id": plan_id, "officer_id": officer_id, "role": role, "option": chosen,
                "line_decisions": decisions, "selected_lines": opt["lines"], "reason": reason,
                "followed_recommendation": chosen == plan["recommended_option"]}, officer_id)
            for fid in sorted({l["from_facility"] for l in opt["lines"]
                               if decisions[l["line_id"]] == "APPROVED_CONTINGENT"}):
                self._request_verification(fid, plan["sku"], plan_id, officer_id)
            if chosen == "ESCALATE" or opt["shortfall_qty"] > 0:
                self.escalate(plan["sku"], plan_id, opt["shortfall_qty"] or plan["total_need"], officer_id,
                              "Approved plan leaves unmet need")
            return self.plans[plan_id]

    def _request_verification(self, facility_id: str, sku: str, plan_id: str, actor: str) -> StateEvent:
        task_id = new_id("vtask")
        return self._emit(EventType.VERIFICATION_REQUESTED, facility_id, sku, {
            "task_id": task_id, "facility_id": facility_id, "sku": sku, "plan_id": plan_id,
            "reason": "Contingent line depends on an unverified report",
            "visit_hours": self.visit_hours(facility_id)}, actor)

    def break_glass(self, plan_id: str, line_id: str, *, officer_id: str, role: str,
                    reason_code: str, justification: str) -> dict:
        with self.lock:
            self._require_role(role, APPROVER_ROLES, "Break-glass release")
            plan = self.plans.get(plan_id)
            line = next((l for l in (plan or {}).get("lines", []) if l["line_id"] == line_id), None)
            if plan is None or line is None:
                raise WorkspaceError("LINE_NOT_FOUND", f"{plan_id}/{line_id}", 404)
            if line["status"] != "APPROVED_CONTINGENT":
                raise WorkspaceError("LINE_NOT_CONTINGENT", f"Line is {line['status']}.", 409)
            if reason_code not in BREAK_GLASS_REASONS or len(justification.strip()) < 20:
                raise WorkspaceError("BREAK_GLASS_REASON_REQUIRED",
                                     f"reason_code in {sorted(BREAK_GLASS_REASONS)} and a 20+ char justification.", 422)
            due = _iso(self.clock + timedelta(hours=BREAK_GLASS_OBLIGATION_HOURS))
            self._emit(EventType.OVERRIDDEN, line["from_facility"], plan["sku"], {
                "plan_id": plan_id, "line_id": line_id, "officer_id": officer_id, "reason_code": reason_code,
                "justification": justification}, officer_id)
            self._emit(EventType.OBLIGATION_CREATED, line["from_facility"], plan["sku"], {
                "kind": "POST_HOC_VERIFICATION", "plan_id": plan_id, "line_id": line_id, "due_at": due,
                "owner": officer_id, "note": "Break-glass released unverified stock; a count is owed."}, officer_id)
            return self.plans[plan_id]

    # ------------------------------------------------------------ physical verification
    def submit_attestation(self, facility_id: str, sku: str, *, attester_id: str, attester_role: str,
                           present_qty: float, usable_qty: float, expired_qty: float = 0.0,
                           evidence_ref: str = "", seconds_spent: float = 0.0,
                           provenance: str = "HUMAN_ATTESTED", client_event_id: Optional[str] = None,
                           observed_at: Optional[datetime] = None) -> dict:
        with self.lock:
            self._require_role(attester_role, VERIFIER_ROLES, "Physical attestation")
            key = (facility_id, sku)
            rep = self.reports.get(key)
            if rep is None:
                raise WorkspaceError("UNKNOWN_RESOURCE", f"No report to verify for {facility_id}/{sku}", 404)
            if not (0 <= usable_qty <= present_qty) or expired_qty < 0 or not all(
                    math.isfinite(v) for v in (present_qty, usable_qty, expired_qty)):
                raise WorkspaceError("INVALID_COUNT", "Require 0 <= usable <= present and non-negative expired.", 422)
            if observed_at is not None and provenance not in ("SAMPLE", "SYNTHETIC"):
                raise WorkspaceError("BACKDATING_FORBIDDEN", "Human counts are stamped with the server clock.", 422)
            custodian = self.facilities.get(facility_id, {}).get("custodian_id")
            if custodian and custodian == attester_id:
                raise WorkspaceError("ATTESTOR_IS_CUSTODIAN",
                                     "The custodian of record may not count their own stock.", 403)
            reported_now = self.resource_state(facility_id, sku)["reported_now"] or 0.0
            att = Attestation(
                attestation_id=client_event_id or new_id("att"), claim_id=rep["claim_id"],
                evidence_refs=[evidence_ref] if evidence_ref else [],
                observed={"present_quantity": present_qty, "usable_quantity": usable_qty,
                          "expired_quantity": expired_qty, "provenance": provenance},
                observed_at=_iso(observed_at or self.clock), attestor_id=attester_id, attestor_role=attester_role,
                delegation_id=f"DEL-{facility_id}", is_custodian=False, seconds_spent=seconds_spent,
                signature=f"unsigned:{attester_id}")
            try:
                ev = self.store.put_attestation(att)
            except (ValueError, KeyError) as exc:
                raise WorkspaceError(str(exc).split(":")[0], str(exc), 403) from exc
            if ev is None:
                raise WorkspaceError("DUPLICATE_EVENT", "Attestation already recorded.", 409)
            self._apply(ev)
            return self._reconcile(facility_id, sku, reported_now, usable_qty, ev, attester_id)

    def _reconcile(self, facility_id: str, sku: str, reported: float, usable: float,
                   att_ev: StateEvent, actor: str) -> dict:
        variance = round(usable - reported, 1)
        tol = max(VARIANCE_TOLERANCE_UNITS, VARIANCE_TOLERANCE_FRACTION * max(reported, 1.0))
        finding = "CONSISTENT" if abs(variance) <= tol else "PHANTOM_STOCK" if variance < 0 else "UNDER_REPORTED"
        task = next((t for t in self.tasks.values() if t["facility_id"] == facility_id and t["sku"] == sku
                     and t["status"] == "OPEN"), None)
        rec = self._emit(EventType.RECONCILED, facility_id, sku, {
            "attestation_event_id": att_ev.event_id, "task_id": task["task_id"] if task else None,
            "reported_qty": round(reported, 1), "usable_qty": usable, "variance_units": variance,
            "tolerance_units": round(tol, 1), "finding": finding}, "tathyon_reconciler")
        replans = self.check_feasibility(actor="tathyon_feasibility_monitor", trigger_event=rec.event_id)
        return {"finding": finding, "variance_units": variance, "reconciled_event_id": rec.event_id,
                "attestation_event_id": att_ev.event_id, "replans": replans,
                "state": self.resource_state(facility_id, sku)}

    # ------------------------------------------------------------ feasibility / replan
    def evaluate_feasibility(self, plan_id: str) -> dict:
        """Read-only: which approved lines can no longer be honoured, and why."""
        plan = self.plans[plan_id]
        problems = []
        for l in plan["lines"]:
            if l["status"] == "APPROVED_CONTINGENT":
                st = self.resource_state(l["from_facility"], l["sku"])
                if st["verification_state"] == "VERIFIED":
                    available = max((st["verified_usable_now"] or 0) - st["safety_floor_qty"], 0.0)
                    if available + 1e-6 < l["qty"]:
                        problems.append({"line_id": l["line_id"], "code": "DONOR_COUNT_BELOW_LINE",
                                         "detail": f"Count shows {available:g} transferable above floor; line needs {l['qty']:g}.",
                                         "evidence_event_id": st["last_attestation_event_id"]})
            if l["status"] in ("DISPATCHED", "DELAYED"):
                sh = self.shipments[l["shipment_id"]]
                st = self.resource_state(l["to_facility"], l["sku"])
                fail_at = st["projected_failure_at"]
                if fail_at and _dt(sh["eta"]) > _dt(fail_at):
                    problems.append({"line_id": l["line_id"], "code": "ETA_AFTER_RECIPIENT_FAILURE",
                                     "detail": f"ETA {sh['eta']} is after projected failure {fail_at}.",
                                     "evidence_event_id": sh["events"][-1]})
        received = [self.shipments[l["shipment_id"]] for l in plan["lines"]
                    if l["status"] == "RECEIVED" and l.get("shipment_id")]
        for sh in received:
            short = sh["qty"] - (sh["received_qty"] - sh["damaged_qty"])
            if short > VARIANCE_TOLERANCE_UNITS and not any(r.get("shipment_id") == sh["shipment_id"] for r in self.replans):
                problems.append({"line_id": sh["line_id"], "code": "PARTIAL_RECEIPT", "shipment_id": sh["shipment_id"],
                                 "detail": f"{short:g} units short on receipt.", "evidence_event_id": sh["events"][-1]})
        return {"plan_id": plan_id, "status": plan["status"], "feasible": not problems, "problems": problems}

    def check_feasibility(self, actor: str, trigger_event: Optional[str] = None) -> list[dict]:
        """Deterministic monitor. Marks REPLAN_REQUIRED; never approves or dispatches a new plan."""
        out = []
        for plan_id, plan in list(self.plans.items()):
            if plan["status"] != "APPROVED":
                continue
            res = self.evaluate_feasibility(plan_id)
            if res["feasible"]:
                continue
            invalid = [p["line_id"] for p in res["problems"] if p["code"] == "DONOR_COUNT_BELOW_LINE"]
            ev = self._emit(EventType.REPLAN_REQUIRED, "DISTRICT", plan["sku"], {
                "plan_id": plan_id, "problems": res["problems"], "invalidated_lines": invalid,
                "shipment_id": next((p.get("shipment_id") for p in res["problems"] if p.get("shipment_id")), None),
                "trigger_event_id": trigger_event, "next_step": "Generate a replan candidate; a human must approve it."},
                actor)
            out.append({"plan_id": plan_id, "event_id": ev.event_id, "problems": res["problems"]})
        return out

    def escalate(self, sku: str, plan_id: Optional[str], qty: float, actor: str, reason: str) -> StateEvent:
        return self._emit(EventType.ESCALATED, "DISTRICT", sku, {
            "plan_id": plan_id, "qty": round(qty, 1), "to": "STATE_DRUG_WAREHOUSE", "reason": reason,
            "note": "Recorded obligation for the state. TATHYON does not place procurement orders."}, actor)

    # ------------------------------------------------------------ action: shipment
    def dispatch(self, plan_id: str, line_id: str, *, actor: str, role: str) -> dict:
        with self.lock:
            self._require_role(role, DISPATCH_ROLES, "Dispatch")
            plan = self.plans.get(plan_id)
            line = next((l for l in (plan or {}).get("lines", []) if l["line_id"] == line_id), None)
            if plan is None or line is None:
                raise WorkspaceError("LINE_NOT_FOUND", f"{plan_id}/{line_id}", 404)
            if plan["status"] != "APPROVED":
                raise WorkspaceError("PLAN_NOT_APPROVED", f"Plan is {plan['status']}.", 409)
            if line["status"] == "APPROVED_CONTINGENT":
                raise WorkspaceError("VERIFICATION_REQUIRED",
                                     "Contingent line: a human count must confirm the donor first, "
                                     "or a medical officer must use recorded break-glass.", 409)
            if line["status"] not in ("APPROVED", "RELEASED_BREAK_GLASS"):
                raise WorkspaceError("LINE_NOT_DISPATCHABLE", f"Line is {line['status']}.", 409)
            eta = _iso(self.clock + timedelta(hours=line["travel_hours"]))
            sid = new_id("shp")
            self._emit(EventType.SHIPMENT_DISPATCHED, line["from_facility"], line["sku"], {
                "shipment_id": sid, "plan_id": plan_id, "line_id": line_id, "from_facility": line["from_facility"],
                "to_facility": line["to_facility"], "sku": line["sku"], "qty": line["qty"],
                "dispatched_at": _iso(self.clock), "eta": eta, "dispatched_by": actor,
                "tracking_provenance": "NO_GPS_STATUS_UPDATES_ONLY"}, actor)
            return self.shipments[sid]

    def confirm_contingent_line(self, plan_id: str, line_id: str, *, actor: str, role: str) -> dict:
        """After a count confirms the donor, the medical officer releases the held line."""
        with self.lock:
            self._require_role(role, APPROVER_ROLES, "Release of a contingent line")
            plan = self.plans[plan_id]
            line = next(l for l in plan["lines"] if l["line_id"] == line_id)
            if line["status"] != "APPROVED_CONTINGENT":
                raise WorkspaceError("LINE_NOT_CONTINGENT", f"Line is {line['status']}.", 409)
            res = self.evaluate_feasibility(plan_id)
            if any(p["line_id"] == line_id for p in res["problems"]):
                raise WorkspaceError("COUNT_DOES_NOT_SUPPORT_LINE", "The latest count cannot honour this line.", 409)
            st = self.resource_state(line["from_facility"], line["sku"])
            if st["verification_state"] != "VERIFIED":
                raise WorkspaceError("VERIFICATION_REQUIRED", "Donor has no current physical count.", 409)
            self._emit(EventType.TRANSFER_APPROVED, line["from_facility"], line["sku"], {
                "plan_id": plan_id, "line_id": line_id, "basis_event_id": st["last_attestation_event_id"]}, actor)
            return self.plans[plan_id]

    def delay_shipment(self, shipment_id: str, hours: float, reason: str, actor: str) -> dict:
        with self.lock:
            sh = self.shipments.get(shipment_id)
            if sh is None:
                raise WorkspaceError("SHIPMENT_NOT_FOUND", shipment_id, 404)
            if sh["status"] not in ("IN_TRANSIT", "DELAYED"):
                raise WorkspaceError("SHIPMENT_CLOSED", f"Shipment is {sh['status']}.", 409)
            if not 0 < hours <= 240:
                raise WorkspaceError("INVALID_DURATION", "delay hours must be in (0, 240].", 422)
            new_eta = _iso(_dt(sh["eta"]) + timedelta(hours=hours))
            self._emit(EventType.SHIPMENT_DELAYED, sh["to_facility"], sh["sku"], {
                "shipment_id": shipment_id, "delay_hours": hours, "new_eta": new_eta, "reason": reason}, actor)
            self.check_feasibility(actor="tathyon_feasibility_monitor")
            return self.shipments[shipment_id]

    def receive(self, shipment_id: str, *, received_qty: float, damaged_qty: float,
                receiver_id: str, receiver_role: str, notes: str = "") -> dict:
        with self.lock:
            self._require_role(receiver_role, RECEIVER_ROLES, "Receipt")
            sh = self.shipments.get(shipment_id)
            if sh is None:
                raise WorkspaceError("SHIPMENT_NOT_FOUND", shipment_id, 404)
            if sh["status"] not in ("IN_TRANSIT", "DELAYED"):
                raise WorkspaceError("SHIPMENT_CLOSED", f"Shipment already {sh['status']} (replay refused).", 409)
            if not (0 <= damaged_qty <= received_qty <= sh["qty"]) or not math.isfinite(received_qty):
                raise WorkspaceError("INVALID_RECEIPT", "Require 0 <= damaged <= received <= dispatched.", 422)
            if receiver_id == sh["dispatched_by"]:
                raise WorkspaceError("RECEIVER_IS_DISPATCHER", "The dispatcher cannot confirm receipt.", 403)
            before = self.resource_state(sh["to_facility"], sh["sku"])
            status = "RECEIVED" if received_qty - damaged_qty >= sh["qty"] - VARIANCE_TOLERANCE_UNITS else "RECEIVED_PARTIAL"
            ev = self._emit(EventType.RECEIVED, sh["to_facility"], sh["sku"], {
                "shipment_id": shipment_id, "received_qty": received_qty, "damaged_qty": damaged_qty,
                "dispatched_qty": sh["qty"], "variance_units": round(received_qty - damaged_qty - sh["qty"], 1),
                "status": status, "receiver_id": receiver_id, "notes": notes[:500]}, receiver_id)
            after = self.resource_state(sh["to_facility"], sh["sku"])
            self._emit(EventType.OUTCOME_RECORDED, sh["to_facility"], sh["sku"], {
                "shipment_id": shipment_id, "plan_id": sh["plan_id"], "receipt_event_id": ev.event_id,
                "runway_before_days": before["runway_days"], "runway_after_days": after["runway_days"],
                "usable_before": before["usable_estimate"], "usable_after": after["usable_estimate"],
                "net_received": round(received_qty - damaged_qty, 1)}, "tathyon_outcome_recorder")
            replans = self.check_feasibility(actor="tathyon_feasibility_monitor", trigger_event=ev.event_id)
            return {"shipment": self.shipments[shipment_id], "replans": replans, "recipient_state": after}

    # ------------------------------------------------------------ outcome metrics
    def snapshot_stockout_days(self) -> dict:
        """Projected stockout-days inside the horizon for every facility, from current state
        plus in-transit supply. A projection over SYNTHETIC or reported data, labelled as such."""
        total, per = 0.0, {}
        for st in self.all_states():
            if not st["daily_consumption"]:
                continue
            cover = ((st["usable_estimate"] or 0.0) + st["inbound_qty"]) / st["daily_consumption"]
            days = round(max(self.policy.horizon_days - cover, 0.0), 2)
            if days > 0:
                per[f"{st['facility_id']}/{st['sku']}"] = days
            total += days
        return {"total": round(total, 2), "by_resource": per, "as_of": _iso(self.clock)}

    def outcome_summary(self) -> dict:
        tasks_done = [t for t in self.tasks.values() if t["status"] == "COMPLETED"]
        hits = [t for t in tasks_done if t.get("finding") != "CONSISTENT"]
        phantom_blocked = 0.0
        for p in self.plans.values():
            naive = next((o for o in p["options"] if o["option"] == "TRUST_REPORTED"), None)
            for l in (naive or {}).get("lines", []):
                att = self.attestations.get((l["from_facility"], l["sku"]))
                if att and att.get("finding") == "PHANTOM_STOCK" and p["version"] == 1:
                    floor = self.resource_state(l["from_facility"], l["sku"])["safety_floor_qty"]
                    phantom_blocked += max(l["qty"] - max(att["usable_qty"] - floor, 0.0), 0.0)
        return {
            "environment": self.environment,
            "label": "Measured from this workspace's event ledger. Stock data is "
                     + ("SYNTHETIC (test fixture)." if self.environment == ENV_SYNTHETIC
                        else "SAMPLE demonstration data (not real facility stock)."
                        if any(r.get("provenance") == "SAMPLE" for r in self.reports.values())
                        else "user-supplied exports and human counts; reports stay unverified until counted."),
            "stockout_days_projected": {"baseline_at_load": self.baseline.get("total"),
                                        "current": self.snapshot_stockout_days()["total"]},
            "phantom_units_blocked": round(phantom_blocked, 1),
            "verification_tasks_completed": len(tasks_done),
            "verification_hit_rate": round(len(hits) / len(tasks_done), 3) if tasks_done else None,
            "verification_hit_rate_definition": "Plan-driven verification tasks whose count differed from "
                                                "the report beyond tolerance / tasks completed.",
            "findings": [{"facility_id": k[0], "sku": k[1], "finding": a["finding"], "variance_units": a.get("variance_units"),
                          "event_id": a["event_id"]} for k, a in self.attestations.items() if a.get("finding")],
            "plans": {s: sum(1 for p in self.plans.values() if p["status"] == s)
                      for s in ("PROPOSED", "APPROVED", "REJECTED", "REPLAN_REQUIRED", "SUPERSEDED")},
            "shipments": [{k: s.get(k) for k in ("shipment_id", "from_facility", "to_facility", "qty", "status",
                                                  "received_qty", "damaged_qty", "eta")} for s in self.shipments.values()],
            "replan_events": [{"event_id": r["event_id"], "plan_id": r["plan_id"],
                               "codes": sorted({p["code"] for p in r["problems"]})} for r in self.replans],
            "escalations": [{"event_id": e["event_id"], "qty": e["qty"], "reason": e["reason"]} for e in self.escalations],
            "outcomes": self.outcomes,
        }

    def record_baseline(self, actor: str) -> StateEvent:
        """Ledger the no-action projection so before/after survives replay."""
        return self._emit(EventType.DECISION_MADE, "DISTRICT", "*", {
            "category": "BASELINE_SNAPSHOT", "snapshot": self.snapshot_stockout_days(),
            "note": "Projected stockout-days if no action is taken, at load time."}, actor)

    def summary(self) -> dict:
        return {"environment": self.environment, "scenario": self.scenario, "clock": _iso(self.clock),
                "clock_is_simulated": self.clock_override is not None,
                "facilities": len(self.facilities), "skus": sorted(self.skus), "reports": len(self.reports),
                "quarantined_rows": len(self.quarantine), "plans": len(self.plans),
                "open_tasks": sum(1 for t in self.tasks.values() if t["status"] == "OPEN"),
                "events": len(self.store.events)}
