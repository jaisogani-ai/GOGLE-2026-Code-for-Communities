"""
TATHYON integration registry — what is actually wired, verified at runtime.

One rule: a capability may only report LIVE_EXTERNAL when a real external source is
configured AND reachable. Nothing here infers "live" from the presence of code.

Provenance vocabulary (never combined):
  LIVE_EXTERNAL   data arriving now from a real external source
  SYNTHETIC       generated demo data
  SIMULATION      modelled future state
  ESTIMATED       derived/interpolated from other values
  STALE           real data, but older than its freshness policy
  NOT_CONFIGURED  integration unavailable (no credentials / not implemented)

Research findings encoded here (2026-09-21):
  - Google Routes requires an API key plus billing; OSRM is used instead.
  - No public, free API exists for real-time ROAD-vehicle telemetry. OpenSky is
    aircraft-only (ADS-B); road fleet telemetry is private for privacy/legal reasons.
    Traccar is real open-source software but needs a self-hosted server and a device.
  - Satellite imagery CANNOT continuously track an individual vehicle. ~30 cm imagery
    identifies vehicle TYPE; tasked revisit is at most a handful of passes per day.
    Satellite-derived data is therefore only ever an ENVIRONMENTAL signal here.
  - OpenStreetMap (Overpass API) DOES serve real facility coordinates, free and
    without authentication, under ODbL. This is the one live source wired today.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

LIVE_EXTERNAL = "LIVE_EXTERNAL"
SYNTHETIC = "SYNTHETIC"
SIMULATION = "SIMULATION"
ESTIMATED = "ESTIMATED"
STALE = "STALE"
NOT_CONFIGURED = "NOT_CONFIGURED"

PROVENANCE_VALUES = (LIVE_EXTERNAL, SYNTHETIC, SIMULATION, ESTIMATED, STALE, NOT_CONFIGURED)


@dataclass(frozen=True)
class Integration:
    capability: str
    source: str                     # the real-world source, or "" when none exists
    state: str                      # one of PROVENANCE_VALUES
    auth_required: bool
    cost: str                       # "FREE" | "PAID" | "FREE_TIER" | "N/A"
    implemented: bool               # is there working code wired to the core loop?
    notes: str
    attribution: Optional[str] = None
    env_var: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# Static capability catalogue. `state` for credentialed integrations is resolved at
# call time by integration_matrix(); the values here are the no-credential defaults.
CATALOGUE: tuple[Integration, ...] = (
    Integration(
        capability="facility_coordinates",
        source="OpenStreetMap Overpass API",
        state=LIVE_EXTERNAL, auth_required=False, cost="FREE", implemented=True,
        notes="Verified working: returns real named facilities with coordinates. "
              "Community-mapped data, NOT a government registry, and completeness varies. "
              "Large bounding boxes time out (HTTP 504); queries must be tiled.",
        attribution="© OpenStreetMap contributors, ODbL",
    ),
    Integration(
        capability="road_routes_osrm",
        source="OSRM over OpenStreetMap roads",
        state=LIVE_EXTERNAL, auth_required=False, cost="FREE", implemented=True,
        notes="Verified working: real road distance, duration and geometry, no key. NOT traffic-aware "
              "and uses a car profile, not a truck/cold-chain vehicle, so real transit is usually slower. "
              "Public demo server is best-effort; routes are cached and a failure degrades to a labelled "
              "straight-line estimate.",
        attribution="Routing © OSRM; road data © OpenStreetMap contributors, ODbL",
    ),
    Integration(
        capability="google_routes",
        source="Google Routes API (Compute Routes / Route Matrix)",
        state=NOT_CONFIGURED, auth_required=True, cost="PAID", implemented=False,
        notes="Route Matrix supports up to 625 elements (100 when traffic-aware optimal). "
              "Not required for the prototype: OSRM supplies real road ETAs without a key. "
              "Google would add live-traffic ETAs. Never labelled live without a key.",
        env_var="GOOGLE_MAPS_API_KEY",
    ),
    Integration(
        capability="shipment_gps_telemetry",
        source="Traccar / fleet telematics (self-hosted)",
        state=NOT_CONFIGURED, auth_required=True, cost="FREE_TIER", implemented=False,
        notes="Traccar is real open-source software (200+ device protocols, REST API) but "
              "needs a server AND a physical tracker on the vehicle. No public free feed of "
              "road-vehicle positions exists anywhere: OpenSky is aircraft-only (ADS-B), and "
              "road telemetry is private under driver-privacy law. Without a device there is "
              "no last known location, and Tathyon shows NOT AVAILABLE rather than animating.",
        env_var="TRACCAR_URL",
    ),
    Integration(
        capability="cold_chain_temperature",
        source="eVIN / IoT temperature loggers",
        state=NOT_CONFIGURED, auth_required=True, cost="N/A", implemented=False,
        notes="eVIN is a real government system: sensors sample every ~10 minutes and upload "
              "over GPRS about hourly, so even when integrated the data is periodic, not "
              "continuous. No public API; access requires a government data agreement.",
        env_var="COLD_CHAIN_API_URL",
    ),
    Integration(
        capability="weather_forecast",
        source="Open-Meteo precipitation forecast",
        state=LIVE_EXTERNAL, auth_required=False, cost="FREE", implemented=True,
        notes="Verified working, no key. A numerical-model FORECAST, not an observation, not satellite "
              "imagery, not a road condition. Informational only: it does not alter any rescue decision "
              "because no validated rainfall-to-road-access threshold exists.",
        attribution="Weather data by Open-Meteo.com (CC BY 4.0)",
    ),
    Integration(
        capability="environmental_access_signal",
        source="Copernicus Sentinel-1 SAR / Earth observation",
        state=NOT_CONFIGURED, auth_required=True, cost="FREE_TIER", implemented=False,
        notes="Legitimate use is an ENVIRONMENTAL signal over a route corridor (for example a "
              "possible inundation extent), never 'road closed' and never vehicle tracking. "
              "Requires Copernicus registration. Not wired.",
        env_var="COPERNICUS_TOKEN",
    ),
    Integration(
        capability="satellite_vehicle_tracking",
        source="",
        state=NOT_CONFIGURED, auth_required=False, cost="N/A", implemented=False,
        notes="NOT TECHNICALLY POSSIBLE as a product capability and will not be built. "
              "~30 cm imagery can identify a vehicle TYPE in a single frame; tasked revisit is "
              "at most a few passes per day. Continuously following one medicine truck by "
              "satellite imagery is not achievable. Vehicle position comes from GNSS/GPS "
              "telemetry, which is a different technology from satellite imagery.",
    ),
    Integration(
        capability="government_stock_system_of_record",
        source="DVDMS / e-Aushadhi",
        state=NOT_CONFIGURED, auth_required=True, cost="N/A", implemented=False,
        notes="Tathyon stages payloads for the system of record and never writes to it. "
              "No government integration exists; all stock data is SYNTHETIC.",
        env_var="DVDMS_API_URL",
    ),
    Integration(
        capability="stock_levels",
        source="Tathyon synthetic generator (CAG-anchored)",
        state=SYNTHETIC, auth_required=False, cost="FREE", implemented=True,
        notes="All stock, consumption and donor quantities are synthetic demo data.",
    ),
)


def _env_present(name: Optional[str], env: dict) -> bool:
    return bool(name and str(env.get(name, "")).strip())


def integration_matrix(env: Optional[dict] = None, probe: bool = False) -> list[dict]:
    """Resolve each capability's state.

    A credentialed integration reports NOT_CONFIGURED unless its env var is set AND
    code is wired. `probe` is accepted for callers that want a reachability check;
    it never upgrades a capability that has no implementation.
    """
    import os
    env = os.environ if env is None else env
    rows = []
    for item in CATALOGUE:
        state = item.state
        if item.env_var and not item.implemented:
            state = NOT_CONFIGURED          # credentials alone never mean "live"
        elif item.env_var and item.implemented and not _env_present(item.env_var, env):
            state = NOT_CONFIGURED
        row = item.to_dict()
        row["state"] = state
        row["credentials_present"] = _env_present(item.env_var, env) if item.env_var else None
        rows.append(row)
    return rows


def capability_state(capability: str, env: Optional[dict] = None) -> str:
    for row in integration_matrix(env):
        if row["capability"] == capability:
            return row["state"]
    raise KeyError(f"UNKNOWN_CAPABILITY: {capability}")
