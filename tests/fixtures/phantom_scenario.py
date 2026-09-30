"""TEST FIXTURE ONLY — synthetic Phantom Trap scenario. Never loaded by the application.

The application runs only on uploaded exports, the public OpenStreetMap
facility registry, and human counts. This fixture exists so the engine's
closed loop can be tested deterministically.

Everything here is SYNTHETIC: facility names, coordinates, stock, consumption,
counts and the clock. Facility names are deliberately generic so no real PHC is
implied to hold phantom stock. The stock export below is fed through the same
intake pipeline a user upload goes through.

Each scene is a real backend transition on the workspace (see `SCENES`); the UI
only calls `run_scene(n)` and renders the resulting state. Nothing is scripted
on the frontend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from tathyon.intake_pipeline import parse_stock_export
from tathyon.workspace import ENV_SYNTHETIC, Workspace, WorkspaceError

SCENARIO_ID = "PHANTOM_TRAP_v2"
SCENARIO_CLOCK = datetime(2026, 9, 28, 6, 0, tzinfo=timezone.utc)
SKU = "OXY-10"

SKUS = {
    SKU: {"name": "Oxytocin 10 IU injection", "unit": "ampoule", "essentiality": 3, "cold_chain": True,
          "provenance": "SYNTHETIC"},
    "ORS-01": {"name": "Oral rehydration salts sachet", "unit": "sachet", "essentiality": 2, "cold_chain": False,
               "provenance": "SYNTHETIC"},
}

# Synthetic placement around a district HQ. Coordinates are illustrative only.
FACILITIES = [
    {"facility_id": "DWH-01", "name": "District Drug Warehouse (synthetic)", "tier": "DISTRICT_WAREHOUSE",
     "block": "HQ", "lat": 19.070, "lon": 82.030, "custodian_id": "cust-dwh"},
    {"facility_id": "PHC-Y", "name": "PHC Y — North Block (synthetic)", "tier": "PHC",
     "block": "North", "lat": 19.340, "lon": 81.650, "custodian_id": "cust-y"},
    {"facility_id": "PHC-A", "name": "PHC A — North Block (synthetic)", "tier": "PHC",
     "block": "North", "lat": 19.475, "lon": 81.745, "custodian_id": "cust-a"},
    {"facility_id": "PHC-X", "name": "PHC X — Central Block (synthetic)", "tier": "PHC",
     "block": "Central", "lat": 19.250, "lon": 81.888, "custodian_id": "cust-x"},
    {"facility_id": "CHC-Z", "name": "CHC Z — West Block (synthetic)", "tier": "CHC",
     "block": "West", "lat": 19.160, "lon": 81.460, "custodian_id": "cust-z"},
    {"facility_id": "CHC-W", "name": "CHC W — South Block (synthetic)", "tier": "CHC",
     "block": "South", "lat": 18.620, "lon": 82.410, "custodian_id": "cust-w"},
    {"facility_id": "PHC-V", "name": "PHC V — North Block, no cold chain (synthetic)", "tier": "PHC",
     "block": "North", "lat": 19.385, "lon": 81.793, "custodian_id": "cust-v", "has_cold_chain": False},
    {"facility_id": "PHC-U", "name": "PHC U — East Block (synthetic)", "tier": "PHC",
     "block": "East", "lat": 19.430, "lon": 82.220, "custodian_id": "cust-u"},
]

# A DVDMS-shaped export (synthetic). period_days=30; issues/period = daily use.
STOCK_EXPORT = """FacilityCode,DrugCode,AvailableStock,IssuedQty,ReceiptQty,Date,period_days,BatchNo,ExpiryDate
DWH-01,OXY-10,0,0,0,2026-09-27,30,B-DWH-0,2027-06-30
PHC-Y,OXY-10,48,720,0,2026-09-28,30,B-Y-11,2027-03-31
PHC-A,OXY-10,30,360,0,2026-09-28,30,B-A-07,2027-02-28
PHC-X,OXY-10,1400,180,0,2026-09-26,30,B-X-02,2027-04-30
CHC-Z,OXY-10,420,300,0,2026-09-26,30,B-Z-19,2027-05-31
CHC-W,OXY-10,600,360,0,2026-09-27,30,B-W-04,2027-05-31
PHC-V,OXY-10,300,150,0,2026-09-27,30,B-V-01,2027-01-31
PHC-U,OXY-10,520,300,0,2026-09-10,30,B-U-03,2027-03-31
PHC-Y,ORS-01,900,600,0,2026-09-28,30,B-Y-ORS,2027-12-31
PHC-X,ORS-01,,90,0,2026-09-26,30,B-X-ORS,2027-12-31
PHC-Q,OXY-10,50,90,0,2026-09-28,30,B-Q-01,2027-03-31
"""

# Prior human counts already on record before the scenario opens (synthetic).
PRIOR_COUNTS = [  # (facility, hours before scenario clock, present, usable)
    ("CHC-Z", 48, 440, 440),
    ("CHC-W", 24, 612, 612),
    ("PHC-V", 30, 305, 305),
    ("PHC-X", 41 * 24, 1450, 1440),
    ("PHC-U", 20 * 24, 300, 300),
]

DEMO_USERS = {
    "dmo": ("dr-meera-dmo", "district_medical_officer"),
    "verifier": ("fv-anil", "field_verifier"),
    "incharge_y": ("mo-phc-y", "facility_incharge"),
}


def load(ws: Workspace) -> dict:
    from datetime import timedelta
    ws.load(ENV_SYNTHETIC, [], SKUS, scenario=SCENARIO_ID, clock=SCENARIO_CLOCK - timedelta(days=41),
            actor="scenario_loader")
    for f in FACILITIES:
        ws.register_facility({**f, "provenance": "SYNTHETIC"}, "scenario_loader")
    report = parse_stock_export(STOCK_EXPORT, "phantom_trap_synthetic_export.csv",
                                known_facilities=set(ws.facilities), known_skus=set(ws.skus),
                                as_of=SCENARIO_CLOCK)
    # Replay history in time order: old counts first, then the current export.
    for fid, hours_before, present, usable in sorted(PRIOR_COUNTS, key=lambda c: -c[1]):
        _advance_to(ws, SCENARIO_CLOCK - timedelta(hours=hours_before))
        row = next(r for r in report.accepted if r.facility_id == fid and r.sku == SKU)
        _seed_claim(ws, row, fid, reported_qty=present)
        ws.submit_attestation(fid, SKU, attester_id="fv-prior", attester_role="field_verifier",
                              present_qty=present, usable_qty=usable, provenance="SYNTHETIC",
                              evidence_ref=f"synthetic-count-sheet-{fid}")
    _advance_to(ws, SCENARIO_CLOCK)
    ws.ingest_rows(report.accepted, report.quarantined, source="phantom_trap_synthetic_export.csv",
                   provenance="SYNTHETIC", actor="scenario_loader")
    ws.record_baseline("scenario_loader")
    return report.summary()


def _advance_to(ws: Workspace, target: datetime) -> None:
    while ws.clock < target:
        hours = min((target - ws.clock).total_seconds() / 3600, 24 * 30)
        ws.advance_clock(hours, "scenario_loader", "Replaying synthetic history")


def _seed_claim(ws: Workspace, row, fid: str, reported_qty: float) -> None:
    """A count references a claim: seed the report that stood at that time (consistent with the count)."""
    from tathyon.intake_pipeline import StockRow
    earlier = StockRow(row.row_number, fid, row.sku, reported_qty, row.daily_consumption,
                       ws.clock.isoformat(), row.batch_no, row.expiry_date, 0.0, row.issues, row.period_days)
    ws.ingest_rows([earlier], [], source="synthetic_prior_export", provenance="SYNTHETIC", actor="scenario_loader")


def _latest_plan(ws: Workspace) -> dict:
    plans = sorted(ws.plans.values(), key=lambda p: p["version"])
    if not plans:
        raise WorkspaceError("SCENE_ORDER", "Run the earlier scenes first.", 409)
    return plans[-1]


def _scene_propose(ws: Workspace) -> dict:
    return ws.propose_plan(SKU, actor="tathyon_planner", trigger="RUNWAY_BELOW_ALERT")


def _scene_approve(ws: Workspace) -> dict:
    plan = _latest_plan(ws)
    uid, role = DEMO_USERS["dmo"]
    ws.decide_plan(plan["plan_id"], officer_id=uid, role=role, decision="APPROVE",
                   reason="Recipient runway allows a count at PHC X before committing the long haul from CHC W.")
    for line in ws.plans[plan["plan_id"]]["lines"]:
        if line["status"] == "APPROVED":
            ws.dispatch(plan["plan_id"], line["line_id"], actor=uid, role=role)
    return ws.plans[plan["plan_id"]]


def _scene_verify(ws: Workspace) -> dict:
    ws.advance_clock(3.0, "scenario_driver", "Verifier travels to PHC X")
    uid, role = DEMO_USERS["verifier"]
    return ws.submit_attestation("PHC-X", SKU, attester_id=uid, attester_role=role, present_qty=95,
                                 usable_qty=60, expired_qty=35, seconds_spent=1260,
                                 evidence_ref="synthetic-count-sheet-phc-x-0928", provenance="SYNTHETIC")


def _scene_replan(ws: Workspace) -> dict:
    parent = _latest_plan(ws)
    plan = ws.propose_plan(SKU, actor="tathyon_planner", parent_plan_id=parent["plan_id"],
                           trigger="REPLAN_REQUIRED")
    uid, role = DEMO_USERS["dmo"]
    ws.decide_plan(plan["plan_id"], officer_id=uid, role=role, decision="APPROVE",
                   reason="PHC X count disproved its report; use verified CHC W stock.")
    for line in ws.plans[plan["plan_id"]]["lines"]:
        if line["status"] == "APPROVED":
            ws.dispatch(plan["plan_id"], line["line_id"], actor=uid, role=role)
    return ws.plans[plan["plan_id"]]


def _scene_receive(ws: Workspace) -> dict:
    ws.advance_clock(2.0, "scenario_driver", "Shipments in transit")
    uid, role = DEMO_USERS["incharge_y"]
    results = []
    delayed_one = False
    for sh in sorted(ws.shipments.values(), key=lambda s: (s["dispatched_at"], s["line_id"])):
        if sh["status"] not in ("IN_TRANSIT", "DELAYED"):
            continue
        if sh["from_facility"] == "CHC-W" and not delayed_one:
            delayed_one = True
            ws.delay_shipment(sh["shipment_id"], 3.0, "Road closure on synthetic route; detour", "logistics-desk")
            ws.advance_clock(4.0, "scenario_driver", "Delayed vehicle arrives")
            damaged = min(20.0, sh["qty"])
            results.append(ws.receive(sh["shipment_id"], received_qty=sh["qty"], damaged_qty=damaged,
                                      receiver_id=uid, receiver_role=role,
                                      notes="Synthetic: 20 ampoules broken, cold box seal intact"))
        else:
            results.append(ws.receive(sh["shipment_id"], received_qty=sh["qty"], damaged_qty=0.0,
                                      receiver_id=uid if sh["to_facility"] == "PHC-Y" else "mo-phc-a",
                                      receiver_role=role))
    return {"receipts": results}


def _scene_close(ws: Workspace) -> dict:
    parent = _latest_plan(ws)
    if parent["status"] != "REPLAN_REQUIRED":
        return {"status": "NO_REPLAN_NEEDED", "plan_id": parent["plan_id"]}
    try:
        return ws.propose_plan(SKU, actor="tathyon_planner", parent_plan_id=parent["plan_id"],
                               trigger="PARTIAL_RECEIPT")
    except WorkspaceError as exc:
        return {"status": exc.code, "message": exc.message}


SCENES: list[tuple[str, str, Callable[[Workspace], dict]]] = [
    ("PROPOSE", "Shortage detected; TATHYON generates options and recommends verifying PHC X first", _scene_propose),
    ("APPROVE", "District medical officer approves; lines from PHC X are held until a physical count", _scene_approve),
    ("VERIFY", "Field verifier counts PHC X: the reported surplus does not exist", _scene_verify),
    ("REPLAN", "Contingent line invalidated; replan with verified CHC W, human approves", _scene_replan),
    ("RECEIVE", "Shipments arrive; one is delayed and partially damaged", _scene_receive),
    ("CLOSE", "Outcome recorded; the monitor re-plans against the partial receipt", _scene_close),
]


def run_scene(ws: Workspace, index: int) -> dict:
    if not 0 <= index < len(SCENES):
        raise WorkspaceError("UNKNOWN_SCENE", f"scene must be 0..{len(SCENES) - 1}", 404)
    if ws.scenario != SCENARIO_ID:
        raise WorkspaceError("SCENARIO_NOT_LOADED", "Load the Phantom Trap scenario first.", 409)
    done = [e for e in ws.store.events if e.payload.get("category") == "SCENE_COMPLETED"]
    if len(done) != index:
        raise WorkspaceError("SCENE_ORDER", f"Next scene is {len(done)}; scenes run in order.", 409)
    name, title, fn = SCENES[index]
    result = fn(ws)
    from tathyon.schema import EventType
    ws._emit(EventType.DECISION_MADE, "DISTRICT", SKU,
             {"category": "SCENE_COMPLETED", "scene": index, "name": name, "title": title}, "scenario_driver")
    return {"scene": index, "name": name, "title": title, "result": result}
