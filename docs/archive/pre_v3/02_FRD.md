# 02 — Functional Requirements Document

Numbered functional requirements for Tathyon. Each requirement cites the implementing function and the test that covers it. Where no test covers a requirement, the Test column says `none` — that is a gap statement, not an omission.

**All data is SYNTHETIC.** Test count at the time of writing: 62 passing (`make test`).

Conventions:
- `MUST` — enforced in code and covered by a test.
- `MUST (untested)` — enforced in code, no test asserts it.
- Reason codes are drawn from `schema.REASONS` (25 codes).

---

## FR-1 Claim ingestion

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-1.1 | The system MUST record what a system of record asserts as an immutable `Claim` carrying facility, resource type, resource key, a type-specific state body, source system, source actor, effective time, ingestion time and provenance. | `schema.Claim` (`@dataclass(frozen=True)`) | `test_gate.py` construction throughout |
| FR-1.2 | Ingesting a claim MUST append exactly one `CLAIM_INGESTED` event carrying the claim digest. | `store.EventStore.put_claim` | `test_gate.py::test_01_verified_stock_is_transferable` |
| FR-1.3 | A correction MUST be a new claim that supersedes the prior one. The system MUST NOT update a claim in place. | `Claim.supersedes`; `EventStore` exposes no update or delete path | `none` (no test plants a superseding claim) |
| FR-1.4 | Every claim MUST carry a `Provenance` value, and synthetic data MUST be labelled `SYNTHETIC`. | `schema.Provenance`; every constructor in `api/main.py::Registry` and `pipeline.py` passes it explicitly | `test_api.py::test_resource_state_is_a_projection_with_provenance` |
| FR-1.5 | Resource types MUST be a closed, registry-governed set. Tenant-defined types MUST be rejected in v1. | `schema.ResourceType` (`MEDICINE`, `EQUIPMENT`) — an enum, so an unknown type cannot be constructed | `none` |
| FR-1.6 | The latest claim for a resource MUST be resolvable by effective time. | `store.EventStore.latest_claim` | `test_api.py::test_evidence_validation_and_unknown_resource` (404 when absent) |

**Rationale for FR-1.5.** Genericity without a schema destroys the gate: a policy cannot reason about "enough amoxicillin" if quantity is an opaque blob (`schema.py`, `ResourceType` docstring).

---

## FR-2 Anomaly detection

### FR-2A Tier-1 — hard violations (free labels)

