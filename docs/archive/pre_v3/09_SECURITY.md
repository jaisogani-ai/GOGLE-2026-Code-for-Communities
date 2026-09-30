# 09 — Security and Threat Model

Tathyon's product claim is that a record is trustworthy because a named human
physically looked at the thing and signed for it. Every serious attack on the
system is therefore an attack on that claim: making the signature mean less than
it appears to mean.

This document is written adversarially. Where a mitigation is partial, it says
partial. Where an attack is undetectable, it says undetectable.

---

## 1. Assets worth attacking

1. **The attestation.** A signed human assertion that the physical state is X. It
   is what unlocks stock for transfer.
2. **The event log.** The append-only, hash-chained ledger in `store.py`. Whoever
   can rewrite it can rewrite the audit trail.
3. **The gate decision.** `optimize.optimise()` returning `ALLOWED` rather than
   `BLOCKED_VERIFICATION_REQUIRED`.
4. **The override.** The break-glass path, which by design can turn a block into
   an action.
5. **The stock itself.** The material motive behind most of the above.

## 2. Adversaries

| Adversary | Capability | Motive |
|---|---|---|
| Facility custodian | Physical access to stock, write access to the source ledger, owns the phone | Conceal a shortfall, conceal diversion, avoid a stock-verification finding |
| Attester | Can sign, has the app, may be socially close to the custodian | Path of least resistance; sign without looking |
| District officer | Override authority, broad read access | Make a number look right before a review |
| Vendor / AMC contractor | Feeds equipment uptime | SLA payments depend on uptime |
| External attacker | Network access, no physical access | Rarely the real threat here; listed for completeness |
| Curious insider | Read access beyond their facility | Not usually malicious, but a real privacy exposure |

The dominant adversary is **not** the external attacker. It is the insider with a
legitimate credential and a reason for a number to look right — which is exactly
the adversary a hash chain does nothing about.

---

## 3. Trust boundaries

```
  [ source system: e-Aushadhi / asset register ]      UNTRUSTED INPUT
                    │ Claim
                    ▼
  ─────────────── boundary 1 ───────────────
  [ Tathyon ingest + detectors ]                      trusted code, untrusted data
                    │ verification task
                    ▼
  ─────────────── boundary 2 ───────────────
  [ device: low-cost Android in a PHC ]               UNTRUSTED ENVIRONMENT
     camera · nonce display · local queue
                    │ Evidence + Attestation (signed)
                    ▼
  ─────────────── boundary 3 ───────────────
  [ event store: append-only, hash-chained ]          trusted store
                    │ VerifiedState (derived, never stored)
                    ▼
  ─────────────── boundary 4 ───────────────
  [ optimizer gate ] ──► [ human approval ] ──► action
```

**Boundary 1** is the one most systems in this category get wrong by not having
it. A claim is *what a system of record asserts*, not a fact — the `Claim`
docstring says so, and the `Provenance` field forces the distinction into the
data model.

**Boundary 2 is the weak one and cannot be made strong.** The device is a
low-cost Android handset in a room the custodian controls. Everything below about
evidence fraud follows from that.

**Boundary 4** exists because a gate with no override gets uninstalled in week
two. The design position, stated in `optimize.Decision.override`, is that the
override is *eligible*, requires a `medical_officer` role, is time-bounded
(`max_duration_s = 3600`), and **creates a verification obligation with a
deadline**. The security property is not prevention; it is that every override
has a name attached.

---

## 4. Evidence fraud — the core attack class

All four variants below are attacks on boundary 2.

### 4.1 Photographing a different shelf

**Attack.** Attester photographs a well-stocked shelf, or a neighbouring
facility's shelf, and attests against the wrong SKU.

**Mitigations in the data model.** `Evidence` carries a server-issued `nonce`
with `nonce_issued_at`, which must appear in the frame; `device_id`;
`captured_at`; and `perceptual_hash` for dedupe against facility history. A nonce
bounds *when* the photograph was taken. It does nothing about *what* was
photographed.

