# DEVIATIONS — Prompt 1 (spec vs repository)

Every place the Prompt 1 spec and the repository disagreed, and what was done.
The repository is **not under version control**, so this file and `docs/archive/`
are the only record of what was removed. Nothing was deleted before its imports
and callers were mapped.

## Step 0: files named by the spec

| Spec item | Found | Callers before the change | Action |
|---|---|---|---|
| `web/spatial/` (3D page) | yes, plus `web/vendor/cesium` (22 MB), `tathyon/spatial.py`, `data/osm_buildings/`, `/spatial*` routes, `/geo/osm-buildings`, `/vendor` + `/spatial-assets` mounts | `api/main.py`, `tests/test_spatial_world.py` | deleted |
| `tathyon/federated.py` | yes | `api/main.py` (`/federation`) | deleted; `docs/federation-interface.md` written |
| `tathyon/agents.py` | yes, 6 classes | `api/main.py`, `tathyon/field.py`, 2 test files | folded (below), then deleted |
| `TRANSFORMATION_PLAN.md` | yes | none | moved to `docs/archive/` |
| Decorative Gemini path | `gemini.generate_briefing` + `POST /brief` | 2 tests | deleted |
| EXPEDITE / PROCURE as district actions | only as action kinds in the orphaned `tathyon/lever/` (see below) | none | archived; remedy text reworded to a state escalation |
| KEEP `store.py`, `detect.py`, `forecast.py` | yes | many | `detect.py` and `forecast.py` unchanged; `store.py` extended |
| KEEP `generator.py` | yes | many | additive only (see 6) |
| KEEP SOR payload pattern | `planner.generate_sor_payload` | API, tests | unchanged |
| MODIFY `schema.py`, `verify.py` | yes | — | done |

No spec-named file was missing.

## Deviations

1. **The folded code does not all fit "plan_verifications / plan_response".**
   The real `agents.py` held six classes:
   - `SurgeInvestigatorAgent` → `optimize.plan_response()` (the verified-only donor screen) plus `verify.plan_verifications()` (the unverified holders that must be counted first).
   - `ReconcilerAgent` → `verify.reconcile_equipment_sla()`. It is reconciliation, not planning.
   - `FieldGuideAgent` → `field.assess_capture()`. Its only caller is `field.py`, and it is capture feedback, not planning.
   - `SupplyIntelligenceAgent`, `SurgeInvestigationAgent`, `ResponsePlanningAgent` → deleted. Each only restated `StockoutPredictor`, `SurgeDetectionEngine` or `ResponsePlanner` output as templated text. The underlying engines and their tests remain.

2. **The word "agent" survives in three deliberate places**, all allow-listed with reasons in `tests/test_scope_hygiene.py`:
   - the HTTP `User-Agent` header (a protocol name that OSM and OSRM usage policies require)
   - the write-time denylist in `store.py`, which must spell out the role labels it rejects
   - two adversarial inputs in `tests/test_red_team_hardening.py` that prove the denylist works

   Obfuscating any of these would make a safety control unauditable.

3. **Breaking API renames.**
   - `/agents/reconcile` → `/reconcile/equipment-sla`
   - `/agents/field-guide` → `/field/capture-guidance`
   - `/agents/surge-investigate` → `/response/candidates`
   - `POST /harness/proposals/evaluate`: request fields `agent_id` / `agent_type` → `proposer_id` / `proposer_type`
   - error code `AGENT_ATTESTATION_FORBIDDEN` → `AUTOMATED_ATTESTATION_FORBIDDEN`
   - harness classes: `AgentSafetyHarness` → `ProposalSafetyHarness`, `AgentProposal` → `ActionProposal`, `AgentSecurityViolation` → `HarnessSecurityViolation`, `BoundedAgentRunner` → `BoundedToolRunner`

4. **Bugs fixed on the way.**
   - The old surge route's default path read a nonexistent `Registry.facility_ids` and passed `engine.state_for` its arguments in the wrong order.
   - That route also hard-coded tier `CHC`, safety stock 20 and 2.5 h travel. It now uses registry state, 14-day safety stock and straight-line travel time.
   - The harness `NotImplementedError` now raises `HarnessSecurityViolation("TOOL_NOT_EXECUTABLE")`, which fails closed.