A tier-1 violation is a record that contradicts itself. No ground truth and no human time is needed to know it is wrong.

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-2.1 | Closing balance ≠ opening + receipts − issues MUST raise `LEDGER_ARITHMETIC`. Tolerance: `max(1.0, 2% of reported_stock)`. | `detect.hard_violations` → `v_arithmetic` | `test_adversarial_data.py::test_11b_two_rows_can_still_violate_arithmetic` |
| FR-2.2 | The first row of a series MUST NOT be flagged for arithmetic — the identity is undefined without an opening balance. | `d["prev_stock"].notna() &` guard in `v_arithmetic` | `test_adversarial_data.py::test_11_single_row_dataframe_does_not_crash_or_false_positive` |
| FR-2.3 | Negative implied consumption MUST raise `NEGATIVE_IMPLIED_CONSUMPTION`. | `v_negative_consumption` | `none` (covered indirectly by FR-2.1 fixtures) |
| FR-2.4 | The same batch recorded with more than one expiry date MUST raise `BATCH_EXPIRY_CONFLICT`. | `v_batch_expiry_conflict` | `test_adversarial_data.py::test_01_same_batch_two_expiries` |
| FR-2.5 | An issue against a batch already past expiry MUST raise `ISSUE_AGAINST_EXPIRED`. | `v_issue_against_expired` | `none` |
| FR-2.6 | A unit value above ₹50,000 MUST raise `ABSURD_UNIT_VALUE`; a plausible value MUST NOT. | `v_absurd_unit_value`, `detect.ABSURD_UNIT_VALUE` | `test_adversarial_data.py::test_04_absurd_unit_value_1110111_20`, `::test_04b_plausible_unit_value_is_not_flagged` |
| FR-2.7 | A dated commodity with no expiry MUST raise `MISSING_EXPIRY`, and that violation MUST have a reason code an officer can read aloud. | `v_missing_expiry` + `detect.reason_codes` | `test_adversarial_data.py::test_05_missing_expiry_is_reported_not_just_flagged` |
| FR-2.8 | A negative quantity in any quantity column MUST raise `NEGATIVE_QUANTITY`. | `v_negative_qty` | `test_adversarial_data.py::test_02_negative_quantity_is_flagged`, `::test_02b_negative_issue_is_flagged` |
| FR-2.9 | NaN, infinite or unparseable quantities MUST raise `NON_FINITE_QUANTITY`. Detection MUST NOT crash on a corrupt cell. | `v_nonfinite_qty` plus the `_coercion_loss` pre-pass over `QTY_COLS` | `::test_08_nan_quantity_is_flagged_not_silently_swallowed`, `::test_08b_infinite_quantity_is_flagged`, `::test_08c_non_numeric_quantity_is_flagged` |
| FR-2.10 | A record dated in the future MUST raise `FUTURE_DATED_RECORD`. | `v_future_dated` | `::test_03_future_dated_record_is_flagged` |
| FR-2.11 | A record entered before the event it describes MUST raise `IMPOSSIBLE_CHRONOLOGY`. | `v_impossible_chronology` | `::test_06_receipt_entered_before_the_event_it_describes` |
| FR-2.12 | An empty ledger MUST produce an empty, correctly-shaped frame, not an exception. | early-return branch in `detect.hard_violations` declaring `VIOLATION_COLUMNS` with dtypes | `::test_10_empty_dataframe_returns_an_empty_frame_not_a_crash` |
| FR-2.13 | Detection MUST accept both tz-naive and tz-aware timestamps. | `pd.to_datetime(..., utc=True)` in `v_future_dated` / `v_impossible_chronology` | `::test_03b_tz_aware_input_does_not_crash_the_detector` |
| FR-2.14 | No corrupt record MUST pass through with an empty reason list. | `detect.reason_codes` coverage of every `v_*` column | `::test_corrupt_rows_never_pass_through_with_an_empty_reason_list` |
| FR-2.15 | Every reason code the detector emits MUST be documented in `schema.REASONS`. | `detect.reason_codes` ↔ `schema.REASONS` | `::test_00b_every_reason_code_the_detector_emits_is_documented` |
| FR-2.16 | Clean data MUST raise nothing. | control path | `::test_00_control_clean_data_raises_nothing` |

**Rationale for the crash-safety requirements (FR-2.9, FR-2.12).** A crash is the one failure mode this layer must never have: one corrupt cell would otherwise take down the detector for every other record in the ingestion batch. Garbage in a quantity column is itself a finding, so it is coerced, recorded and reported.

### FR-2B Tier-2 — weak signals

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-2.17 | The system MUST compute weak signals from the **observed ledger only**: entry lag, round-number density, facility-relative quantity deviation, consumption per OPD deviation, inter-arrival smoothness, expired-but-stocked, staleness. | `detect.weak_features`, `detect.FEATURES` (8 features) | `test_no_leakage.py::test_features_are_computable_without_any_ground_truth_column` |
| FR-2.18 | No feature MUST be named after, or derived from, a ground-truth column or an injected pathology flag. | `eval.assert_no_leakage`, `detect.VIOLATION_COLUMNS` declared separately from `FEATURES` | `test_no_leakage.py::test_no_feature_is_named_after_ground_truth`, `::test_no_feature_is_named_after_an_injected_pathology`, `::test_hard_violation_flags_are_not_features`, `::test_no_feature_name_contains_a_forbidden_token`, `::test_every_feature_uses_the_f_prefix_convention` |
| FR-2.19 | No single feature MUST recover the label, by correlation or by mutual information. | leakage test battery | `::test_no_single_feature_recovers_the_label`, `::test_no_single_feature_has_near_deterministic_mutual_information` |
| FR-2.20 | The leakage detector itself MUST catch a planted leak. | negative control | `::test_the_leakage_detector_itself_catches_a_planted_leak` |
| FR-2.21 | Quantity deviation MUST be computed **facility-relative**, not globally normalised. | rolling 28-day mean/std within `(facility_id, sku)` in `weak_features` | covered by FR-2.18 tests |

**Rationale for FR-2.21.** Normalising globally is exactly how a model learns "big hospital = wrong". See FR-2.22.

