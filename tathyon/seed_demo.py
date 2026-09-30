"""
Deterministic hero scenario.

The directive is explicit: `seed demo` should produce the exact hero scenario
with no random behaviour. The main pipeline picks its scene from the data,
which is right for the honest end-to-end run, but wrong when a judge wants
the same story reproduced at 09:15 tomorrow.

So this file is a SECOND pipeline path that pins every choice: fixed facility,
fixed SKU, fixed asset, fixed attester, fixed reported/verified/unusable
quantities. It writes to a persistent SQLite store when TATHYON_DB=1 so the
demo browser sees the same events after every restart.

If you can't reproduce a scene bit-for-bit, you can't defend it. This closes
that loop.
"""
from __future__ import annotations

import argparse
import os

from .persist import SQLiteStore
from .schema import (
    Attestation, Claim, Evidence, EventType, Provenance, ResourceType,
    new_id, sha256,
)
from .store import EventStore


# --- pinned hero numbers ----------------------------------------------------
DEMO_AS_OF          = "2026-09-19T09:00:00+00:00"
DEMO_MED_FACILITY   = "FAC003"
DEMO_MED_SKU        = "AMX250"
DEMO_MED_REPORTED   = 340.0
DEMO_MED_USABLE     = 40.0
DEMO_MED_UNUSABLE   = 300.0
DEMO_MED_BATCH      = "B2411"

DEMO_EQ_FACILITY    = "FAC000"
DEMO_EQ_ASSET       = "FAC000-PSA-1"
DEMO_EQ_ASSET_NAME  = "PSA Oxygen Plant"
DEMO_EQ_UPTIME      = 0.982

DEMO_ATTESTOR       = "usr_incharge_demo"
DEMO_DELEGATION     = "dlg_demo_221"


def _fixed_nonce(scope: str) -> str:
    return sha256({"demo": scope, "as_of": DEMO_AS_OF})[:8].upper()


def seed_medicine(store) -> dict:
    """Reported 340, verified 40 usable, 300 unusable -- the exact scene."""
    claim = Claim(
        claim_id=new_id("clm"), facility_id=DEMO_MED_FACILITY,
        resource_type=ResourceType.MEDICINE, resource_key=DEMO_MED_SKU,
        state={"reported_stock": DEMO_MED_REPORTED, "batch": DEMO_MED_BATCH,
               "unit_value": 8.0},
        source_system="e-Aushadhi-shaped CSV adapter (DEMO CONNECTOR)",
        source_actor="store_clerk",
        effective_at=DEMO_AS_OF, ingested_at=DEMO_AS_OF,
        provenance=Provenance.SYNTHETIC)
    store.put_claim(claim)

    store.append(EventType.FLAGGED, DEMO_MED_FACILITY, ResourceType.MEDICINE,
                 DEMO_MED_SKU,
                 {"reasons": ["ATTESTATION_STALE", "BATCH_EXPIRY_CONFLICT",
                              "EXPIRED_STOCK_COUNTED_LIVE"]},
                 actor="detector", occurred_at=DEMO_AS_OF)

    store.append(EventType.VERIFICATION_REQUESTED, DEMO_MED_FACILITY,
                 ResourceType.MEDICINE, DEMO_MED_SKU,
                 {"reasons": ["ATTESTATION_STALE"], "priority": 8.7},
                 actor="queue", occurred_at=DEMO_AS_OF)

    ev = Evidence(
        evidence_id=new_id("ev"), claim_id=claim.claim_id,
        facility_id=DEMO_MED_FACILITY, kind="photo_register",
        captured_at=DEMO_AS_OF, device_id="dev_PHC_demo_01",
        nonce=_fixed_nonce("med"), nonce_issued_at=DEMO_AS_OF,
        frame_count=3, artifact_hash=sha256({"demo": "med_shelf"}),
        perceptual_hash=sha256({"phash": DEMO_MED_FACILITY})[:16],
        extraction={"present_qty": DEMO_MED_REPORTED,
                    "usable_qty": DEMO_MED_USABLE,
                    "expired_qty": DEMO_MED_UNUSABLE,
                    "transcribed_by": "gemini-adapter(mock)",
                    "note": "AI transcribes what the human wrote; it does not count."},
        extraction_model="tathyon-mock-extractor/v1",
        extraction_confidence=0.91, provenance=Provenance.SYNTHETIC)
    store.put_evidence(ev)
    store.append(EventType.EXTRACTED, DEMO_MED_FACILITY, ResourceType.MEDICINE,
                 DEMO_MED_SKU, {"extraction": ev.extraction,
                                "confidence": ev.extraction_confidence},
                 actor="gemini_adapter", occurred_at=DEMO_AS_OF)

    att = Attestation(
        attestation_id=new_id("att"), claim_id=claim.claim_id,
        evidence_refs=[ev.evidence_id],
        observed={"present_qty": DEMO_MED_REPORTED,
                  "usable_qty": DEMO_MED_USABLE},
        observed_at=DEMO_AS_OF, attestor_id=DEMO_ATTESTOR,
        attestor_role="facility_incharge", delegation_id=DEMO_DELEGATION,
        is_custodian=False, seconds_spent=193.0,
        signature="PROTOTYPE_IDENTIFIER:not-cryptographic",
        extraction_agreement={"model_total": DEMO_MED_USABLE,
                              "human_total": DEMO_MED_USABLE, "delta_pct": 0.0})
    store.put_attestation(att)

    store.append(EventType.DECISION_MADE, DEMO_MED_FACILITY,
                 ResourceType.MEDICINE, DEMO_MED_SKU,
                 {"status": "ALLOWED", "allowed": True,
                  "note": "Verified state -- transfer proceeds against usable qty."},
                 actor="optimizer", occurred_at=DEMO_AS_OF)
    return {"claim_id": claim.claim_id, "attestation_id": att.attestation_id,
            "evidence_id": ev.evidence_id}


