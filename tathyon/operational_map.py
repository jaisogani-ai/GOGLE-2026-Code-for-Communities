"""Tathyon Operational Map Service — API-backed Facility & Event Geographic Layer.

Integrates:
1. Real OpenStreetMap facilities cached in data/osm_bastar_facilities.json
2. Sovereign EventStore attestation ledger (physical counts, freshness, EVT IDs)
3. Deterministic Trust Queue (P(wrong), expected hidden stockout days, budget targets)
4. Multi-physics operational movements:
   - Approved medicine transfers (donor-to-recipient road routes)
   - Patient diversions (authorized emergency bed reroutes)
   - Staff redeployments (role-level routes strictly without PII)
5. Approximate Bastar-shaped demo polygon (not an official geographic boundary)
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .osrm import OSRMClient, straight_line_route
from .schema import FacilityType, ResourceType, now

DATA_DIR = os.environ.get(
    "TATHYON_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)

# Approximate Bastar-shaped illustration; never use as an official boundary.
BASTAR_DISTRICT_BOUNDARY: Dict[str, Any] = {
    "type": "Feature",
    "properties": {
        "name": "Bastar District",
        "district": "Bastar",
        "state": "Chhattisgarh",
        "country": "India",
        "headquarters": "Jagdalpur",
        "area_sq_km": 4029.98,
        "provenance": "APPROXIMATE_DEMO_POLYGON_NOT_OFFICIAL_BOUNDARY",
    },
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [81.650, 18.850],
            [81.720, 18.780],
            [81.850, 18.720],
            [81.980, 18.710],
            [82.120, 18.760],
            [82.260, 18.880],
            [82.350, 19.050],
            [82.340, 19.220],
            [82.220, 19.380],
            [82.080, 19.460],
            [81.920, 19.450],
            [81.750, 19.350],
            [81.620, 19.180],
            [81.580, 19.020],
            [81.650, 18.850],
        ]],
    },
}

# Fixed synthetic & demo facilities anchored to Bastar district coordinates
ANCHORED_SYNTHETIC_FACILITIES: List[Dict[str, Any]] = [
    {
        "facility_id": "PHC_X",
        "name": "Primary Health Centre Tokapal (PHC X)",
        "facility_type": "PHC",
        "district": "Bastar",
        "lat": 19.1245,
        "lon": 81.8820,
        "amenity": "clinic",
    },
    {
        "facility_id": "PHC_Y",
        "name": "Primary Health Centre Bakawand (PHC Y)",
        "facility_type": "PHC",
        "district": "Bastar",
        "lat": 19.2512,
        "lon": 82.0234,
        "amenity": "clinic",
    },
    {
        "facility_id": "PHC_Z",
        "name": "Community Health Centre Jagdalpur (PHC Z)",
        "facility_type": "CHC",
        "district": "Bastar",
        "lat": 19.0788,
        "lon": 82.0164,
        "amenity": "hospital",
    },
    {
        "facility_id": "DH_CENTRAL_HOSPITAL",
        "name": "District Civil Hospital & Trauma Centre (Apex)",
        "facility_type": "DH",
        "district": "Bastar",
        "lat": 19.0820,
        "lon": 82.0280,
        "amenity": "hospital",
    },
    {
        "facility_id": "DWH_DISTRICT_DEPOT_01",
        "name": "District Central Medical Depot (Apex Warehouse)",
        "facility_type": "DWH",
        "district": "Bastar",
        "lat": 19.0750,
        "lon": 82.0150,
        "amenity": "warehouse",
    },
    {
        "facility_id": "CHC_RURAL_NORTH",
        "name": "Community Health Centre Rural North",
        "facility_type": "CHC",
        "district": "Bastar",
        "lat": 19.3450,
        "lon": 81.9540,
        "amenity": "hospital",
    },
    {
        "facility_id": "PHC_REMOTE_EAST",
        "name": "Primary Health Centre Remote East",
        "facility_type": "PHC",
        "district": "Bastar",
        "lat": 19.2100,
        "lon": 82.3400,
        "amenity": "clinic",
    },
    {
        "facility_id": "PHC_VALLEY_WEST",
        "name": "Primary Health Centre Valley West",
        "facility_type": "PHC",
        "district": "Bastar",
        "lat": 18.9200,
        "lon": 81.7800,
        "amenity": "clinic",
    },
]


def _calc_hours_ago(ts_str: Optional[str]) -> Optional[float]:
    if not ts_str:
        return None
    try:
        dt = pd.Timestamp(ts_str)
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        else:
            dt = dt.tz_convert("UTC")
        current_time = pd.Timestamp.now(tz="UTC")
        hours = (current_time - dt).total_seconds() / 3600.0
        return max(0.0, round(float(hours), 1))
    except Exception:
        return None


def get_operational_facilities_dataset(
    registry: Any,
    resource_type_filter: Optional[str] = None,
    facility_type_filter: Optional[str] = None,
) -> Dict[str, Any]:
    """Compiles the operational facilities layer combining OSM, Trust Queue, and EventStore."""
    cache_path = os.path.join(DATA_DIR, "osm_bastar_facilities.json")
    raw_facilities: List[Dict[str, Any]] = []
    fetched_at = ""
    fetch_status = "OK"

    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                raw_facilities = data.get("facilities", [])
                fetched_at = data.get("fetched_at", "")
        except Exception:
            fetch_status = "PARTIAL"
    else:
        fetch_status = "UNAVAILABLE"

    # 1. Fetch current Trust Queue ranking
    queue_map: Dict[str, Dict[str, Any]] = {}
    try:
        from .verify import target_verifications
        tq = target_verifications(budget=20)
        for r in tq.ranking.itertuples():
            fid = str(r.facility_id)
            is_sel = bool(getattr(r, "selected", False))
            p_w = round(float(r.p_wrong), 3)
            h_days = round(float(r.hidden_stockout_days), 1)
            ess = float(r.essentiality)
            val = round(float(r.value), 2)

            if fid not in queue_map:
                queue_map[fid] = {
                    "p_wrong": p_w,
                    "hidden_stockout_days": h_days,
                    "essentiality": ess,
                    "expected_value": val,
                    "selected": is_sel,
                    "rank": int(getattr(r, "rank", 0)),
                    "reason": (
                        f"Deterministic rank: P(wrong)={p_w} x {h_days}d x VEN={int(ess)} "
                        f"= expected value {val}"
                    ),
                    "sku": getattr(r, "sku", "MED-ARV-01"),
                }
            else:
                # If current SKU is selected, elevate facility selected state
                if is_sel and not queue_map[fid]["selected"]:
                    queue_map[fid]["selected"] = True
                    queue_map[fid]["p_wrong"] = p_w
                    queue_map[fid]["expected_value"] = val
                    queue_map[fid]["rank"] = int(getattr(r, "rank", 0))
                    queue_map[fid]["sku"] = getattr(r, "sku", "MED-ARV-01")
                    queue_map[fid]["reason"] = (
                        f"Deterministic rank: P(wrong)={p_w} x {h_days}d x VEN={int(ess)} "
                        f"= expected value {val}"
                    )
                elif val > queue_map[fid]["expected_value"] and not queue_map[fid]["selected"]:
                    queue_map[fid]["expected_value"] = val
                    queue_map[fid]["p_wrong"] = p_w
                    queue_map[fid]["rank"] = int(getattr(r, "rank", 0))
    except Exception:
        queue_map = {}

    # 2. Extract latest attestations per facility from EventStore
    latest_attestations: Dict[str, Dict[str, Any]] = {}
    if hasattr(registry, "store") and registry.store:
        for ev in reversed(registry.store._events):
            if ev.facility_id not in latest_attestations:
                latest_attestations[ev.facility_id] = {
                    "event_id": ev.event_id,
                    "event_type": str(ev.event_type),
                    "recorded_at": ev.recorded_at,
                    "occurred_at": ev.occurred_at,
                    "actor": ev.actor,
                    "payload": ev.payload,
                }

    # 3. Build unified facility list
    unified_facilities: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Add anchored synthetic / demo facilities first
    for s_fac in ANCHORED_SYNTHETIC_FACILITIES:
        fid = s_fac["facility_id"]
        seen_ids.add(fid)
        seen_ids.add(fid.lower())

        q_item = queue_map.get(fid)
        att = latest_attestations.get(fid)

        # Baseline trust logic
        if att and ("ATTEST" in att.get("event_type", "")):
            trust_status = "VERIFIED"
            hours_ago = _calc_hours_ago(att.get("recorded_at"))
            freshness_str = f"{hours_ago:.0f}h ago" if hours_ago is not None else "Verified"
            observed = att.get("payload", {}).get("observed", {})
            reported = float(observed.get("present_quantity", att["payload"].get("present_quantity", 2500.0)))
            verified = float(observed.get("usable_quantity", att["payload"].get("usable_quantity", 2500.0)))
            p_w = 0.0
            last_eid = att["event_id"]
        elif q_item and q_item.get("selected"):
            trust_status = "HIGH_RISK" if q_item["p_wrong"] >= 0.35 else "UNVERIFIED"
            freshness_str = "41d unverified claim"
            reported = 5000.0 if fid == "PHC_X" else 1200.0
            verified = 0.0 if fid == "PHC_X" else None
            p_w = q_item["p_wrong"]
            last_eid = None
        elif fid == "PHC_X":
            trust_status = "HIGH_RISK"
            freshness_str = "41d stale claim (phantom trap)"
            reported = 5000.0
            verified = 0.0
            p_w = 0.92
            last_eid = None
        elif fid == "PHC_Z":
            trust_status = "VERIFIED"
            freshness_str = "12h ago (Physically Verified)"
            reported = 2500.0
            verified = 2500.0
            p_w = 0.04
            last_eid = "EVT-20260928-PHCZ-001"
        elif fid == "PHC_Y":
            trust_status = "UNVERIFIED"
            freshness_str = "Stockout Imminent (0d runway)"
            reported = 50.0
            verified = 50.0
            p_w = 0.20
            last_eid = None
        else:
            trust_status = "VERIFIED"
            freshness_str = "24h ago"
            reported = 1500.0
            verified = 1450.0
            p_w = 0.08
            last_eid = None

        is_target = bool(q_item and q_item.get("selected"))
        target_rank = q_item["rank"] if q_item and q_item.get("selected") else None

        unified_facilities.append({
            "facility_id": fid,
            "osm_id": None,
            "name": s_fac["name"],
            "facility_type": s_fac["facility_type"],
            "tier": s_fac["facility_type"],
            "district": s_fac["district"],
            "lat": s_fac["lat"],
            "lon": s_fac["lon"],
            "amenity": s_fac["amenity"],
            "trust_status": trust_status,
            "trust_score": round(1.0 - p_w, 3),
            "p_wrong": p_w,
            "freshness": freshness_str,
            "freshness_hours": _calc_hours_ago(att.get("recorded_at")) if att else (12.0 if trust_status == "VERIFIED" else 984.0),
            "last_attestation_id": last_eid,
            "reported_stock": reported,
            "verified_usable_stock": verified,
            "is_target": is_target,
            "is_verification_target": is_target,
            "target_rank": target_rank,
            "target_reason": q_item["reason"] if q_item else None,
            "queue_score": q_item["expected_value"] if q_item else None,
            "queue_rank": target_rank,
            "queue_reasons": [q_item["reason"]] if q_item and q_item.get("reason") else [],
            "resources": {
                "medicines": {
                    "sku": "MED-ARV-01",
                    "reported": reported,
                    "verified": verified,
                    "trust": trust_status,
                },
                "beds": {
                    "total": 60 if s_fac["facility_type"] in ("DH", "CHC") else 20,
                    "occupied": 48 if s_fac["facility_type"] in ("DH", "CHC") else 14,
                    "available": 12 if s_fac["facility_type"] in ("DH", "CHC") else 6,
                    "broken": 0,
                    "trust": trust_status,
                },
                "personnel": {
                    "sanctioned": 22 if s_fac["facility_type"] in ("DH", "CHC") else 8,
                    "present": 19 if s_fac["facility_type"] in ("DH", "CHC") else 7,
                    "absent": 3 if s_fac["facility_type"] in ("DH", "CHC") else 1,
                    "ghost_flagged": 0,
                    "trust": trust_status,
                },
            },
        })

    # Add 20 synthetic facilities mapped to Bastar bounds
    for i in range(20):
        fac_code = f"FAC{i:03d}"
        if fac_code in seen_ids:
            continue
        seen_ids.add(fac_code)

        # Spread systematically across Bastar latitude & longitude
        norm_x = (i * 17) % 100
        norm_y = (i * 23) % 100
        lat_val = round(18.96 + (norm_y / 100.0) * 0.32, 6)
        lon_val = round(81.72 + (norm_x / 100.0) * 0.42, 6)
        tier_val = "DH" if i in (0, 4) else ("CHC" if i % 3 == 0 else "PHC")

        q_item = queue_map.get(fac_code)
        att = latest_attestations.get(fac_code)

        if att and ("ATTEST" in att.get("event_type", "")):
            trust_status = "VERIFIED"
            hours_ago = _calc_hours_ago(att.get("recorded_at"))
            freshness_str = f"{hours_ago:.0f}h ago" if hours_ago is not None else "Verified"
            observed = att.get("payload", {}).get("observed", {})
            reported = float(observed.get("present_quantity", att["payload"].get("present_quantity", 1200.0)))
            verified = float(observed.get("usable_quantity", att["payload"].get("usable_quantity", 1200.0)))
            p_w = 0.0
            last_eid = att["event_id"]
        elif q_item and q_item.get("selected"):
            trust_status = "HIGH_RISK" if q_item["p_wrong"] >= 0.35 else "UNVERIFIED"
            freshness_str = f"{int(q_item['hidden_stockout_days'])}d hidden risk"
            reported = float(getattr(q_item, "reported_stock", 850.0) or 850.0)
            verified = None
            p_w = q_item["p_wrong"]
            last_eid = None
        else:
            trust_status = "VERIFIED" if i % 2 == 0 else "UNVERIFIED"
            freshness_str = "18h ago" if trust_status == "VERIFIED" else "14d uninspected"
            reported = 800.0 + i * 40.0
            verified = 780.0 + i * 40.0 if trust_status == "VERIFIED" else None
            p_w = 0.08 if trust_status == "VERIFIED" else 0.28
            last_eid = None

        is_target = bool(q_item and q_item.get("selected"))
        target_rank = q_item["rank"] if is_target else None

        unified_facilities.append({
            "facility_id": fac_code,
            "osm_id": None,
            "name": f"{tier_val} {chr(65 + i)} ({fac_code})",
            "facility_type": tier_val,
            "tier": tier_val,
            "district": "Bastar",
            "lat": lat_val,
            "lon": lon_val,
            "amenity": "hospital" if tier_val in ("DH", "CHC") else "clinic",
            "trust_status": trust_status,
            "trust_score": round(1.0 - p_w, 3),
            "p_wrong": p_w,
            "freshness": freshness_str,
            "freshness_hours": 18.0 if trust_status == "VERIFIED" else 336.0,
            "last_attestation_id": last_eid,
            "reported_stock": reported,
            "verified_usable_stock": verified,
            "is_target": is_target,
            "is_verification_target": is_target,
            "target_rank": target_rank,
            "target_reason": q_item["reason"] if q_item else None,
            "queue_score": q_item["expected_value"] if q_item else None,
            "queue_rank": target_rank,
            "queue_reasons": [q_item["reason"]] if q_item and q_item.get("reason") else [],
            "resources": {
                "medicines": {
                    "sku": "MED-ARV-01",
                    "reported": reported,
                    "verified": verified,
                    "trust": trust_status,
                },
                "beds": {
                    "total": 40 if tier_val in ("DH", "CHC") else 15,
                    "occupied": 32 if tier_val in ("DH", "CHC") else 10,
                    "available": 8 if tier_val in ("DH", "CHC") else 5,
                    "broken": 0,
                    "trust": trust_status,
                },
                "personnel": {
                    "sanctioned": 15 if tier_val in ("DH", "CHC") else 6,
                    "present": 13 if tier_val in ("DH", "CHC") else 5,
                    "absent": 2 if tier_val in ("DH", "CHC") else 1,
                    "ghost_flagged": 0,
                    "trust": trust_status,
                },
            },
        })

    # Add real OSM facilities from cached JSON
    for osm_f in raw_facilities:
        osm_id = str(osm_f.get("osm_id", ""))
        fac_code = f"FAC_BASTAR_{osm_id}"
        if fac_code in seen_ids or osm_id in seen_ids:
            continue
        seen_ids.add(fac_code)

        amenity = osm_f.get("amenity", "clinic")
        if "hospital" in amenity.lower():
            fac_type = "DH" if "district" in osm_f.get("name", "").lower() or "medical" in osm_f.get("name", "").lower() else "CHC"
        else:
            fac_type = "PHC"

        q_item = queue_map.get(fac_code) or queue_map.get(osm_id)
        att = latest_attestations.get(fac_code) or latest_attestations.get(osm_id)

        if att and ("ATTEST" in att.get("event_type", "")):
            trust_status = "VERIFIED"
            hours_ago = _calc_hours_ago(att.get("recorded_at"))
            freshness_str = f"{hours_ago:.0f}h ago" if hours_ago is not None else "Verified"
            observed = att.get("payload", {}).get("observed", {})
            reported = float(observed.get("present_quantity", att["payload"].get("present_quantity", 950.0)))
            verified = float(observed.get("usable_quantity", att["payload"].get("usable_quantity", 950.0)))
            p_w = 0.0
            last_eid = att["event_id"]
        elif q_item and q_item.get("selected"):
            trust_status = "HIGH_RISK" if q_item["p_wrong"] >= 0.35 else "UNVERIFIED"
            freshness_str = f"{int(q_item['hidden_stockout_days'])}d unverified"
            reported = 620.0
            verified = None
            p_w = q_item["p_wrong"]
            last_eid = None
        else:
            # Deterministic hash assignment based on OSM ID for consistent state
            h_val = int(osm_id[-2:]) if len(osm_id) >= 2 and osm_id[-2:].isdigit() else 10
            if h_val % 4 == 0:
                trust_status = "VERIFIED"
                freshness_str = "2 days ago"
                p_w = 0.07
                reported = 1100.0
                verified = 1080.0
                last_eid = f"EVT-HIST-{osm_id[:6]}"
            elif h_val % 7 == 0:
                trust_status = "HIGH_RISK"
                freshness_str = "60d stale (high variance)"
                p_w = 0.58
                reported = 2200.0
                verified = 0.0
                last_eid = None
            else:
                trust_status = "UNVERIFIED"
                freshness_str = "15d unverified claim"
                p_w = 0.22
                reported = 750.0
                verified = None
                last_eid = None

        is_target = bool(q_item and q_item.get("selected"))
        target_rank = q_item["rank"] if is_target else None

        unified_facilities.append({
            "facility_id": fac_code,
            "osm_id": osm_f.get("osm_id"),
            "name": osm_f.get("name") or f"Health Centre {osm_id[-4:]}",
            "facility_type": fac_type,
            "tier": fac_type,
            "district": "Bastar",
            "lat": float(osm_f.get("lat", 19.07)),
            "lon": float(osm_f.get("lon", 82.02)),
            "amenity": amenity,
            "trust_status": trust_status,
            "trust_score": round(1.0 - p_w, 3),
            "p_wrong": p_w,
            "freshness": freshness_str,
            "freshness_hours": 48.0 if trust_status == "VERIFIED" else 360.0,
            "last_attestation_id": last_eid,
            "reported_stock": reported,
            "verified_usable_stock": verified,
            "is_target": is_target,
            "is_verification_target": is_target,
            "target_rank": target_rank,
            "target_reason": q_item["reason"] if q_item else None,
            "queue_score": q_item["expected_value"] if q_item else None,
            "queue_rank": target_rank,
            "queue_reasons": [q_item["reason"]] if q_item and q_item.get("reason") else [],
            "resources": {
                "medicines": {
                    "sku": "MED-ARV-01",
                    "reported": reported,
                    "verified": verified,
                    "trust": trust_status,
                },
                "beds": {
                    "total": 30 if fac_type in ("DH", "CHC") else 10,
                    "occupied": 24 if fac_type in ("DH", "CHC") else 6,
                    "available": 6 if fac_type in ("DH", "CHC") else 4,
                    "broken": 0,
                    "trust": trust_status,
                },
                "personnel": {
                    "sanctioned": 12 if fac_type in ("DH", "CHC") else 4,
                    "present": 10 if fac_type in ("DH", "CHC") else 3,
                    "absent": 2 if fac_type in ("DH", "CHC") else 1,
                    "ghost_flagged": 0,
                    "trust": trust_status,
                },
            },
        })

    # Apply filters if provided
    filtered_facilities = unified_facilities
    if facility_type_filter and facility_type_filter != "ALL":
        filtered_facilities = [
            f for f in filtered_facilities
            if f["facility_type"].upper() == facility_type_filter.upper()
        ]

    # Compute summary stats
    verified_cnt = sum(1 for f in filtered_facilities if f["trust_status"] == "VERIFIED")
    unverified_cnt = sum(1 for f in filtered_facilities if f["trust_status"] == "UNVERIFIED")
    high_risk_cnt = sum(1 for f in filtered_facilities if f["trust_status"] == "HIGH_RISK")
    target_cnt = sum(1 for f in filtered_facilities if f["is_verification_target"])

    return {
        "fetch_status": fetch_status,
        "data_environment": "SYNTHETIC_DEMO",
        "resource_state_provenance": "GENERATED_DEMO_VALUES",
        "facility_coordinates_provenance": "MIXED_OPENSTREETMAP_AND_SYNTHETIC_ANCHORS",
        "advisory": "Trust states, stock, bed/personnel counts, and selected targets in this response are demo values, not live facility reports. OSM geography does not validate operational data.",
        "count": len(filtered_facilities),
        "total_unfiltered_count": len(unified_facilities),
        "facilities": filtered_facilities,
        "attribution": "© OpenStreetMap contributors, ODbL. Synthetic TATHYON map/resource demo; boundary is illustrative.",
        "fetched_at": fetched_at or now(),
        "district_boundary": BASTAR_DISTRICT_BOUNDARY,
        "stats": {
            "total": len(filtered_facilities),
            "verified": verified_cnt,
            "unverified": unverified_cnt,
            "high_risk": high_risk_cnt,
            "targets": target_cnt,
        },
    }


def get_operational_routes_dataset(registry: Any) -> Dict[str, Any]:
    """Returns generated route illustrations; this dataset is not dispatch authority."""
    routes: List[Dict[str, Any]] = []

    # Map of coordinates by facility_id for quick geometry lookup
    fac_coords: Dict[str, Dict[str, float]] = {
        "PHC_X": {"lat": 19.1245, "lon": 81.8820, "name": "PHC Tokapal (PHC X)"},
        "PHC_Y": {"lat": 19.2512, "lon": 82.0234, "name": "PHC Bakawand (PHC Y)"},
        "PHC_Z": {"lat": 19.0788, "lon": 82.0164, "name": "CHC Jagdalpur (PHC Z)"},
        "DH_CENTRAL_HOSPITAL": {"lat": 19.0820, "lon": 82.0280, "name": "District Hospital Jagdalpur"},
        "CHC_RURAL_NORTH": {"lat": 19.3450, "lon": 81.9540, "name": "CHC Rural North"},
        "PHC_REMOTE_EAST": {"lat": 19.2100, "lon": 82.3400, "name": "PHC Remote East"},
        "DWH_DISTRICT_DEPOT_01": {"lat": 19.0750, "lon": 82.0150, "name": "District Warehouse Depot"},
    }

    # 1. Medicine Transfers from registry.plans
    has_medicine_transfers = False
    if hasattr(registry, "plans") and registry.plans:
        for plan_id, plan in registry.plans.items():
            for t in plan.get("transfers", []):
                from_id = t.get("from_facility", "PHC_Z")
                to_id = t.get("to_facility", "PHC_Y")
                from_c = fac_coords.get(from_id, {"lat": 19.0788, "lon": 82.0164, "name": from_id})
                to_c = fac_coords.get(to_id, {"lat": 19.2512, "lon": 82.0234, "name": to_id})

                # Compute or generate route geometry
                dist_km = 24.3
                dur_hrs = 0.65
                geom = [
                    [from_c["lon"], from_c["lat"]],
                    [round((from_c["lon"] + to_c["lon"]) / 2 + 0.015, 6), round((from_c["lat"] + to_c["lat"]) / 2 - 0.01, 6)],
                    [to_c["lon"], to_c["lat"]],
                ]

                routes.append({
                    "route_id": f"ROUTE-MED-{t.get('line_id', plan_id[:8])}",
                    "type": "MEDICINE_TRANSFER",
                    "resource_type": "MEDICINES",
                    "from_facility_id": from_id,
                    "from_facility_name": from_c.get("name", from_id),
                    "from_lat": from_c["lat"],
                    "from_lon": from_c["lon"],
                    "to_facility_id": to_id,
                    "to_facility_name": to_c.get("name", to_id),
                    "to_lat": to_c["lat"],
                    "to_lon": to_c["lon"],
                    "resource_key": t.get("resource", "MED-ARV-01"),
                    "quantity": float(t.get("quantity", 1900.0)),
                    "unit": "vials",
                    "approval_status": "DEMO_ONLY",
                    "plan_id": plan_id,
                    "distance_km": dist_km,
                    "duration_hours": dur_hrs,
                    "geometry": geom,
                    "is_real_road_route": False,
                    "route_geometry_status": "GENERATED_DEMO_GEOMETRY_NOT_ROUTED",
                    "description": f"Synthetic demo transfer illustration of {t.get('quantity', 1900.0)} units from {from_id} to {to_id}; not authorized or executable.",
                })
                has_medicine_transfers = True

    # If no plans exist yet, provide the primary approved reference transfer route
    if not has_medicine_transfers:
        routes.append({
            "route_id": "ROUTE-MED-LINE-1",
            "type": "MEDICINE_TRANSFER",
            "resource_type": "MEDICINES",
            "from_facility_id": "PHC_Z",
            "from_facility_name": "CHC Jagdalpur (Verified Donor PHC Z)",
            "from_lat": 19.0788,
            "from_lon": 82.0164,
            "to_facility_id": "PHC_Y",
            "to_facility_name": "PHC Bakawand (Stockout Imminent PHC Y)",
            "to_lat": 19.2512,
            "to_lon": 82.0234,
            "resource_key": "MED-ARV-01",
            "quantity": 1900.0,
            "unit": "vials",
            "approval_status": "DEMO_ONLY",
            "plan_id": "plan_baseline_approved",
            "distance_km": 24.3,
            "duration_hours": 0.65,
            "geometry": [
                [82.0164, 19.0788],
                [82.0210, 19.1450],
                [82.0195, 19.1980],
                [82.0234, 19.2512],
            ],
            "is_real_road_route": False,
            "route_geometry_status": "GENERATED_DEMO_GEOMETRY_NOT_ROUTED",
            "description": "Synthetic demo cold-chain transfer illustration; not approved, road-routed, or executable.",
        })

    # 2. Patient Diversions (Beds workflow)
    # Authorized route data from saturated hospital to safe recipient
    routes.append({
        "route_id": "ROUTE-BEDS-DIVERT-01",
        "type": "PATIENT_DIVERSION",
        "resource_type": "BEDS",
        "from_facility_id": "DH_CENTRAL_HOSPITAL",
        "from_facility_name": "District Hospital (Saturated ICU)",
        "from_lat": 19.0820,
        "from_lon": 82.0280,
        "to_facility_id": "CHC_RURAL_NORTH",
        "to_facility_name": "CHC Rural North (Demo Capacity Example)",
        "to_lat": 19.3450,
        "to_lon": 81.9540,
        "ward_type": "ICU_O2 (High-Dependency Oxygen)",
        "patients_diverted": 4,
        "unit": "patients",
        "approval_status": "DEMO_ONLY",
        "plan_id": "bed_divert_20260929",
        "distance_km": 36.2,
        "duration_hours": 0.85,
        "geometry": [
            [82.0280, 19.0820],
            [82.0100, 19.1650],
            [81.9820, 19.2600],
            [81.9540, 19.3450],
        ],
        "is_real_road_route": False,
                    "route_geometry_status": "GENERATED_DEMO_GEOMETRY_NOT_ROUTED",
        "description": "Synthetic patient-diversion illustration; destination capacity is not verified and this is not an approved or executable movement.",
    })

    # 3. Staff Redeployments (Personnel workflow)
    # Role-level routes strictly without personal names or PII
    routes.append({
        "route_id": "ROUTE-STAFF-REDEPLOY-01",
        "type": "STAFF_REDEPLOYMENT",
        "resource_type": "PERSONNEL",
        "from_facility_id": "DH_CENTRAL_HOSPITAL",
        "from_facility_name": "District Hospital (Staff Surplus)",
        "from_lat": 19.0820,
        "from_lon": 82.0280,
        "to_facility_id": "PHC_REMOTE_EAST",
        "to_facility_name": "PHC Remote East (Staff Deficit)",
        "to_lat": 19.2100,
        "to_lon": 82.3400,
        "staff_role": "General Duty Medical Officer (GDMO)",
        "staff_count": 2,
        "unit": "personnel",
        "approval_status": "DEMO_ONLY",
        "plan_id": "staff_redeploy_20260929",
        "distance_km": 38.5,
        "duration_hours": 0.90,
        "geometry": [
            [82.0280, 19.0820],
            [82.1250, 19.1300],
            [82.2400, 19.1750],
            [82.3400, 19.2100],
        ],
        "is_real_road_route": False,
                    "route_geometry_status": "GENERATED_DEMO_GEOMETRY_NOT_ROUTED",
        "description": "Synthetic staff-redeployment illustration; not an approved or executable movement.",
    })

    return {
        "status": "OK",
        "data_environment": "SYNTHETIC_DEMO",
        "routes_provenance": "GENERATED_DEMO_ROUTES; NOT AUTHORIZED OR EXECUTABLE",
        "count": len(routes),
        "route_count": len(routes),
        "routes": routes,
        "attribution": "Example routes are synthetic and not road-routed. Base geography attribution: © OpenStreetMap contributors, ODbL.",
        "timestamp": now(),
    }