### FR-2C The "unusual ≠ wrong" guard

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-2.22 | The system MUST run three independent detectors for the failure mode "the model learned facility identity rather than misreporting": D1 (flag rate regressed on facility covariates vs on confirmed discrepancies), D2 (flagged precision vs random-arm base rate), D3 (AUC after facility residualisation). | `eval.unusual_not_wrong` | `none` (the function is the check; no test asserts a verdict) |
| FR-2.23 | At least 10% of the verification queue MUST be uniform random, as the only unbiased base-rate estimate and the only clean calibration data. | `eval.RANDOM_ARM_FRACTION = 0.10`, used by D2 | `none` |
| FR-2.24 | Both the residualised and non-residualised model arms MUST be reported. | `eval.run` writes `unusual_not_wrong` and `unusual_not_wrong_NAIVE_ARM` | `none` |

**Measured outcome, stated as a finding, not a feature.** Detector **D1 fired**, and facility-relative residualisation (`eval.facility_relative`) did **not** fix it. Both arms are retained in `artifacts/eval_report.json`. Diagnosis: the generator assigns pathologies per (facility, SKU) series, so facility genuinely carries signal about wrongness — which violates D1's own assumption. The detector is working; its threshold is mis-specified for this data-generating process. **Limitation:** on real data this ambiguity would have to be resolved before the priority queue could be trusted.

---

## FR-3 Verification task prioritisation

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-3.1 | The system MUST rank candidate verifications under a limited human budget using expected discrepancy, financial impact, staleness and expected cost in minutes. | `verify.VerificationEngine.priority` | `none` |
| FR-3.2 | The priority score MUST NOT be persisted, exported, or placed in an attestation. It is a resource-allocation quantity, not a truth claim. | `priority` is a `@staticmethod` returning a float; `VerifiedState` has no score field; `Attestation` has no score field | enforced structurally by `schema.py`; `none` asserts the absence |
| FR-3.3 | The trust model MUST be trained on tier-1 hard violations (weak supervision) and MUST NOT see the ground-truth label during training. | `eval.TrustModel.fit(X_tr, y_weak_tr)` where `y_weak_tr = tr["hard_violation"]` | `test_no_leakage.py` battery |
| FR-3.4 | Train/test split MUST be temporal (forward chaining), never a random row split. | `eval.run`: `cut = max(date) - 45 days` | `none` |
| FR-3.5 | The system MUST report a yield curve (discrepancies found per fixed verification budget) against a uniform-random arm. | `eval.yield_curve` | `none` |
| FR-3.6 | The system MUST compute, not assert, the effort reduction to reach a target recall. | `eval.effort_to_catch` | `none` |

**Measured.** Trust model PR-AUC **0.079** against a base rate of **0.053**; the deterministic staleness-rule baseline scores **0.047**. The model reduces the verifications needed to catch 50% of wrong records by **~37%** versus random. This is a weak model that narrowly beats a rule. It is reported as such.

---

## FR-4 Offline evidence capture

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-4.1 | Evidence MUST carry a **server-issued nonce** and the time it was issued, so a photograph can be bound to a verification request. | `schema.Evidence.nonce`, `.nonce_issued_at`; `EvidenceIn.nonce` (min length 4) | `test_api.py::test_evidence_validation_and_unknown_resource` |
| FR-4.2 | Evidence SHOULD be a **3-frame burst**, which defeats photo-of-a-photo capture. A submission with fewer than 3 frames MUST be accepted but MUST carry a `SINGLE_FRAME_CAPTURE` warning. | `Evidence.frame_count`; warning emitted in `api/main.py::submit_evidence` | `test_api.py::test_evidence_validation_and_unknown_resource` |
| FR-4.3 | Evidence MUST carry both a cryptographic hash of the bytes and a perceptual hash, so re-submission of a prior image is detectable (`EVIDENCE_REUSED`). | `Evidence.artifact_hash`, `.perceptual_hash` | `none` — **the reuse check itself is not implemented**; the field exists, no code compares it against facility history |
| FR-4.4 | Evidence submission MUST be **idempotent on `client_event_id`**, so a device replaying its whole offline queue after a partial sync does not double-append. | deterministic `evidence_id` from `sha256(facility|resource|client_event_id)` in `submit_evidence`; `EventStore.append` dedupes on `client_event_id` | `test_api.py::test_evidence_submission_is_idempotent`, `test_gate.py::test_07_replayed_client_event_id_appends_exactly_once` |
| FR-4.5 | Evidence for an unknown resource MUST return 404 with `RESOURCE_NOT_FOUND`. | `submit_evidence` | `test_api.py::test_evidence_validation_and_unknown_resource` |
| FR-4.6 | Evidence submitted through the API MUST be labelled `REAL_USER_PROVIDED`, distinct from the synthetic seed corpus. | `provenance=Provenance.REAL_USER_PROVIDED` in `submit_evidence` | `none` |

