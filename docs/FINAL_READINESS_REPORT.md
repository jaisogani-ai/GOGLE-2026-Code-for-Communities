# Final readiness report — 2026-09-30

Statuses: **READY** · **READY WITH LIMITATIONS** · **NOT READY**. Overall: **READY WITH LIMITATIONS as a working
prototype; NOT READY for production** (no government integration, identity provider, or field validation).

## 1. Thesis and problem
District stock records (DVDMS / e-Aushadhi exports) are the only picture most authorities have, and some reports
are stale or wrong. Redistribution against a wrong report wastes the vehicle and leaves the shortage unserved.
TATHYON decides **which records to count, which shortages to serve from verified stock, when to wait for a count,
and when to escalate**, then tracks whether the action worked and re-plans when it did not.

*Scope note:* no external literature or competitor research (MoHFW, CAG, HealthGrid AI, etc.) was performed in this
session; the thesis rests on the product's design and the synthetic evaluation. Validate with a district before
making impact claims.

## 2. Government workflow
Data officer uploads the weekly export → queue picks counts within verifier hours → verifier (not the custodian)
counts → DMO approves an option with a reason (line-level veto, break-glass with 24 h obligation) → logistics
dispatches → receiving in-charge confirms receipt/damage → outcome recorded → watcher re-plans → a DVDMS-shaped
voucher is staged (not sent) for the system of record.

## 3. Existing systems vs TATHYON
| Existing | Does | TATHYON adds |
|---|---|---|
| DVDMS / e-Aushadhi / state LMIS | Record receipts, issues, balances | Treats balances as *claims*; decides which to physically verify first and plans only on verified stock |
| Generic dashboards | Show stock and alerts | A decision with counterfactual (what trusting the report would do), human approval, and a closed loop to outcome and replan |
| Previous track winners (e.g. HealthGrid AI) | **Not researched in this session** | — |

## 4. Architecture
`web/` console → `api/` (FastAPI: auth, RBAC, rate limits, CSP) → `tathyon/workspace.py` (event-sourced loop) →
`optimize.py` (OR-Tools CP-SAT, knapsack) · `planning.py` (options) · `intake_pipeline.py` · `observations.py` ·
`agents/` (Gemini) → `store.py` hash-chained ledger (in-memory or SQLite; replayed on start).

## 5. Status by area
| Area | Status | Evidence / limitation |
|---|---|---|
| End-to-end loop | **READY** | Browser walkthrough on real OSM facilities + sample stock; API test `test_full_workflow_over_the_api`; replay test |
| AI agents (5) | **READY WITH LIMITATIONS** | Live Gemini runs grounded 8/8; Hindi verified; adversarial suite passes. Output quality depends on the model |
| Gemini | **READY** | Key verified; `gemini-2.5-flash` + fallback verified; `gemini-2.0-flash` and `gemini-2.5-flash-lite` return 404 for this key |
| Google Maps 2D | **READY** | Maps JS renders backend markers; tiles verified loading |
| Google Routes | **READY** | Real road geometry and durations server-side; labelled fallback |
| Google 3D Tiles | **NOT READY** | Implemented (CesiumJS); **Map Tiles API not enabled** on the key's project (403). UI explains and stays 2D |
| Real data | **READY WITH LIMITATIONS** | Real OSM facility locations for 5 districts/5 states (3 partial). No real stock/bed/attendance data |
| Sample data | **READY** | Opt-in, badged SAMPLE everywhere; judging rules allow sample data |
| Beds & personnel | **READY WITH LIMITATIONS** | Upload + anomaly advisories + drawer; no live source; no bed/staff optimisation in the live loop |
| Forecasting | **READY WITH LIMITATIONS** | Runway from period-average consumption. Croston/TSB models exist offline but need multi-period history |
| ML trust scorer | **NOT SERVED** | PR-AUC 0.93 synthetic; +15% over best rule; no real labels |
| Evaluation | **READY WITH LIMITATIONS** | Fair benchmark; TATHYON loses on raw stockout-days, wins on phantom shipments/visits. Synthetic |
| Multilingual / voice | **READY WITH LIMITATIONS** | 12 languages via Gemini; voice via browser Web Speech (not Cloud Speech-to-Text) |
| State sharing ("federated" modelling across states) | **FUTURE** | `federation.py` weight-exchange library + tests on synthetic data; no second state connected |
| Security | **READY WITH LIMITATIONS** | See `SECURITY.md`; demo identities only; keys must be rotated |
| Deployment | **READY WITH LIMITATIONS** | Dockerfile + Cloud Run commands prepared; image-equivalent run verified; **not deployed** (no `gcloud`/Docker on this machine) |
| Tests | **READY** | 554 passed, 0 failed |

## 6. Judging criteria — honest self-assessment
| Criterion (weight) | Strength | Gap to close |
|---|---|---|
| Problem–solution fit (20%) | Directly targets stock-out early warning and cross-district redistribution with verification | Real bed/attendance sources; forecasting on real history |
| AI/technical execution (25%) | Gemini does real work (tool-calling agents, multimodal intake, 12 languages) inside hard safety bounds; CP-SAT; working loop | Enable 3D Tiles; deploy |
| Depth & reach across India (20%) | Real facilities across 5 states; per-district verifier cost; DVDMS-shaped adapter | Only 5 districts cached; OSM coverage incomplete; state→national roll-up UI not built |
| Impact potential (15%) | Measurable metrics (phantom shipments, visits, stockout-days) | Synthetic evidence only |
| Deployability & scalability (20%) | One container, Cloud Run plan, file-export integration pilotable in weeks | Single-instance state; needs managed DB + OIDC |

## 7. Remaining red flags
1. Keys were pasted into a chat during development → **rotate before any public demo**.
2. Same Maps key serves browser and server → split and restrict.
3. Not deployed; no public URL yet.
4. Single-process state; Cloud Run must be `max-instances=1`.
5. Sample data uses real facility names with invented stock (badged SAMPLE) — say so in the pitch.
6. The benchmark shows TATHYON averting fewer raw stockout-days than naive arms — present it, don't hide it.
