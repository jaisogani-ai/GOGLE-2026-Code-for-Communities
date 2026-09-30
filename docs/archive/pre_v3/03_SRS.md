# 03 — Software Requirements Specification

Scope: the software as built in this repository — the `tathyon` package, the FastAPI service in `api/main.py`, and the test suite. **All data is SYNTHETIC.** Prototype-grade authentication; not deployable as-is.

---

## 1. System context

Tathyon is a **sidecar**. The system of record (e-Aushadhi, DVDMS, an asset register) remains authoritative for transactions. Tathyon consumes claims from it, adds evidence and attestation, and returns gated decisions.

| Element | Reality in this repository |
|---|---|
| Ingest | `pandas.read_parquet` over `data/facilities.parquet`, `data/observed.parquet`, `data/equipment.parquet`, produced by `tathyon.generator`. No live connector exists. |
| Core | `tathyon/` — pure Python, in-process |
| Store | `store.EventStore` — an in-memory Python list with a write-only JSONL `save()`. **No load path, no database.** |
| API | FastAPI, single process, module-level singleton `Registry` |
| Client | None. `web/workspace.html` is a static page built from `artifacts/demo_scenario.json` by `build_web.py`. |
| Solver | OR-Tools CP-SAT, in-process, 5-second time limit |

---

## 2. Functional requirements (summary)

The full numbered set with per-requirement test citations is `02_FRD.md`. Restated here as system-level capabilities:

| SRS-F | Capability | Primary module |
|---|---|---|
| SRS-F1 | Ingest immutable claims from a system of record | `store.put_claim` |
| SRS-F2 | Detect tier-1 hard violations and tier-2 weak signals, emitting reason codes | `detect` |
| SRS-F3 | Rank verification candidates under a limited human budget | `verify.priority`, `eval.TrustModel` |
| SRS-F4 | Accept offline-captured evidence idempotently, with server nonce and frame count | `api::submit_evidence` |
| SRS-F5 | Accept human attestations, rejecting custodian self-attestation at write time | `store.put_attestation` |
| SRS-F6 | Project a verified state as a pure function of (log, policy, α) | `verify.state_for` |
| SRS-F7 | Forecast intermittent demand and emit explained stockout warnings | `forecast` |
| SRS-F8 | Plan constrained redistribution behind a verification gate | `optimize.optimise` |
| SRS-F9 | Record break-glass overrides as time-boxed obligations | `api::override_decision` |
| SRS-F10 | Expose a hash-chained audit timeline and a tailable event log | `store.timeline`, `GET /events` |
| SRS-F11 | Refuse policy-based certification over unverified lines | `pipeline` scene 2 |

---

## 3. API surface (`api/main.py`)

Read directly from the source. FastAPI app title `Tathyon API`, version `0.1.0`.

### 3.1 Endpoints

| Method | Path | Min role | Success | Notes |
|---|---|---|---|---|
| `GET` | `/health` | none | 200 / **503** | 503 when data failed to load **or** the hash chain is broken. Returns file presence, facility and series counts, chain status with `first_bad_offset`, `policy_version`, and `RBAC_NOTE`. |
| `GET` | `/resources/{facility_id}/{resource_key}/state` | none | 200 | Query: `alpha` (0,1) default 0.60. Returns the `VerifiedState` dict plus `reason_text`, `alpha` and the applicable policy window. 404 `RESOURCE_NOT_FOUND`; 422 on α out of range. |
| `POST` | `/decisions` | `field_operator` | **200 always** (well-formed) | Runs the gate, then CP-SAT. Returns a typed `Decision` plus `decision_id`, a `gate` block (`sources_considered` / `sources_eligible` / `sources_blocked` / `alpha`), `requested_by`, and `event_offset`. Appends `DECISION_MADE`. 422 for malformed input only. |
| `GET` | `/decisions/{decision_id}` | none | 200 | Returns the stored decision plus its overrides. 404 `DECISION_NOT_FOUND`. |
| `POST` | `/evidence` | `field_operator` | 200 | Idempotent on `client_event_id`. Returns `duplicate`, `event_offset`, `events_before`/`events_after`, and warnings (`SINGLE_FRAME_CAPTURE` when `frame_count < 3`). Provenance `REAL_USER_PROVIDED`. 404 for an unknown resource. |
| `POST` | `/attestations` | `facility_incharge` | 200 | 422 `ATTESTOR_IS_CUSTODIAN` when `is_custodian=true`; 422 `OBSERVATION_SHAPE` on a wrong-shaped observation. Returns `resulting_state` — the recomputed projection — and a `RUBBER_STAMP_SUSPECTED` warning below 20 s. |
| `POST` | `/decisions/{decision_id}/override` | `medical_officer` | 200 | 404 unknown decision; **409 `NOTHING_TO_OVERRIDE`** if the decision was allowed. Writes `OVERRIDDEN` + `OBLIGATION_CREATED` per blocked target. |
| `GET` | `/events` | none | 200 | Query: `since_offset` ≥ 0, `limit` 1–2000 (default 200), `facility_id`, `event_type`. Returns rows with `prev_hash` and `hash`, `next_offset`, `has_more`, `total_events` and chain status. 422 on an unknown `event_type`. |
| `GET` | `/audit/{facility_id}/{resource_key}` | none | 200 | Current projection + reason text + full timeline + chain status. 404 unknown resource. |
| `GET` | `/counterfactual` | none | 200 | Query: `district`, `resource_key` (default `AMX250`), `alpha`, `lead_time_days` (default 14). Runs naive and gated planners on the same solver and returns both plus `phantom_units_naive_would_move` and a written `interpretation`. 422 unknown district; 404 when no facility in the district holds the SKU. |
| `GET` | `/reasons` | none | 200 | The full reason-code dictionary, override reason codes, the role table and `RBAC_NOTE`. The vocabulary, published. |

