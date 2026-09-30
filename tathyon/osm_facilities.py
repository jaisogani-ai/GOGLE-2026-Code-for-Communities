"""
Real healthcare-facility coordinates from OpenStreetMap (Overpass API).

This is the ONE live external source wired into Tathyon today. Verified working on
2026-09-21 against Bastar district, Chhattisgarh: real named facilities with real
coordinates (e.g. "Government Medical College Hospital, Bastar").

Honest limits, enforced in code:
  - OpenStreetMap is COMMUNITY-MAPPED data under ODbL. It is NOT a government facility
    registry, coverage is uneven, and an unmapped PHC simply will not appear. Every
    record carries source="OPENSTREETMAP" and its OSM id so it can be checked.
  - Overpass rejects large bounding boxes with HTTP 504. Queries are tiled, and a tile
    that fails is REPORTED as failed rather than silently dropped, so callers can tell
    "no facilities there" apart from "we could not ask".
  - Nothing here invents a facility. If the network is unavailable the result is empty
    with fetch_status=UNAVAILABLE.

This module performs network I/O only when fetch_osm_facilities() is called with a
transport. Tests inject a fake transport; nothing hits the network during the suite.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable, Optional

from .integrations import LIVE_EXTERNAL

OVERPASS_ENDPOINT = "https://overpass-api.de/api/interpreter"
ATTRIBUTION = "© OpenStreetMap contributors, ODbL"
USER_AGENT = "Tathyon/0.1 (healthcare supply-chain research)"

# Tags we accept. "pharmacy"/"doctors" are included for context but are not PHCs.
FACILITY_AMENITIES = ("hospital", "clinic", "doctors", "pharmacy")
# Many Indian health centres are tagged healthcare=* with no amenity tag (e.g. "CHC TOKAPAL").
FACILITY_HEALTHCARE = ("hospital", "clinic", "centre", "doctor")

FETCH_OK = "OK"
FETCH_PARTIAL = "PARTIAL"           # some tiles failed
FETCH_UNAVAILABLE = "UNAVAILABLE"   # every tile failed

# Overpass 504s on wide areas; keep tiles small.
MAX_TILE_DEGREES = 0.5


@dataclass(frozen=True)
class OSMFacility:
    osm_type: str
    osm_id: int
    name: Optional[str]
    amenity: str
    lat: float
    lon: float
    source: str = "OPENSTREETMAP"
    provenance: str = LIVE_EXTERNAL
    attribution: str = ATTRIBUTION
    fetched_at: str = ""
    raw_tags: dict = field(default_factory=dict)

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/{self.osm_type}/{self.osm_id}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["osm_url"] = self.osm_url
        return d


def classify_facility(f: "OSMFacility") -> str:
    """Facility class inferred ONLY from OSM name/description text.

    OSM has no PHC/CHC field. 'CHC TOKAPAL' with description 'Community Health Center' is
    classified CHC because the text says so; anything else stays HOSPITAL or CLINIC. This is
    an inference from community-entered text, not a government classification.
    """
    text = f"{f.name or ''} {f.raw_tags.get('description', '')}".lower()
    if "subcentre" in text or "sub centre" in text or "sub-centre" in text or "subcenter" in text:
        return "SUB_CENTRE"
    if "phc" in text or "primary health" in text:
        return "PHC"
    if "chc" in text or "community health" in text:
        return "CHC"
    if f.amenity == "hospital":
        return "HOSPITAL"
    return "CLINIC"


@dataclass
class OSMFetchResult:
    fetch_status: str
    facilities: list[OSMFacility]
    tiles_requested: int
    tiles_failed: int
    failures: list[str]
    endpoint: str
    attribution: str = ATTRIBUTION

    @property
    def named(self) -> list[OSMFacility]:
        return [f for f in self.facilities if f.name]

    def to_dict(self) -> dict:
        return {
            "fetch_status": self.fetch_status,
            "provenance": LIVE_EXTERNAL if self.facilities else "NOT_CONFIGURED",
            "count": len(self.facilities),
            "named_count": len(self.named),
            "tiles_requested": self.tiles_requested,
            "tiles_failed": self.tiles_failed,
            "failures": self.failures,
            "endpoint": self.endpoint,
            "attribution": self.attribution,
            "facilities": [f.to_dict() for f in self.facilities],
        }


def tile_bbox(south: float, west: float, north: float, east: float,
              step: float = MAX_TILE_DEGREES) -> list[tuple[float, float, float, float]]:
    """Split a bounding box into Overpass-sized tiles."""
    if south >= north or west >= east:
        raise ValueError("bbox must satisfy south < north and west < east")
    if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
        raise ValueError("bbox out of geographic range")
    if step <= 0:
        raise ValueError("step must be > 0")
    tiles, lat = [], south
    while lat < north:
        lon = west
        top = min(lat + step, north)
        while lon < east:
            right = min(lon + step, east)
            tiles.append((lat, lon, top, right))
            lon = right
        lat = top
    return tiles


def build_query(bbox: tuple[float, float, float, float], timeout_s: int = 25,
                amenities: Iterable[str] = FACILITY_AMENITIES, limit: int = 40) -> str:
    s, w, n, e = bbox
    pattern = "|".join(amenities)
    return (
        f'[out:json][timeout:{timeout_s}];'
        f'(node["amenity"~"^({pattern})$"]({s},{w},{n},{e});'
        f'way["amenity"~"^({pattern})$"]({s},{w},{n},{e});'
        f'node["healthcare"~"^({"|".join(FACILITY_HEALTHCARE)})$"][!"amenity"]({s},{w},{n},{e});'
        f'way["healthcare"~"^({"|".join(FACILITY_HEALTHCARE)})$"][!"amenity"]({s},{w},{n},{e}););'
        f'out center {limit};'
    )


def _http_transport(query: str, endpoint: str, timeout_s: float) -> str:
    data = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(endpoint, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:   # noqa: S310 (fixed endpoint)
        return resp.read().decode()


def parse_elements(payload: str, fetched_at: str) -> list[OSMFacility]:
    """Parse one Overpass JSON response. Malformed or incomplete elements are skipped."""
    out = []
    for el in json.loads(payload).get("elements", []):
        tags = el.get("tags") or {}
        amenity = tags.get("amenity") or tags.get("healthcare")
        centre = el.get("center") or el
        lat, lon = centre.get("lat"), centre.get("lon")
        if not amenity or lat is None or lon is None or el.get("id") is None:
            continue
        out.append(OSMFacility(
            osm_type=el.get("type", "node"), osm_id=int(el["id"]), name=tags.get("name"),
            amenity=amenity, lat=float(lat), lon=float(lon), fetched_at=fetched_at,
            raw_tags=tags,
        ))
    return out


def fetch_osm_facilities(
    bbox: tuple[float, float, float, float],
    fetched_at: str,
    transport: Optional[Callable[[str, str, float], str]] = None,
    endpoint: str = OVERPASS_ENDPOINT,
    timeout_s: float = 70.0,
    step: float = MAX_TILE_DEGREES,
    retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> OSMFetchResult:
    """Fetch real facilities for a bounding box. Never invents data.

    `transport` is injected in tests; the default performs a real HTTP request.
    """
    send = transport or _http_transport
    tiles = tile_bbox(*bbox, step=step)
    facilities: dict[tuple[str, int], OSMFacility] = {}
    failures: list[str] = []

    for t in tiles:
        last_error = ""
        for attempt in range(retries + 1):
            try:
                payload = send(build_query(t), endpoint, timeout_s)
                for f in parse_elements(payload, fetched_at):
                    facilities[(f.osm_type, f.osm_id)] = f      # de-duplicate across tiles
                last_error = ""
                break
            except Exception as exc:                             # noqa: BLE001
                last_error = f"tile {t}: {type(exc).__name__}: {exc}"
                if attempt < retries:
                    sleep(2.0 * (attempt + 1))                   # Overpass 504s are transient
        if last_error:
            failures.append(last_error)

    if failures and len(failures) == len(tiles):
        status = FETCH_UNAVAILABLE
    elif failures:
        status = FETCH_PARTIAL
    else:
        status = FETCH_OK
    return OSMFetchResult(
        fetch_status=status, facilities=sorted(facilities.values(), key=lambda f: (f.osm_id,)),
        tiles_requested=len(tiles), tiles_failed=len(failures), failures=failures, endpoint=endpoint,
    )


# ------------------------------------------------------------------ disk cache
def save_cache(result: OSMFetchResult, path: str, fetched_at: str) -> None:
    """Persist a fetch so the app works offline afterwards. The cache keeps its fetch time,
    so nothing cached is ever presented as freshly live."""
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump({"fetched_at": fetched_at, "fetch_status": result.fetch_status,
                   "attribution": ATTRIBUTION, "endpoint": result.endpoint,
                   "facilities": [f.to_dict() for f in result.facilities]}, fh, indent=1)


def load_cache(path: str) -> Optional[dict]:
    import os
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)


# ------------------------------------------------------------------ real building footprints
@dataclass(frozen=True)
class OSMBuilding:
    osm_id: int
    ring: tuple                     # ((lon, lat), ...) real footprint vertices
    levels: Optional[float]         # from building:levels when mapped, else None
    height_m: Optional[float]       # from height tag when mapped, else None
    name: Optional[str]
    source: str = "OPENSTREETMAP"

    def to_dict(self) -> dict:
        return {"osm_id": self.osm_id, "ring": [list(p) for p in self.ring], "levels": self.levels,
                "height_m": self.height_m, "name": self.name, "source": self.source,
                "osm_url": f"https://www.openstreetmap.org/way/{self.osm_id}"}


def _num(v: Optional[str]) -> Optional[float]:
    try:
        return float(str(v).split()[0].replace(",", "."))
    except (TypeError, ValueError, IndexError):
        return None


def parse_buildings(payload: str) -> list[OSMBuilding]:
    out = []
    for el in json.loads(payload).get("elements", []):
        geom = el.get("geometry") or []
        if el.get("type") != "way" or len(geom) < 4:
            continue
        ring = tuple((float(p["lon"]), float(p["lat"])) for p in geom)
        tags = el.get("tags") or {}
        out.append(OSMBuilding(int(el["id"]), ring, _num(tags.get("building:levels")),
                               _num(tags.get("height")), tags.get("name")))
    return out


def fetch_osm_buildings(bbox: tuple[float, float, float, float],
                        transport: Optional[Callable[[str, str, float], str]] = None,
                        limit: int = 60, retries: int = 2,
                        sleep: Callable[[float], None] = time.sleep) -> list[OSMBuilding]:
    """Real building footprints in a small bbox. Raises on total failure so callers can say
    'buildings unavailable' instead of showing an empty map as if there were none."""
    s, w, n, e = bbox
    if not (s < n and w < e) or (n - s) > 0.05 or (e - w) > 0.05:
        raise ValueError("building bbox must be valid and no larger than 0.05 degrees")
    send = transport or _http_transport
    query = f'[out:json][timeout:30];way["building"]({s},{w},{n},{e});out geom {limit};'
    err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return parse_buildings(send(query, OVERPASS_ENDPOINT, 60.0))
        except Exception as exc:                                 # noqa: BLE001
            err = exc
            if attempt < retries:
                sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"OSM buildings unavailable: {err}")


def _cli() -> None:                                                     # pragma: no cover (network)
    """python -m tathyon.osm_facilities S W N E OUT.json  -> fetch real facilities and cache them."""
    import sys
    from .schema import now
    if len(sys.argv) != 6:
        raise SystemExit("usage: python -m tathyon.osm_facilities SOUTH WEST NORTH EAST OUT.json")
    bbox = tuple(float(x) for x in sys.argv[1:5])
    at = now()
    result = fetch_osm_facilities(bbox, at, step=0.15, retries=3)
    save_cache(result, sys.argv[5], at)
    print(f"{result.fetch_status}: {len(result.facilities)} facilities, "
          f"{result.tiles_failed}/{result.tiles_requested} tiles failed -> {sys.argv[5]}")
    for f in result.failures:
        print("  FAILED:", f)


if __name__ == "__main__":                                               # pragma: no cover
    _cli()