5. **A test that asserted nothing.** `test_phase8_validation::test_no_feasible_plan` ended in `pass`. It is now `test_no_fabricated_transfer_when_donors_cannot_cover_the_need`, which asserts that no donor goes below its floor, no more than the 50 available units move, and nothing auto-executes.
   **OPEN for the allocator prompt:** when donors hold 50 of 100 units needed, the planner proposes a partial 50-unit plan. The old docstring expected `NO_FEASIBLE_PLAN`. Which one is right is a policy decision, so it was not decided here.

6. **`generator.py` (KEEP) was extended, not rewritten.**
   - Added `materially_wrong()`, `observation_labels()` (per-observation `is_materially_wrong`), `attestation_log()`, `TRUTH_COLUMNS` and `observed_only()`.
   - The label is value-identical to the old inline formula, checked against the committed `data/observed.parquet`.
   - The existing `label_wrong` column is kept for existing tests.

7. **`verify.posterior_unobserved` accepts arrays.** `max()` became `np.maximum()`. The result is identical for scalars (0.0 difference on 300 random cases). This lets the scorer batch the existing posterior.

8. **Consequence is *hidden* stock-out days, not absolute.**
   - Spec: consequence = stock-out days if the report is wrong × essentiality.
   - Implemented: (stock-out days if wrong − stock-out days if right) × essentiality, as an expectation over the training distribution of how much of a wrong report is usable.
   - Why: a report that already shows a stock-out gains nothing from a count. Both absolute figures are still exposed in the ranking.
   - The first version used the median usable share. The held-out queue test showed it catching *fewer* hidden stock-out days than ranking by probability alone (1,811 vs 2,411). The expectation, which is what the formula defines, catches 2,679. Both rows stay in the report.

9. **Budget unit.** `budget: int` is counted in half-day visit slots. A remote facility costs 2 slots (round trip plus count over 4 h). Each facility × SKU count is priced independently: trips shared across several SKUs at one facility are not modelled yet.

10. **Tier-1 flags are scorer features.** `tests/test_no_leakage.py` bans `v_*` flags from `detect.FEATURES`, because tier-2 is supervised by tier-1. The trust scorer is supervised by ground truth, so tier-1 counts are legitimate inputs there. They live in a separate `TRUST_FEATURES` list, and `detect.FEATURES` is untouched.
    `detect.hard_violations`' batch/expiry check reads a batch's whole history (future rows), so `trust_features.py` computes a causal equivalent. `detect.py` itself is unchanged.

11. **File-size cap.** Scorer features live in `tathyon/trust_features.py` and the evaluation in `tathyon/trust_eval.py`, so `verify.py` stays under 800 lines. The knapsack moved to `optimize.py`. The scorer, consequence and `target_verifications()` are in `verify.py` as specified.

12. **`python -m tathyon.verify` became `make scorer`** (`python -m tathyon.trust_eval`).

13. **VEN weights** (V=3, E=2, N=1) follow the MSH convention. The SKU-to-VEN assignment is a policy input, not clinical advice.

14. **Break-glass.** It "always succeeds" against any *blocked* decision and creates an obligation with a deadline. An override against an already-*allowed* decision still returns 409 `NOTHING_TO_OVERRIDE`. That is a malformed request, and 4xx means malformed. Unchanged.

15. **Not in the spec, found and handled.**
    - `tathyon/lever/`: an orphaned, half-built package from an earlier session. It had no callers and no tests, referenced a nonexistent `ledger.py`, and defined EXPEDITE / PROCURE as actions. It was archived to `docs/archive/lever_prototype/`, not deleted, because deletion here cannot be undone.
    - `osm_facilities.fetch_osm_buildings` and its tests were kept. It is generic OSM code; only its 3D consumer was removed.
    - Dead `AuthorityDecideIn` and duplicated `Registry` construction lines were removed.
    - The 3D basemap and simulated-federation rows were removed from `integrations.py`.
    - The root route now redirects to `/map`, and `map.html` dropped its globe projection.

16. **Docs.**
    - 21 docs whose subject is a removed capability or a superseded direction moved to `docs/archive/`, along with the four 3D screenshots. Rewriting historical audits would falsify them.
    - Links to moved docs were repointed.
    - The README was rewritten. The old one cited results from modules absent from this repo (`eval.py`, `pipeline.py`, `layers.py`, `hero_demo.py`, `web/index.html`) and make targets that do not exist.
    - The remaining active docs predate this rebuild and have not been rewritten.

