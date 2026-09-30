# 16 — Red-Team Report

Hostile-judge Q&A, answered from the running code rather than the pitch. Every
figure quoted here comes from `make all` on this repository and re-derives on
every run; the exact JSON is in `artifacts/`.

The rules for this document: **name the weakness before the strength**, cite
either code or `docs/14_LIMITATIONS.md` for every claim, and use the same
verdict labels the directive requires — IMPLEMENTED, PARTIAL, SIMULATED,
HYPOTHESIS, NOT IMPLEMENTED.

---

## 1. Is the problem real?

**IMPLEMENTED / PUBLIC EVIDENCE.** The failure taxonomy is anchored to
paragraph-level CAG findings — Punjab Rpt 4/2019 (952/13,322 duplicate batches,
FEFO breach rate 3.6%, 947-day entry lag, 38–90% skipped monthly verification),
Maharashtra Rpt 4 (₹11,10,111 unit value from missing input validation),
Telangana Rpt 4/2024 (₹390.26 cr expired in stock), West Bengal Rpt 3/2024
(43/75 PSA plants not commissioned or non-functional), NHSRC BEMMP (7.56 lakh
items, ₹4,564 cr, 13–34% dysfunctional). These are the actual paragraphs, not
paraphrases. See `docs/07_DATA_PROVENANCE.md`.

The problem exists at the population level the audits describe. What is
**not** proven is that our synthetic corpus matches the covariance structure
of real Indian PHC ledgers. That is stated in `docs/14_LIMITATIONS.md §2`.

## 2. Is the data honest?

**SYNTHETIC.** Every row is fabricated by `tathyon/generator.py` from a seeded
RNG. The UI carries an undismissible SYNTHETIC banner, every API payload
carries a `provenance: "SYNTHETIC"` field, and every ML metric report begins
with `PROVENANCE: SYNTHETIC DATA -- TECHNICAL VALIDATION ONLY. These numbers
are NOT evidence of real-world performance.` The one place the simulation
stands in for a human is when the pipeline reads `truth.parquet` to attest —
that is intentional and documented in `tathyon/pipeline.py`.

## 3. Is the AI useful?

**PARTIAL.** `tathyon/gemini.py` implements a bounded adapter: transcribes
what a human wrote on a register, abstains on any field below a confidence
threshold (`MIN_FIELD_CONFIDENCE = 0.70`), and never returns a count. If the
real Gemini path is unreachable it falls back to a deterministic mock, clearly
labelled. Vision-language object-counting numbers (0.23–0.58) are cited as the
reason the model is not permitted to count. Live-API tests are HYPOTHESIS.

## 4. Is the ML valid?

**PARTIAL.** Weak supervision over tier-1 hard violations (arithmetic,
FEFO, expiry, absurd unit value) is real and produces free labels; the tier-2
model is calibrated with `CalibratedClassifierCV(method="isotonic")` and
evaluated with temporal + facility holdouts. Results are modest: PR-AUC 0.056
vs base rate 0.025 (2.2× baseline), 61.7% fewer verifications than random to
reach 50% recall. Neither PR-AUC nor MAPE hide anything — MAPE is not
computed anywhere in the codebase, deliberately. `docs/14_LIMITATIONS.md §3`
lists what has NOT been validated: hierarchical prior, hazard model
calibration, freshness posterior coverage-check.

## 5. Is the optimization real?

**IMPLEMENTED.** `tathyon/optimize.py` uses OR-Tools CP-SAT with an explicit
donor-retention cap (≤ 40% of Q_α above the safety floor), travel-time window,
and unmet-need penalty. The greedy fallback is deterministic and named. The
verification gate runs *before* the solver, not inside it — a source with
`verified_state != VERIFIED` is not exposed to the model at all, which is why
the refusal is correct by construction rather than by a threshold anyone can
tune away. `tests/test_gate.py` covers ten cases.

## 6. Is the verification real?

**IMPLEMENTED at the event level, HYPOTHESIS at the human level.** The
software half is real: nonces are stored per evidence artifact, attestations
are hardware-independent JWS-shaped payloads, `EventStore.put_attestation`
raises `ATTESTOR_IS_CUSTODIAN` for separation-of-duties violations, the log
hash-chains and replays deterministically. What is HYPOTHESIS: that any of
this stops attesting-without-looking. `docs/14_LIMITATIONS.md §4` says so
explicitly, and calls out that the countermeasure is random independent
back-check — a process, not code.

## 7. Is the offline workflow real?

**PARTIAL.** Idempotency via `client_event_id` is implemented and tested end
to end (a duplicated evidence submission produces exactly one ledger event —
covered by `tests/test_ui_and_persistence.py::test_sqlite_persistence_survives_restart`).
The 3-frame capture, perceptual hash, and nonce comparison are stored in the
data model but not enforced — `docs/14_LIMITATIONS.md §4` lists them as
"designed, not implemented".

## 8. Is the system secure?

**PROTOTYPE-GRADE.** The `X-Role` header is self-asserted with no signature,
no session, no identity provider — stated at every response and repeated in
the API docstring. This is not deployable as-is. `docs/09_SECURITY.md` carries
the threat model. The one control that IS real and worth naming: separation
of duties is enforced at write time, not by a policy document.

## 9. Can it scale?

