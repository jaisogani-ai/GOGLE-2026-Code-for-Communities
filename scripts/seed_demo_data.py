"""Reproducible data seed pipeline for TATHYON.

Executes the complete 10-step healthcare supply chain pipeline:
1. create/import facilities (real OpenStreetMap facilities)
2. create resources (essential medicine SKUs)
3. create stock observations (reported stock from registry)
4. create consumption observations (daily issues over 30-day periods)
5. create verification observations (physical attestations by field verifiers)
6. create shortage/risk cases (facilities with low runway)
7. create donor candidates (facilities with verified surplus above safety stock)
8. create allocation opportunities (CP-SAT multi-line plan proposal)
9. create shipments (DMO approval and dispatch to IN_TRANSIT)
10. create receipts/outcomes (recipient verification, variance, and ledger outcome)

Can be executed standalone:
    python scripts/seed_demo_data.py [--db-path data/tathyon_ledger.sqlite3]
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tathyon.intake_pipeline import facilities_from_osm, parse_stock_export
from tathyon.persist import SQLiteStore
from tathyon.sample_dataset import load_sample_dataset
from tathyon.store import EventStore
from tathyon.workspace import ENV_REAL, Workspace

REFERENCE_DIR = os.path.join(ROOT, "reference")
OSM_DISTRICTS = {
    "bastar_chhattisgarh": ("Bastar", "Chhattisgarh"),
    "gaya_bihar": ("Gaya", "Bihar"),
    "nandurbar_maharashtra": ("Nandurbar", "Maharashtra"),
    "kalahandi_odisha": ("Kalahandi", "Odisha"),
    "varanasi_uttar_pradesh": ("Varanasi", "Uttar Pradesh"),
}


def seed_pipeline(store: EventStore | None = None, actor: str = "system:seed") -> Workspace:
    """Runs the genuine 10-step data pipeline into an EventStore."""
    if store is None:
        store = EventStore()
    ws = Workspace(store=store)
    ws.load(ENV_REAL, [], {}, actor=actor)

    # 1. create/import facilities from legitimate OpenStreetMap public data
    print("Step 1: Importing real OpenStreetMap health facilities...")
    bastar_file = os.path.join(REFERENCE_DIR, "osm_bastar_chhattisgarh_facilities.json")
    if os.path.isfile(bastar_file):
        with open(bastar_file, encoding="utf-8") as fh:
            doc = json.load(fh)
        bastar_facs = facilities_from_osm(doc, "Bastar", "Chhattisgarh")
        for f in bastar_facs:
            if f["facility_id"] not in ws.facilities:
                ws.register_facility(f, actor)
        print(f"  Loaded {len(bastar_facs)} facilities for Bastar, Chhattisgarh.")

    gaya_file = os.path.join(REFERENCE_DIR, "osm_gaya_bihar_facilities.json")
    if os.path.isfile(gaya_file):
        with open(gaya_file, encoding="utf-8") as fh:
            doc = json.load(fh)
        gaya_facs = facilities_from_osm(doc, "Gaya", "Bihar")
        for f in gaya_facs:
            if f["facility_id"] not in ws.facilities:
                ws.register_facility(f, actor)
        print(f"  Loaded {len(gaya_facs)} facilities for Gaya, Bihar.")

    # 2. create resources, 3. stock observations, 4. consumption observations, 5. verification observations
    print("Steps 2-5: Seeding essential commodities, stock, consumption, and physical attestations...")
    res = load_sample_dataset(ws, "Bastar", actor=actor)
    print(f"  Populated {res['facilities']} facilities, {res['stock_rows']} stock rows, {res['prior_counts']} counts.")

    # 6. create shortage/risk cases & 7. create donor candidates are derived deterministically
    queue = ws.trust_queue(sku="OXY-10")
    shortage_count = sum(1 for r in queue["rows"] if r.get("is_recipient"))
    donor_count = sum(1 for r in queue["rows"] if r.get("verification_state") == "VERIFIED" and (r.get("usable_estimate") or 0) > 50)
    print(f"Step 6 & 7: Trust queue evaluated: {shortage_count} shortage recipient(s), {donor_count} verified donor candidate(s).")

    # 8. create allocation opportunities (CP-SAT multi-line plan proposal)
    print("Step 8: Proposing cold-chain transfer plan via OR-Tools CP-SAT optimizer...")
    plan = ws.propose_plan(sku="OXY-10", actor=actor)
    print(f"  Plan {plan['plan_id']} generated with {len(plan['lines'])} rebalance line(s).")

    # 9. create shipments (DMO approval & dispatch)
    print("Step 9: Approving plan and dispatching line to create active in-transit shipment...")
    dec = ws.decide_plan(
        plan["plan_id"],
        officer_id="dr-meera-dmo",
        role="district_medical_officer",
        decision="APPROVE",
        option="TRANSFER_VERIFIED_NOW",
        reason="Verified cold-chain dispatch approved for immediate shortage relief.",
    )
    approved_lines = [l for l in dec.get("lines", []) if l.get("status") == "APPROVED"]
    shipment_id = None
    if approved_lines:
        line = approved_lines[0]
        sh = ws.dispatch(
            plan_id=plan["plan_id"],
            line_id=line["line_id"],
            actor="dr-meera-dmo",
            role="district_medical_officer",
        )
        shipment_id = sh["shipment_id"]
        print(f"  Shipment {shipment_id} dispatched: {sh['qty']} units from {sh['from_facility']} -> {sh['to_facility']}.")

    # 10. create receipts/outcomes (recipient verification, receipt confirmation, outcome on ledger)
    # Propose second SKU plan (ORS-01) and complete full receipt loop
    print("Step 10: Executing full loop closure (ORS-01 plan -> approve -> dispatch -> receive -> outcome)...")
    plan2 = ws.propose_plan(sku="ORS-01", actor=actor)
    dec2 = ws.decide_plan(
        plan2["plan_id"],
        officer_id="dr-meera-dmo",
        role="district_medical_officer",
        decision="APPROVE",
        option="TRANSFER_VERIFIED_NOW",
        reason="Routine rehydration stock transfer authorized.",
    )
    approved_lines2 = [l for l in dec2.get("lines", []) if l.get("status") == "APPROVED"]
    if approved_lines2:
        sh2 = ws.dispatch(
            plan_id=plan2["plan_id"],
            line_id=approved_lines2[0]["line_id"],
            actor="dr-meera-dmo",
            role="district_medical_officer",
        )
        rec_res = ws.receive(
            sh2["shipment_id"],
            received_qty=sh2["qty"],
            damaged_qty=0.0,
            receiver_id="phc-pharmacist-01",
            receiver_role="store_clerk",
            notes="Full consignment received and inspected in good condition.",
        )
        print(f"  Shipment {sh2['shipment_id']} received! Outcome recorded on ledger. Recipient runway extended.")

    # Integrity verification
    intact, bad_offset = ws.store.verify_chain()
    print(f"\nSeed Complete! Total ledger events: {len(ws.store.events)}. Cryptographic chain intact: {intact}.")
    assert intact, f"Cryptographic integrity error at offset {bad_offset}"
    return ws


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed reproducible demonstration pipeline into TATHYON")
    parser.add_argument("--db-path", default=None, help="Path to SQLite database file. If omitted, uses in-process.")
    args = parser.parse_args()

    if args.db_path:
        os.makedirs(os.path.dirname(args.db_path) or ".", exist_ok=True)
        print(f"Opening SQLite store at {args.db_path}...")
        store = SQLiteStore(args.db_path)
    else:
        db_env = os.environ.get("TATHYON_DB", "0").strip() == "1"
        if db_env:
            data_dir = os.environ.get("TATHYON_DATA_DIR", os.path.join(ROOT, "data"))
            db_path = os.environ.get("TATHYON_DB_PATH", os.path.join(data_dir, "tathyon_ledger.sqlite3"))
            print(f"TATHYON_DB=1 detected. Using SQLite store at {db_path}...")
            store = SQLiteStore(db_path)
        else:
            print("Using in-memory EventStore...")
            store = EventStore()

    seed_pipeline(store)


if __name__ == "__main__":
    main()
