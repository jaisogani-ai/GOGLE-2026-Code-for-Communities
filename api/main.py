"""TATHYON HTTP API.

Data sources served here, and nothing else:
  - OpenStreetMap facility locations (REAL_PUBLIC_OSM, ODbL) from reference/
  - facility registries and stock exports uploaded by a data officer (USER_SUPPLIED_UNVERIFIED)
  - physical counts entered by field verifiers (HUMAN_ATTESTED)
  - everything derived from those by the deterministic engine, on the hash-chained ledger

No synthetic records are loaded. Screens stay empty until real data is uploaded.
"""
from __future__ import annotations

from .env import load_dotenv

load_dotenv()

import base64  # noqa: E402
import csv  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import threading  # noqa: E402
from typing import Literal, Optional  # noqa: E402

from fastapi import Depends, FastAPI, HTTPException, Query, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from tathyon.agents import AGENT_CLASSES, build_agent, gemini_status  # noqa: E402
from tathyon.agents.readers import explain_event, facility_detail, plan_view, search_events  # noqa: E402
from tathyon.intake_pipeline import (  # noqa: E402
    FORMULA_PREFIXES, IntakeError, facilities_from_osm, parse_facility_registry, parse_stock_export,
)
from tathyon.store import EventStore  # noqa: E402
from tathyon.workspace import BREAK_GLASS_REASONS, ENV_REAL, Workspace, WorkspaceError  # noqa: E402

from .env import maps_browser_key  # noqa: E402
from .maps import map_layers, maps_status, tiles3d_status  # noqa: E402
from .security import (  # noqa: E402
    DEMO_USERS, HardeningMiddleware, Principal, client_key, current_principal, demo_login_enabled,
    issue_demo_session, limiter, require_roles,
)

log = logging.getLogger("tathyon.api")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(ROOT, "web")
REFERENCE_DIR = os.path.join(ROOT, "reference")
# Real OpenStreetMap extracts cached in reference/ (district bounding boxes are approximate).
OSM_DISTRICTS = {
    "bastar_chhattisgarh": ("Bastar", "Chhattisgarh"),
    "gaya_bihar": ("Gaya", "Bihar"),
    "nandurbar_maharashtra": ("Nandurbar", "Maharashtra"),
    "kalahandi_odisha": ("Kalahandi", "Odisha"),
    "varanasi_uttar_pradesh": ("Varanasi", "Uttar Pradesh"),
}


def osm_path(key: str) -> str:
    return os.path.join(REFERENCE_DIR, f"osm_{key}_facilities.json")
VERSION = "3.0.0"

APPROVER = ("district_medical_officer",)
DISPATCHER = ("district_medical_officer", "logistics_officer")
UPLOADER = ("data_officer", "district_medical_officer")
PLANNER = ("district_medical_officer", "logistics_officer", "data_officer")


# --------------------------------------------------------------------------- state
class State:
    lock = threading.Lock()
    ws: Optional[Workspace] = None

    @classmethod
    def get(cls) -> Workspace:
        with cls.lock:
            if cls.ws is None:
                cls.ws = cls._boot()
            return cls.ws

    @classmethod
    def reset(cls) -> None:
        with cls.lock:
            cls.ws = None

    @staticmethod
    def _store() -> EventStore:
        if os.environ.get("TATHYON_DB", "0").strip() == "1":
            from tathyon.persist import SQLiteStore
            data_dir = os.environ.get("TATHYON_DATA_DIR", os.path.join(ROOT, "data"))
            return SQLiteStore(os.environ.get("TATHYON_DB_PATH", os.path.join(data_dir, "tathyon_ledger.sqlite3")))
        return EventStore()

    @classmethod
    def _boot(cls) -> Workspace:
        store = cls._store()
        ws = Workspace.replay(store)  # a restart rebuilds every projection from the ledger
        if not store.events:
            ws.load(ENV_REAL, [], {}, actor="system")
            # Hosts with ephemeral disks (Render free, Cloud Run) start empty: optionally reload a REAL
            # OpenStreetMap registry. Stock data is never auto-loaded.
            for key in filter(None, os.environ.get("TATHYON_AUTOLOAD_OSM", "").split(",")):
                if key in OSM_DISTRICTS and os.path.isfile(osm_path(key)):
                    with open(osm_path(key), encoding="utf-8") as fh:
                        doc = json.load(fh)
                    for f in facilities_from_osm(doc, *OSM_DISTRICTS[key]):
                        if f["facility_id"] not in ws.facilities:
                            ws.register_facility(f, "system:autoload")
        return ws


