# 05 — Work Breakdown Structure

Status vocabulary is deliberately three-valued and nothing else:

| Status | Meaning |
|---|---|
| `DONE` | Implemented, exercised by the test suite or by `make all`, and its output is in `artifacts/` |
| `PARTIAL` | Something real runs, but a named part of the described behaviour is absent. The gap is written out in the row. |
| `NOT_STARTED` | No code. A docstring, a dataclass field or a design paragraph is `NOT_STARTED`. |

There is no `IN_PROGRESS`. Work either produces an artifact or it does not, and
"in progress" is where honest status reporting goes to die. Two items below are
being written during this cycle and are marked `PARTIAL` with the specific
missing piece named, not `DONE` in anticipation.

**Provenance note applying to every row:** all data is synthetic
(`tathyon/generator.py`). No pilot, no deployment, no partnership, no revenue.
See `14_LIMITATIONS.md`.

Priority: `P0` = the demo and the thesis fail without it. `P1` = a reviewer will
ask and the answer must be code. `P2` = credibility and completeness. `P3` =
deferred with intent.

---

## WS-A — Research and problem framing

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| A1 | Locate and read CAG audit paragraphs establishing the failure taxonomy (phantom stock, expired-counted-live, retroactive entry, issued-never-received) | P0 | — | `DONE` |
| A2 | Anchor incidence rates to published paragraphs where they exist; record the ones we chose ourselves | P0 | A1 | `DONE` — assumptions listed in `07_DATA_PROVENANCE.md` §anchors |
| A3 | Map GFR 2017 Rule 211 / 213(1) and Form GFR-22 onto the claim/attestation pair | P1 | A1 | `DONE` |
| A4 | BEMMP equipment-dysfunction band (13–34%) sourced and its over-generalisation documented | P1 | A1 | `PARTIAL` — one anchor (PSA plants, combined figure) is applied across five asset classes; the source does not support that. Recorded in `14_LIMITATIONS.md` §2. |
| A5 | Competitive and prior-art review, including the Hack2Skills winning entry | P1 | A1 | `DONE` — `11_COMPETITIVE_ANALYSIS.md`, with the identity caveat stated there |
| A6 | Field interviews with district store officers | P2 | — | `NOT_STARTED` — no user has ever touched this system |

## WS-B — Data and synthetic corpus

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| B1 | Seeded generator: 20 facilities, 10 SKUs, 240 days, 120 assets, ~48,000 ledger rows | P0 | A2 | `DONE` |
| B2 | Pathology injection with ground-truth columns (`true_stock`, `true_usable`, `true_demand`) held separately from observed | P0 | B1 | `DONE` |
| B3 | Expired-stock pathology actually reaching the simulation window | P0 | B2 | `DONE` — was inert (expiry dates drawn past the horizon); fixed in the generator, not in the demo |
| B4 | Equipment corpus: register status, vendor-reported uptime, physical truth | P0 | B1 | `DONE` — register says FUNCTIONAL for 100% of assets, 49.2% actually functional, divergence 50.8%, mean vendor uptime 97.9% against a 95% SLA |
| B5 | FEFO-violation pathway as a distinct injected pathology | P2 | B2 | `NOT_STARTED` — `fefo_violation` (0.036) sits in `CAG_ANCHORS` with no day-loop pathway behind it. The related detector fires on expiry-derived issues, which is not the same thing. |
| B6 | Road-network / terrain travel model replacing Euclidean × 1.35 ÷ 38 km/h | P3 | B1 | `NOT_STARTED` |
| B7 | Any real data, under any agreement | P3 | — | `NOT_STARTED` |

