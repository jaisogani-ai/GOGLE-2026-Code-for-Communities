"""Map layers built only from workspace state, each item carrying provenance.

Markers: one per registered facility; nothing decorative. Routes: plan lines
and shipments. Geometry is a straight line labelled SYNTHETIC_STRAIGHT_LINE
unless GOOGLE_MAPS_SERVER_KEY is set, in which case the Google Routes API is
called server-side (the key never reaches the browser) and the result is
labelled with its provider. A provider failure falls back to the labelled
straight line; it is never silently presented as a road route.
"""
from __future__ import annotations

import os
import threading
from typing import Optional

import httpx

from tathyon.workspace import Workspace

from .env import maps_browser_key, maps_server_key

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
ROUTES_TIMEOUT_S = 5.0
_cache: dict[tuple, dict] = {}
_cache_lock = threading.Lock()


def maps_status() -> dict:
    browser = bool(maps_browser_key())
    server = bool(maps_server_key())
    return {
        "basemap": "GOOGLE_MAPS_JS" if browser else "OPENSTREETMAP_TILES",
        "browser_key": "CONFIGURED_REFERRER_RESTRICTION_REQUIRED" if browser else "NOT_CONFIGURED",
        "routes_provider": "GOOGLE_ROUTES_API" if server else "NOT_CONFIGURED",
        "route_fallback": "SYNTHETIC_STRAIGHT_LINE",
    }


def _decode_polyline(encoded: str) -> list[list[float]]:
    coords, index, lat, lng = [], 0, 0, 0
    while index < len(encoded):
        for is_lng in (False, True):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lng:
                lng += delta
            else:
                lat += delta
        coords.append([lat / 1e5, lng / 1e5])
    return coords


def route_geometry(a: dict, b: dict) -> dict:
    straight = {"path": [[a["lat"], a["lon"]], [b["lat"], b["lon"]]],
                "provenance": "SYNTHETIC_STRAIGHT_LINE", "distance_km": None, "duration_min": None}
    key = maps_server_key()
    if not key:
        return straight
    ck = (a["lat"], a["lon"], b["lat"], b["lon"])
    with _cache_lock:
        if ck in _cache:
            return _cache[ck]
    body = {"origin": {"location": {"latLng": {"latitude": a["lat"], "longitude": a["lon"]}}},
            "destination": {"location": {"latLng": {"latitude": b["lat"], "longitude": b["lon"]}}},
            "travelMode": "DRIVE"}
    try:
        resp = httpx.post(ROUTES_URL, json=body, timeout=ROUTES_TIMEOUT_S, headers={
            "X-Goog-Api-Key": key,
            "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline"})
        resp.raise_for_status()
        route = resp.json()["routes"][0]
        out = {"path": _decode_polyline(route["polyline"]["encodedPolyline"]),
               "provenance": "GOOGLE_ROUTES_API",
               "distance_km": round(route.get("distanceMeters", 0) / 1000, 1),
               "duration_min": round(float(str(route.get("duration", "0s")).rstrip("s")) / 60, 1)}
    except Exception as exc:
        return {**straight, "provenance": "SYNTHETIC_STRAIGHT_LINE_ROUTE_PROVIDER_FAILED",
                "provider_error": type(exc).__name__}
    with _cache_lock:
        _cache[ck] = out
    return out