def workspace() -> Workspace:
    return State.get()


# --------------------------------------------------------------------------- app
app = FastAPI(title="TATHYON", version=VERSION,
              description="Healthcare supply resilience: verify-before-trust decisions on a hash-chained ledger.")
app.add_middleware(HardeningMiddleware)
if os.environ.get("TATHYON_CORS_ORIGINS"):
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=os.environ["TATHYON_CORS_ORIGINS"].split(","),
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.exception_handler(WorkspaceError)
def _workspace_error(_: Request, exc: WorkspaceError) -> JSONResponse:
    return JSONResponse({"error": exc.code, "message": exc.message}, status_code=exc.status)


@app.exception_handler(IntakeError)
def _intake_error(_: Request, exc: IntakeError) -> JSONResponse:
    return JSONResponse({"error": str(exc), "message": "The file was rejected before any row was read."}, 422)


@app.exception_handler(RequestValidationError)
def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"error": "INVALID_REQUEST",
                         "message": "; ".join(f"{'.'.join(map(str, e['loc'][1:]))}: {e['msg']}"
                                              for e in exc.errors()[:5])}, 422)


@app.exception_handler(HTTPException)
def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"error": "HTTP_ERROR", "message": str(exc.detail)}
    return JSONResponse(detail, exc.status_code)


@app.exception_handler(Exception)
def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse({"error": "INTERNAL_ERROR", "message": "Unexpected server error; nothing was changed."}, 500)


@app.get("/", include_in_schema=False)
def index() -> Response:
    path = os.path.join(WEB_DIR, "index.html")
    return FileResponse(path) if os.path.isfile(path) else JSONResponse({"status": "api-only"})


# --------------------------------------------------------------------------- public status
@app.get("/health")
def health() -> dict:
    ws = workspace()
    intact, bad = ws.store.verify_chain()
    return {"status": "ok" if intact else "degraded", "version": VERSION,
            "ledger": {"events": len(ws.store.events), "chain_intact": intact, "first_bad_offset": bad,
                       "durable": os.environ.get("TATHYON_DB", "0") == "1"},
            "environment": ws.environment}


@app.get("/api/system/status")
def system_status(request: Request) -> dict:
    origin = f"{request.url.scheme}://{request.url.netloc}/"
    return {
        "version": VERSION,
        "data_sources": {
            "facility_locations": "REAL_PUBLIC_OSM (OpenStreetMap, ODbL) when loaded; uploaded registries otherwise",
            "stock": "USER_SUPPLIED_UNVERIFIED exports only; no government system is connected",
            "physical_counts": "HUMAN_ATTESTED via field verifiers",
            "beds": "NOT_CONFIGURED — no bed-occupancy source connected",
            "personnel": "NOT_CONFIGURED — no attendance source connected",
            "dvdms_e_aushadhi_api": "NOT_CONFIGURED — file exports only",
            "brics_federation": "NOT_CONFIGURED — interface specification only (docs/federation-interface.md)",
        },
        "gemini": gemini_status(),
        "maps": {**maps_status(), "tiles_3d": tiles3d_status(origin)},
        "identity": {"mode": "SYNTHETIC_DEMO_IDENTITIES" if demo_login_enabled() else "DISABLED",
                     "note": "Signed demo sessions with server-side roles; no identity provider is connected."},
    }


@app.get("/api/config/public")
def public_config() -> dict:
    """The only key sent to the browser is the Maps browser key, which Google requires client-side.
    It must be restricted by HTTP referrer and API in the Cloud Console."""
    return {"maps_browser_key": maps_browser_key() or None, "demo_login": demo_login_enabled(),
            "demo_users": {k: {"display": v["display"], "role": v["role"], "scoped": bool(v.get("scoped"))}
                           for k, v in DEMO_USERS.items()}
            if demo_login_enabled() else {}}


# --------------------------------------------------------------------------- sessions
class SessionIn(BaseModel):
    user: str = Field(min_length=1, max_length=40)
    facility_id: Optional[str] = Field(default=None, max_length=60)


