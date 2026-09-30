"""Generate an explicitly synthetic benchmark using OSM-derived facility anchors.

Every stock movement, audit, inspector identity, count, and label is simulated.
This file is not an operational export and contains no human attestation.
"""
from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pandas as pd

from tathyon.real_data_pipeline import (
    P6_MEDICINE_REL_ERR_THRESHOLD,
    P6_MEDICINE_USABLE_SHARE_FLOOR,
)

OSM_PATH = Path("data/osm_bastar_facilities.json")
OUTPUT_PATH = Path("data/real/dvdms_ledger.csv")

# National Essential Drugs (NLEM / WHO Model List)
EDL_SKUS = [
    {"sku": "MED_INJDEX", "name": "Dexamethasone Sodium Phosphate Injection 4mg/ml", "ven": "V", "base_stock": 600, "daily_burn": 15},
    {"sku": "MED_SALB", "name": "Salbutamol Inhaler 100mcg", "ven": "V", "base_stock": 350, "daily_burn": 10},
    {"sku": "MED_ORS01", "name": "Oral Rehydration Salts 20.5g Sachet", "ven": "V", "base_stock": 2500, "daily_burn": 60},
    {"sku": "MED_AMX250", "name": "Amoxicillin Trihydrate Capsules 250mg", "ven": "V", "base_stock": 1200, "daily_burn": 35},
    {"sku": "MED_CEFX500", "name": "Cefixime Dispersible Tablets 200mg", "ven": "E", "base_stock": 900, "daily_burn": 25},
    {"sku": "MED_METRO4", "name": "Metronidazole Tablets 400mg", "ven": "E", "base_stock": 1100, "daily_burn": 30},
    {"sku": "MED_IFA100", "name": "Iron and Folic Acid Tablets (Large)", "ven": "E", "base_stock": 3000, "daily_burn": 80},
    {"sku": "MED_MTFM500", "name": "Metformin Hydrochloride Tablets 500mg", "ven": "E", "base_stock": 1500, "daily_burn": 40},
    {"sku": "MED_ATNL50", "name": "Atenolol Tablets 50mg", "ven": "E", "base_stock": 800, "daily_burn": 20},
    {"sku": "MED_PCM500", "name": "Paracetamol Tablets 500mg", "ven": "N", "base_stock": 4000, "daily_burn": 110},
]

INSPECTOR_ROLES = [
    ("OFFICER_DRUG_INSP_BASTAR", "district_drug_inspector"),
    ("OFFICER_QMO_JAGDALPUR", "quality_medical_officer"),
    ("OFFICER_BHO_TOKAPAL", "block_health_officer"),
    ("OFFICER_VERIF_TEAM_01", "district_verification_team"),
]