def map_layers(ws: Workspace, sku: Optional[str] = None, block: Optional[str] = None,
               tier: Optional[str] = None) -> dict:
    queue = {(r["facility_id"], r["sku"]): r for r in ws.trust_queue(sku)["rows"]} if ws.skus else {}
    markers = []
    for fid, f in sorted(ws.facilities.items()):
        if block and f.get("block") != block or tier and f.get("tier") != tier:
            continue
        rows = [r for (fac, s), r in queue.items() if fac == fid and (not sku or s == sku)]
        worst = min((r["runway_days"] for r in rows if r["runway_days"] is not None), default=None)
        markers.append({
            "facility_id": fid, "name": f["name"], "tier": f["tier"], "block": f.get("block"),
            "lat": f["lat"], "lon": f["lon"], "has_cold_chain": f.get("has_cold_chain", True),
            "coordinates_provenance": f.get("coordinates_provenance"), "registry_event_id": f.get("event_id"),
            "resources": [{k: r[k] for k in ("sku", "verification_state", "usable_estimate", "reported_now",
                                            "runway_days", "recommended_action", "reason", "report_provenance",
                                            "evidence_confidence", "last_finding")} for r in rows],
            "min_runway_days": worst,
            "is_verification_target": any(r["recommended_action"] == "VERIFY" for r in rows),
            "is_recipient": any(r["is_recipient"] for r in rows),
            "has_phantom_finding": any(r["last_finding"] == "PHANTOM_STOCK" for r in rows),
        })
    routes = []
    live_statuses = ("PROPOSED", "APPROVED", "APPROVED_CONTINGENT", "RELEASED_BREAK_GLASS", "DISPATCHED",
                     "DELAYED", "RECEIVED", "INVALIDATED")
    for p in sorted(ws.plans.values(), key=lambda p: p["version"]):
        if p["status"] in ("REJECTED",) or (sku and p["sku"] != sku):
            continue
        for l in p["lines"]:
            if l["status"] not in live_statuses:
                continue
            a, b = ws.facilities.get(l["from_facility"]), ws.facilities.get(l["to_facility"])
            if not a or not b:
                continue
            sh = ws.shipments.get(l.get("shipment_id") or "")
            routes.append({"plan_id": p["plan_id"], "plan_version": p["version"], "plan_status": p["status"],
                           "line_id": l["line_id"], "from": l["from_facility"], "to": l["to_facility"],
                           "qty": l["qty"], "kind": l["kind"], "line_status": l["status"],
                           "shipment_status": sh["status"] if sh else None, "eta": sh["eta"] if sh else None,
                           "planner_travel_hours": l["travel_hours"],
                           "planner_travel_basis": l["route_provenance"],
                           **route_geometry(a, b)})
    blocks = sorted({f.get("block") for f in ws.facilities.values() if f.get("block")})
    tiers = sorted({f["tier"] for f in ws.facilities.values()})
    return {"environment": ws.environment, "as_of": ws.clock.isoformat(), "markers": markers, "routes": routes,
            "filters": {"blocks": blocks, "tiers": tiers, "skus": sorted(ws.skus)}, "maps": maps_status()}


_TILES_TTL_S = 600
_tiles_cache: dict = {}


def tiles3d_status(origin: str = "http://127.0.0.1:8000/") -> dict:
    """Probe Google Photorealistic 3D Tiles once per 10 min. The key value is never returned."""
    import time
    key = maps_browser_key()
    if not key:
        return {"state": "NOT_CONFIGURED", "reason": "GOOGLE_MAPS_BROWSER_KEY (or GOOGLE_MAPS_API_KEY) is not set."}
    cached = _tiles_cache.get(origin)
    if cached and time.monotonic() - cached[0] < _TILES_TTL_S:
        return cached[1]
    try:
        r = httpx.get("https://tile.googleapis.com/v1/3dtiles/root.json", params={"key": key},
                      headers={"Referer": origin}, timeout=ROUTES_TIMEOUT_S)
        if r.status_code == 200:
            out = {"state": "AVAILABLE"}
        else:
            msg = r.json().get("error", {}).get("message", "") if "json" in r.headers.get("content-type", "") else ""
            reason = ("Map Tiles API is not enabled for this key's project." if "not been used" in msg or
                      "disabled" in msg else "The key is not allowed to call the 3D Tiles API (key restrictions).")
            out = {"state": "UNAVAILABLE", "http_status": r.status_code, "reason": reason}
    except Exception as exc:
        out = {"state": "UNAVAILABLE", "reason": f"Probe failed: {type(exc).__name__}"}
    _tiles_cache[origin] = (time.monotonic(), out)
    return out