@app.post("/api/auth/session")
def create_session(body: SessionIn, request: Request) -> dict:
    limiter.check(client_key(request), "login", 30)
    if body.facility_id and body.facility_id not in workspace().facilities:
        raise HTTPException(404, {"error": "UNKNOWN_FACILITY", "message": body.facility_id})
    token, p = issue_demo_session(body.user, body.facility_id)
    return {"token": token, "user": p.__dict__}


@app.get("/api/auth/me")
def me(p: Principal = Depends(current_principal)) -> dict:
    return p.__dict__


# --------------------------------------------------------------------------- workspace + intake
@app.get("/api/workspace")
def get_workspace(p: Principal = Depends(current_principal)) -> dict:
    ws = workspace()
    return {**ws.summary(),
            "has_sample_data": any(r.get("provenance") == "SAMPLE" for r in ws.reports.values()),
            "skus": {k: v for k, v in ws.skus.items()},
            "facility_provenance": sorted({f.get("provenance") for f in ws.facilities.values()}),
            "osm_registry_available": any(os.path.isfile(osm_path(k)) for k in OSM_DISTRICTS),
            "states": sorted({f.get("state") for f in ws.facilities.values() if f.get("state")}),
            "facility_districts": sorted({f.get("district") for f in ws.facilities.values() if f.get("district")}),
            "quarantine": ws.quarantine[-50:]}


@app.get("/api/reference/districts")
def reference_districts(p: Principal = Depends(current_principal)) -> dict:
    out = []
    for key, (district, state) in OSM_DISTRICTS.items():
        path = osm_path(key)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        out.append({"key": key, "district": district, "state": state, "facilities": len(doc.get("facilities", [])),
                    "fetched_at": doc.get("fetched_at"), "fetch_status": doc.get("fetch_status"),
                    "attribution": doc.get("attribution")})
    return {"districts": out, "provenance": "REAL_PUBLIC_OSM",
            "note": "OpenStreetMap coverage of Indian health facilities is incomplete; PARTIAL means some map tiles "
                    "timed out during the fetch."}


class OsmIn(BaseModel):
    district_key: str = Field(default="bastar_chhattisgarh", max_length=60)


@app.post("/api/intake/osm-registry")
def load_osm_registry(body: Optional[OsmIn] = None, p: Principal = Depends(current_principal)) -> dict:
    require_roles(p, *UPLOADER)
    key = (body or OsmIn()).district_key
    if key not in OSM_DISTRICTS or not os.path.isfile(osm_path(key)):
        raise HTTPException(404, {"error": "UNKNOWN_DISTRICT", "message": f"one of {sorted(OSM_DISTRICTS)}"})
    district, state = OSM_DISTRICTS[key]
    ws = workspace()
    with open(osm_path(key), encoding="utf-8") as fh:
        doc = json.load(fh)
    added = 0
    with ws.lock:
        for f in facilities_from_osm(doc, district, state):
            if f["facility_id"] not in ws.facilities:
                ws.register_facility(f, p.user_id)
                added += 1
    return {"added": added, "district": district, "state": state, "total_facilities": len(ws.facilities),
            "provenance": "REAL_PUBLIC_OSM", "attribution": doc.get("attribution"),
            "fetched_at": doc.get("fetched_at"), "fetch_status": doc.get("fetch_status"),
            "limits": "OSM confirms a facility exists at a location; tier, ownership, cold chain and stock are "
                      "unknown until an authorised registry or export supplies them."}


class SampleIn(BaseModel):
    district_key: str = Field(max_length=60)


@app.post("/api/intake/sample-dataset")
def load_sample(body: SampleIn, p: Principal = Depends(current_principal)) -> dict:
    """Opt-in SAMPLE stock/bed/staff data on real OSM facilities of one loaded district (badged SAMPLE)."""
    require_roles(p, *UPLOADER)
    if body.district_key not in OSM_DISTRICTS:
        raise HTTPException(404, {"error": "UNKNOWN_DISTRICT", "message": f"one of {sorted(OSM_DISTRICTS)}"})
    from tathyon.sample_dataset import load_sample_dataset
    ws = workspace()
    with ws.lock:
        return load_sample_dataset(ws, OSM_DISTRICTS[body.district_key][0], p.user_id)