**Limitation.** There is no mobile client. FR-4.1–FR-4.6 define and enforce the server-side contract an offline client would have to satisfy; nothing in this repository captures a photograph.

---

## FR-5 Transcription (the model never counts)

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-5.1 | The extraction model MUST transcribe the figures a human wrote on the register. It MUST NOT count objects from the photograph. | evidence `extraction.note` in `pipeline.py`: "The AI transcribes the figures the human wrote on the register. It does not count items from the photograph." Equipment: "Gemini reads the serial plate. It does not judge whether the machine works." | `none` |
| FR-5.2 | An extraction MUST NEVER by itself change a verification state. Only a human attestation can. | `verify.VerificationEngine.state_for` reads `store.latest_attestation`, never an extraction | `test_gate.py::test_04_extraction_disagreement_is_conflicted_not_verified` |
| FR-5.3 | Disagreement between the human count and the transcription above 5% MUST project the record to `CONFLICTED` with `EXTRACTION_DISAGREEMENT`, not to `VERIFIED`. | `state_for`: `att.extraction_agreement["delta_pct"] > 5` | `test_gate.py::test_04_extraction_disagreement_is_conflicted_not_verified` |
| FR-5.4 | A small disagreement MUST remain `VERIFIED`. | same branch | `test_gate.py::test_04b_small_disagreement_stays_verified` |
| FR-5.5 | The model MUST be able to abstain, and abstention MUST be recorded. | `Evidence.extraction_abstained`, `EvidenceIn.extraction_abstained` | `none` |
| FR-5.6 | Low-confidence cells MUST render blank-to-fill, never pre-filled. | **Not implemented** — no UI renders extraction cells. Stated as a design rule in `README.md`. | `none` |

**Rationale.** Vision-language models score **0.23–0.58** on object-counting benchmarks. A pre-filled count would launder a model error into a *signed* human record, which is strictly worse than no extraction at all.

---

## FR-6 Deterministic reconciliation

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-6.1 | The verified state MUST be a **pure function** of (event log, policy version, α). It MUST NOT read a stored status column. | `verify.VerificationEngine.state_for`; `VerifiedState` is never persisted | `test_api.py::test_resource_state_is_a_projection_with_provenance` |
| FR-6.2 | Unobserved consumption since the physical anchor MUST be modelled Gamma-Poisson, yielding a Negative Binomial predictive in closed form. | `verify.posterior_unobserved` | `none` (asserted structurally; no test checks the distribution) |
| FR-6.3 | The consumer MUST supply α, because the consumer owns the risk. A gate MUST answer against `q_alpha`, never the point estimate and never the reported figure. | `verify.quantity_posterior`; `alpha` query parameter on `/resources/.../state`, `/audit`, `/counterfactual`; `DecisionRequest.alpha` | `test_gate.py::test_03_solver_moves_against_verified_usable_never_reported`; `test_api.py::test_unknown_resource_is_404_and_bad_alpha_is_422` |
| FR-6.4 | Quantities present but unusable (expired) MUST be separated from usable quantity and MUST raise `EXPIRED_STOCK_COUNTED_LIVE`. | `state_for`: `unusable_qty = present - usable` | `none` |
| FR-6.5 | Equipment freshness MUST use a discrete-time hazard with a logistic link, not a TTL. | `verify.survival` | `none` |
| FR-6.6 | A cold-start series with no history MUST produce a correctly wide posterior, collapsing `q_alpha` toward zero. | `ConsumptionPrior` defaults `a0=2.0, b0=1.0` when fewer than 3 observations | `none` |

**Rationale for FR-6.2.** The earlier design subtracted a p95 of unobserved consumption from the anchor. That is wrong: quantiles do not add across time windows. Poisson rates do.

---