### 3.2 Pydantic request models

All request models set `model_config = {"extra": "forbid"}` — an unexpected field is a 422, never silently ignored.

**`NeedIn`** — `facility_id` (1–64), `resource_key` (1–64), `shortfall` (>0, ≤1e6), `days_to_stockout` (>0, ≤3650), `criticality` (>0, ≤10, default 1.0).

**`DecisionRequest`** — `district` (optional, ≤32), `needs` (1–200 `NeedIn`, validator `_unique` rejects duplicate `(facility_id, resource_key)` pairs), `alpha` (0,1) default 0.60, `max_travel_hours` (>0, ≤48) default 6.0, `max_donor_fraction` (>0, ≤1.0) default 0.40, `source_facility_ids` (optional list ≤200 — restricts the donor pool, which is how a district officer asks "can these three facilities cover it?" and how the gate path is made reproducible in tests).

**`EvidenceIn`** — `client_event_id` (8–128, the idempotency key), `facility_id`, `resource_key`, `kind` ∈ {`photo_shelf`, `photo_register`, `photo_asset`}, `captured_at` (optional), `device_id`, `nonce` (4–128), `nonce_issued_at` (optional), `frame_count` (1–32), `artifact_hash` (16–128), `perceptual_hash` (4–128), `extraction` (optional dict), `extraction_model`, `extraction_confidence` (0–1), `extraction_abstained` (bool).

**`AttestationIn`** — `client_event_id`, `facility_id`, `resource_key`, `evidence_refs` (≤32), `observed` (dict, validator `_observed_shape`), `observed_at` (optional), `attestor_id`, `attestor_role`, `delegation_id`, **`is_custodian` (bool, required — no default)**, `seconds_spent` (0–86400), `extraction_agreement` (optional).

`_observed_shape` requires a non-empty dict and enforces one of two shapes: equipment must carry `status` ∈ {`FUNCTIONAL`, `NON_FUNCTIONAL`, `NOT_COMMISSIONED`, `NOT_PRESENT`}; a quantity resource must carry `usable_qty` (with optional `present_qty`), both non-negative numbers.

**`OverrideIn`** — `actor`, `actor_role` (`Literal["medical_officer"]`), `reason_code` (validator `_closed_list` against `OVERRIDE_REASONS`), `justification` (10–2000 chars), `duration_s` (300–86400, default 3600).

### 3.3 RBAC — the `X-Role` header

`require_role(minimum)` is a FastAPI dependency factory reading the `X-Role` request header against an ordered rank table:

```
field_operator 1  <  facility_incharge 2  <  district_officer 3  <  medical_officer 4
```

Failure modes, all 403 and all carrying `RBAC_NOTE`: `ROLE_REQUIRED` (header absent), `ROLE_UNKNOWN` (not in the table), `ROLE_INSUFFICIENT` (below the floor, with `required_role` and `actor_role` echoed).

