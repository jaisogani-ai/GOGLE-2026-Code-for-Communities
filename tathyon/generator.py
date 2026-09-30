"""
CAG-anchored synthetic generator.

PROVENANCE: SYNTHETIC. Every row produced here is fabricated. What is NOT
fabricated is the *taxonomy of failure modes* and their rough incidence, which
are taken from named paragraphs of Comptroller & Auditor General of India
performance audits. Those citations are in docs/07_DATA_PROVENANCE.md.

Anti-leakage discipline (this is the part most synthetic pipelines get wrong):
  - `truth` and `observed` are generated as SEPARATE tables.
  - Labels come from comparing them.
  - Features are computed from `observed` ONLY.
  - The injected-pathology flags are never exposed as features; if they were,
    a classifier would trivially recover the label and the evaluation would be
    meaningless. tests/test_no_leakage.py enforces this.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Failure-mode incidence, anchored to published audit findings.
# See docs/07_DATA_PROVENANCE.md for the exact paragraph citation per row.
CAG_ANCHORS = {
    # CAG Punjab Rpt 4/2019 Para 2.1.7.2(ii): 952 of 13,322 records (7.1%)
    "batch_expiry_conflict": 0.071,
    # CAG Punjab Para 2.1.7.6(vi): 24,164 of 6,74,253 issue instances (3.6%)
    "fefo_violation": 0.036,
    # CAG Punjab Para 2.1.7.6(vii): offline indents entered up to 947 days late
    "retroactive_entry": 0.045,
    # CAG Maharashtra Para 2.4.8.12: absurd unit value from missing validation
    "absurd_unit_value": 0.004,
    # CAG Maharashtra Para 2.4.8.9: issued-but-never-received
    "issued_never_received": 0.030,
    # CAG Punjab Para 2.1.8.4: monthly stock verification skipped 38-90%
    "verification_skipped": 0.60,
    # CAG Telangana Para 4.5.3 / Delhi Ch.4: expired stock still counted live
    "expired_counted_live": 0.055,
}

EQUIPMENT_ANCHORS = {
    # CAG West Bengal Rpt 3/2024 Para 4.5: 43 of 75 PSA plants (57%)
    "not_commissioned": 0.24,
    "non_functional": 0.19,
    "not_present": 0.06,
    # NHSRC BEMMP: 13-34% dysfunctional across states
}

SKU_CATALOGUE = [
    ("AMX250", "Amoxicillin 250mg cap", 8.0, "antibiotic"),
    ("PCM500", "Paracetamol 500mg tab", 22.0, "analgesic"),
    ("ORS01", "ORS sachet", 6.0, "essential"),
    ("IFA100", "Iron Folic Acid tab", 14.0, "programme"),
    ("METRO4", "Metronidazole 400mg tab", 5.0, "antibiotic"),
    ("CEFX500", "Cefixime 500mg tab", 3.2, "antibiotic"),
    ("SALB", "Salbutamol inhaler", 1.1, "respiratory"),
    ("ATNL50", "Atenolol 50mg tab", 4.4, "chronic"),
    ("MTFM500", "Metformin 500mg tab", 9.5, "chronic"),
    ("INJDEX", "Inj Dexamethasone", 0.9, "emergency"),
]

ASSET_CATALOGUE = [
    ("VENT", "ICU Ventilator", 1_800_000),
    ("PSA", "PSA Oxygen Plant", 4_500_000),
    ("XRAY", "X-Ray Unit 300mA", 1_200_000),
    ("ILR", "Ice-Lined Refrigerator", 95_000),
    ("AUTOCL", "Autoclave", 180_000),
    ("DIAL", "Dialysis Machine", 900_000),
]


# "Materially wrong" = the reported number would change a replenishment
# decision: it is off by more than 15% of what is physically on the shelf, or
# less than 70% of what it claims is actually usable (expired stock counted live).
MATERIAL_REL_ERR = 0.15
MATERIAL_USABLE_SHARE = 0.70

# Columns in generate()["observed"] that only the generator can know. They are
# the answer key: strip them before building any feature.
TRUTH_COLUMNS = ("true_stock", "true_usable", "true_demand", "rel_err", "label_wrong")


def observed_only(observed: pd.DataFrame) -> pd.DataFrame:
    """The ledger as a district officer would see it: truth columns removed."""
    return observed.drop(columns=[c for c in TRUTH_COLUMNS if c in observed.columns])


def materially_wrong(reported: pd.Series, true_stock: pd.Series,
                     true_usable: pd.Series) -> pd.Series:
    """The ground-truth label. Only the generator can compute it: it needs truth."""
    rel_err = (reported - true_stock).abs() / true_stock.replace(0, np.nan)
    return ((rel_err > MATERIAL_REL_ERR)
            | (true_usable < reported * MATERIAL_USABLE_SHARE)).fillna(False).astype(int)


def observation_labels(generated: dict, snapshot_days: list[int]) -> pd.DataFrame:
    """Ground-truth label per facility x SKU observation.

    An observation is the stock figure the system of record shows for one
    facility x SKU on one snapshot day (day index from the start of the run).
    `is_materially_wrong` is computed from truth, which the trust scorer never
    sees: it is the answer key used for training and evaluation only.
    """
    obs = generated["observed"]
    day0 = obs["date"].min()
    dates = {day0 + pd.Timedelta(days=int(d)) for d in snapshot_days}
    snap = obs[obs["date"].isin(dates)]
    out = snap[["date", "facility_id", "sku", "reported_stock",
                "true_stock", "true_usable"]].copy()
    out["is_materially_wrong"] = materially_wrong(
        out["reported_stock"], out["true_stock"], out["true_usable"])
    return out.reset_index(drop=True)


def attestation_log(generated: dict) -> pd.DataFrame:
    """What a human physical count records on a verification day.

    This is the ONE channel through which truth legitimately reaches features:
    a count reveals the physical quantity on the day it happens, and only for
    the series that were actually verified that day. It mirrors the attestation
    that api.main.Registry writes for the same rows.
    """
    obs = generated["observed"]
    rows = obs[obs["physically_verified"].astype(bool)]
    return pd.DataFrame({
        "date": rows["date"].to_numpy(),
        "facility_id": rows["facility_id"].to_numpy(),
        "sku": rows["sku"].to_numpy(),
        "counted_present": rows["true_stock"].to_numpy(dtype=float),
        "counted_usable": rows["true_usable"].to_numpy(dtype=float),
    })


def generate(
    n_facilities: int = 20,
    n_days: int = 240,
    n_assets_per_facility: int = 6,
    seed: int = 7,
) -> dict:
    rng = np.random.default_rng(seed)

    facilities = []
    for i in range(n_facilities):
        tier = rng.choice(["PHC", "CHC", "DH"], p=[0.6, 0.3, 0.1])
        facilities.append({
            "facility_id": f"FAC{i:03d}",
            "name": f"{tier} {chr(65+i%26)}{i}",
            "tier": tier,
            "district": f"D{i%4}",
            "opd_per_day": {"PHC": 60, "CHC": 180, "DH": 500}[tier] * rng.uniform(0.7, 1.3),
            # transport time matrix is built from these
            "x": rng.uniform(0, 100), "y": rng.uniform(0, 100),
        })
    fac = pd.DataFrame(facilities)

    truth_rows, obs_rows = [], []
    dates = pd.date_range("2026-01-01", periods=n_days, freq="D")

    for _, f in fac.iterrows():
        scale = f["opd_per_day"] / 100.0
        for sku, name, base_rate, cls in SKU_CATALOGUE:
            # --- ground truth physical process -----------------------------
            rate = base_rate * scale * rng.uniform(0.7, 1.4)
            # intermittent demand: many zero days, which is why Croston/TSB
            p_demand = 0.45 if cls in ("emergency", "respiratory") else 0.82
            true_stock = float(rng.integers(120, 900))
            batch = f"B{rng.integers(1000, 9999)}"
            # Expiry must land INSIDE the simulation window often enough for the
            # expired-stock pathology to actually occur. The first version drew
            # uniformly over [-40, 400) days from day zero, so with a 240-day
            # window almost every batch expired after the run ended and the
            # pathology was inert -- zero of twenty facilities carried expired
            # stock for the demo SKU. That is a generator bug, not a finding.
            # CAG Telangana Rpt 4/2024 Para 4.5.3 documents Rs 390.26 crore of
            # expired drugs sitting in stores, so a real corpus carries a
            # meaningful share of near- and past-expiry batches at any moment.
            if rng.random() < 0.30:
                expiry_offset = int(rng.integers(20, n_days - 20))   # expires mid-run
            else:
                expiry_offset = int(rng.integers(n_days, n_days + 400))
            expiry = dates[0] + pd.Timedelta(days=expiry_offset)

            # pathology assignment per (facility, sku) series
            path = {
                k: bool(rng.random() < p) for k, p in CAG_ANCHORS.items()
            }
            lag_days = int(rng.integers(20, 947)) if path["retroactive_entry"] else 0

            # A second batch with a LATER expiry. Without at least two batches
            # FEFO cannot be violated at all, which is why the declared
            # fefo_violation anchor never fired in earlier revisions: the
            # incidence was documented but structurally impossible. Two batches
            # is the minimum that makes the rule meaningful.
            batch_b = f"B{rng.integers(1000, 9999)}"
            expiry_b = expiry + pd.Timedelta(days=int(rng.integers(60, 300)))
            reported_stock = true_stock
            expired_qty = None
            for d_i, day in enumerate(dates):
                occurs = rng.random() < p_demand
                demand = float(rng.poisson(rate)) if occurs else 0.0
                # seasonal bump: monsoon/dengue window
                if 150 <= d_i <= 200 and cls in ("antibiotic", "essential"):
                    demand *= 1.6
                issued = min(demand, true_stock)
                true_stock -= issued

                if d_i % 30 == 0 and true_stock < rate * 12:
                    receipt = float(rng.integers(200, 700))
                    true_stock += receipt
                else:
                    receipt = 0.0

                # On the day the batch expires, whatever is on the shelf becomes
                # unusable and STAYS on the shelf -- that is the documented
                # failure: expired stock is not segregated, not written off, and
                # continues to be counted as live inventory. Later receipts are
                # usable; the expired quantity is frozen at its expiry-day level.
                if day > expiry and path["expired_counted_live"]:
                    if expired_qty is None:
                        expired_qty = true_stock
                    expired_qty = min(expired_qty, true_stock)
                usable = max(true_stock - (expired_qty or 0.0), 0.0)

                # FEFO: first-expiry-first-out. A violation is issuing from the
                # later-expiring batch while earlier-expiring stock is still on
                # the shelf. CAG Punjab Para 2.1.7.6(vi) recorded this in 24,164
                # of 6,74,253 issue instances (3.6%).
                # NOTE ON THE DENOMINATOR: the CAG figure is 3.6% of ISSUE
                # INSTANCES, not 3.6% of series. Gating this behind a
                # series-level flag (as an earlier revision did) compounded two
                # probabilities and produced 0.1% -- a thirty-fold undershoot of
                # the anchor it claimed to reproduce. Applied per issue.
                fefo_violated = bool(
                    issued > 0
                    and rng.random() < CAG_ANCHORS["fefo_violation"])
                issued_from = batch_b if fefo_violated else batch

                truth_rows.append({
                    "date": day, "facility_id": f["facility_id"], "sku": sku,
                    "true_stock": true_stock, "true_usable": usable,
                    "true_demand": demand, "batch": batch,
                    "batch_b": batch_b, "expiry_b": expiry_b,
                    "issued_from_batch": issued_from,
                    "fefo_violated": fefo_violated,
                    "expiry": expiry, "receipt": receipt, "issued": issued,
                })

                # --- observed ledger: what the system of record says --------
                rep = true_stock
                if path["issued_never_received"] and rng.random() < 0.08:
                    rep += issued                      # issued, never arrived
                if path["expired_counted_live"]:
                    rep = true_stock                   # expired still counted
                if rng.random() < 0.02:
                    rep = rep * rng.uniform(1.4, 3.0)  # quantity error
                unit_value = SKU_UNIT_VALUE.get(sku, 3.5)
                if path["absurd_unit_value"] and rng.random() < 0.02:
                    unit_value = 1_110_111.20          # Maharashtra Para 2.4.8.12

                obs_expiry = expiry
                if path["batch_expiry_conflict"] and rng.random() < 0.25:
                    obs_expiry = expiry + pd.Timedelta(days=int(rng.integers(30, 400)))
                if rng.random() < 0.01:
                    obs_expiry = pd.NaT                # missing expiry

                entered = day + pd.Timedelta(days=lag_days)
                verified_this_month = (
                    (d_i % 30 == 0) and not path["verification_skipped"]
                )

                obs_rows.append({
                    "date": day, "facility_id": f["facility_id"], "sku": sku,
                    "sku_name": name, "sku_class": cls,
                    "reported_stock": round(rep, 1),
                    "issued": issued, "receipt": receipt,
                    "batch": batch, "obs_expiry": obs_expiry,
                    "issued_from_batch": issued_from,
                    "alt_batch": batch_b, "alt_expiry": expiry_b,
                    "unit_value": unit_value,
                    "entered_at": entered, "entry_lag_days": lag_days,
                    "physically_verified": verified_this_month,
                    "tier": f["tier"], "district": f["district"],
                    "opd": f["opd_per_day"],
                })

    truth = pd.DataFrame(truth_rows)
    obs = pd.DataFrame(obs_rows)

    # --- LABEL: derived by comparing truth to observed, never from flags ----
    merged = obs.merge(
        truth[["date", "facility_id", "sku", "true_stock", "true_usable", "true_demand"]],
        on=["date", "facility_id", "sku"], how="left")
    denom = merged["true_stock"].replace(0, np.nan)
    merged["rel_err"] = (merged["reported_stock"] - merged["true_stock"]).abs() / denom
    merged["label_wrong"] = materially_wrong(
        merged["reported_stock"], merged["true_stock"], merged["true_usable"])

    # --- equipment ---------------------------------------------------------
    eq_rows = []
    for _, f in fac.iterrows():
        for a_i in range(n_assets_per_facility):
            code, aname, value = ASSET_CATALOGUE[a_i % len(ASSET_CATALOGUE)]
            asset_id = f"{f['facility_id']}-{code}-{a_i}"
            r = rng.random()
            if r < EQUIPMENT_ANCHORS["not_present"]:
                true_status = "NOT_PRESENT"
            elif r < EQUIPMENT_ANCHORS["not_present"] + EQUIPMENT_ANCHORS["not_commissioned"]:
                true_status = "NOT_COMMISSIONED"
            elif r < (EQUIPMENT_ANCHORS["not_present"]
                      + EQUIPMENT_ANCHORS["not_commissioned"]
                      + EQUIPMENT_ANCHORS["non_functional"]):
                true_status = "NON_FUNCTIONAL"
            else:
                true_status = "FUNCTIONAL"
            eq_rows.append({
                "asset_id": asset_id, "facility_id": f["facility_id"],
                "asset_code": code, "asset_name": aname, "value_inr": value,
                # The register almost always says FUNCTIONAL, because the record
                # is written at procurement and never again. That is the thesis.
                "register_status": "FUNCTIONAL",
                # Vendor dashboard uptime: self-reported, and therefore high.
                # BEMMP Arunachal mid-term evaluation: dashboard showed >99%
                # against a 95% SLA while evaluators physically found equipment
                # absent, idle in stores, or moved to other facilities.
                "vendor_reported_uptime": round(float(rng.uniform(0.962, 0.998)), 4),
                "sla_target": 0.95,
                "true_status": true_status,
                "age_months": int(rng.integers(3, 120)),
                "amc_active": bool(rng.random() < 0.7),
                "months_since_verification": int(rng.integers(0, 60)),
                "last_service_months": int(rng.integers(0, 36)),
            })
    equipment = pd.DataFrame(eq_rows)
    equipment["label_wrong"] = (equipment["true_status"] != "FUNCTIONAL").astype(int)

    return {
        "facilities": fac,
        "truth": truth,
        "observed": merged,
        "equipment": equipment,
        "provenance": "SYNTHETIC",
        "anchors": CAG_ANCHORS,
    }


SKU_UNIT_VALUE = {s[0]: round(1.5 + i * 0.9, 2) for i, s in enumerate(SKU_CATALOGUE)}


if __name__ == "__main__":
    import os
    d = generate()
    os.makedirs("data", exist_ok=True)
    for k in ("facilities", "truth", "observed", "equipment"):
        d[k].to_parquet(f"data/{k}.parquet")
    print("SYNTHETIC data written to ./data")
    print(f"  facilities {len(d['facilities']):>6}")
    print(f"  ledger rows{len(d['observed']):>7}  "
          f"wrong={d['observed']['label_wrong'].mean():.1%}")
    print(f"  assets     {len(d['equipment']):>6}  "
          f"wrong={d['equipment']['label_wrong'].mean():.1%}")