## FR-7 Human attestation with custodian separation

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-7.1 | The attester MUST NOT be the custodian of the resource. This MUST be enforced **at write time**, not as a warning in a report. | `store.EventStore.put_attestation` raises `ValueError` when `att.is_custodian` | `test_gate.py::test_06_custodian_may_not_attest` |
| FR-7.2 | The API MUST surface that rejection as HTTP 422 with `reason_code: ATTESTOR_IS_CUSTODIAN`, the reason text, and a remedy naming who may attest instead. | `api/main.py::submit_attestation` try/except | `test_api.py::test_custodian_attestation_is_rejected` |
| FR-7.3 | A non-custodian attestation MUST be accepted and MUST move the resource to `VERIFIED` (subject to FR-7.4, FR-7.6). | same handler | `test_api.py::test_non_custodian_attestation_is_accepted`, `test_gate.py::test_01_verified_stock_is_transferable` |
| FR-7.4 | An attestation completed faster than `POLICY["min_attestation_seconds"]` (20 s) MUST project to `CONFLICTED` with `RUBBER_STAMP_SUSPECTED`, and the API MUST warn at submission time. | `state_for` integrity branch; warning in `submit_attestation` | `test_gate.py::test_05_attestation_faster_than_minimum_is_rubber_stamp_and_conflicted`, `test_api.py::test_rubber_stamp_attestation_projects_to_conflicted` |
| FR-7.5 | Submitting an attestation MUST require role `facility_incharge` or above. | `require_role("facility_incharge")` | `test_api.py::test_attestation_requires_facility_incharge_or_above` |
| FR-7.6 | An attestation older than the policy window (30 days medicine, 180 days equipment) MUST project back to `UNVERIFIED` with `ATTESTATION_STALE`. `STALE` MUST NOT be a stored state. | `state_for` age branch; `VerificationState` has no `STALE` member | `test_gate.py::test_09_attestation_beyond_the_policy_window_is_unverified_and_stale` |
| FR-7.7 | An attestation MUST reference the evidence it rests on, name the attester and their delegation, record seconds spent, and carry a signature. | `schema.Attestation` fields | construction asserted throughout `test_gate.py` |
| FR-7.8 | An equipment attestation MUST carry `observed.status` from a closed set; a medicine attestation MUST carry `observed.usable_qty`. Anything else MUST be 422. | `AttestationIn._observed_shape` validator + `OBSERVATION_SHAPE` checks in `submit_attestation` | `test_api.py::test_non_custodian_attestation_is_accepted` (shape path) |
| FR-7.9 | Attestation submission MUST be idempotent on `client_event_id`. | deterministic `attestation_id`; `EventStore` dedupe | `test_gate.py::test_07_replayed_client_event_id_appends_exactly_once` |
| FR-7.10 | An attestation whose observed status is not `FUNCTIONAL` MUST project to `REJECTED` with the specific code (`ASSET_NOT_PRESENT` / `ASSET_NOT_COMMISSIONED` / `ASSET_NON_FUNCTIONAL`) and survival 0. | `state_for` equipment branch | `none` (exercised by `pipeline.py` scene 2) |

**Limitation.** The signature is a software keypair in the prototype (`sig:prototype-software-keypair`), not a hardware-held key. `docs/09_SECURITY.md` states the consequence.

---

## FR-8 Verified-state projection

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-8.1 | `GET /resources/{facility_id}/{resource_key}/state` MUST return the projected state, machine-readable reasons, human-readable reason text, α, the policy version and the attestation-age window. | `api/main.py::resource_state` | `test_api.py::test_resource_state_is_a_projection_with_provenance` |
| FR-8.2 | A resource with no attestation MUST return `UNVERIFIED` and name `NO_ATTESTATION`. | `state_for` early return | `test_api.py::test_unverified_resource_names_its_reason` |
| FR-8.3 | An unknown resource MUST be 404; an α outside (0,1) MUST be 422. | `resource_state`, `Query(gt=0.0, lt=1.0)` | `test_api.py::test_unknown_resource_is_404_and_bad_alpha_is_422` |
| FR-8.4 | Every response MUST carry `provenance`, `as_of` and `ledger_offset`. | `api/main.py::_envelope` | `test_api.py::test_resource_state_is_a_projection_with_provenance` |

---