@app.post("/api/workspace/reset")
def reset_workspace(p: Principal = Depends(current_principal)) -> dict:
    """Start a fresh workspace. The ledger is append-only: history stays, projections restart empty."""
    require_roles(p, "district_medical_officer")
    ws = workspace()
    with ws.lock:
        ev = ws.load(ENV_REAL, [], {}, actor=p.user_id)
    return {"status": "RESET", "event_id": ev.event_id, "ledger_events": len(ws.store.events)}


class FileIn(BaseModel):
    content: str = Field(min_length=1, max_length=2_000_000)
    source_filename: str = Field(min_length=1, max_length=120)


@app.post("/api/intake/facility-registry")
def upload_facility_registry(body: FileIn, p: Principal = Depends(current_principal)) -> dict:
    require_roles(p, *UPLOADER)
    ws = workspace()
    accepted, quarantined = parse_facility_registry(body.content, body.source_filename)
    with ws.lock:
        for f in accepted:
            ws.register_facility(f, p.user_id)
        from tathyon.schema import EventType
        for q in quarantined:
            ws._emit(EventType.ROW_QUARANTINED, str(q["raw"].get("facility_id") or "UNKNOWN")[:60], "*",
                     {"source": body.source_filename, **q}, p.user_id)
    return {"accepted": len(accepted), "quarantined": quarantined, "provenance": "USER_SUPPLIED_UNVERIFIED"}


@app.post("/api/intake/stock-export")
def upload_stock_export(body: FileIn, p: Principal = Depends(current_principal)) -> dict:
    require_roles(p, *UPLOADER)
    ws = workspace()
    if not ws.facilities:
        raise HTTPException(409, {"error": "NO_FACILITY_REGISTRY",
                                  "message": "Load a facility registry first; rows must match known facilities."})
    report = parse_stock_export(body.content, body.source_filename, known_facilities=set(ws.facilities),
                                known_skus=None, as_of=ws.clock)
    result = ws.ingest_rows(report.accepted, report.quarantined, source=body.source_filename,
                            provenance="USER_SUPPLIED_UNVERIFIED", actor=p.user_id)
    if not ws.baseline and report.accepted:
        ws.record_baseline(p.user_id)  # the "before" for Outcome: projection if nothing is done
    return {**report.summary(), "quarantined": report.quarantined[:100], "accepted_events": result["accepted"][:50],
            "notice": "Accepted rows are UNVERIFIED reports. They become supply only after a physical count."}


# --------------------------------------------------------------------------- state views
@app.get("/api/trust/queue")
def trust_queue(sku: Optional[str] = Query(default=None, max_length=40),
                verifier_hours: int = Query(default=6, ge=1, le=80),
                p: Principal = Depends(current_principal)) -> dict:
    return workspace().trust_queue(sku, verifier_hours)


@app.get("/api/resources")
def resources(sku: Optional[str] = Query(default=None, max_length=40), p: Principal = Depends(current_principal)):
    return {"resources": workspace().all_states(sku)}


@app.get("/api/facilities/{facility_id}")
def get_facility(facility_id: str, p: Principal = Depends(current_principal)) -> dict:
    return facility_detail(workspace(), facility_id)


@app.get("/api/map/layers")
def get_map_layers(sku: Optional[str] = Query(default=None, max_length=40),
                   block: Optional[str] = Query(default=None, max_length=80),
                   tier: Optional[str] = Query(default=None, max_length=40),
                   p: Principal = Depends(current_principal)) -> dict:
    return map_layers(workspace(), sku, block, tier)


@app.get("/api/tasks")
def tasks(p: Principal = Depends(current_principal)) -> dict:
    return {"tasks": sorted(workspace().tasks.values(), key=lambda t: t["status"])}


# --------------------------------------------------------------------------- plans + decisions
class PlanIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)


@app.post("/api/plans")
def propose(body: PlanIn, p: Principal = Depends(current_principal)) -> dict:
    require_roles(p, *PLANNER)
    return workspace().propose_plan(body.sku, actor=p.user_id)


@app.get("/api/plans")
def list_plans(p: Principal = Depends(current_principal)) -> dict:
    ws = workspace()
    return {"plans": [plan_view(ws, pid) for pid in
                      sorted(ws.plans, key=lambda k: (ws.plans[k]["sku"], ws.plans[k]["version"]))]}


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: str, p: Principal = Depends(current_principal)) -> dict:
    ws = workspace()
    if plan_id not in ws.plans:
        raise HTTPException(404, {"error": "PLAN_NOT_FOUND", "message": plan_id})
    return {**ws.plans[plan_id], "feasibility": ws.evaluate_feasibility(plan_id)
            if ws.plans[plan_id]["status"] == "APPROVED" else None}