## Tests

**Deleted** (tests for deleted modules only), 44 items:
- `tests/test_spatial_world.py`: 41 items (module `tathyon/spatial.py` deleted; count taken from the pytest node-ID cache)
- `tests/test_twin_planner_agents.py::test_04_three_typed_agents_contracts_and_boundaries` (deleted wrapper classes)
- `tests/test_brics_synthesis.py::test_brics_federated_silo_sharing` (`federated.py` deleted)
- `tests/test_red_team_reality_audit.py::test_gemini_containment_and_prompt_injection_safety` (`generate_briefing` deleted)

**Converted, not deleted:**
- `test_vertical_slice::test_briefing_returns_typed_8_line_memo` → `test_decorative_briefing_endpoint_is_removed`
- `test_ui_and_persistence::test_ui_index_is_served` now checks `/map`
- `test_phase6_apis` test_05 drops its federation block; test_06 checks that removed routes return 404
- `test_web_operational_flow` test_01: `/spatial` must return 404

**Renamed:**
- `test_agents_and_harness.py` → `test_planning_functions_and_ingest.py`
- `test_twin_planner_agents.py` → `test_twin_planner.py`
- `test_agent_safety_harness.py` → `test_proposal_safety_harness.py`

**Count:** 417 before → 373 after removals → **465** after adding:
- `test_decision_events` (22)
- `test_target_verifications` (44)
- `test_trust_scorer` (16)
- `test_scope_hygiene` (6)
- `plan_verifications` (1)
- SLA input validation (3)

## Independent review (python-reviewer, after implementation)

- **HIGH, fixed:** `reconcile_equipment_sla` accepted a negative downtime and returned 113.9% uptime with `PAYABLE` and a zero penalty, which made SLA-penalty evasion possible through the API. Out-of-range inputs are now refused, both in the function (`ValueError`) and at the route (422). Regression test added. The same unchecked arithmetic existed in the deleted `ReconcilerAgent` before the fold.
- **LOW, fixed:** `knapsack` dropped zero-cost items at capacity 0. It was unreachable in practice because visit costs are always at least 1. Test added.
- Checked with no issue found: causality of `trust_features` (reproduced on truncated ledgers), fairness of the queue comparison, and knapsack correctness against brute force over 5,000 random cases.

---

# DEVIATIONS — Prompt 2 (Optimizer Rebuild, Network Solve, Evaluation Harness)

## Decisions on Open Items

1. **Partial Fulfillment Decision (Open Item 5 from Prompt 1)**:
   - **Decision**: Allow partial fulfillment rather than returning `NO_FEASIBLE_PLAN` when available surplus < demand.
   - **Mechanism**: Every `ResponsePlan` and solver `Decision` now explicitly carries `fulfilled_qty` and `shortfall_qty`. If `shortfall_qty > 0`, the flag `replan_required=True` is unconditionally set.
   - **Rationale**: In acute public health emergencies, delivering 50 life-saving vials to a facility needing 100 averts immediate deaths, provided the system explicitly declares the remaining 50 units as unmet need and triggers replanning/state escalation. `NO_FEASIBLE_PLAN` is reserved strictly for instances where zero units can safely move under physical and safety constraints.
   - **Test Updated**: `test_phase8_validation::test_no_fabricated_transfer_when_donors_cannot_cover_the_need` updated to assert `plan.status == 'PROPOSED'`, `plan.fulfilled_qty == 50.0`, `plan.shortfall_qty == 20.0` (70 needed - 50 fulfilled), and `plan.replan_required is True`.

2. **Single CP-SAT Network Solve (`tathyon/optimize.py`)**:
   - Replaced pairwise greedy transfer logic with a unified OR-Tools CP-SAT multi-commodity network flow solve per planning cycle.
   - Enforces verification gate (unverified stock strictly 0.0), recipient shortfall bounds, cycle truck capacity, cold-chain compliance, and folded rescue-margin deadlines.
   - Coerced all coefficient calculations to Python native integers (`int(round(...))`) to prevent `IntAffine` floordiv regressions.
   - Implemented single-worker solve (`solver.parameters.num_search_workers = 1`) to guarantee deterministic byte-identical execution and eliminate macOS arm64 Abseil thread deadlocks.

