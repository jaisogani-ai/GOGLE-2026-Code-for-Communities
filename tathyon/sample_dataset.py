"""Opt-in SAMPLE dataset for demonstration on REAL facility locations.

The hackathon rules allow "real or realistic data — public datasets, sample data".
This module produces realistic SAMPLE stock exports, prior counts and bed/staff
reports for OpenStreetMap facilities already loaded for one district. It is
loaded only when an authorised user clicks "Load sample dataset", and every
value it creates carries provenance SAMPLE (rendered as a red SAMPLE badge).

Facility locations and names are real (OpenStreetMap). The stock, counts, bed
and staff figures are NOT real and say nothing about those facilities.

Consumption magnitudes are order-of-magnitude assumptions for a small rural
facility, not estimates for any named facility. Deterministic (seeded).
"""
from __future__ import annotations

import csv
import io
import random
from datetime import datetime, timedelta, timezone

from .intake_pipeline import parse_stock_export
from .observations import record_observations
from .workspace import Workspace, WorkspaceError

SEED = 20260930
SAMPLE_SKUS = [  # code, name, cold chain, VED, daily issues range at a small facility (assumption)
    ("OXY-10", "Oxytocin 10 IU injection", True, "V", (4, 12)),
    ("ORS-01", "Oral rehydration salts sachet", False, "E", (20, 60)),
    ("AMX-500", "Amoxicillin 500 mg capsule", False, "E", (40, 120)),
]
ROLES = ("RECIPIENT", "RECIPIENT", "RECIPIENT", "VERIFIED_DONOR", "VERIFIED_DONOR", "VERIFIED_DONOR",
         "SUSPECT_DONOR", "SUSPECT_DONOR", "NORMAL", "NORMAL", "NORMAL", "NORMAL")
SAMPLE_VERIFIER = "sample-prior-verifier"
MAX_FACILITIES = len(ROLES)


def _pick_facilities(ws: Workspace, district: str) -> list[dict]:
    fac = [f for f in ws.facilities.values() if f.get("district") == district and f.get("provenance") == "REAL_PUBLIC_OSM"]
    if len(fac) < 6:
        raise WorkspaceError("DISTRICT_NOT_LOADED", f"Load the OpenStreetMap registry for {district} first.", 409)
    ranked = sorted(fac, key=lambda f: ({"HOSPITAL": 0, "CLINIC": 1}.get(f["tier"], 2), f["facility_id"]))
    return ranked[:MAX_FACILITIES]


def load_sample_dataset(ws: Workspace, district: str, actor: str) -> dict:
    if any(r.get("provenance") == "SAMPLE" for r in ws.reports.values()):
        raise WorkspaceError("SAMPLE_ALREADY_LOADED", "Sample data is already in this workspace.", 409)
    rng = random.Random(f"{SEED}:{district}")
    chosen = _pick_facilities(ws, district)
    now = ws.clock
    today = now.date().isoformat()
    rows, prior = [], []
    for f, role in zip(chosen, ROLES):
        if f.get("has_cold_chain") is None:  # OSM does not say; the sample assumes it, on the ledger
            ws.register_facility({**f, "has_cold_chain": True, "cold_chain_provenance": "SAMPLE_ASSUMPTION"}, actor)
        for code, name, cold, ved, (lo, hi) in SAMPLE_SKUS:
            daily = rng.randint(lo, hi)
            if role == "RECIPIENT":
                stock = round(daily * rng.uniform(1.6, 3.0))
            elif role == "VERIFIED_DONOR":
                # verified cold-chain stock is scarce in the sample; other medicines are comfortable
                stock = round(daily * (rng.uniform(19, 24) if cold else rng.uniform(45, 70)))
                prior.append((f["facility_id"], code, stock + daily * 2, rng.uniform(20, 44)))
            elif role == "SUSPECT_DONOR":
                stock = round(daily * rng.uniform(150, 260))
                prior.append((f["facility_id"], code, round(stock * 0.9), rng.uniform(35 * 24, 50 * 24)))
            else:
                stock = round(daily * rng.uniform(14, 35))
            rows.append({"FacilityCode": f["facility_id"], "DrugCode": code, "DrugName": name,
                         "AvailableStock": stock, "IssuedQty": daily * 30, "ReceiptQty": 0, "Date": today,
                         "period_days": 30, "cold_chain": str(cold).lower(), "VED": ved})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    report = parse_stock_export(buf.getvalue(), f"sample_{district.lower()}_stock.csv",
                                known_facilities=set(ws.facilities), as_of=now)
    _record_prior_counts(ws, report, prior, actor)
    ws.ingest_rows(report.accepted, report.quarantined, source=report.source_filename, provenance="SAMPLE", actor=actor)
    if not ws.baseline:
        ws.record_baseline(actor)
    obs = _bed_and_staff_reports(ws, chosen, rng, now, actor)
    return {"district": district, "facilities": len(chosen), "stock_rows": len(report.accepted),
            "prior_counts": len(prior), "bed_staff_observations": obs, "provenance": "SAMPLE",
            "notice": "Facility locations are real (OpenStreetMap). Stock, counts, beds and staff figures are "
                      "SAMPLE data for demonstration and describe no real facility."}