> **SRS-NF-SEC-1 (stated in the source, repeated here).** The `X-Role` header is a **self-asserted claim with no signature, no session, no identity provider and no revocation**. It demonstrates where the authorisation boundaries sit; it does not enforce them against an adversary. demonstration requires a signed assertion bound to a device key, a delegation record, and an attestor keypair held in hardware. **Do not deploy this as-is.** The note is returned in every auth failure and in `/health`, so a caller cannot mistake it for real authentication.

### 3.4 The status-code contract

Three positions, carried up from the core and stated in the module docstring:

1. **A blocked decision is HTTP 200.** `POST /decisions` returns a typed `Decision` with `allowed: false`, machine-readable `reasons`, the `blocked_sources` list and a `remedy`. It never returns 4xx/5xx for a gate refusal. Rationale: an error status gets retried, then swallowed by a client library, and the operator sees a spinner instead of a refusal; a typed decision gets rendered. **4xx means "your request was malformed"; it never means "the answer was no."** Covered by `test_api.py::test_blocked_decision_returns_200_with_allowed_false`.
2. **Separation of duties is a write-time constraint.** `is_custodian=true` is rejected by `EventStore.put_attestation` itself, surfaced as 422 `ATTESTOR_IS_CUSTODIAN` — not accepted with a warning row.
3. **Every payload carries provenance.** `_envelope()` stamps `provenance`, `as_of` and `ledger_offset` on every response. Silently mixing synthetic and real data is the one unrecoverable mistake in this domain.

Error bodies are uniform: `_fail(status, code, message, **extra)` produces `{"error": code, "message": ..., "provenance": "SYNTHETIC", ...}`.

### 3.5 Bootstrap (`Registry`)

A thread-locked singleton that loads the three parquet files once and builds the real event log from them: a `Claim` per `(facility_id, sku)` from the latest ledger row, an `Attestation` for rows the generator marked `physically_verified`, a `Claim` per asset, and an `Attestation` for assets verified within six months. Consumption history over a 28-day window fits a `ConsumptionPrior` per series. `_seconds_spent()` is a deterministic hash-derived value so roughly 15% of seeded attestations fall below the 20-second rubber-stamp floor — deliberately, so some resources project to `CONFLICTED`.

Every timestamp written to the store passes through `_utc_iso()`. This is a documented workaround: the parquet ledger is tz-naive while `schema.now()` is tz-aware, and subtracting one from the other raises. The store therefore only ever holds tz-aware UTC.

`observed_deltas` is deliberately passed as `0.0` to the engine: the posterior already models the whole post-anchor gap as unobserved consumption, and passing the ledger's own post-anchor issues would subtract that consumption twice. The ledger issues are themselves unverified claims, so the posterior over the full gap is the defensible floor.

---

## 4. Data model

### 4.1 Four immutable write objects, one derived read projection

| Object | Mutability | Author | Purpose |
|---|---|---|---|
| `Claim` | `frozen=True` | system of record | what a source asserts |
| `Evidence` | `frozen=True` | the device | a captured artifact plus its extraction |
| `Attestation` | `frozen=True` | a human, not the custodian | a signed physical assertion |
| `StateEvent` | append-only, sealed | the store | the ledger; the only writable table |
| `VerifiedState` | **never stored** | the engine | recomputed per (resource, ledger offset, policy, α) |

Full rationale in `04_SDA.md` §9.

### 4.2 Enumerations

- `ResourceType`: `medicine`, `equipment` — closed set.
- `StateKind`: `quantity_multidim`, `enum_plus_health` — what the freshness engine and the gate actually read. Three kinds, not twenty resource types.
- `VerificationState`: `UNVERIFIED`, `VERIFIED`, `CONFLICTED`, `REJECTED`, `OVERRIDDEN`. **`STALE` is deliberately not a state** — it is a read-time projection of `VERIFIED(as_of)` through the decay model. A stored staleness flag would let a record change state with no event, breaking replayability.
- `Provenance`: `REAL_PUBLIC`, `REAL_USER_PROVIDED`, `SYNTHETIC`, `SIMULATED`, `DERIVED`, `HYBRID`. Never silently mixed; the UI must surface `SYNTHETIC`.
- `EventType`: 14 members, from `claim_ingested` through `obligation_created`.
- `REASONS`: 25 machine-readable reason codes with human-legible text, published at `GET /reasons`.