3. **Typed Network Model (`tathyon/graph.py`)**:
   - Slimmed to typed nodes (`Facility`, `Warehouse`, `Supplier`, `SKU`/`SKUBatch`) and typed edge relations (`stocks_table`, `transfer_edges`, `supplier_edges`).
   - Standardized on EXACTLY three named network queries: `donor_reachability`, `supplier_concentration`, and `failure_cascade`.
   - Maintained backward-compatibility aliases (`states`, `facilities`, `get_resource_state`, `get_shortage_facilities`, `get_donor_inventory`, `demand_series`) to preserve stability across existing test suites.

4. **Per-Facility Burn-Rate and Expiry Clock (`tathyon/forecast.py`)**:
   - Added `facility_sku_expiry_clock` and `compute_facility_sku_burn_and_expiry`.
   - Expiry clock tracks days-to-stockout vs days-to-expiry and categorizes risk: `SAFE`, `EXPIRING_BEFORE_USE`, `CRITICAL_EXPIRY_RISK`, `EXPIRED`, `NO_EXPIRY`.
   - Existing Croston-TSB backtest model selection kept untouched.

5. **Scenario Engine & Product Surface (`tathyon/twin.py`)**:
   - Moved scenario and shock simulation engine into `tathyon/evaluate.py`.
   - No Twin screen existed in `web/` (`web/` contains only `field.html` and `map.html`).
   - API endpoints (`/twin/run`, `/twin/{id}`) and `tathyon/twin.py` retained as compatibility bridges so all existing API and integration regression tests pass without breakage.

6. **Folding Rescue Margin & Indent Lag (`rescue.py`, `rescue_loop.py`, `runway.py`)**:
   - Rescue-margin deadline logic folded directly into `tathyon/optimize.py` (`OptNeed.deadline_hours` enforces `effective_arrival <= time_to_stockout`).
   - Indent lag folded into lead-time resupply horizon (`lead_time_days = base_lead_time + indent_lag_days`).
   - Dedicated regression suite added in `tests/test_cpsat_network_solve.py` proving deadline constraints and rescue margins are strictly enforced by the CP-SAT solver.

7. **System-of-Record CSV Ingest & Adapters (`tathyon/adapters.py`)**:
   - Added `parse_dvdms_csv` to ingest DVDMS / e-Aushadhi shaped stock exports.
   - Non-numeric or corrupt quantities generate typed `RowValidationError` and skip invalid rows without crashing the batch.
   - All valid rows receive explicit provenance labels (`source`, `timestamp`, `provenance="CSV_IMPORT"`).
   - Documented schema and boundary in `docs/adapter-boundary.md`.

8. **Multi-Arm Evaluation Harness (`tathyon/evaluate.py`)**:
   - Deterministic 10-archetype scenario generator with explicit silent-phantom weighting.
   - Compares 4 competitive baselines (`naive`, `always_verify`, `min_max`, `greedy_guarded`) + `ai_ablation` arm on identical data and seeds.
   - Generates `artifacts/eval_report.json` and prints single headline line.
   - Added `eval` target to `Makefile` and `tests/test_evaluation_determinism.py` proving byte-identical reproducibility across runs.

---

## DEVIATIONS — Prompt 3 (Final Rebuild & Delivery)