## WS-C — Backend / core engine

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| C1 | Immutable schema: `Claim`, `Evidence`, `Attestation`, `StateEvent` | P0 | — | `DONE` |
| C2 | Append-only hash-chained event store with replay | P0 | C1 | `DONE` — chain verifies; tamper test in the suite returns the breaking offset |
| C3 | Attester-is-not-custodian enforced at write time (`store.put_attestation` raises) | P0 | C2 | `DONE` |
| C4 | State machine `VERIFIED / UNVERIFIED / CONFLICTED / REJECTED / OVERRIDDEN`; `STALE` deliberately a read-time projection, never stored | P0 | C1 | `DONE` |
| C5 | FastAPI service over the real engine (11 routes incl. `/decisions`, `/evidence`, `/attestations`, `/events`, `/audit`, `/counterfactual`) | P0 | C2 | `DONE` |
| C6 | Blocked decision returns HTTP 200 with a typed decision, never an exception | P0 | C5 | `DONE` |
| C7 | Break-glass override creating a verification obligation with a deadline | P1 | C4 | `PARTIAL` — the override object, eligibility and required role exist and are returned; the obligation is not durably enforced, no deadline expiry job, no escalation |
| C8 | Durable persistence (event store is in-process) | P1 | C2 | `NOT_STARTED` — the chain is rebuilt on every run; nothing survives process exit |
| C9 | Multi-tenant identity, real signing keys | P2 | C3 | `NOT_STARTED` — attestation signature is the literal string `sig:prototype-software-keypair` |

## WS-D — ML: trust model, forecast, hazard

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| D1 | Tier-1 hard-violation detectors producing free weak-supervision labels | P0 | B2 | `DONE` |
| D2 | Trust model on an explicit 8-feature allow-list, temporal split, ground truth held out | P0 | D1 | `DONE` — PR-AUC **0.079** against a base rate of **0.053**; deterministic staleness baseline **0.047**; **~37%** fewer verifications than random to catch half the wrong records. Weak, and reported as weak. |
| D3 | Leakage guard | P0 | D2 | `PARTIAL` — enforced by the `detect.FEATURES` allow-list and covered by `tests/test_no_leakage.py`, but the observed table still physically carries the truth columns. The guard is an allow-list, not a schema separation. |
| D4 | "Unusual ≠ wrong" detectors D1–D3 | P0 | D2 | `DONE` |
| D5 | Fix the D1 failure by facility-relative residualisation | P0 | D4 | `PARTIAL` — **residualisation did not fix D1.** Both arms (naive and residualised) are kept in `artifacts/eval_report.json` so the failure is auditable. Diagnosis: the generator assigns pathologies per (facility, SKU) series, so facility genuinely carries signal about wrongness, violating D1's own assumption. The detector works; its threshold is mis-specified for this data-generating process. Not deleted, not silenced. |
| D6 | Intermittent-demand forecasting: TSB, Croston-SBA, naive, seasonal-naive; MASE + pinball, no MAPE | P0 | B1 | `DONE` |
| D7 | ADI-based per-series model selection (`forecast.select_model()`, Syntetos–Boylan 1.32) | P1 | D6 | `DONE` — pooled MASE **TSB 0.709 vs naive 0.699**, i.e. naive wins pooled; on the intermittent stratum **ADI > 1.32**, TSB **0.819** beats naive **0.827**. The pooled loss stays in the report. |
| D8 | Gamma–Poisson (Negative Binomial) freshness posterior with consumer-supplied α | P0 | C4 | `DONE` |
| D9 | Tier-2 weak-signal calibration against tier-1 labels | P1 | D1 | `NOT_STARTED` — what ships is eight hand-chosen features with hand-chosen thresholds and reason codes, not a calibrated probability |
| D10 | Equipment hazard model fitted to data | P2 | B4 | `PARTIAL` — four hand-seeded coefficients chosen to land inside BEMMP's published band. A prior, not estimates. No time-dependent AUC, Brier or calibration, because the longitudinal failure data does not exist here. |

## WS-E — AI (Gemini) integration

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| E1 | Transcription-only boundary: the human counts, the model transcribes what the human wrote | P0 | C1 | `DONE` as a design constraint enforced in code paths |
| E2 | Low-confidence cells render blank-to-fill, never pre-filled | P0 | E1 | `DONE` — pre-filling would launder a model error into a *signed* record |
| E3 | Gemini adapter | P0 | E1 | `PARTIAL` — **the adapter is a mock interface.** `extraction_model` is literally `gemini-transcription-adapter/mock` and the actor is `gemini_adapter`. The interface, the event it emits and the confidence-gating are real; no Gemini API call is made anywhere in this repository. |
| E4 | Serial-plate reading for equipment (model reads the plate, does not judge the asset) | P1 | E3 | `PARTIAL` — same mock adapter |
| E5 | Hindi / regional-language voice capture | P2 | E3 | `NOT_STARTED` |
| E6 | Root-cause narration over verified state | P3 | E3 | `NOT_STARTED` — deliberately last: narration over unverified state is the failure mode this project exists to prevent |

