# 14 — Limitations

What this prototype does **not** prove. Read this before reading any number
produced by this repository.

---

## 1. The three sentences that matter most

**There is no real data.** Every row is fabricated by `tathyon/generator.py` from
a seeded RNG. The failure *taxonomy* and its rough incidence are anchored to
published CAG audit paragraphs; the records are not. Any accuracy figure this
system produces is a measurement of how well our detectors find errors our own
generator injected.

**Test coverage is real.** The test suite runs **384 tests** covering the verification gate, trust features, CP-SAT single network solve, anti-leakage guards, adversarial/corrupt-input handling, byte-identical evaluation determinism, and the slim API transport surface.

**Nothing has been deployed.** No pilot, no government partnership, no paying
customer, no user other than us, no facility has ever used this.

---

## 2. Data limitations

- **Scale is a toy.** 20 facilities, 10 SKUs, 240 days, 120 assets, 48,000 ledger
  rows. Claims about national-scale behaviour are architectural arguments, not
  measurements.
- **Geography is fake.** Facilities are points on a 100×100 unit square. Travel
  time is Euclidean distance × 1.35 ÷ 38 km/h. There is no road network, no
  terrain, no seasonal access problem, no ferry, no monsoon-cut road — which in
  practice is exactly what governs redistribution feasibility in the districts
  this is meant to serve.
- **Several anchor rates are our assumptions, not published figures.** The CAG
  paragraphs frequently give a *count* or a *value*, not an incidence. Where we
  needed a rate and the source did not give one, we chose it: `retroactive_entry`
  0.045, `absurd_unit_value` 0.004, `issued_never_received` 0.030,
  `expired_counted_live` 0.055, `not_present` 0.06. These are documented as
  assumptions in `07_DATA_PROVENANCE.md` and they materially determine every
  detection metric.
- **One equipment anchor is over-generalised.** The CAG West Bengal figure — 43 of
  75 PSA oxygen plants not commissioned or not functional — is about **PSA plants
  specifically** and is a **combined** figure. We split it 24% / 19% and applied it
  to ventilators, X-ray units, refrigerators, autoclaves and dialysis machines
  alike. The source does not support that.
- **`fefo_violation` is now injected, but only after two failures worth naming.**
  It appeared in `CAG_ANCHORS` for several revisions while the day loop contained
  no FEFO pathway at all — and could not have contained one, because each series
  held a single batch and FEFO cannot be violated with one batch. Series now carry
  a second, later-expiring batch and issues are drawn from the wrong one at the
  anchored rate. The first working implementation then gated it behind a
  series-level flag while the CAG figure is per *issue instance*, compounding two
  probabilities into a thirty-fold undershoot (0.1% against a 3.6% anchor). It now
  reproduces at **3.47%**. Two separate errors on one anchor is a fair measure of
  how easily a documented incidence diverges from an injected one.
- **The observed table carries truth columns.** `true_stock`, `true_usable` and
  `true_demand` survive the merge onto `observed`. Leakage is currently prevented
  only by `detect.FEATURES` being an explicit allow-list — by code review, not by
  the named test.
- **Synthetic data validates a pipeline, not a hypothesis.** Everything in this
  repository is consistent with the world being exactly as our generator models
  it, and consistent with the world being nothing like that.

---

## 3. What the models do not establish

- **The forecast evaluation is partly self-fulfilling.** Demand is generated as
  Bernoulli occurrence × Poisson size. TSB is close to the correct model family
  for that process. A good MASE against a naive baseline on this corpus is weak
  evidence about real PHC series, which are non-stationary, programme-driven, and
  affected by supply as much as by demand. The *protocol* (temporal split plus
  disjoint facility holdout, MASE and pinball, no MAPE) is honest. The *corpus*
  is not evidence.
- **The tier-2 weak-signal ensemble is uncalibrated.** The design says weak
  signals are calibrated against tier-1 free labels so the output is a probability
  rather than a vibe. That calibration is not implemented. What ships is eight
  hand-chosen features with hand-chosen thresholds and a list of reason codes.
- **The equipment hazard model has never seen data.** Its four coefficients
  (−3.4, 0.011, 0.55, 0.020) are hand-seeded to produce dysfunction rates in
  BEMMP's published 13–34% band. They are a prior, not estimates. No survival
  metric — time-dependent AUC, Brier score, calibration — has been computed,
  because the longitudinal failure data required does not exist here.
- **The freshness posterior has never been calibration-checked.** `q_alpha` claims
  to be a quantity we have at least, with probability 1−α. Nobody has verified
  that the empirical coverage matches. Doing so on synthetic data would be easy
  and has not been done.
- **The hierarchical prior does not exist.** `ConsumptionPrior.fit()` does method
  of moments on whatever array is passed. The pooling over SKU × facility-tier ×
  district that makes cold start work "by construction" is a docstring.