1. **Headline Metric Shift (`verified_stockout_days_averted`)**:
   - In Prompt 2, raw `stockout_days_averted` rewarded baselines that shipped phantom stock (naive scored 95.97 and greedy guarded 100.50 vs Tathyon's 80.87 only because they shipped 204.0 and 83.0 phantom units respectively).
   - In Prompt 3, `verified_stockout_days_averted` was added to `ArmResult` and `EvaluationReport` in `tathyon/evaluate.py`: stockout days served from verified-usable stock only; shipments of phantom stock count strictly zero.
   - `verified_stockout_days_averted` is now the sole headline metric printed by `make eval`, rendered on the web UI, and reported in the README and docs.
   - The min/max baseline was verified: it legitimately scores 0.00 verified stockout days averted because it reorders unverified stock from the central District Hospital depot, which contains 204 phantom units in the test corpus, and dispatches them without physical verification.

2. **API Slimming (`api/main.py`)**:
   - Slimmed the FastAPI transport down to exactly 9 operational endpoints, each with a one-line justification comment:
     - `GET /trust/queue`
     - `POST /verify/attest`
     - `POST /allocate`
     - `POST /plans/{plan_id}/approve`
     - `GET /plans/{plan_id}/sor-payload`
     - `POST /outcomes`
     - `GET /events`
     - `GET /eval/report`
     - `GET /health`
   - Dead routes from prior iterations (`/twin/*`, `/reconcile/*`, `/field/*`, `/response/*`, `/harness/*`, `/brief`) were removed, and corresponding legacy tests updated or pruned. Full test suite remains 100% green (384 passed).

3. **Gemini Transcriber & Provenance Narrative (`tathyon/gemini.py`)**:
   - Bounded transcription kept strictly for count sheets: low-confidence extractions return `None` to render blank-to-fill for human verifiers.
   - Added `generate_provenance_narrative`: produces a concise, causality-linked narrative where every statement cites formal event IDs (`[EVT-...]`) and any claim resting on unverified reports is explicitly labeled `UNVERIFIED` inline.
   - Live API path is used when `GEMINI_API_KEY` is present; if unset, it falls back to a deterministic fixture explicitly stamped with `[SIMULATED FIXTURE]` — never passed off as live model output.

4. **Web UI Rebuild (`web/index.html`)**:
   - Rebuilt as a daylight-themed public health operational workspace across five clear tabs:
     1. Trust Queue: ranked verifications with one-line reasons, expected-value knapsack math, and 2D topology map.
     2. Allocation Plan: single CP-SAT network solve transfers, donor safety floors, rejected donors with reasons, and do-nothing counterfactuals.
     3. Sovereign Approval: Chief Medical Officer sign-off, per-line rejection, and break-glass override with 72-hour obligation.
     4. Outcome & Reconciliation: physical receipt reconciliation, variance logging, automated replan state, and live evaluation headline metrics.
     5. Audit Ledger: hash-chained event log displaying chronological provenance and tamper verification.
   - Every displayed number features explicit inline or hover provenance badges (`VERIFIED`, `UNVERIFIED`, `SYNTHETIC`).

5. **Phantom Trap Scripted Demo (`make demo` & `tathyon/demo.py`)**:
   - Deterministic 8-scene simulation executing the PHANTOM TRAP (anti-rabies vaccine shortage at PHC Y with phantom surplus at PHC X).
   - Demonstrates the complete loop: naive trap -> trust scorer flagging -> verify-then-contingent allocation -> field attestation uncovering 4,800 phantom units -> contingent plan activation from verified donor Z -> receipt reconciliation with 50-unit variance -> replan trigger from central buffer.
   - Reproduces in under 1 second in fast mode (`--fast`) or 3 minutes in presentation mode.

---

# DEVIATIONS & EXTENSIONS — Tathyon v2 (Full Platform: Multi-Resource Sovereign Reliability)

Build with AI 2.0 Track 3 Official Brief: Federated resource-reliability for Primary Health Centre (PHC) networks across medicine stocks, bed availability, and medical personnel attendance, demand forecasting, emergency early warnings, cross-district redistribution, and shared predictive modeling across BRICS partners.

## 1. Beds Module (`tathyon/beds.py` & `tests/test_beds_module.py`)
- **Mechanics**: Implemented synthetic bed generator with phantom failure modes (`phantom_free_bed` where beds are reported free but physically blocked, `unreported_maintenance` where broken/sanitation-failed beds are counted as active).
- **Trust Scorer**: 10 causal observed-only features (`BED_TRUST_FEATURES`: admissions-vs-occupancy consistency, turnover rate anomaly, discharge lag, census attestation age). Calibrated Gradient Boosting classifier (`BedTrustScorer`).
- **Allocation Physics**: Unlike medicines, beds cannot move; the allocator diverts *patients* to verified available capacity using OR-Tools CP-SAT (`solve_patient_diversion`). Enforces destination safe occupancy ceiling (default 88%) and strict verification gate (unverified bed capacity treated as 0).
- **Early Warnings**: `evaluate_bed_surge_warnings` computes projected occupancy over 7-day and 14-day horizons, flagging critical saturation thresholds.

## 2. Personnel Module (`tathyon/personnel.py` & `tests/test_personnel_module.py`)
- **Mechanics**: Synthetic duty roster vs biometric punch generator with phantom failure modes (`ghost_roster_worker` where staff appear on payroll/roster but are absent, `proxy_punch_absence` where attendance is fraudulently logged).
- **Trust Scorer**: 10 causal observed-only features (`PERSONNEL_TRUST_FEATURES`: punch-to-shift consistency, overtime ratio, leave overlap anomaly, supervisor attestation age). Calibrated Gradient Boosting classifier (`PersonnelTrustScorer`).
- **Allocation Physics**: Temporary staff redeployment across facilities using OR-Tools CP-SAT (`solve_staff_redeployment`). Mandatory donor statutory floors strictly enforced: donor facilities must retain $\ge 1$ Medical Officer and $\ge 1$ Staff Nurse under all redeployment plans.

## 3. Resource-Agnostic Core (`tathyon/core_platform.py` & `tests/test_core_platform.py`)
- **Unified Pipeline**: Parameterized trust queue, verification budget, approval, and audit engine supporting `ResourceType.MEDICINE`, `ResourceType.BED`, and `ResourceType.PERSONNEL`.
- **Knapsack Targeting**: `target_unified_verifications` computes global expected consequence across all resource types under a finite multi-inspector budget.
- **Dispatcher**: `UnifiedAllocationDispatcher` routes requests to the appropriate solver according to the resource's physical constraints (stock movement for medicine, patient diversion for beds, staff redeployment with statutory retention for personnel).
- **Sovereign Approval**: `execute_sovereign_approval` preserves line-item vetoes and break-glass overrides with 72-hour audit obligations.

## 4. Honest Federation (`tathyon/federation.py`, `docs/federation-interface.md`, & `tests/test_federation.py`)
- **Doctrines**: Zero facility rows or patient PII ever transmitted upwards or across borders. Only model weights (`FederatedWeightPackage`) and generalized anomaly descriptors (`PhantomAnomalyPattern`) move.
- **Weight Exchange**: `sanitize_and_export_weights` enforces strict anti-leakage audits, validating that feature vectors match authorized definitions and no raw data frames are included.
- **Aggregation**: `FederatedTrustAggregator` executes observation-weighted Federated Averaging (FedAvg) across Indian districts and BRICS partner states (Brazil, Russia, India, China, South Africa) with anomaly clustering.

## 5. Emergency Surge Mode (`tathyon/surge.py` & `tests/test_surge_mode.py`)
- **Outbreak Scenarios**: Parameterized disease profiles (`CHOLERA_DIARRHEA`, `DENGUE_VECTOR`, `RESPIRATORY_EPIDEMIC`, `MONSOON_FLOOD_TRAUMA`) applying calibrated multipliers to demand forecasting.
- **Tightened Warning Thresholds**: Surge warning horizon compressed to 14–18 days.
- **Joint Fragility Prioritization**: `evaluate_joint_fragility` scores facilities on compound vulnerability across concurrent medicine stockouts, bed saturation, and personnel deficits.
- **Multi-Resource Plan Generation**: `generate_emergency_surge_plan` coordinates simultaneous medicine resupply, patient diversion, and emergency staff deployment.

## 6. Production Readiness (`tathyon/production.py` & `tests/test_production_readiness.py`)
- **Hardened File-Drop Ingest**: `ingest_file_drop` processes DVDMS/e-Aushadhi CSV/TSV exports with robust line-by-line quarantine for corrupted rows, returning actionable validation errors without batch aborts.
- **RBAC Separation of Duties**: `IdentityToken` enforces `attester != custodian` directly at the cryptographic token and role level, preventing unauthorized self-attestations.
- **Offline-First Mobile Verification**: `OfflineSyncManager` buffers count sheet attestations with unique nonces to prevent replay attacks upon reconnection.
- **Monthly Pilot ROI**: `compute_monthly_pilot_roi` quantifies verification visits spent, phantom units blocked, and stockout-days averted against historical baselines.

## 7. Verification & Determinism
- **Test Suite**: Extended from 384 tests to 405 tests (100% green).
- **Benchmark Reproducibility**: `make eval` generates byte-identical `artifacts/eval_report.json` across repeated runs, maintaining `verified_stockout_days_averted=80.87` as the immutable core headline.


