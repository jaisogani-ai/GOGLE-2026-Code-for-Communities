# Off-core inventory (Phase 10)

Core loop: ingest -> data-age runway -> pending-indent check -> rescue assessment -> CP-SAT plan ->
human approval -> shipment -> receipt -> recalculation -> replan or close.
Modules: `runway.py`, `rescue.py`, `rescue_loop.py`, `planner.py`, `cases.py`, `shipment.py`, `graph.py`, `routing.py` (estimate only).

| Module | Status | Why |
|---|---|---|
| `business.py` (Itch Index, ROI) | OFF_CORE / REMOVE | Invented weights, not decision logic |
| `data_moat.py` | OFF_CORE / REMOVE | Dashboard metrics only |
| `country.py` | OFF_CORE / FUTURE | BRICS config, unused by the loop |
| `scorecard.py` | OFF_CORE / REMOVE | Unreferenced |
| `agents.py`, `orchestrator.py` | OFF_CORE / REMOVE | Agent layer unused by the loop |
| `control_room.py` | OFF_CORE / REMOVE | Scripted fixtures |
| `gemini.py` | OFF_CORE / FUTURE | Prose/extraction, never a decision input |
| `layers.py` | OFF_CORE / REMOVE | Unwired, no live source |
| `policy.py` "statutory" engine | DEMOTED | Every citation is `citation_verified=False`; basis is SYSTEM POLICY |
| Live Maps / Earth Engine / satellite | REMOVED | Never integrated (`routing.py` is a haversine estimate) |

Constraint basis vocabulary: LAW / REGULATION, OFFICIAL GUIDELINE, SYSTEM POLICY, FACILITY POLICY,
CONFIGURATION, DEMO ASSUMPTION. No constraint is currently labelled LAW / REGULATION because none was
independently verified.