class DecisionIn(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    option: Optional[str] = Field(default=None, max_length=40)
    rejected_lines: list[str] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=1000)


@app.post("/api/plans/{plan_id}/decision")
def decide(plan_id: str, body: DecisionIn, p: Principal = Depends(current_principal)) -> dict:
    return workspace().decide_plan(plan_id, officer_id=p.user_id, role=p.role, decision=body.decision,
                                   option=body.option, rejected_lines=body.rejected_lines, reason=body.reason)


class BreakGlassIn(BaseModel):
    reason_code: str = Field(min_length=1, max_length=40)
    justification: str = Field(min_length=20, max_length=1000)


@app.post("/api/plans/{plan_id}/lines/{line_id}/break-glass")
def break_glass(plan_id: str, line_id: str, body: BreakGlassIn, p: Principal = Depends(current_principal)):
    return workspace().break_glass(plan_id, line_id, officer_id=p.user_id, role=p.role,
                                   reason_code=body.reason_code, justification=body.justification)


@app.get("/api/break-glass/reasons")
def break_glass_reasons(p: Principal = Depends(current_principal)) -> dict:
    return {"reasons": BREAK_GLASS_REASONS}


@app.post("/api/plans/{plan_id}/lines/{line_id}/release")
def release(plan_id: str, line_id: str, p: Principal = Depends(current_principal)) -> dict:
    return workspace().confirm_contingent_line(plan_id, line_id, actor=p.user_id, role=p.role)


@app.post("/api/plans/{plan_id}/lines/{line_id}/dispatch")
def dispatch(plan_id: str, line_id: str, p: Principal = Depends(current_principal)) -> dict:
    return workspace().dispatch(plan_id, line_id, actor=p.user_id, role=p.role)


@app.get("/api/plans/{plan_id}/sor-payload")
def sor_payload(plan_id: str, p: Principal = Depends(current_principal)) -> dict:
    """A staged DVDMS-shaped transfer voucher. It is NOT sent anywhere; no DVDMS API is configured."""
    ws = workspace()
    plan = ws.plans.get(plan_id)
    if plan is None or plan["status"] not in ("APPROVED", "REPLAN_REQUIRED", "SUPERSEDED"):
        raise HTTPException(409, {"error": "PLAN_NOT_APPROVED", "message": "Only approved plans can be staged."})
    lines = [l for l in plan["lines"] if l["status"] in ("APPROVED", "DISPATCHED", "DELAYED", "RECEIVED",
                                                          "RELEASED_BREAK_GLASS")]
    return {"status": "STAGED_NOT_SENT", "target_system": "DVDMS/e-Aushadhi (file drop)", "plan_id": plan_id,
            "approved_by": plan.get("decision", {}).get("officer_id"),
            "vouchers": [{"line_id": l["line_id"], "from": l["from_facility"], "to": l["to_facility"],
                          "item_code": l["sku"], "quantity": l["qty"]} for l in lines]}


# --------------------------------------------------------------------------- verification + shipments
class AttestIn(BaseModel):
    facility_id: str = Field(min_length=1, max_length=60)
    sku: str = Field(min_length=1, max_length=40)
    present_qty: float = Field(ge=0, le=10_000_000)
    usable_qty: float = Field(ge=0, le=10_000_000)
    expired_qty: float = Field(default=0.0, ge=0, le=10_000_000)
    evidence_ref: str = Field(default="", max_length=200)
    seconds_spent: float = Field(default=0.0, ge=0, le=86_400)
    client_event_id: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


@app.post("/api/verify/attest")
def attest(body: AttestIn, p: Principal = Depends(current_principal)) -> dict:
    if p.facility_id and p.facility_id == body.facility_id:
        raise HTTPException(403, {"error": "ATTESTOR_IS_CUSTODIAN",
                                  "message": "Staff of this facility cannot verify its own stock."})
    return workspace().submit_attestation(
        body.facility_id, body.sku, attester_id=p.user_id, attester_role=p.role, present_qty=body.present_qty,
        usable_qty=body.usable_qty, expired_qty=body.expired_qty, evidence_ref=body.evidence_ref,
        seconds_spent=body.seconds_spent, client_event_id=body.client_event_id)