- **The posterior models consumption only.** It does not model theft, breakage,
  unrecorded transfer out, or expiry occurring during the gap — each of which
  reduces usable stock and none of which is Poisson consumption. `q_alpha` is
  therefore optimistic in exactly the facilities where it matters most.

### Operational Meaning of the Trust Scorer's PR-AUC
- **What 0.918 PR-AUC means operationally**: Against a synthetic base anomaly rate of 2.3%, a PR-AUC of 0.918 means that when budget is constrained to 20 visits per month, the top of the queue achieves ~88–92% precision. Operationally, this represents a **9.3× lift over random inspection**.
- **What it does NOT mean**: It does NOT mean 92% of all errors across the entire district are eliminated. Low-consequence errors (e.g. non-essential SKUs or facilities with 6 months of buffer stock) are deliberately deprioritized by the knapsack value function. Furthermore, this metric is evaluated on synthetic generative noise; real-world behavioral falsification is far more subtle and will yield lower lift.

### What the Evaluation Does NOT Prove
- **Compliance and Workload Feasibility**: The evaluation assumes that scheduled verification visits are executed on time. It does not prove that overburdened rural medical staff will actually conduct physical counts within the allocated time windows.
- **Unmodelled Environmental Shocks**: Real-world transit disruptions (monsoon landslides, washed-out bridges, unpaved tribal roads) are simplified in the simulator.
- **Collusive Falsification**: If both the facility storekeeper and the visiting attester collude to record a fabricated figure, no algorithmic detector in this harness can distinguish it from genuine stock.
- **Generalizability Outside the Generator**: The benchmark numbers prove mathematical correctness of the pipeline and single-solve optimization under the synthetic CAG fault taxonomy; they do not prove identical performance on real state ledgers.

---

## 4. What the evidence pipeline does not establish

Four anti-fraud mechanisms are designed, carried in the data model, and **not
implemented**:

- `Evidence.frame_count` records a three-frame burst intended to defeat
  photo-of-a-screen. No code examines inter-frame differences, parallax, moiré, or
  refresh banding.
- `Evidence.perceptual_hash` is stored for reuse detection. No comparison code
  exists and no threshold is specified. The threshold problem is real and
  unresolved: a shelf photographed monthly with nothing changed *should* look
  nearly identical, so a threshold tight enough to catch reuse will flag honest
  repeat captures.
- `Evidence.nonce` and `nonce_issued_at` are stored. Nothing issues, expires, or
  verifies a nonce in a frame.
- `Attestation.signature` is stored. Nothing verifies a signature anywhere in the
  repository.

And one mechanism is not merely unimplemented but **structurally undetectable**:

- **Attesting without looking.** An attester who waits past the 20-second
  rubber-stamp floor, photographs the real shelf with the real nonce, and
  transcribes the ledger figure without counting produces evidence indistinguishable
  from a correct attestation in every signal Tathyon captures. No timing feature and
  no image feature separates them. The only countermeasure is out of band — random
  independent back-checks — and **no back-check sampler is implemented.**

Signatures are **software keypairs, not hardware-attested.** That is not laziness;
hardware-backed key attestation is not reliably available on the low-cost Android
devices actually used in PHCs. The consequence is that a signature proves that
*something holding that key* signed, and a shared or rooted handset breaks the
binding to a person. There is no enrolment, rotation, or revocation.

The custodian/attester separation — the strongest control in the system, enforced
at write time — depends on a **client-asserted boolean**. Nothing cross-references
the attester against an authoritative posting register. And it is defeated by two
colluding people in the same room, which is the ordinary situation in a
two-person PHC.

---

## 5. Integrity limitations

The event store is **tamper-evident, not tamper-proof**, and the distinction is
load-bearing. An attacker with write access to the store can alter a past event
and recompute the whole chain forward, presenting a perfectly valid chain. A local
hash chain proves internal consistency, not history. Closing that requires an
external anchor — a periodic root hash published somewhere the operator does not
control — plus an independent verifier. Neither exists.

Also absent: durable append-on-write (the log is a Python list in memory),
a load-and-verify path (`save()` writes JSONL; nothing reads it back), and any
signature over the persisted file.

---

## 6. Engineering & Architecture Limitations

- **Prototype Auth is NOT Production Auth**: The `X-Role: medical_officer` HTTP header accepted by `api/main.py` is a demonstration contract designed to exercise state machine authorization transitions (`HTTP 403` vs `200`). In a production government deployment, header-based role claims are completely insecure; production requires PKI mutual TLS (mTLS), sovereign OpenID Connect / OAuth2 tokens issued by national identity providers (e.g. India Stack / ABDM / e-Pramaan), and cryptographic signature validation.
- **Client-Side Verification**: The web interface in `web/index.html` is a zero-dependency static operational workspace. It connects to the local FastAPI transport, but does not provide multi-tenant session management or air-gapped field sync.
- **No mobile native client.** The field evidence-capture flow exists as HTTP contracts and automated test fixtures; physical field deployments require an offline-capable Android APK with camera sensor binding and SQLite sync.
- **No production database migrations.** The SQLite / in-memory store is designed for single-process district sidecars. High-availability cluster replication and schema migrations are unbuilt.
- **Break-glass enforcement is local.** Overrides create immutable hash-chained events with 72-hour obligations in the event log, but alerting upstream statutory bodies depends on external monitoring adapters.