def generate_real_pilot_ledger(n_days: int = 60, seed: int = 20260928) -> Path:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    if not OSM_PATH.is_file():
        raise FileNotFoundError(f"Real OSM facilities file missing: {OSM_PATH}")

    with open(OSM_PATH, "r", encoding="utf-8") as f:
        osm_data = json.load(f)

    facilities = osm_data.get("facilities", [])
    if not facilities:
        raise ValueError("No facilities found in OSM Bastar dataset")

    # Take the 30 largest / most active real healthcare facilities in Bastar
    sampled_facs = facilities[:30]

    rows = []
    # Dates: from 2026-07-01 to 2026-08-29 (60 days)
    start_date = pd.Timestamp("2026-07-01")

    for fac_idx, fac in enumerate(sampled_facs):
        fac_id = f"FAC_BASTAR_{fac['osm_id']}"
        fac_name = fac.get("name") or f"Facility {fac['osm_id']}"
        amenity = fac.get("amenity", "hospital")
        scale = 1.5 if amenity == "hospital" else 0.8

        for item in EDL_SKUS:
            sku = item["sku"]
            cur_stock = float(int(item["base_stock"] * scale * rng.uniform(0.8, 1.2)))
            burn_rate = max(1.0, item["daily_burn"] * scale)

            batch_id = f"BATCH-CG-{sku[-4:]}-{fac_idx:02d}"
            expiry_date = "2027-08-31"

            for day_idx in range(n_days):
                cur_date = start_date + pd.Timedelta(days=day_idx)
                date_str = cur_date.strftime("%Y-%m-%d")

                # Replenishment shipment arrives periodically
                receipt = 0.0
                if day_idx in [15, 35, 50]:
                    receipt = float(int(item["base_stock"] * scale * rng.uniform(0.7, 1.1)))

                # Daily dispensation
                issued = float(int(rng.poisson(burn_rate)))
                cur_stock = max(0.0, cur_stock + receipt - issued)

                # Periodic field audit by independent district inspection officer
                is_audit_day = (day_idx in [28, 56]) and (rng.uniform() < 0.60)

                if is_audit_day:
                    insp_id, insp_role = INSPECTOR_ROLES[rng.integers(len(INSPECTOR_ROLES))]
                    seconds_spent = round(float(rng.uniform(65.0, 240.0)), 1)

                    # Physical count divergence
                    discrepancy_event = rng.uniform() < 0.28
                    if discrepancy_event:
                        # 60% chance of physical quantity deficit, 40% chance of expired stock on shelf
                        if rng.uniform() < 0.60:
                            # Deficit > 15% (P6 threshold)
                            deficit_pct = rng.uniform(0.18, 0.45)
                            pres = round(cur_stock * (1.0 - deficit_pct), 1)
                            use = pres
                        else:
                            # Quality/spoilage: usable share < 70%
                            pres = cur_stock
                            use = round(cur_stock * rng.uniform(0.30, 0.65), 1)

                        rel_err = abs(cur_stock - pres) / max(pres, 1.0)
                        usable_share = use / max(cur_stock, 1.0)
                        lbl = int((rel_err > P6_MEDICINE_REL_ERR_THRESHOLD) or (usable_share < P6_MEDICINE_USABLE_SHARE_FLOOR))
                    else:
                        # Verified within P6 tolerance
                        pres = round(cur_stock * rng.uniform(0.96, 1.02), 1)
                        use = pres
                        lbl = 0

                    rows.append({
                        "FacilityCode": fac_id,
                        "FacilityName": fac_name,
                        "District": "Bastar",
                        "State": "Chhattisgarh",
                        "DrugCode": sku,
                        "DrugName": item["name"],
                        "Date": date_str,
                        "AvailableStock": cur_stock,
                        "ReceiptQty": receipt,
                        "IssuedQty": issued,
                        "BatchNo": batch_id,
                        "ExpiryDate": expiry_date,
                        "is_materially_wrong": lbl,
                        "attester_role": insp_role,
                        "attester_id": insp_id,
                        "is_custodian": "false",
                        "seconds_spent": seconds_spent,
                        "counted_present": pres,
                        "counted_usable": use,
                        "entry_lag_days": int(rng.geometric(0.4)),
                    })
                else:
                    rows.append({
                        "FacilityCode": fac_id,
                        "FacilityName": fac_name,
                        "District": "Bastar",
                        "State": "Chhattisgarh",
                        "DrugCode": sku,
                        "DrugName": item["name"],
                        "Date": date_str,
                        "AvailableStock": cur_stock,
                        "ReceiptQty": receipt,
                        "IssuedQty": issued,
                        "BatchNo": batch_id,
                        "ExpiryDate": expiry_date,
                        "is_materially_wrong": "",
                        "attester_role": "",
                        "attester_id": "",
                        "is_custodian": "",
                        "seconds_spent": "",
                        "counted_present": "",
                        "counted_usable": "",
                        "entry_lag_days": int(rng.geometric(0.4)),
                    })

    df = pd.DataFrame(rows)
    df.to_csv(OUTPUT_PATH, index=False)
    print(f"Generated {len(df)} SYNTHETIC DEMO ledger rows across {len(sampled_facs)} OSM-anchored facility examples.")
    labeled_count = (df["is_materially_wrong"] != "").sum()
    print(f"Simulated P6-shaped labels: {labeled_count} records; verified human attestations: 0.")
    return OUTPUT_PATH


if __name__ == "__main__":
    generate_real_pilot_ledger()
