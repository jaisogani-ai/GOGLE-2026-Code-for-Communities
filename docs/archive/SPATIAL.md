# TATHYON 3D Spatial Operations

Run: `make api` (or `uvicorn api.main:app --port 8765`), open `/spatial`.
Refresh real facilities: `python -m tathyon.osm_facilities 18.95 81.70 19.30 82.15 data/osm_bastar_facilities.json`.

## Reference repositories and licences (checked 2026-09-21)

| Repository | Licence | What was used |
|---|---|---|
| Awais-H/Halo | MIT | Architectural idea only (backend state drives a 3D scene). No code copied. Only its README was read; source was not audited. |
| CesiumGS/cesium-unreal-samples | Apache-2.0 | Not used. Unreal Engine project; not applicable to a web client. README only. |
| CesiumGS/cesium-unity-samples | Apache-2.0 | Not used. Unity project; not applicable. README only. |
| leon-juenemann/cesiumjs-with-threejs | **NONE STATED** | **Not used. No licence means no right to copy.** Read only its description of the two-canvas approach. |
| optgeo/google-pr-3dtiles | MIT | Not copied. Confirms Cesium and deck.gl can render Google Photorealistic 3D Tiles. |

## Libraries actually used

| Package | Version | Licence | How |
|---|---|---|---|
| CesiumJS (`cesium`) | 1.145.0 | Apache-2.0 | Built files vendored in `web/vendor/cesium` with its LICENSE.md. No Cesium Ion token; no Ion assets. |
| OpenStreetMap standard tiles | live | ODbL data / OSMF tile policy | Basemap imagery. Light-use only: not for production traffic. |
| Overpass API | live | ODbL | Facility coordinates and building footprints. |
| OSRM public server | live | BSD-2 software, ODbL data | Road routes. Best-effort, fair use. |

No `package.json` was added; nothing needs npm at runtime.

## What is real, synthetic, simulated, not configured

| Item | State |
|---|---|
| Facility names, coordinates, building footprints | LIVE_EXTERNAL: OpenStreetMap community data, cached with fetch time. Not government verified. |
| Road distance, ETA, geometry | LIVE_EXTERNAL: OSRM over OSM roads. Not traffic-aware, car profile. Falls back to a labelled straight line. |
| Stock, consumption, donor quantities, prep times, indent history | SYNTHETIC |
| Demo clock, simulated telemetry | SIMULATION |
| Google Photorealistic 3D, Google Routes | NOT_CONFIGURED (no key). Flag branch implemented, never run successfully. |
| Real GPS telemetry (Traccar) | NOT_CONFIGURED. Adapter unit-tested with a fake server only. |
| Cold chain, environmental layer, terrain | NOT_CONFIGURED |

"CHC TOKAPAL" is a real OSM record, classified CHC from its own name/description. It is not a PHC.

## API

`GET /spatial/config` · `GET /spatial/snapshot[?at_seq=N]` · `POST /spatial/actions` · `POST /spatial/reset` · `GET /geo/osm-buildings` · `GET /integrations`

The world is event-sourced (`data/spatial_world.json`): restart replays the events, and `at_seq` replays a prefix for the time machine.

## Not implemented

GLB/glTF model loading, 3D facility interiors, warehouses/district stores (no OSM data), clustering (distance-based LOD only), environmental layer, Google 3D success path, real telemetry against a real tracker, WebSocket/SSE (2 s polling).