@app.get("/api/shipments")
def shipments(p: Principal = Depends(current_principal)) -> dict:
    return {"shipments": list(workspace().shipments.values()),
            "tracking": "Status updates entered by people. No GPS or live vehicle tracking is connected."}


class DelayIn(BaseModel):
    hours: float = Field(gt=0, le=240)
    reason: str = Field(min_length=3, max_length=300)


@app.post("/api/shipments/{shipment_id}/delay")
def delay(shipment_id: str, body: DelayIn, p: Principal = Depends(current_principal)) -> dict:
    require_roles(p, *DISPATCHER)
    return workspace().delay_shipment(shipment_id, body.hours, body.reason, p.user_id)


class ReceiveIn(BaseModel):
    received_qty: float = Field(ge=0, le=10_000_000)
    damaged_qty: float = Field(default=0.0, ge=0, le=10_000_000)
    notes: str = Field(default="", max_length=500)


@app.post("/api/shipments/{shipment_id}/receive")
def receive(shipment_id: str, body: ReceiveIn, p: Principal = Depends(current_principal)) -> dict:
    ws = workspace()
    sh = ws.shipments.get(shipment_id)
    if sh and p.facility_id and p.facility_id != sh["to_facility"]:
        raise HTTPException(403, {"error": "NOT_RECIPIENT_FACILITY",
                                  "message": "Only the receiving facility can confirm receipt."})
    return ws.receive(shipment_id, received_qty=body.received_qty, damaged_qty=body.damaged_qty,
                      receiver_id=p.user_id, receiver_role=p.role, notes=body.notes)


@app.post("/api/replan/check")
def replan_check(p: Principal = Depends(current_principal)) -> dict:
    require_roles(p, *PLANNER)
    return {"flagged": workspace().check_feasibility(actor=p.user_id)}


# --------------------------------------------------------------------------- outcome + audit
@app.get("/api/outcome")
def outcome(p: Principal = Depends(current_principal)) -> dict:
    return workspace().outcome_summary()


@app.get("/api/events")
def events(limit: int = Query(default=100, ge=1, le=1000), event_type: Optional[str] = Query(default=None),
           facility_id: Optional[str] = Query(default=None), p: Principal = Depends(current_principal)) -> dict:
    ws = workspace()
    intact, bad = ws.store.verify_chain()
    return {"total_events": len(ws.store.events), "chain_intact": intact, "first_bad_offset": bad,
            "events": search_events(ws, event_type, facility_id, None, None, limit)}


@app.get("/api/events/{event_id}")
def event(event_id: str, p: Principal = Depends(current_principal)) -> dict:
    return explain_event(workspace(), event_id)


def _csv_cell(value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


@app.get("/api/events.csv")
def export_events(p: Principal = Depends(current_principal)) -> Response:
    ws = workspace()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["offset", "event_id", "event_type", "occurred_at", "recorded_at", "actor", "facility_id", "sku",
                "prev_hash", "hash", "payload"])
    for e in ws.store.events:
        w.writerow([_csv_cell(v) for v in (e.offset, e.event_id, e.event_type.value, e.occurred_at, e.recorded_at,
                                           e.actor, e.facility_id, e.resource_key, e.prev_hash, e.hash, e.payload)])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=tathyon_ledger.csv"})


# --------------------------------------------------------------------------- agents
LANGUAGES = {"en": "English", "hi": "Hindi", "bn": "Bengali", "te": "Telugu", "mr": "Marathi", "ta": "Tamil",
             "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "or": "Odia", "pa": "Punjabi", "ur": "Urdu"}


class AgentIn(BaseModel):
    request: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="en", max_length=5)


@app.get("/api/agents")
def agents(p: Principal = Depends(current_principal)) -> dict:
    ws = workspace()
    return {"agents": [build_agent(n, ws).describe() for n in AGENT_CLASSES], "gemini": gemini_status(),
            "label": "AI suggestion — human decides."}


