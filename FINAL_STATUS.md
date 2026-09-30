# FINAL STATUS — TATHYON v3.0.0 (2026-09-30)

**A. Verified** — Code read end to end; suite run (baseline 494 pass / 47 fail / 2 errors); benchmark and trust
scorer re-run; Gemini key and models tested; Maps JS, Routes and 3D Tiles tested against the key; full judge
walkthrough driven in a browser; restart/replay, tampering and adversarial agent prompts tested.

**B. Fixed** — Stubbed API (loop did not run); `new_id` crash in CSV intake; rigged benchmark (by-construction
headline, by-name phantom credit, hard-coded latency, zero credit for real units); per-district verification cost
(was one hard-coded HQ); agent audit gap (blocked model attempts were lost on fallback); plan view dropping line
ids (approval failed in UI); agent provenance wording; outcome label for sample data.

**C. Removed / archived** — `ai_roles.py`, `ops_copilot.py`, `intake_agent.py`, `ai_prompts.py`, `train_ai_roles.py`
(fixture-backed "AI"), `demo.py` (hard-coded output), legacy 4,260-line UI, disabled deploy stubs, superseded docs,
obsolete tests → `docs/archive/`. Nothing deleted outright.

**D. Built** — Event-sourced workspace loop with replay; options/counterfactual planner; DVDMS-shaped intake with
typed quarantine; facility registry + OSM for 5 districts/5 states; beds/staff observations with advisories; opt-in
SAMPLE dataset; signed sessions + RBAC + rate limits + CSP; Google Maps 2D, Routes, CesiumJS 3D (gated); six-view
console with provenance badges; auto-refresh monitor; agent drawer with 12 languages and voice; Dockerfile and
Cloud Run plan.

**E. AI agents** — Intake, Operations Copilot, Resilience Analyst, Replan Watcher, Evidence/Audit on one bounded
harness (allowlist, schemas, budget, timeout, screening, grounded citations, redaction, audit). See `docs/AGENTS.md`.

**F. AI/ML training** — Trust scorer: PR-AUC 0.931 on held-out synthetic worlds, +15% hit rate over the best rule;
not served (no real labels). Personnel scorer PR-AUC 1.0 = trivially separable synthetic data, not evidence. No
model was trained on real data. See `docs/EVALUATION.md`.

**G. Real data** — OpenStreetMap facility locations (ODbL): Bastar CG (119), Gaya BR (111, partial), Nandurbar MH
(132, partial), Kalahandi OD (131, partial), Varanasi UP (45). User uploads and human counts are real inputs.

**H. Synthetic / sample data** — Opt-in SAMPLE stock/beds/staff (badged, banner). Synthetic test fixture and offline
benchmark never served. `data/real/dvdms_ledger.csv` is generated despite its folder name and is unused.

**I. Gemini** — Working: `gemini-2.5-flash` (primary), `gemini-flash-lite-latest` (fallback). `gemini-2.0-flash`
and `gemini-2.5-flash-lite` return 404 for this key. Key in git-ignored `.env` (chmod 600), server-side only.

**J. Google Maps** — Maps JavaScript API: working. Routes API: working (server-side). **Map Tiles API (3D): not
enabled (403)** — enable it in Cloud Console. One key currently serves browser and server; split it.

**K. Tests** — **554 passed, 0 failed** (`make test`, ~90 s). Includes loop, replay, tamper, RBAC, intake
validation, agent adversarial suite (48 prompt×agent cases), API workflow, restart, route failure, escalation,
misleading-claims guard.

**L. Evaluation** — Fair synthetic benchmark: stockout-days averted naive 103.5, TATHYON 80.9, always-verify 80.6,
random-targeting 27.1; phantom units shipped naive 204, TATHYON 0; visits TATHYON 20 vs always-verify 50.

**M. Security** — See `docs/SECURITY.md`. Demo identities only; rotate the pasted keys.

**N. Deployment decision** — Google Cloud Run, `asia-south1`, single container, `max-instances=1`, Secret Manager.
Render is the fallback. Firebase/Netlify not used (static/Node-oriented; backend is Python + OR-Tools).

**O. Live URL** — **None.** `gcloud` and Docker are not installed on this machine and the project is not in a git
repository, so it could not be deployed from here. Exact commands: `DEPLOYMENT.md`.

**P. Remaining limitations** — No government integration; no real stock/bed/attendance data; single-process
state; no OIDC; 3D gated on Map Tiles API; voice uses the browser engine; OSM coverage incomplete; forecasting is
period-average; state-to-state model sharing is a library, not a running service.

**Q. Demo script** — `docs/DEMO_SCRIPT.md` (12 steps, all verified in the browser).

**R. Final red flags** — (1) rotate the Gemini and Maps keys pasted in chat; (2) split/restrict Maps keys;
(3) not yet deployed; (4) sample data sits on real facility names — say it is SAMPLE; (5) present the negative
stockout-days result honestly.