**Honest assessment: partial.** Geolocation on a consumer Android device is
spoofable and indoor accuracy is poor. The practical defence is not technical —
it is that the attester is not the custodian and has no motive to walk to another
shelf, plus random back-checks. Reason code `EVIDENCE_REUSED` exists;
`ASSET_NOT_PRESENT` covers the equipment analogue.

### 4.2 Re-using yesterday's photo

**Attack.** Submit a previously captured image.

**Mitigations.** `perceptual_hash` is stored precisely to dedupe against the
facility's own history, and `artifact_hash` detects a byte-identical resubmit.
The server-issued `nonce` must appear in the frame and carries `nonce_issued_at`,
so a stale nonce is detectable. `REASONS["EVIDENCE_REUSED"]` is the reason code.

**Honest assessment: good against naive reuse, weaker against a re-shoot.**
Perceptual hashing has a genuine false-positive problem here: a shelf photographed
monthly *should* look nearly identical when nothing changed. A pHash threshold
tight enough to catch reuse will flag honest repeat captures. The threshold is
not specified in the repository and this tension is unresolved.

### 4.3 Photographing a screen (photo-of-a-photo)

**Attack.** Display a previous photograph on a second device and photograph it.
This defeats hash-based reuse detection entirely, because the bytes are new.

**Mitigation.** `Evidence.frame_count` — a three-frame burst, documented in
`schema.py` as "3-frame burst defeats photo-of-a-photo". Natural parallax, hand
motion and rolling-shutter interaction across a burst differ measurably from a
burst of a flat screen; moiré and refresh-rate banding are further signals.

**Honest assessment: the field carries the count; the analysis does not exist.**
`frame_count` is recorded. No code in this repository examines inter-frame
differences, moiré, or screen-refresh artefacts. This is a designed mitigation
with no implementation.

### 4.4 Attesting without looking

**Attack.** The attester stands next to the shelf, photographs it, and signs the
number the ledger already said — without counting.

**Partial mitigation.** `Attestation.seconds_spent`, compared against
`POLICY["min_attestation_seconds"] = 20.0`. Below that the record goes to
`CONFLICTED` with `RUBBER_STAMP_SUSPECTED`. The schema comment gives the
rationale: a sub-20-second median across a person's attestations is
rubber-stamping.

**This is the honest part. It is undetectable in an asynchronous flow.** A
patient attester who waits 45 seconds, takes a real photograph of the real shelf
with the real nonce, and transcribes the ledger figure without counting produces
evidence that is indistinguishable, in every signal Tathyon captures, from a
correct attestation. There is no timing feature, no image feature, and no
cryptographic mechanism that separates them, because the only difference happened
inside a person's head.

**The only real countermeasure is out of band: random independent back-checks.**
A small random sample of attestations must be re-verified by a second,
independently assigned person, and the disagreement rate per attester must be
tracked and acted on. This turns an undetectable individual act into a
statistically detectable pattern. **No back-check sampler is implemented in this
repository.** Without it, `seconds_spent` is the only defence and it is a weak
one. `10_IMPACT_METHODOLOGY.md` treats the back-check as the primary measurement
instrument for exactly this reason.

### 4.5 The transcription boundary

A related design decision that is also a security property: **the AI never
counts.** Vision-language models score roughly 0.23–0.58 on object counting, so a
model-derived count would be a fabricated number with a machine's authority. In
Tathyon the human counts and the model transcribes what the human wrote;
`extraction_agreement` records whether they match, and a `delta_pct > 5`
disagreement sends the record to `CONFLICTED` rather than resolving it. This
removes an entire class of attack in which an attacker manipulates a model into
producing a convenient count — there is no count for the model to produce.

---

## 5. Custodian / attester separation

This is the strongest control in the system and it is enforced at write time, not
in a report.

`Attestation.is_custodian` must be `False`, and `EventStore.put_attestation()`
raises on violation with the message: *"the custodian of record may not attest to
their own resource. Route to the facility in-charge, a block supervisor, or a
peer facility."*