### 4.3 Event record

`StateEvent(event_id, offset, event_type, facility_id, resource_type, resource_key, payload, occurred_at, recorded_at, actor, prev_hash, hash)`. `seal(prev_hash)` sets `prev_hash` and computes `hash = sha256(body-without-hash)`. Genesis is 64 zeros.

`occurred_at` and `recorded_at` are distinct on purpose: an offline device syncing three days later records when the observation happened and when the server learned of it.

### 4.4 Policy

```python
POLICY = {"version": "v1",
          "medicine":  {"max_attestation_age_days": 30,  "default_alpha": 0.60},
          "equipment": {"max_attestation_age_days": 180, "default_alpha": 0.90},
          "min_attestation_seconds": 20.0}
```

A tenant may tighten; a consumer may tighten further but never loosen below the tenant floor. The equipment window is half the GFR 2017 Rule 213(1) annual cadence, as a product choice.

### 4.5 Storage

**There is none that survives a restart.** `EventStore` holds four in-memory dicts plus a list. `save(path)` writes JSONL; there is no corresponding load. This is the single largest gap between this repository and a deployable system, and it is stated first here and in `14_LIMITATIONS.md`.

---

## 5. Security requirements

Detailed threat model in `09_SECURITY.md`. The specification-level requirements:

| ID | Requirement | Status |
|---|---|---|
| SEC-1 | Separation of duties between custody and verification MUST be enforced at write time | **Implemented** (`put_attestation` raises) |
| SEC-2 | The ledger MUST be tamper-evident: any mutation, deletion or reordering MUST be detectable by replay | **Implemented** (`verify_chain`) |
| SEC-3 | The system MUST state that it is tamper-evident and not tamper-proof, because a full-log rewrite by a party with write access is not detectable from the chain alone | **Implemented** (`/health` `chain.note`) |
| SEC-4 | Evidence MUST be bound to a verification request by a server-issued nonce that appears in frame | **Partially implemented** — the nonce is carried and stored; nothing verifies it is visible in the image |
| SEC-5 | Re-submission of a prior image MUST be detectable | **Not implemented** — `perceptual_hash` is stored; no comparison against facility history runs. Reason code `EVIDENCE_REUSED` exists and is never emitted |
| SEC-6 | Break-glass MUST be role-gated, reason-constrained to a closed list, time-boxed, and MUST create a named obligation | **Implemented** in the API; **the deadline is not enforced** |
| SEC-7 | No patient PII MUST enter the system | **Implemented by construction** — no patient entity exists in the object model |
| SEC-8 | Attestations MUST be signed by a key held by the attester | **Prototype only** — `signature` is `sha256(attestation_id)[:32]` or the literal string `sig:prototype-software-keypair`. **This is not a signature in any meaningful sense.** |
| SEC-9 | Authentication and authorisation MUST bind an action to an identity | **Not implemented** — see §3.3 |
| SEC-10 | Every payload MUST carry provenance so synthetic data cannot be mistaken for real | **Implemented** (`_envelope`) |

---

## 6. Performance requirements

Measured characteristics of what runs; no formal targets have been set or benchmarked.

| ID | Property | Actual |
|---|---|---|
| PERF-1 | Solver bound | CP-SAT `max_time_in_seconds = 5.0`, with a deterministic greedy fallback if the status is neither `OPTIMAL` nor `FEASIBLE` (`greedy_fallback`). The demo never freezes; it degrades to a worse, explainable answer. |
| PERF-2 | Projection cost | `state_for` is O(attestations for that resource) because `attestations_for` scans the full attestation dict and sorts. At corpus scale (20 facilities × 10 SKUs) this is immaterial; **it is linear in total attestations and would need an index at scale.** |
| PERF-3 | Chain verification | O(n) over the whole log, recomputing every hash. `/events` and `/audit` call it on every request. At the demo's 46-event ledger this is free; on a long log it is not, and there is no incremental verification. |
| PERF-4 | Event query | `GET /events` scans the full list from offset 0 and filters in Python. No index on `facility_id` or `event_type`. |
| PERF-5 | Bootstrap | The whole parquet corpus is read once at first request and held in memory (39,000 train + 9,000 test rows in the eval split). |
| PERF-6 | Evaluation run | `make eval` trains a calibrated gradient-boosting model with 3-fold isotonic calibration over 39,000 rows; `make all` (data, eval, demo, web, test) completes on a single machine. |