def seed_equipment(store) -> dict:
    """Register says FUNCTIONAL, vendor uptime 98.2% self-reported, physical
    verification finds NOT_COMMISSIONED. Certificate refused."""
    claim = Claim(
        claim_id=new_id("clm"), facility_id=DEMO_EQ_FACILITY,
        resource_type=ResourceType.EQUIPMENT, resource_key=DEMO_EQ_ASSET,
        state={"register_status": "FUNCTIONAL",
               "vendor_reported_uptime": DEMO_EQ_UPTIME,
               "sla_target": 0.95, "value_inr": 4_500_000,
               "asset_name": DEMO_EQ_ASSET_NAME},
        source_system="BEMMP asset register + vendor dashboard (DEMO CONNECTOR)",
        source_actor="maintenance_vendor",
        effective_at=DEMO_AS_OF, ingested_at=DEMO_AS_OF,
        provenance=Provenance.SYNTHETIC)
    store.put_claim(claim)
    store.append(EventType.FLAGGED, DEMO_EQ_FACILITY, ResourceType.EQUIPMENT,
                 DEMO_EQ_ASSET, {"reasons": ["UPTIME_SELF_REPORTED"]},
                 actor="detector", occurred_at=DEMO_AS_OF)

    ev = Evidence(
        evidence_id=new_id("ev"), claim_id=claim.claim_id,
        facility_id=DEMO_EQ_FACILITY, kind="photo_asset",
        captured_at=DEMO_AS_OF, device_id="dev_PHC_demo_01",
        nonce=_fixed_nonce("eq"), nonce_issued_at=DEMO_AS_OF, frame_count=3,
        artifact_hash=sha256({"demo": "eq_asset"}),
        perceptual_hash=sha256({"p": DEMO_EQ_ASSET})[:16],
        extraction={"asset_id_read": DEMO_EQ_ASSET, "serial_visible": True,
                    "note": "Gemini reads the plate; it does not judge if the machine works."},
        extraction_model="tathyon-mock-extractor/v1",
        extraction_confidence=0.88, provenance=Provenance.SYNTHETIC)
    store.put_evidence(ev)

    att = Attestation(
        attestation_id=new_id("att"), claim_id=claim.claim_id,
        evidence_refs=[ev.evidence_id],
        observed={"status": "NOT_COMMISSIONED", "age_months": 30,
                  "amc_active": False, "last_service_months": 24},
        observed_at=DEMO_AS_OF, attestor_id="usr_biomed_demo",
        attestor_role="facility_incharge", delegation_id="dlg_demo_310",
        is_custodian=False, seconds_spent=88.0,
        signature="PROTOTYPE_IDENTIFIER:not-cryptographic")
    store.put_attestation(att)
    store.append(EventType.REJECTED, DEMO_EQ_FACILITY, ResourceType.EQUIPMENT,
                 DEMO_EQ_ASSET,
                 {"certificate": "GFR-22 line item", "issued": False,
                  "refusal_reason": "physical verification contradicts the register"},
                 actor="certificate_renderer", occurred_at=DEMO_AS_OF)
    return {"claim_id": claim.claim_id, "attestation_id": att.attestation_id,
            "evidence_id": ev.evidence_id}


def run(clean: bool = True, use_sqlite: Optional[bool] = None) -> dict:
    if use_sqlite is None:
        use_sqlite = os.environ.get("TATHYON_DB", "").strip() not in ("", "0", "false")
    if use_sqlite:
        path = os.path.join("data", "tathyon.sqlite3")
        if clean:
            for s in ("", "-wal", "-shm"):
                if os.path.exists(path + s):
                    os.remove(path + s)
        store = SQLiteStore(path)
    else:
        store = EventStore()

    med = seed_medicine(store)
    eq = seed_equipment(store)
    ok, bad = store.verify_chain()
    result = {"medicine": med, "equipment": eq,
              "chain_intact": ok, "first_bad_offset": bad,
              "events": len(store.events),
              "store_kind": type(store).__name__,
              "as_of": DEMO_AS_OF}
    return result


if __name__ == "__main__":
    from typing import Optional  # noqa: E402 -- keep the import out of the module surface
    p = argparse.ArgumentParser(description="Seed the deterministic hero scenario.")
    p.add_argument("--no-clean", action="store_true",
                   help="Append to the existing store rather than starting fresh.")
    p.add_argument("--sqlite", action="store_true",
                   help="Force SQLite backing store (default follows TATHYON_DB).")
    p.add_argument("--memory", action="store_true",
                   help="Force in-memory backing store.")
    args = p.parse_args()
    use_sqlite = True if args.sqlite else (False if args.memory else None)
    r = run(clean=not args.no_clean, use_sqlite=use_sqlite)
    print(f"Seeded hero scenario  as_of={r['as_of']}  events={r['events']}  "
          f"chain_intact={r['chain_intact']}  store={r['store_kind']}")
    print(f"  medicine  {r['medicine']}")
    print(f"  equipment {r['equipment']}")


# Optional import kept out of the module surface -- only used by argparse block
from typing import Optional        # noqa: E402