---

## 7. Product and evidence limitations

- **No pilot.** No facility has run this. Every operational assumption — that an
  attester can be routed to a peer facility, that 20 seconds is the right
  rubber-stamp floor, that a 30-day medicine attestation window is achievable
  against CAG's finding of 38–90% shortfalls in monthly verification — is untested
  against a real roster and a real workload.
- **No government partnership and no procurement path.** We do not know whether a
  state health department would accept a system that blocks a transfer, who owns
  the override decision, or how the obligation it creates would be discharged.
- **No paying customer, no pricing, no unit economics.** The scarce resource this
  system rations is *human verification minutes*, and we have never measured what
  one costs.
- **No user research.** We have not watched a storekeeper, a facility in-charge, or
  a block supervisor attempt an attestation. The 20-second floor is a guess about
  a human behaviour we have not observed.
- **No impact measurement of any kind.** `10_IMPACT_METHODOLOGY.md` describes how
  impact *would* be measured and defines verification yield as the first metric.
  That metric has never been computed on real data because computing it requires
  people physically checking shelves.
- **The counterfactual is internally generated.** `phantom_units_naive_would_move`
  is a real and well-constructed comparison — same solver, same constraints, one
  line different — but the phantom units are phantom because our generator made
  them so. It demonstrates that the gate does what it says. It does not
  demonstrate how much stock is phantom in the world.

---

## 8. Things that are genuinely established

To be fair to the reader, the list of what this repository *does* show:

- The verification gate works as specified: unverified stock is invisible to the
  solver, and the decision returned is a typed `BLOCKED_VERIFICATION_REQUIRED`
  with blocked sources, reason codes and a remedy, not an exception.
- The event store is append-only, hash-chained, idempotent, and replayable, and
  `verify_chain()` detects modification.
- Custodian self-attestation is rejected at write time, not warned about in a
  report.
- `VerifiedState` is never persisted, so computed values cannot drift from the log
  and a policy change requires no backfill.
- The freshness model is mathematically correct: Gamma-Poisson gives a Negative
  Binomial predictive in closed form, and quantiles of that — unlike the
  subtract-a-p95 approach it replaced — compose correctly across time.
- The forecasting evaluation protocol is honest: temporal plus disjoint-facility
  holdout, MASE and pinball, MAPE explicitly excluded, three baselines.
- Synthetic data generation keeps truth and observation separate, derives labels
  by comparison, and never emits the injected-pathology flags as columns.

That is a coherent verification primitive with a defensible statistical core. It
is not a product, it is not evidence about Indian public health supply chains, and
it has never met a user.

---

## Addendum — findings from the final build pass

These are recorded because a limitations document that only lists known unknowns
is less useful than one that admits what changed under scrutiny.

**A selection rule was deleted after its evidence evaporated.** `select_model()`
originally chose forecasters on the Syntetos–Boylan ADI > 1.32 heuristic,
justified by a stratified evaluation on an earlier corpus. After a generator bug
was fixed (expiry dates had been drawn almost entirely outside the simulation
window), that result did not reproduce — TSB now loses to the naive mean at every
stratum. The rule was removed and replaced with per-series backtesting. The
lesson generalises: any metric in this repository is one generator fix away from
changing, which is why nothing here should be quoted without re-running `make all`.

**Two declared CAG anchors were structurally unreachable.** `expired_counted_live`
could not fire because of the expiry-date bug above; `fefo_violation` could not
fire at all because each series had a single batch, and FEFO cannot be violated
with one batch. Both were documented as reproduced incidences before they were
actually implemented. They are implemented now (FEFO reproduces at 3.47% against
a 3.6% anchor), but the episode is the clearest illustration in this project of
the difference between a documented claim and a verified one.

**Simulated cross-border model sharing was removed, and it never helped the
smallest silo reliably.** In its last run, FedAvg beat local-only training in
3 of 5 synthetic silos, but RU (n=3,600) went 0.816 → 0.797; an earlier run
showed the opposite. The benefit was unstable across seeds, and the module
changed no district decision, so it was deleted. What could ever cross a border
is specified, without code, in `docs/federation-interface.md`.

**Signatures are software keypairs.** Hardware-backed attestation is unavailable
on the low-cost Android devices actually present in PHCs, so the trust root this
design assumes does not exist on the hardware its users have. This is named in
`docs/09_SECURITY.md` and is not solved.

**The dominant fraud is undetectable here.** A real photograph of a real shelf
with a number copied from the register defeats every control in this system.
Only random independent back-check by a different person addresses it, and that
is a process, not code.
