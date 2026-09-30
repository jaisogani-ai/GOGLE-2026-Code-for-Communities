# Final system audit (2026-09-30)

Verified by reading code, running the suite, running the benchmark and scorer, and driving the running app in a
browser. Earlier reports were not trusted.

## Starting state found

| Finding | Evidence |
|---|---|
| Test suite red | 494 passed / 47 failed / 2 errors (documented as passing) |
| API gutted to stubs | `/trust/queue`, `/allocate`, approval, outcomes returned fixed "NOT_CONNECTED" JSON; the core loop did not run |
| Crash bug | `/intake/dvdms-csv` used `new_id` without importing it |
| Fake AI surface | `ai_roles.py` "roles" backed by canned fixture handlers; `train_ai_roles.py` "trained" them |
| Scripted demo with hard-coded numbers | `demo.py` printed a literal results table and `p_phantom_probability 0.88` regardless of inputs |
| Rigged benchmark | headline metric 0 for non-verifying arms by construction; "phantom blocked" credited by arm name; latency hard-coded |
| Misleading data folder | `data/real/dvdms_ledger.csv` is generated data with real-looking facility names |
| Deploy manifests | intentionally empty (`services: []`) |

## Component classification

| Component | Decision | Notes |
|---|---|---|
| `store.py` hash-chained EventStore, `persist.py` SQLite | **KEEP** | Foundation; replay verified |
| `optimize.py` CP-SAT + verification gate + knapsack | **KEEP** | Used by the new loop |
| `schema.py` | **FIX** | New event types; `SAMPLE` provenance |
| `workspace.py` event-sourced operational loop | **BUILT** | Ingest → trust → need → options → approval → action → receipt → reconcile → outcome → replan; replayable |
| `planning.py` options + recommendation rule | **BUILT** | Counterfactual, gated, contingent, escalate |
| `intake_pipeline.py` | **BUILT** | DVDMS-shaped CSV/TSV, typed quarantine, formula-injection, facility registry, OSM |
| `observations.py` beds/staff + advisories | **BUILT** (refactor of API code) | History-gated robust z |
| `sample_dataset.py` | **BUILT** | Opt-in, badged SAMPLE, on real OSM facilities |
| `agents/` (5 agents, harness, Gemini client) | **BUILT / REPLACE** | Replaces `ai_roles.py`, `ops_copilot.py`, `intake_agent.py`, `ai_prompts.py`, `train_ai_roles.py` (archived) |
| `api/` (main, security, maps, env) | **REPLACE** | Auth, RBAC, rate limits, headers, map layers, Routes, 3D probe |
| `web/` console | **REPLACE** | 4,260-line Tailwind-CDN page archived; new dependency-light console |
| `evaluate.py` | **FIX** | Fairness fixes; honest headline |
| `trust_eval.py`, `verify.py` trust scorer | **KEEP (offline)** | Evaluated; not served (no real labels) |
| `federation.py` weight exchange | **FUTURE** | Library + tests, synthetic demo only; status NOT_CONFIGURED in app |
| `rescue_loop.py`, `planner.py`, `graph.py`, `twin.py`, `beds.py`, `personnel.py`, `surge.py`, `harness.py` | **KEEP (library)** | Tested; not on the operational path. Candidates for consolidation |
| `demo.py` | **DELETE** (archived) | Hard-coded output |
| `generator.py`, `generate_real_pilot_data.py`, `real_training.py` | **KEEP (offline)** | Benchmark generation; never served |
| Old docs (PRD/FRD/…/readiness) | **ARCHIVED** to `docs/archive/pre_v3/` | Contained superseded claims |

## Red-flag search (UI and docs)

`tests/test_no_misleading_claims.py` fails the build if the UI claims live government data, GPS/vehicle or
satellite tracking, or unmeasured accuracy. Every stock value renders a provenance badge (REAL · OSM,
USER-SUPPLIED, VERIFIED COUNT, SAMPLE, NOT CONFIGURED).

## Red-team results (all fail safely; each is a test)

| Attack | Result |
|---|---|
| No / forged / tampered session token | 401 |
| Wrong role approves, uploads, resets, runs intake agent | 403 (and a FLAGGED event for approval attempts) |
| Custodian counts own stock; dispatcher confirms own receipt; other facility confirms receipt | 403 typed refusals |
| Replayed decision / receipt; duplicate client event id | 409 |
| Negative, non-finite, usable>present counts; received>dispatched | 422 |
| Empty / headerless / 2 MB+ / path-traversal filename / 20k+ row files | whole file rejected |
| Formula injection, unknown facility, future-dated, duplicate, bad VED rows | quarantined with typed reasons |
| 9 MB body | 413 |
| Unknown SKU / event / plan | 404 typed |
| Tampered ledger payload | chain check fails at the exact offset; `/health` → degraded |
| Backend restart (durable mode) | projections rebuilt from ledger, identical |
| 12 adversarial prompts × 4 agents | refused, ledgered, no tool executed |
| Model names a forbidden tool / bad args / loops / hallucinates event ids / leaks a key / throws | blocked, budgeted, dropped, redacted, deterministic fallback — all recorded |
| Gemini model retired (404) | automatic switch to fallback model |
| Missing Gemini / Maps keys | labelled DETERMINISTIC / OSM basemap / straight-line routes |
| Map Tiles API disabled | 3D button disabled with the reason; 2D continues |
| Route provider failure | straight line labelled `SYNTHETIC_STRAIGHT_LINE_ROUTE_PROVIDER_FAILED` |
| Optimizer finds no feasible plan / partial | `ESCALATE` option with shortfall; escalation event on approval |