**The real performance question is not compute.** It is human verification capacity. See `04_SDA.md` §8.

---

## 7. Reliability requirements

| ID | Requirement | Implementation |
|---|---|---|
| REL-1 | Corrupt input MUST NOT crash a batch | Quantities coerced before arithmetic; unparseable values raise `NON_FINITE_QUANTITY` (`detect.hard_violations`) |
| REL-2 | An empty input frame MUST return an empty, correctly-shaped frame | early-return branch declaring `VIOLATION_COLUMNS` with dtypes |
| REL-3 | A replayed offline queue MUST NOT double-append | `client_event_id` dedupe in `EventStore.append`; deterministic ids in the API |
| REL-4 | Solver failure MUST degrade, not fail | `greedy_fallback` |
| REL-5 | An unknown facility MUST fail loud and named, never silently drop — a silent drop would look like a legitimate "no safe source" and hide an integration bug behind a plausible refusal | `UNKNOWN_FACILITY` `ValueError` in `optimise`; 422 in `create_decision` |
| REL-6 | Unloadable data MUST return 503 `DATA_UNAVAILABLE`, not a 500 or an empty success | `reg()` dependency |
| REL-7 | A broken hash chain MUST make `/health` report `degraded` with 503 | `health()` |
| REL-8 | Insufficient evidence MUST fail **closed** | `SourceStock.transferable` returns 0.0 by default; `NO_ATTESTATION` is the default projection |

---

## 8. Constraints

| ID | Constraint |
|---|---|
| CON-1 | Python, single process. `EventStore` is in-memory with no concurrency control beyond the `Registry` construction lock. |
| CON-2 | Distances are Euclidean on a synthetic 100×100 coordinate plane with a 1.35 road factor at 38 km/h — **not a road network**. |
| CON-3 | The solver works in integer units at 0.1 granularity (`SCALE = 10`). |
| CON-4 | Objective coefficients must be reduced to Python ints before multiplying a decision variable: recent OR-Tools exposes `IntAffine`, which has no `__floordiv__`. `(1000 * urgency) // 100 == 10 * urgency` exactly, so the rewrite is value-identical. (This bug made every CP-SAT solve raise `TypeError`.) |
| CON-5 | The transcription adapter is mocked (`gemini-transcription-adapter/mock`). No VLM is called. |
| CON-6 | Resource types are a closed enum; a tenant cannot add one in v1. |
| CON-7 | The corpus is 20 facilities, 10 SKUs, 120 assets, one district. Nothing has been run at scale. |
| CON-8 | Tests run against `TATHYON_DATA_DIR`; the API requires `data/*.parquet` to exist (`make data`). |

---

## 9. Assumptions

| ID | Assumption | If false |
|---|---|---|
| ASM-1 | A person other than the custodian is available to attest at each facility | The custodian rule blocks all verification; a delegation model to peer facilities or block supervisors is required (the remedy text already names this path) |
| ASM-2 | Consumption between physical counts is approximately Poisson with a facility-specific rate | The Negative Binomial posterior is mis-specified and `q_alpha` is wrong in an unknown direction. **This assumption is doing a lot of work and has not been validated against real consumption data.** |
| ASM-3 | Tier-1 hard violations are correlated with material wrongness well enough to serve as weak labels | The trust model's ranking is uninformative. Measured: PR-AUC 0.079 against a 0.053 base rate — the correlation is real but weak |
| ASM-4 | The system of record can be read without writing back to it | A bidirectional integration changes the trust model entirely |
| ASM-5 | An attestation taking longer than 20 seconds reflects a real physical check | A 21-second rubber stamp passes. The threshold is a deterrent, not a proof |
| ASM-6 | A facility's own history is a valid baseline for "unusual" | Facility-relative residualisation is the wrong correction. **Detector D1 fired and residualisation did not fix it** — on this generator, facility genuinely carries signal about wrongness, so the assumption underlying D1 is itself violated. Unresolved |
| ASM-7 | Synthetic incidence rates anchored to CAG audit paragraphs approximate real incidence | Every measured number in this repository moves. They are technical validation only, never evidence of real-world performance |
| ASM-8 | Human verification capacity is the binding constraint, not compute | The scaling analysis in `04_SDA.md` §8 is wrong in its ordering |