## FR-9 Demand forecasting

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-9.1 | The system MUST forecast intermittent demand with a Croston-family method, and MUST return a **distribution** over the lead time, not a point. | `forecast.fit_tsb`, `forecast.TSBFit.lead_time_distribution` (compound Bernoulli × size, Gamma moment-matched) | `none` |
| FR-9.2 | TSB MUST be preferred over plain Croston, because Croston's estimate is biased and never decays — a discontinued drug would forecast demand forever. | `fit_tsb` updates demand *probability* every period; `fit_croston_sba` retained as a comparison arm with the Syntetos-Boylan correction | `none` |
| FR-9.3 | Model choice MUST be made **per series** from that series' own intermittency, not globally. | `forecast.select_model` — TSB when ADI > 1.32 (Syntetos & Boylan 2005), otherwise a stationary mean carrying the TSB variance structure | `none` |
| FR-9.4 | Evaluation MUST use MASE and pinball loss. MAPE MUST NOT be reported, being undefined on zero-demand days. | `forecast.mase`, `forecast.pinball`, `forecast.coverage`; the exclusion is stated in the report's own `note` | `none` |
| FR-9.5 | Evaluation MUST use a temporal holdout **and** a disjoint facility holdout, never a random row split. | `forecast.evaluate` — last 30 days plus 25% disjoint facilities | `none` |
| FR-9.6 | Results MUST be stratified by ADI, and the pooled result MUST be published even when it contradicts the model choice. | `forecast.evaluate` returns `all_series`, `intermittent_series_ADI_gt_1.32`, `dense_series_ADI_le_1.32`, plus a `HONEST_FINDING` field | `none` |

**Measured, including the inconvenient result.** Pooled across all series, TSB does **not** beat the naive mean: MASE **0.709 vs 0.699** — *naive wins pooled*. Stratified, the textbook result returns: on intermittent series (ADI > 1.32) TSB **0.819 vs naive 0.827**; on dense series naive wins. This is a property of the generator, which draws demand from a largely stationary Poisson process — and for stationary Poisson the sample mean is the maximum-likelihood estimator, so naive is near-optimal by construction. `forecast.select_model()` picks per series on exactly that rule (FR-9.3). The pooled number stays in the report.

---

## FR-10 Stockout early warning

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-10.1 | The system MUST emit a four-level warning (`LOW / WATCH / HIGH / CRITICAL`) from days-to-stockout and P(stockout within lead time), scaled by facility criticality. | `forecast.early_warning`, `forecast.LEVELS` | `none` |
| FR-10.2 | Every warning MUST carry a written explanation naming the scenario, the rate and the lead-time requirement. It MUST NOT say "AI predicts shortage". | `Warning_.explanation` | `none` |
| FR-10.3 | Every warning MUST record its **basis** — whether it was computed on verified usable stock or on reported, unverified stock. | `Warning_.basis`; `pipeline.py` passes `"REPORTED stock (unverified)"` when the state is not `VERIFIED` | `none` |

**Limitation.** The level thresholds (0.15 / 0.35 / 0.6 on score; 0.5× / 1× / 2× lead time on days) are hand-set and unvalidated.

---