def _record_prior_counts(ws: Workspace, report, prior: list, actor: str) -> None:
    """Earlier counts: a matching earlier report, then the count, stamped in the past (SAMPLE only)."""
    by_key = {(r.facility_id, r.sku): r for r in report.accepted}
    for fid, sku, qty, hours_ago in prior:
        row = by_key[(fid, sku)]
        earlier = type(row)(row.row_number, fid, sku, float(qty), row.daily_consumption, ws.clock.isoformat(),
                            row.batch_no, row.expiry_date, 0.0, row.issues, row.period_days,
                            row.sku_name, row.cold_chain, row.ved)
        ws.ingest_rows([earlier], [], source="sample_prior_report", provenance="SAMPLE", actor=actor)
        ws.submit_attestation(fid, sku, attester_id=SAMPLE_VERIFIER, attester_role="field_verifier",
                              present_qty=float(qty), usable_qty=float(qty), provenance="SAMPLE",
                              observed_at=ws.clock - timedelta(hours=hours_ago), evidence_ref="sample")


def _bed_and_staff_reports(ws: Workspace, chosen: list[dict], rng: random.Random, now: datetime, actor: str) -> int:
    """10 days of bed occupancy and staff attendance; one facility shows an unusual last day."""
    total = 0
    odd = chosen[0]["facility_id"]
    for day in range(10, -1, -1):
        at = (now - timedelta(days=day)).replace(hour=9, minute=0, second=0, microsecond=0)
        batch = []
        for f in chosen:
            beds = 30 if f["tier"] == "HOSPITAL" else 6
            occupied = min(beds, max(0, round(beds * rng.uniform(0.5, 0.8))))
            staff_hours = rng.uniform(7.0, 8.0) * (3 if f["tier"] == "HOSPITAL" else 1)
            if day == 0 and f["facility_id"] == odd:
                occupied, staff_hours = beds, staff_hours * 0.25
            batch += [
                {"facility_id": f["facility_id"], "resource_module": "BEDS", "resource_key": "GENERAL",
                 "metric": "occupied_beds", "value": occupied, "unit": "beds", "observed_at": at.isoformat()},
                {"facility_id": f["facility_id"], "resource_module": "PERSONNEL", "resource_key": "NURSING",
                 "metric": "present_hours", "value": round(staff_hours, 1), "unit": "hours",
                 "observed_at": at.isoformat()},
            ]
        total += len(record_observations(ws, f"sample-beds-staff-{at.date().isoformat()}", batch,
                                         provenance="SAMPLE", actor=actor))
    return total