@app.post("/api/agents/{name}/run")
def run_agent(name: str, body: AgentIn, request: Request, p: Principal = Depends(current_principal)) -> dict:
    if name not in AGENT_CLASSES:
        raise HTTPException(404, {"error": "UNKNOWN_AGENT", "message": f"one of {sorted(AGENT_CLASSES)}"})
    limiter.check(client_key(request), "agent", 30)
    if name == "intake_agent":
        require_roles(p, "field_verifier", "data_officer", "district_medical_officer")
    if body.language not in LANGUAGES:
        raise HTTPException(422, {"error": "UNSUPPORTED_LANGUAGE", "message": f"one of {sorted(LANGUAGES)}"})
    ws = workspace()
    with ws.lock:
        agent = build_agent(name, ws)
        agent.language = LANGUAGES[body.language]
        return agent.run(body.request, p.user_id)


class ImageIn(BaseModel):
    image_base64: str = Field(min_length=10, max_length=5_600_000)
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]


@app.post("/api/agents/intake_agent/image")
def intake_image(body: ImageIn, request: Request, p: Principal = Depends(current_principal)) -> dict:
    require_roles(p, "field_verifier", "data_officer", "district_medical_officer")
    limiter.check(client_key(request), "agent", 30)
    try:
        image = base64.b64decode(body.image_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(422, {"error": "INVALID_BASE64", "message": "Image is not valid base64."}) from exc
    ws = workspace()
    with ws.lock:
        return build_agent("intake_agent", ws).run_image(image, body.mime_type, p.user_id)  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- beds / personnel observations
class ResourceObservation(BaseModel):
    facility_id: str = Field(min_length=1, max_length=120)
    resource_module: Literal["MEDICINES", "BEDS", "PERSONNEL"]
    resource_key: str = Field(min_length=1, max_length=120)
    metric: str = Field(min_length=1, max_length=80)
    value: float
    unit: str = Field(min_length=1, max_length=40)
    observed_at: str = Field(min_length=10, max_length=40)


class ResourceReportIn(BaseModel):
    source_report_id: str = Field(min_length=1, max_length=160)
    observations: list[ResourceObservation] = Field(min_length=1, max_length=5000)


@app.post("/api/intake/resource-report")
def resource_report(body: ResourceReportIn, p: Principal = Depends(current_principal)) -> dict:
    """Bed occupancy / staff attendance / stock observations from an uploaded report.
    No bed or attendance system is connected; values are USER_SUPPLIED_UNVERIFIED."""
    require_roles(p, *UPLOADER)
    from tathyon.observations import ADVISORY_LABEL, record_observations
    rows = record_observations(workspace(), body.source_report_id, [o.model_dump() for o in body.observations],
                               provenance="USER_SUPPLIED_UNVERIFIED", actor=p.user_id)
    return {"status": "STORED_UNVERIFIED_OBSERVATIONS", "provenance": "USER_SUPPLIED_UNVERIFIED",
            "verified_state_changed": False, "observations": rows, "advisory_label": ADVISORY_LABEL}


@app.get("/api/trust/anomalies")
def anomalies(limit: int = Query(default=50, ge=1, le=200), p: Principal = Depends(current_principal)) -> dict:
    from tathyon.schema import EventType
    ws = workspace()
    out = [{"event_id": e.event_id, "facility_id": e.facility_id, "resource_key": e.resource_key, **e.payload}
           for e in reversed(ws.store.events)
           if e.event_type == EventType.DECISION_MADE and e.payload.get("category") == "STATISTICAL_ANOMALY_ADVISORY"]
    return {"count": len(out[:limit]), "advisories": out[:limit], "advisory_label": "Statistical anomaly signal — human decides"}


@app.get("/api/model/status")
def model_status(p: Principal = Depends(current_principal)) -> dict:
    """The trust-scorer ML model is evaluated offline on a synthetic benchmark only; it is not served."""
    from tathyon.verify import default_trust_scorer
    served = default_trust_scorer() is not None
    return {"trust_scorer_served": served,
            "status": "APPROVED_REAL_MODEL_SERVED" if served else "NOT_SERVED",
            "reason": None if served else "No model trained and approved on real, human-attested labels exists. "
                      "The operational queue uses transparent rules (count age, report staleness, district outlier, "
                      "units at stake) and an exact knapsack instead.",
            "forecasting": "Period-average consumption from uploaded exports; no multi-period history loaded.",
            "offline_benchmark": "docs/MODEL_BENCHMARK.md (SYNTHETIC benchmark; not operational evidence)"}