## FR-11 Constrained redistribution with the verification gate

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-11.1 | **The gate.** Stock that is not `VERIFIED` MUST NOT be transferable, and MUST NOT be visible to the solver. | `optimize.SourceStock.transferable` returns `0.0` unless `verified_state == VERIFIED` and `q_alpha is not None`; the gate loop runs **before** the CP-SAT model is built | `test_gate.py::test_02_unverified_stock_is_blocked_with_reasons` |
| FR-11.2 | Only the quantity above the facility's **own safety floor** MUST be transferable. | `max(q_alpha - safety_stock, 0.0)`; the API sets the floor at 14 days of consumption (`Registry.source_stock`) | `test_gate.py::test_01_verified_stock_is_transferable` |
| FR-11.3 | The solver MUST optimise against `q_alpha`, never the reported figure. | `cap = min(s.transferable, n.shortfall)` in `optimise` | `test_gate.py::test_03_solver_moves_against_verified_usable_never_reported` |
| FR-11.4 | A gate refusal MUST be a typed `Decision` with `allowed=false`, `status=BLOCKED_VERIFICATION_REQUIRED`, the blocked sources, their reason codes, and a remedy. It MUST NOT be an exception. | `optimize.optimise` gate branch, `optimize.Decision` | `test_gate.py::test_02_unverified_stock_is_blocked_with_reasons` |
| FR-11.5 | A blocked decision MUST be HTTP **200**, never 4xx/5xx. | `api/main.py::create_decision` | `test_api.py::test_blocked_decision_returns_200_with_allowed_false` |
| FR-11.6 | The objective MUST minimise unmet critical need, then transport time, then number of moves (weights 1000 / 3 / 50). | `optimise` objective assembly | `none` asserts the weights |
| FR-11.7 | A donor MUST NOT give away more than `max_donor_fraction` (default 40%) of its verified transferable stock; travel MUST be within `max_travel_hours` (default 6 h); a recipient MUST NOT receive more than its shortfall. | constraint block in `optimise` | `test_gate.py::test_01_verified_stock_is_transferable` |
| FR-11.8 | An unknown facility MUST raise a named error, never a bare `KeyError` and never a silent drop that would look like a legitimate refusal. | `UNKNOWN_FACILITY` `ValueError` at the top of `optimise` | `test_adversarial_data.py::test_09_unknown_facility_raises_a_named_error`, `::test_09b_unknown_recipient_facility_raises` |
| FR-11.9 | If the solver fails or times out, a deterministic greedy fallback MUST produce a worse but explainable answer rather than freezing. | `optimize.greedy_fallback` (status `GREEDY_FALLBACK`) | `none` |
| FR-11.10 | The system MUST be able to run the naive (ledger-trusting) planner on the identical solver with identical constraints, and report the difference in **phantom units** — units that would have been moved and do not physically exist. | `optimize.naive_optimise`, `optimize.counterfactual` | `test_api.py::test_counterfactual_runs_both_planners` |
| FR-11.11 | Malformed decision requests MUST be 422: empty needs, negative shortfall, unknown facility, unknown district, duplicate (facility, resource) pairs, missing role header. | `DecisionRequest` validators + explicit checks in `create_decision` | `test_api.py::test_decision_validation_rejects_malformed_input` |
| FR-11.12 | Every decision MUST be written to the ledger as `DECISION_MADE`, including refusals. | `create_decision` appends before returning | `test_api.py::test_blocked_decision_returns_200_with_allowed_false` (asserts the event) |

**Measured.** District-wide across 10 SKUs, the gate blocks **151** candidate sources and prevents approximately **1,600 phantom units** from being moved (`artifacts/demo_scenario.json → district_wide_counterfactual`).

**Rationale for FR-11.5.** An error status gets retried, then swallowed by a client library, and the operator sees a spinner instead of a refusal. A typed decision gets rendered. 4xx here means "your request was malformed"; it never means "the answer was no".

---

## FR-12 Break-glass override

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-12.1 | An override MUST be available on any blocked decision. A gate that cannot be overridden gets uninstalled in week two. | `api/main.py::override_decision`; `Decision.override.eligible` | `test_api.py::test_override_creates_obligation_event` |
| FR-12.2 | Override MUST require role `medical_officer`. | `require_role("medical_officer")` and `OverrideIn.actor_role: Literal["medical_officer"]` | `test_api.py::test_override_rbac_and_closed_reason_list` |
| FR-12.3 | The reason MUST come from a **closed list** of five codes. Free text MUST NOT be accepted as the reason. A free-text justification of ≥10 characters is required *in addition*. | `OVERRIDE_REASONS`, `OverrideIn._closed_list`, `OverrideIn.justification` | `test_api.py::test_override_rbac_and_closed_reason_list` |
| FR-12.4 | Overriding an already-allowed decision MUST be 409 `NOTHING_TO_OVERRIDE`. | `override_decision` | `test_api.py::test_override_rbac_and_closed_reason_list` |
| FR-12.5 | Every override MUST create a **verification obligation** with a deadline (≤ 86,400 s) for each blocked target, written to the ledger as `OBLIGATION_CREATED`, and MUST record the escalation path. | `override_decision` obligation loop; `MAX_OBLIGATION_S` | `test_api.py::test_override_creates_obligation_event` |
| FR-12.6 | The override MUST be recorded as `OVERRIDDEN` with actor, role, reason code, reason text, justification, the gate reasons it overrode, and an expiry. | same handler | `test_gate.py::test_10_break_glass_override_is_an_event_with_an_actor_and_a_reason` |

**Rationale.** A closed reason list is the auditable part: "emergency" written 400 times in free text is indistinguishable from a habit. An override is not a bypass — it is a recorded, time-boxed debt with a name attached.

**Limitation.** Nothing enforces the deadline. No scheduler chases an expired obligation; the escalation text describes a process that does not run.

---

