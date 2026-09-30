"""
TATHYON route estimate adapter.

STATUS (purged of unsupported claims):
  - Google Routes API ........ NOT CONFIGURED / NOT INTEGRATED. No HTTP client exists here.
  - Google Earth Engine ...... NOT CONFIGURED / NOT INTEGRATED. No dataset is read.
  - Every route this module returns is a great-circle (haversine) distance at a fixed
    40 km/h, or an explicit test override. Both are SYNTHETIC ESTIMATES, never live data.
"""
import math
from dataclasses import dataclass
from typing import Any, Dict, Optional

from tathyon.schema import now


@dataclass
class AccessRiskSignal:
    dataset: str
    observation_date: str
    location: str
    source: str
    confidence: str
    analysis_method: str
    signal_type: str  # e.g., "Normal", "POTENTIAL ACCESS DISRUPTION"


@dataclass
class RouteInfo:
    distance_km: float
    eta_hours: float
    traffic_aware: bool
    access_risk: AccessRiskSignal
    data_source: str


class RouteIntelligenceAdapter:
    MAPS_STATUS = "NOT_CONFIGURED"
    EARTH_ENGINE_STATUS = "NOT_CONFIGURED"
    ESTIMATE_SPEED_KMH = 40.0

    def __init__(self):
        self.overrides: Dict[str, Dict[str, Any]] = {}
        # Optional real route source: callable(origin_id, dest_id, o_lat, o_lon, d_lat, d_lon)
        # -> (distance_km, eta_hours, data_source) or None. Overrides still take priority.
        self.provider = None

    def set_synthetic_override(self, origin_id: str, dest_id: str, eta_hours: float, risk_signal_type: str = "Normal"):
        """For testing: inject synthetic routes."""
        self.overrides[f"{origin_id}_{dest_id}"] = {
            "eta_hours": eta_hours,
            "risk": risk_signal_type
        }

    def get_route_intelligence(
        self,
        origin_id: str,
        dest_id: str,
        origin_lat: float,
        origin_lon: float,
        dest_lat: float,
        dest_lon: float
    ) -> RouteInfo:
        key = f"{origin_id}_{dest_id}"
        if key in self.overrides:
            ov = self.overrides[key]
            # fake distance derived from eta for simplicity
            dist_km = ov["eta_hours"] * self.ESTIMATE_SPEED_KMH
            return RouteInfo(
                distance_km=dist_km,
                eta_hours=ov["eta_hours"],
                traffic_aware=False,
                access_risk=AccessRiskSignal(
                    dataset="SYNTHETIC",
                    observation_date=now(),
                    location=key,
                    source="Override",
                    confidence="HIGH",
                    analysis_method="N/A",
                    signal_type=ov["risk"]
                ),
                data_source="SYNTHETIC_OVERRIDE"
            )

        provided = self.provider(origin_id, dest_id, origin_lat, origin_lon, dest_lat, dest_lon) \
            if self.provider else None

        # Earth Engine is not integrated: report that, never a signal.
        risk_signal = AccessRiskSignal(
            dataset="NONE",
            observation_date=now(),
            location=f"Corridor {origin_id} -> {dest_id}",
            source=self.EARTH_ENGINE_STATUS,
            confidence="NONE",
            analysis_method="NONE",
            signal_type="NOT_EVALUATED",
        )

        if provided is not None:
            dist_km, eta_h, source = provided
            return RouteInfo(distance_km=round(dist_km, 1), eta_hours=round(eta_h, 2), traffic_aware=False,
                             access_risk=risk_signal, data_source=source)

        # Routes API is not integrated: great-circle estimate only.
        r_earth_km = 6371.0
        dlat = math.radians(dest_lat - origin_lat)
        dlon = math.radians(dest_lon - origin_lon)
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(origin_lat)) * math.cos(math.radians(dest_lat)) * math.sin(dlon / 2) ** 2)
        distance_km = r_earth_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        eta_hours = distance_km / self.ESTIMATE_SPEED_KMH

        return RouteInfo(
            distance_km=round(distance_km, 1),
            eta_hours=round(eta_hours, 1),
            traffic_aware=False,
            access_risk=risk_signal,
            data_source="SYNTHETIC_HAVERSINE_ESTIMATE",
        )
