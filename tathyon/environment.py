"""
Environmental context layer: precipitation FORECAST from Open-Meteo (free, no key).

What this is: numerical-weather-model forecast output for a point. Verified reachable 2026-09-21.
What this is NOT, and the code refuses to say otherwise:
  - not satellite imagery, not Sentinel/Earth Engine, not a flood extent
  - not an observation, it is a model forecast
  - not a road-condition or road-closure source
The layer is informational. It does NOT change any rescue decision: turning rainfall into an
access-risk penalty would need a calibrated threshold that nobody has validated for these roads.

License note: Open-Meteo is free for non-commercial use with attribution (CC BY 4.0 data).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, Optional

from .integrations import LIVE_EXTERNAL, NOT_CONFIGURED, STALE

ENDPOINT = "https://api.open-meteo.com/v1/forecast"
ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"
STALE_AFTER_HOURS = 6.0                      # CONFIGURATION
FORECAST_DAYS = 2


@dataclass(frozen=True)
class PrecipitationForecast:
    facility_id: str
    lat: float
    lon: float
    fetched_at: str
    state: str                               # LIVE_EXTERNAL | STALE
    horizon_hours: int
    total_mm: float
    max_mm_per_hour: float
    peak_hour_utc: Optional[str]
    hourly: tuple                            # ((iso_hour, mm), ...)
    kind: str = "FORECAST (numerical model output, not an observation)"
    source: str = "OPEN-METEO"
    attribution: str = ATTRIBUTION
    used_in_decisions: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hourly"] = [list(h) for h in self.hourly]
        return d


def _http_get(url: str, timeout_s: float) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Tathyon/0.1 (research)"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:          # noqa: S310 (fixed https endpoint)
        return resp.read().decode()


def parse_forecast(payload: str, points: list[tuple[str, float, float]], fetched_at: str) -> list[PrecipitationForecast]:
    data = json.loads(payload)
    items = data if isinstance(data, list) else [data]          # Open-Meteo returns a list for multi-point calls
    if len(items) != len(points):
        raise ValueError(f"Open-Meteo returned {len(items)} locations for {len(points)} requested")
    out = []
    for (fid, lat, lon), item in zip(points, items):
        h = item.get("hourly") or {}
        times, mm = h.get("time") or [], h.get("precipitation") or []
        if not times or len(times) != len(mm):
            raise ValueError(f"Open-Meteo returned no hourly precipitation for {fid}")
        clean = [(t, float(v)) for t, v in zip(times, mm) if v is not None]     # never turn null into 0 rain
        if not clean:
            raise ValueError(f"Open-Meteo precipitation is all null for {fid}")
        peak = max(clean, key=lambda x: x[1])
        out.append(PrecipitationForecast(
            facility_id=fid, lat=lat, lon=lon, fetched_at=fetched_at, state=LIVE_EXTERNAL,
            horizon_hours=len(clean), total_mm=round(sum(v for _, v in clean), 2),
            max_mm_per_hour=round(peak[1], 2), peak_hour_utc=peak[0] if peak[1] > 0 else None,
            hourly=tuple(clean)))
    return out


def fetch_precipitation(points: list[tuple[str, float, float]], fetched_at: str,
                        get: Optional[Callable[[str, float], str]] = None,
                        timeout_s: float = 20.0) -> list[PrecipitationForecast]:
    """points = [(facility_id, lat, lon)]. Raises on failure; callers must show NOT_CONFIGURED, not zeros."""
    if not points:
        return []
    q = urllib.parse.urlencode({
        "latitude": ",".join(f"{p[1]:.4f}" for p in points), "longitude": ",".join(f"{p[2]:.4f}" for p in points),
        "hourly": "precipitation", "forecast_days": FORECAST_DAYS, "timezone": "UTC"})
    try:
        return parse_forecast((get or _http_get)(f"{ENDPOINT}?{q}", timeout_s), points, fetched_at)
    except Exception as exc:                                              # noqa: BLE001
        raise RuntimeError(f"Open-Meteo unavailable: {exc}") from exc


def age_hours(fetched_at: str, now: datetime) -> float:
    dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def mark_stale(items: list[PrecipitationForecast], now: datetime,
               stale_after_hours: float = STALE_AFTER_HOURS) -> list[PrecipitationForecast]:
    """A cached forecast older than the policy is STALE, with the same numbers but an honest state."""
    from dataclasses import replace
    return [replace(i, state=STALE) if age_hours(i.fetched_at, now) > stale_after_hours else i for i in items]


def save_cache(items: list[PrecipitationForecast], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as fh:
        json.dump([i.to_dict() for i in items], fh)


def load_cache(path: str) -> list[PrecipitationForecast]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        raw = json.load(fh)
    return [PrecipitationForecast(**{**r, "hourly": tuple(tuple(h) for h in r["hourly"])}) for r in raw]


def state_of(items: list[PrecipitationForecast]) -> str:
    if not items:
        return NOT_CONFIGURED
    return STALE if any(i.state == STALE for i in items) else LIVE_EXTERNAL