## FR-13 Audit timeline and ledger integrity

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-13.1 | The store MUST be append-only. No UPDATE, no DELETE. A correction is a new event. | `store.EventStore` exposes only `append` / `put_*` | structurally enforced; `none` asserts the absence of a delete path |
| FR-13.2 | Each event MUST seal the previous event's hash, so a silent rewrite of history is detectable. | `schema.StateEvent.seal`, `store.GENESIS` | `test_gate.py::test_08_mutating_a_payload_breaks_the_chain_at_that_offset` |
| FR-13.3 | `verify_chain()` MUST recompute every hash and return the first bad offset. | `store.EventStore.verify_chain` | `test_gate.py::test_08_...`, `::test_08b_chain_detects_a_deleted_event`, `test_api.py::test_chain_integrity_detects_tampering` |
| FR-13.4 | `/health` MUST report chain integrity and MUST return 503 when the chain is broken or data is unloadable. | `api/main.py::health` | `test_api.py::test_health_reports_chain_integrity` |
| FR-13.5 | The system MUST expose everything that happened to one resource, in order, with hashes — the audit-trail source and the CAG export. | `store.EventStore.timeline`, `GET /audit/{facility_id}/{resource_key}` | `test_api.py::test_audit_returns_full_timeline` |
| FR-13.6 | The event log MUST be tailable by offset, as the integration surface for downstream systems, and MUST carry `prev_hash` and `hash` per event. | `GET /events` with `since_offset`, `limit`, filters | `test_api.py::test_events_endpoint_paginates_and_carries_hashes` |
| FR-13.7 | The system MUST state, in its own responses, that it is tamper-**evident**, not tamper-proof. | `/health` `chain.note` | `test_api.py::test_health_reports_chain_integrity` |

---

## FR-14 GFR-22 certificate rendering

| ID | Requirement | Implementation | Test |
|---|---|---|---|
| FR-14.1 | The system MUST refuse to emit a GFR-22 line for any resource whose projected state is not `VERIFIED`. | `pipeline.py` scene 2: `certifiable = a_state.state == VerificationState.VERIFIED` | `none` |
| FR-14.2 | A refusal MUST be written to the ledger as a `REJECTED` event carrying the refusal reasons, and MUST name why in text. | `store.append(EventType.REJECTED, ..., {"certificate": "GFR-22 line item", "issued": False, "refusal_reason": a_state.reasons})` | `none` |
| FR-14.3 | A self-reported vendor uptime above the SLA MUST NOT be treated as evidence of function; it MUST raise `UPTIME_SELF_REPORTED`. | `Registry._build_equipment` emits the code when `vendor_reported_uptime > sla_target`; `schema.REASONS["UPTIME_SELF_REPORTED"]` | `none` |
| FR-14.4 | The verification cadence for fixed assets MUST be at least as frequent as GFR 2017 Rule 213(1) requires. | `POLICY["equipment"]["max_attestation_age_days"] = 180` — twice the policy-based annual frequency, as a product choice | `none` |

**Measured (demo run).** Asset `FAC000-VENT-0` (ICU Ventilator): register says `FUNCTIONAL`, vendor-reported uptime 97.5% against a 95% SLA, physical verification finds `NOT_COMMISSIONED` → state `REJECTED`, `gfr22_certificate_issued: false`, refusal recorded.

**Limitation — stated plainly.** There is **no rendered GFR-22 document**. What exists is the refusal decision and the ledger event. Producing the policy-based form itself is on the roadmap (`01_PRD.md` §11).

---

## Coverage summary

| Area | Requirements | Covered by a test |
|---|---|---|
| FR-1 Claim ingestion | 6 | 3 |
| FR-2 Detection (incl. leakage guards) | 24 | 17 |
| FR-3 Prioritisation | 6 | 1 (via leakage battery) |
| FR-4 Evidence capture | 6 | 4 |
| FR-5 Transcription | 6 | 3 |
| FR-6 Reconciliation | 6 | 3 |
| FR-7 Attestation | 10 | 8 |
| FR-8 Projection | 4 | 4 |
| FR-9 Forecasting | 6 | 0 |
| FR-10 Early warning | 3 | 0 |
| FR-11 Gate + optimisation | 12 | 9 |
| FR-12 Break-glass | 6 | 4 |
| FR-13 Audit + integrity | 7 | 6 |
| FR-14 GFR-22 | 4 | 0 |

The uncovered areas are forecasting, early warning and certificate rendering. That ordering is deliberate: the gate, the custodian rule and the hash chain are the requirements whose failure would be silent, so they are tested hardest.