The rationale in the schema docstring is the correct one: every audit tradition
separates custody, recording and verification, and asking the person who wrote
the register to sign that their own books were wrong is a structural conflict of
interest. Reason code `ATTESTOR_IS_CUSTODIAN` exists for reporting.

**Limitations.**

- The flag is **self-asserted by the client**. Nothing in the repository
  cross-references `attestor_id` against an authoritative custodian register. A
  client that sets `is_custodian = False` passes. Making this real requires an HR
  or posting registry as a trusted source, which does not exist here.
- Separation is defeated by collusion between two people in the same room, which
  is the ordinary case in a single-doctor PHC where there may be only two staff
  members. In small facilities the correct routing is a *peer facility* or a block
  supervisor, which costs travel time, which is why the system must ration
  verification effort rather than verify everything.
- `delegation_id` is carried on the attestation for the case where authority is
  delegated, but no delegation validity check is implemented.

---

## 6. Ledger integrity — tamper-evident, not tamper-proof

`store.py` states the distinction in its own docstring and it is worth keeping
exactly that precise.

**What is implemented.** Append-only (no UPDATE, no DELETE — a correction is a
new event); hash-chained (`StateEvent.seal()` folds the previous hash into this
event's SHA-256); `verify_chain()` recomputes every hash and returns the first
bad offset; idempotency via `client_event_id`, so an offline device can safely
replay its whole queue after a partial sync; and `project()`/`state_for()` as
pure functions, so state is replayable from the log.

**What this gets you.** Silent, undetected modification of a past event is
detectable, because the chain breaks from that offset onward.

**What this does not get you.** An attacker with write access to the store can
recompute the entire chain forward from the point of alteration and present a
perfectly valid chain. A local hash chain proves *internal consistency*, not
*history*. Closing that gap requires an external anchor — a periodic root hash
published or countersigned somewhere the operator does not control, plus an
independent verifier that actually checks it. **Neither is implemented.**

Also absent: `save()` writes JSONL with no signature over the file, the chain is
in-memory with no durable append-on-write, and there is no load/replay function
that verifies on read.

---

## 7. Key management and signatures

`Attestation.signature` is documented in `schema.py` as **"software keypair in
prototype"**.

**The honest position.** These are software keypairs held on the device. They are
**not hardware-attested**. Hardware-backed keystores and key attestation are not
reliably available on the low-cost Android devices actually used in PHCs — that
is the deployment reality, not a shortcut. A signature therefore proves that
*something holding that key* signed, and a rooted or shared device breaks the
binding between the key and the person.

**Consequences to state plainly.**

- A signature is evidence of process, not proof of identity.
- A shared facility handset with one enrolled key makes every attestation from
  that facility indistinguishable by signer.
- No key rotation, revocation, enrolment ceremony, or CRL is implemented.
- No signature *verification* code path exists in this repository. The field is
  stored and carried into the event payload; nothing checks it.

**What would improve this without hardware attestation:** server-side key
enrolment tied to a posting record, one key per person rather than per device,
short-lived credentials, and — again — random back-checks, which catch a
compromised key by its outputs rather than its cryptography.

---

## 8. RBAC

The repository carries role information — `Attestation.attestor_role`,
`delegation_id`, and the override's `required_role: "medical_officer"` — but
**no access-control enforcement code exists**. There is no authentication, no
session, no authorisation check, and no per-facility read scoping. This is a
library and a simulation, not a deployed service.

The model that would be required:

| Role | Read | Attest | Override | Notes |
|---|---|---|---|---|
| Storekeeper / custodian | Own facility | **Never for own resources** | No | Enforced today only by the `is_custodian` write check |
| Facility in-charge | Own facility | Yes | No | |
| Block supervisor | Block | Yes, incl. peer facilities | No | The routing target when separation cannot be met locally |
| Medical officer | District | Yes | Yes, ≤1 h, creates an obligation | The only override role in the code |
| District officer | District | No | Review only | Broad read is a privacy exposure; scope it |
| Auditor | Read-only, wide | No | No | Needs the `timeline()` export |

Read scoping matters even though the data is about stock rather than people:
combined with duty rosters, verification records reveal individual work patterns,
which is the same reason personnel attendance is deliberately excluded from scope
(`archive/12_REQUIREMENTS_TRACEABILITY.md`).

---

## 9. Attacks on the gate and the optimizer

- **Inflate the claim.** Moot by design — the gate optimises over `q_alpha` from
  a verified attestation, never over `reported_qty`. Inflating the ledger changes
  nothing downstream, which is the single most important security property in the
  system.
- **Inflate the attestation.** Works, and is the real attack. Countered only by
  custodian separation plus back-checks.
- **Deflate a rival's stock to attract transfers in.** The gate blocks transfers
  *out of* unverified stock; it does not require the *recipient's* need to be
  verified. A fabricated shortfall pulls real stock. Unaddressed.
- **Override abuse.** Time-bounded, role-gated, and obligation-creating by design.
  Not implemented — `override` is a static dictionary on `Decision` with no
  enforcement path.
- **Starve the queue.** Flood the system with weak-signal noise so real
  discrepancies fall below the human-capacity cutoff. `priority()` is designed for
  rationing, not for adversarial load. Unaddressed.

---

## 10. Why we do not use blockchain

A verification system for public-health supply chains is the canonical setting in
which someone proposes a blockchain. We do not use one, for four reasons.

1. **It solves the wrong problem.** A blockchain provides consensus among
   mutually distrusting writers about the *order and immutability of records*. It
   provides nothing about whether a record is *true*. Every failure in the CAG
   findings we anchor against — expired stock counted live, same batch with two
   expiry dates, ₹11 lakh unit values, 85 drugs issued and never received — would
   be recorded just as faithfully on a chain. The gap between the ledger and the
   shelf is a physical gap, and it is closed by a human with a torch, not by a
   consensus protocol.

2. **The trust model does not fit.** There is a single legitimate authority here —
   the state health department. There are no mutually distrusting writers needing
   trustless consensus. When one party is authoritative, a hash-chained
   append-only log signed by that party gives the same tamper-evidence at a
   fraction of the complexity. That is precisely what `store.py` implements.

3. **It makes the real requirements harder.** India's DPDP Act creates erasure and
   correction obligations. An architecture whose selling point is that nothing can
   ever be removed is a poor foundation for that. A correction in Tathyon is a new
   superseding event, which satisfies audit needs while leaving the data under the
   operator's lawful control.

4. **Operational cost at the wrong end.** PHC connectivity is intermittent, which
   is why the store is offline-first and idempotent. Adding consensus, key
   ceremonies and node operations to that environment spends the scarce resource —
   staff attention and connectivity — on a property nobody asked for, at the
   expense of the one that matters: getting a second human to physically look at
   the shelf.

The honest summary: **the hard problem is oracle integrity, and a blockchain does
not touch oracle integrity.**

---

## 11. Known limitations — consolidated

Security properties that are **designed and documented but not implemented** in
this repository:

- Frame-burst analysis for photo-of-a-screen detection (`frame_count` is stored,
  never analysed)
- Perceptual-hash reuse detection (field stored, no comparison code, no threshold)
- Nonce issuance, expiry, and in-frame verification (fields stored, no checker)
- Signature verification of any kind
- Any authentication, authorisation, or RBAC enforcement
- Override enforcement, time-bounding, and obligation creation
- External anchoring of the hash chain
- Random independent back-check sampling — **the only real defence against
  attesting-without-looking**
- Cross-referencing `is_custodian` against an authoritative posting register

Security properties that are **implemented and tested by inspection**:

- Append-only store with hash chaining and `verify_chain()`
- Idempotency by `client_event_id`
- Write-time rejection of custodian self-attestation
- The verification gate itself: unverified stock is not transferable, and the
  decision is a typed `BLOCKED_VERIFICATION_REQUIRED` rather than an exception
- `VerifiedState` never persisted, so no stored status column can be tampered with
  independently of the log

There has been **no penetration test, no security review by a third party, and no
deployment**. Nothing in this section should be read as an assurance.