**HYPOTHESIS.** The event store is append-only with WAL and per-facility
indexes; a single container comfortably handles the 20-facility corpus. The
architecture doc (`04_SDA.md`) argues that the real ceiling is human
verification capacity and per-state integration headcount, not compute — but
that argument is *not* validated at scale here. No load test exists.

## 10. Is it different from HealthGrid?

**IMPLEMENTED.** The delta lives in the code, not the pitch:

- HealthGrid computes a facility risk score from a ledger it does not question.
  Tathyon runs the same optimizer twice — once naively, once against verified
  state — and returns `BLOCKED_VERIFICATION_REQUIRED` when the source is
  untrusted (`tathyon/optimize.py::optimise`, `naive_optimise`, `counterfactual`).
- HealthGrid's audit trail is a Firestore events collection. Tathyon's is
  hash-chained, replay-verified in tests, and refuses to emit a policy-based
  GFR-22 line when any input attestation is missing (`pipeline.py`, seen in
  the seeded demo).
- HealthGrid trusts what the LLM says. Tathyon's Gemini adapter is forbidden
  from counting and abstains on low confidence.

The comparison table is in `docs/11_COMPETITIVE_ANALYSIS.md`.

## 11. Is the demo reproducible?

**IMPLEMENTED.** `tathyon/seed_demo.py` pins every scene number (facility, SKU,
reported=340, usable=40, unusable=300, asset, uptime=98.2%). `make seed`
regenerates the exact hero state; `make docker` builds and runs a container
whose entrypoint seeds on first boot. Two of the new tests
(`test_seed_demo_produces_the_pinned_hero_numbers`,
`test_seeded_hero_notifications_contain_the_refused_certificate`) fail if any
of those numbers drift.

## 12. Can the system fail safely?

**IMPLEMENTED.** A blocked decision returns HTTP 200 with `allowed:false` and
typed reasons; errors are never retried into silent successes. A malformed
attestation returns 422 with a machine-readable `reason_code` the SPA
renders. A non-numeric quantity that would previously have crashed the
detector now raises `v_nonfinite_qty` (fixed in this session; see
`test_adversarial_data.py`). Break-glass override creates a verification
obligation with a deadline rather than silently permitting an action.

## 13. Can government realistically integrate it?

**HYPOTHESIS.** The API surface is FHIR-shaped for the resource types it
covers, and the docs argue for adoption paths — but no state has integrated
this, no e-Aushadhi adapter has run against a real instance, no CERT-In audit
has been done. `docs/06_AD.md` names the gaps.

## 14. Would someone pay?

**HYPOTHESIS.** `docs/archive/15_STARTUP_STRATEGY.md` argues the wedge is
independent verification of physical-asset claims that money is paid against,
and identifies the single most important business risk: if verification
labour grows linearly with revenue this is a services business (~2× revenue
multiple), not software. There is no revenue.

## 15. What is proven?

- The verification gate refuses unverified transfers, deterministically
  (`test_gate.py`).
- The event store is append-only, idempotent, hash-chained, and durable
  (`test_ui_and_persistence.py`).
- The custodian/attester separation is enforced at write time.
- The forecast module reports MASE stratified by ADI; TSB loses to naive at
  every stratum on this corpus, and we say so.
- FedAvg beats local-only training in 3 of 5 simulated national silos this
  run (not stable across seeds — reported honestly).
- The seeded hero scenario is bit-for-bit reproducible and its
  notification-engine output is asserted by test.

## 16. What is synthetic?

Every row of data. Every ledger figure. Every model metric. The banner in
the SPA says so, the API says so on every payload, and every reader of
`docs/14_LIMITATIONS.md §1` sees so within three sentences.

## 17. What is only a hypothesis?

- That anyone will do the verification at the rate the design assumes.
- That attestation without looking can be caught by anything other than
  random independent back-check.
- That the model metrics transfer to real-world ledgers.
- That state health corporations will pay for independent verification of
  a BEMMP-style SLA.
- That the FedAvg result stabilises across seeds; the current run has one
  silo where it regressed.

---

## Highest-impact fixes made this pass

1. **Notification engine, event-driven.** No timer, no random, cites the
   source event offsets. Fires on the seeded refused GFR-22 certificate.
2. **Deterministic hero seed.** `tathyon/seed_demo.py` pins the 340/40/300
   figures, the equipment scene, the AS_OF timestamp, and the nonces. Two
   assertions in the test suite protect it.
3. **Durable persistence.** `tathyon/persist.py` mirrors every write to
   SQLite with WAL; API restarts preserve the chain. Idempotency also
   survives restart, verified by test.
4. **Dockerfile.** `docker build -t tathyon . && docker run -p 8000:8000
   tathyon` produces a container whose entrypoint regenerates data and seeds
   the hero scene on first boot — the "runs from a clean environment"
   requirement, literally.
5. **Live SPA at `/ui/`.** No injected JSON. Every card is populated by a
   real HTTP call to the same FastAPI server. `test_ui_index_is_served`
   fails if the SPA stops calling the two endpoints it depends on.

## Highest remaining risks

1. The trust model is weak (PR-AUC 0.056). It beats the deterministic
   baseline and it beats random, but it is nowhere near the numbers a tuned
   model on friendly data would show.
2. Nonce and perceptual-hash verification are stored but not enforced.
3. No real deployment, no pilot, no revenue.
4. No load test against realistic national-scale volumes.
