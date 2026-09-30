"""
Real road routing from OSRM (OpenStreetMap road network). No API key.

WHAT THIS IS: distance, duration and road geometry computed by the Open Source Routing
Machine over OpenStreetMap roads. Verified working 2026-09-21 (Jagdalpur GMC -> Ghotiya:
37.6 km, real road geometry).

WHAT THIS IS NOT (kept in the data so the UI cannot overclaim):
  - Not traffic-aware. Durations use the OSRM car profile's static speeds.
  - Not a truck / cold-chain-vehicle profile. Real transit is usually slower.
  - The public demo server is best-effort with a fair-use policy: every route is cached
    to disk so a route is fetched once, and a failure degrades to an explicitly labelled
    straight-line estimate, never to a fake "live" route.
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Optional

OSRM_ENDPOINT = "https://router.project-osrm.org"
USER_AGENT = "Tathyon/0.1 (healthcare supply-chain research)"
ATTRIBUTION = "Routing © OSRM; road data © OpenStreetMap contributors, ODbL"

SOURCE_OSRM = "OSRM_OSM_DRIVING_ESTIMATE"
SOURCE_STRAIGHT_LINE = "SYNTHETIC_STRAIGHT_LINE_ESTIMATE"
STRAIGHT_LINE_KMH = 40.0


@dataclass(frozen=True)
class RoadRoute:
    origin_id: str
    dest_id: str
    distance_km: float
    duration_hours: float
    geometry: tuple             # ((lon, lat), ...)
    source: str                 # SOURCE_OSRM | SOURCE_STRAIGHT_LINE
    fetched_at: str
    traffic_aware: bool = False
    vehicle_profile: str = "OSRM_CAR (not a truck profile)"
    attribution: str = ATTRIBUTION

    @property
    def is_real_road_route(self) -> bool:
        return self.source == SOURCE_OSRM

    def to_dict(self) -> dict:
        d = asdict(self)
        d["geometry"] = [list(p) for p in self.geometry]
        d["is_real_road_route"] = self.is_real_road_route
        return d

    @staticmethod
    def from_dict(d: dict) -> "RoadRoute":
        return RoadRoute(
            origin_id=d["origin_id"], dest_id=d["dest_id"], distance_km=d["distance_km"],
            duration_hours=d["duration_hours"], geometry=tuple(tuple(p) for p in d["geometry"]),
            source=d["source"], fetched_at=d["fetched_at"], traffic_aware=d.get("traffic_aware", False),
            vehicle_profile=d.get("vehicle_profile", "OSRM_CAR (not a truck profile)"),
            attribution=d.get("attribution", ATTRIBUTION),
        )


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _http_get(url: str, timeout_s: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:          # noqa: S310 (fixed https endpoint)
        return resp.read().decode()


def parse_osrm_route(payload: str, origin_id: str, dest_id: str, fetched_at: str) -> RoadRoute:
    data = json.loads(payload)
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"OSRM returned no route: code={data.get('code')}")
    r = data["routes"][0]
    coords = r.get("geometry", {}).get("coordinates") or []
    if len(coords) < 2:
        raise ValueError("OSRM route has no geometry")
    return RoadRoute(
        origin_id=origin_id, dest_id=dest_id, distance_km=round(r["distance"] / 1000.0, 3),
        duration_hours=round(r["duration"] / 3600.0, 4),
        geometry=tuple((float(x), float(y)) for x, y in coords), source=SOURCE_OSRM, fetched_at=fetched_at,
    )


def straight_line_route(origin_id: str, dest_id: str, o: tuple[float, float], d: tuple[float, float],
                        fetched_at: str) -> RoadRoute:
    """Fallback when OSRM is unreachable. Clearly synthetic: a straight line, not a road."""
    km = haversine_km(o[0], o[1], d[0], d[1])
    return RoadRoute(origin_id, dest_id, round(km, 3), round(km / STRAIGHT_LINE_KMH, 4),
                     ((o[1], o[0]), (d[1], d[0])), SOURCE_STRAIGHT_LINE, fetched_at,
                     vehicle_profile="STRAIGHT_LINE_40KMH (not a road)")


class OSRMClient:
    def __init__(self, endpoint: str = OSRM_ENDPOINT, get: Optional[Callable[[str, float], str]] = None,
                 timeout_s: float = 20.0, retries: int = 1, sleep: Callable[[float], None] = time.sleep):
        self.endpoint, self._get, self.timeout_s = endpoint.rstrip("/"), get or _http_get, timeout_s
        self.retries, self._sleep = retries, sleep

    def route(self, origin_id: str, dest_id: str, o: tuple[float, float], d: tuple[float, float],
              fetched_at: str) -> RoadRoute:
        """o and d are (lat, lon). Raises on failure; callers decide how to degrade."""
        url = (f"{self.endpoint}/route/v1/driving/{o[1]},{o[0]};{d[1]},{d[0]}"
               f"?overview=full&geometries=geojson")
        last: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                return parse_osrm_route(self._get(url, self.timeout_s), origin_id, dest_id, fetched_at)
            except Exception as exc:                                     # noqa: BLE001
                last = exc
                if attempt < self.retries:
                    self._sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"OSRM unavailable: {last}")