## WS-F — Optimisation

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| F1 | OR-Tools CP-SAT redistribution: minimise unmet critical need, then transport time, then number of moves | P0 | C4 | `DONE` |
| F2 | **The verification gate.** Unverified stock is not eligible; the solver never sees it; decision returns `BLOCKED_VERIFICATION_REQUIRED` with reasons and a remedy | P0 | F1, C4 | `DONE` — this is the product |
| F3 | Optimise over `Q_alpha` (confident-at-least quantity), never the point estimate and never the reported figure | P0 | D8, F1 | `DONE` |
| F4 | Donor safety floor and max-donor-fraction constraint | P1 | F1 | `DONE` |
| F5 | Counterfactual: what a naive optimizer would have moved | P0 | F2 | `DONE` — district-wide, **151 sources blocked** and **≈1,600 phantom units** a naive optimizer would have moved |
| F6 | CP-SAT objective bug (`IntAffine` has no `__floordiv__`; every solve raised `TypeError`) | P0 | F1 | `DONE` — coefficient reduced to a Python int before multiplying the variable |
| F7 | Vehicle routing, load consolidation, multi-leg | P3 | F1 | `NOT_STARTED` |

## WS-G — Security, privacy, federation

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| G1 | Threat model and security document | P1 | C2 | `DONE` — `09_SECURITY.md` |
| G2 | Evidence binding: nonce, frame count, perceptual hash, artifact hash | P1 | C1 | `DONE` |
| G3 | Role separation and delegation on attestation | P1 | C3 | `PARTIAL` — roles and delegation ids are carried on the event; there is no authentication, no authorisation middleware and no key management |
| G4 | Federated / hierarchical pooling module (SKU × facility-tier × district) | P1 | D8 | `PARTIAL` — **being added this cycle.** Until it lands, this is `ARCHITECTURAL_ONLY`: a `ConsumptionPrior` docstring. No federated learning, no secure aggregation, no cross-tenant protocol, no differential privacy. The cold-start case pooling would improve is currently handled by a weak default prior that correctly widens the posterior and forces verification. |
| G5 | Differential privacy on any cross-district aggregate | P3 | G4 | `NOT_STARTED` |
| G6 | PII handling | P0 | — | `DONE` by exclusion — no patient PII enters the system at all |

## WS-H — Testing and evaluation

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| H1 | Test suite: **62 tests pass** | P0 | C5, F2 | `DONE` — `test_gate.py`, `test_no_leakage.py`, `test_adversarial_data.py`, `test_api.py` |
| H2 | Gate tests: unverified stock cannot be transferred under any input | P0 | F2 | `DONE` |
| H3 | Tamper test: modifying a past event breaks the chain and `verify_chain()` returns the offset | P0 | C2 | `DONE` — hash chain verifies |
| H4 | Adversarial-data tests (non-numeric quantity, missing opening balance) | P1 | D1 | `DONE` — a non-numeric quantity used to crash the detector and take down the whole ingestion batch; quantities are now coerced and unparseable values raise `v_nonfinite_qty`. The first row of every series was a guaranteed false positive because the arithmetic identity is undefined without an opening balance. |
| H5 | API contract tests including the HTTP-200-on-block behaviour | P1 | C6 | `DONE` |
| H6 | Property-based / fuzz testing of the state machine | P2 | C4 | `NOT_STARTED` |
| H7 | Load or performance testing | P3 | C5 | `NOT_STARTED` — no figure in this repository is a latency or throughput claim |

## WS-I — Frontend

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| I1 | Decision workspace (`web/workspace.html`) — a workspace, not a dashboard | P0 | C5 | `PARTIAL` — renders the decision, the reasons, the audit timeline and the refusal; built from a static snapshot by `build_web.py` rather than live-bound to the API |
| I2 | Offline-first capture surface (evidence captured with connectivity off) | P0 | I1 | `PARTIAL` — the capture path and its event are real and exercised in the demo; there is no service worker, no Firestore offline persistence and no real device sync queue |
| I3 | Verification queue ordered by the priority score (score never persisted, never exported, never placed in an attestation) | P1 | D2 | `PARTIAL` — ordering logic exists; no queue UI |
| I4 | Investigation agent (walks an operator from a flagged claim to the evidence request) | P1 | I1, E3 | `PARTIAL` — **being added this cycle.** Interface and routing only; no completed end-to-end run is claimed. |
| I5 | Mobile capture client | P3 | I2 | `NOT_STARTED` |

## WS-J — Documentation

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| J1 | `07_DATA_PROVENANCE.md` | P0 | A2 | `DONE` |
| J2 | `08_MODEL_CARD.md` | P0 | D2 | `DONE` |
| J3 | `09_SECURITY.md` | P1 | G1 | `DONE` |
| J4 | `10_IMPACT_METHODOLOGY.md` (five evidence categories; `PUBLIC_EVIDENCE` may never be multiplied by a `SIMULATED` rate) | P0 | A1 | `DONE` |
| J5 | `12_REQUIREMENTS_TRACEABILITY.md` | P1 | — | `DONE` |
| J6 | `14_LIMITATIONS.md` | P0 | — | `PARTIAL` — **stale in one respect that matters:** it states "there are no tests" and that `tathyon/eval.py` does not exist. Both were true when written and are now false — 62 tests pass and `eval.py` runs. The document must be corrected; leaving a false self-deprecation in place is as much a documentation defect as a false claim. |
| J7 | The nine documents missing at the start of this cycle: `01`–`04`, `05_WBS`, `06_AD`, `11_COMPETITIVE_ANALYSIS`, `13_DEMO_SCRIPT`, `15_STARTUP_STRATEGY` | P0 | — | `PARTIAL` — five (`05`, `06`, `11`, `13`, `15`) are written in this cycle; `01`–`04` remain `NOT_STARTED` |

## WS-K — Demo

| ID | Task | Pri | Depends on | Status |
|---|---|---|---|---|
| K1 | End-to-end pipeline writing `artifacts/demo_scenario.json`, every number computed rather than hardcoded | P0 | F5 | `DONE` |
| K2 | Data-driven scene selection (the SKU is picked from the data, not pinned) | P0 | K1 | `DONE` — pinning made the scene silently depend on a 5.5%-incidence pathology landing on one SKU. Inflating the rate to make the demo work would have been exactly the dishonesty this project exists to avoid. |
| K3 | Scene 2: GFR-22 certificate refuses to emit when no independent evidence supports the claimed status | P0 | K1 | `DONE` |
| K4 | `make all` runs clean from a fresh clone | P0 | K1, H1 | `PARTIAL` — runs clean on this machine; not yet verified on a clean clone in a clean environment. This is a demo-blocking item (see `06_AD.md` §demo readiness). |
| K5 | Timestamped demo script with spoken words and clicks | P0 | K1 | `DONE` — `13_DEMO_SCRIPT.md` |
| K6 | Pre-recorded fallback replayed through the unmodified server path | P0 | K5 | `NOT_STARTED` — the plan is written; the recording does not exist |

---

## Critical path

The path below is the ordered set of items where a one-day slip moves the demo
date by one day. Everything not on it can be cut without changing what is shown.

```
B1 ─ B2 ─ B3 ──┐
               ├─ D1 ─ D2 ──┐
C1 ─ C2 ─ C4 ──┘            ├─ F1 ─ F6 ─ F2 ─ F3 ─ F5 ─ K1 ─ K2 ─ K4 ─ K6 ─ K5
        └─ D8 ──────────────┘
```

Read in words: the corpus must inject a pathology that actually reaches the
window (**B3**); the event store and state machine must exist before anything
can be gated (**C2 → C4**); the posterior must exist before the optimiser can be
handed `Q_alpha` rather than a reported figure (**D8 → F3**); CP-SAT must solve
at all (**F6**) before the gate has anything to gate (**F2**); the counterfactual
(**F5**) is what makes the refusal legible on stage; and the pipeline (**K1**)
must be reproducible from a clean clone (**K4**) with a fallback recording
(**K6**) before the script (**K5**) can be rehearsed against the real thing.

**The two live critical-path risks:**

1. **K4** — `make all` has never been run from a genuinely clean clone. Every
   demo failure in this class is discovered on stage.
2. **K6** — the fallback recording does not exist. A fallback that is written
   down but not made is not a fallback.

**Off the critical path and deliberately so:** G4 (federated module), I4
(investigation agent), E5/E6 (voice, narration), D9 (tier-2 calibration), C8
(durable store). Each is defensible work; none of them is required for the
refusal to happen in front of an audience, and if any of them slips, the demo is
unaffected.
