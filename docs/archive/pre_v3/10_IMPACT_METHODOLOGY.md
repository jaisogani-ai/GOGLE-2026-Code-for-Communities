# 10 — Impact Methodology

This document describes how Tathyon's impact **would** be measured. It reports no
impact, because none has been measured: there has been no pilot, no deployment and
no real user (`14_LIMITATIONS.md`).

The discipline here is the same one the codebase applies to data provenance — every
claim carries a label saying what kind of statement it is. Mixing these five
categories is the usual way impact claims become dishonest, and it is almost always
done by quoting a `PUBLIC_EVIDENCE` figure next to a `SIMULATED` one and letting the
reader join them.

---

## Evidence categories

| Label | Definition | What it can support |
|---|---|---|
| `OBSERVED_IN_DEMO` | Directly observable by running this repository | Statements about what the software does |
| `SIMULATED` | Produced by our own synthetic generator and our own detectors | Statements about internal consistency of the pipeline. Nothing about the world. |
| `MODELLED` | An arithmetic projection combining a `SIMULATED` or `OBSERVED` quantity with an external assumption | Sensitivity ranges, explicitly bounded. Never a headline number. |
| `PUBLIC_EVIDENCE` | A figure from a published audit, cited to paragraph | Statements about the size of the problem — never about our effect on it |
| `FUTURE_PILOT_METRIC` | Defined, instrumented, and not yet collected | Statements about what we would measure and what result would falsify us |

**The rule we hold ourselves to:** a `PUBLIC_EVIDENCE` figure describes the
problem. It may never be multiplied by a `SIMULATED` detection rate to produce a
savings claim. "CAG Telangana found ₹390.26 crore of expired drugs in stock, and
our detector catches 80% of injected expiry errors, therefore Tathyon saves ₹312
crore" is the exact sentence this taxonomy exists to prevent. The 80% is about our
generator; the ₹390.26 crore is about Telangana; they do not multiply.

---

## What is observable today

### `OBSERVED_IN_DEMO`

These are properties of the software, verifiable by running it. They are claims
about behaviour, not about outcomes.

1. An optimizer request whose source stock is unverified returns
   `BLOCKED_VERIFICATION_REQUIRED`, with the blocked sources, their reason codes,
   and a remedy attached — as a typed decision, not an exception
   (`optimize.optimise`).
2. An attestation by the custodian of the resource is rejected at write time with
   `ATTESTOR_IS_CUSTODIAN` (`store.put_attestation`).
3. Modifying any past event breaks the hash chain and `verify_chain()` returns the
   offset at which it broke.
4. Every flagged record carries machine-readable reason codes with human-legible
   text (`schema.REASONS`), so the basis for a flag can be read aloud.
5. Replaying the event log reproduces the state exactly; no computed status is
   stored.

### `SIMULATED`

Produced by running our detectors against our generator. Useful for development.
Not evidence.

- Detection rate of hard violations against `label_wrong`
- `phantom_units_naive_would_move` from `optimize.counterfactual` — units a
  ledger-trusting optimiser would have moved that do not physically exist
- Forecast MASE against naive, seasonal-naive and Croston-SBA baselines, on both
  seen and held-out facilities
- Number of sources blocked by the gate

Every one of these is determined by the injection rates in
`generator.CAG_ANCHORS`, several of which are our assumptions
(`07_DATA_PROVENANCE.md`). Change the assumption and the number changes. That is
what makes them simulation outputs rather than findings.

### `PUBLIC_EVIDENCE`

The published findings that motivate the work. Each describes the size of a
problem. None of them is a Tathyon result.

| Finding | Source |
|---|---|
| 952 of 13,322 records with the same batch number but different expiry dates | CAG Punjab Report No. 4 of 2019, Para 2.1.7.2(ii) |
| 24,164 of 6,74,253 instances where FEFO was not followed | CAG Punjab, Para 2.1.7.6(vi) |
| 6,120 offline indents entered after delays of up to 947 days | CAG Punjab, Para 2.1.7.6(vii) |
| Shortfalls of 38 to 90 per cent in carrying out monthly stock verification; physical verification entries never captured in the system | CAG Punjab, Para 2.1.8.4 |
| A unit rate of ₹11,10,111.20 recorded for an Amoxycillin 250 mg capsule, attributed by the audit to the software not being properly tested — **a data-validation failure, not a purchase price** | CAG Maharashtra Report No. 4, IT Audit of e-Aushadhi, Para 2.4.8.12 |
| 85 drugs valuing ₹1.36 crore not received by PHCs | CAG Maharashtra, Para 2.4.8.9 |
| 1,681 of 3,970 vital medicines unavailable (42%) on joint physical verification | CAG Maharashtra PA 2024, Ch.4 Table 4.3 |
| Expired drugs valuing ₹390.26 crore in stock | CAG Telangana Report No. 4 of 2024, Para 4.5.3 |
| 43 of 75 PSA oxygen plants not commissioned or not functional | CAG West Bengal Report No. 3 of 2024, Para 4.5 |
| 7,56,750 equipment items in 29,115 facilities valued ~₹4,564 crore, 13–34% dysfunctional | NHSRC BEMMP |

The Maharashtra PA 2024 figure deserves particular attention, because it is the
closest published analogue to what Tathyon proposes to measure: **on joint
physical verification, 42% of vital medicines recorded as available were not
available.** That is an auditor doing, at one point in time and at great expense,
precisely the thing Tathyon proposes to do continuously and cheaply. It is also
the benchmark against which our first metric should be read.

---

## The first metric: verification yield

**Definition.** Of the records Tathyon's gate flagged and dispatched for physical
verification, what percentage turned out to be **materially wrong** when a human
physically checked them?

```
verification_yield = (flagged records found materially wrong on physical check)
                     ─────────────────────────────────────────────────────────
                     (flagged records physically checked)
```

**"Materially wrong" must be defined before the pilot starts, not after.** We
adopt the same operational definition the generator uses for its labels, because
it is the one that connects to a decision:

- **Medicines:** the usable quantity differs from the recorded quantity by more
  than 15% of the recorded quantity, **or** stock recorded as usable is not usable
  (expired, damaged, or absent). The 15% threshold is our stated definition of
  "would change a replenishment decision" and is itself a pilot parameter to be
  reviewed.
- **Equipment:** the asset's true status differs from `register_status` — not
  present, not commissioned, or not functional.

**Why this metric first.** It is the only metric that is simultaneously (a)
measurable in weeks rather than years, (b) directly falsifying — a low yield says
the ranking is not working, with no room for narrative — and (c) about the thing
the product actually claims. Downstream metrics (stockout days averted, value of
stock correctly redistributed) depend on this one being non-trivial, and they take
far longer to move.

**What makes it honest.** Verification yield must be reported **against a control
arm**, because a yield figure alone is uninterpretable. If Tathyon flags records
at 55% yield and a random sample of unflagged records yields 45%, the ranking has
almost no value even though 55% sounds impressive. The comparison, not the level,
is the result.

**Its limitation, stated up front.** Verification yield measures the *precision*
of the flagging. It says nothing about *recall* — the materially wrong records
that were never flagged. Recall is only estimable via the random control arm, and
only imprecisely at pilot scale. A system could achieve excellent yield by
flagging only the most obviously broken records and missing most of the problem.
The control arm is the sole defence against that and must not be cut.

---

## Pilot design

**Status: `FUTURE_PILOT_METRIC`. This has not been run, and no facility has
agreed to it.**

### Scope

| Parameter | Value | Rationale |
|---|---|---|
| Facilities | **3** — ideally one PHC, one CHC, one district hospital in a single district | Three tiers exercise the routing problem for custodian/attester separation. One district keeps travel feasible for the independent back-check arm. |
| Assets and lines under verification | **200 total** across the three facilities — roughly 150 medicine SKU-lines and 50 equipment items | Small enough that every flagged line can be physically checked; large enough that a 10-point yield difference is distinguishable from noise. |
| Duration | **8–12 weeks** | Covers at least two medicine attestation cycles at the 30-day policy window, plus an initial baseline sweep. Twelve weeks if the first two are consumed by enrolment and device setup, which they will be. |
| Verification capacity | Fixed and declared in advance — e.g. 40 physical checks per week across the three sites | The scarce resource is human minutes. Ranking under a *fixed* budget is the thing being tested; an unbounded budget tests nothing. |

### Arms

1. **Baseline sweep (week 1–2).** A full joint physical verification of all 200
   lines, conducted once, establishing the true prevalence of material error. This
   is the pilot's most valuable output regardless of whether Tathyon works, and it
   is directly comparable to the CAG Maharashtra PA 2024 joint-verification
   methodology.
2. **Flagged arm.** Each week, the top *k* records by `VerificationEngine.priority()`
   are dispatched for physical check.
3. **Random control arm.** Each week, an equally sized random sample from the
   *unflagged* population is dispatched, **indistinguishable to the attester from
   the flagged arm**. Attesters must not know which arm a task belongs to;
   otherwise the flagged arm gets more careful counting and the comparison is
   destroyed.
4. **Independent back-check arm.** 10% of all completed attestations, sampled at
   random, are re-verified by a second, independently assigned person who does not
   see the first attestation. This is the **only** instrument that detects
   attesting-without-looking, which is undetectable in the asynchronous flow
   (`09_SECURITY.md` §4.4). It is not optional.

### Primary endpoint

**Verification yield in the flagged arm minus verification yield in the random
control arm**, measured over the full pilot.

### Success and failure thresholds

Declared before the pilot, and not to be revised afterwards.

| Outcome | Threshold | Interpretation |
|---|---|---|
| **Success** | Flagged-arm yield ≥ **2×** control-arm yield, **and** flagged-arm yield ≥ 30% in absolute terms | The ranking earns its place. A human sent to a flagged record finds a real problem at twice the rate of chance, at a level that justifies the trip. |
| **Qualified success** | Flagged yield between **1.3× and 2×** control | The signal is real but the ranking is weak. Continue only with a specific hypothesis about which features to fix; do not scale. |
| **Failure** | Flagged yield **< 1.3×** control | The detectors are finding "unusual", not "wrong" — the exact failure mode `detect.py` names. Stop. Do not proceed to a wider pilot on a narrative. |
| **Hard failure** | Flagged yield **≤** control yield | The ranking is worthless or inverted. |

### Secondary endpoints

| Endpoint | Threshold / purpose |
|---|---|
| **Back-check disagreement rate** | If >15% of back-checked attestations materially disagree with the original, the *attestation* is unreliable and every downstream number is void — this invalidates the pilot regardless of yield. This is the most important secondary endpoint and can override the primary. |
| **Attestation completion rate** | Fraction of dispatched verification tasks actually completed within the policy window. If staff cannot complete them, the 30-day window is wrong. CAG Punjab Para 2.1.8.4's 38–90% shortfall in *statutorily required* monthly verification is the sobering prior here. |
| **Median `seconds_spent`** | A median near the 20-second floor across an attester's record indicates rubber-stamping and is a per-person signal, not a system-level one. |
| **Custodian-separation routing cost** | How often separation could not be satisfied within the facility, and the travel time incurred. This is the pilot's main operational-feasibility question and we do not know the answer. |
| **Gate blocks and overrides** | Count of `BLOCKED_VERIFICATION_REQUIRED` decisions, count of overrides, and the fraction of overrides whose verification obligation was discharged before deadline. An override rate near 100% means the gate is theatre. |
| **`q_alpha` calibration** | Fraction of physical checks in which true usable quantity was at least `q_alpha`. Should be ≈ 1−α. A materially lower figure means the freshness posterior is over-claiming and α must rise. |

### What the pilot explicitly cannot establish

- **Stockout reduction.** Eight to twelve weeks at three facilities cannot detect a
  change in stockout days with any power. Reporting one would be noise dressed as
  a result.
- **Financial savings.** Requires a counterfactual procurement and distribution
  path we would not have.
- **Behaviour change.** The hypothesis that a named, recorded override changes
  behaviour is a multi-quarter question, not a 12-week one.
- **Anything about scale.** Three facilities say nothing about 29,115.

### `MODELLED` extrapolation — and its rule

If verification yield and the baseline prevalence figure come in, they may be
combined with facility counts to produce a bounded range of how many materially
wrong records exist in a district — labelled `MODELLED`, presented as a range with
its assumptions listed, and never as a headline. It may **not** be converted into
a rupee savings figure, because the step from "this record was wrong" to "this
money was saved" requires a causal chain — that the correction changed a decision,
that the changed decision changed a physical outcome — that no pilot of this size
can support.

---

## Reporting discipline

Every figure Tathyon publishes carries its label. A slide, a README, or a demo
that shows a number without one is a defect. In particular:

- No CAG figure may appear adjacent to a Tathyon performance figure without an
  explicit statement that they are not multiplicable.
- The ₹11,10,111.20 Amoxycillin figure must always be described as a **validation
  failure attributed by the audit to inadequate software testing**, never as a
  price paid. Presenting it as procurement fraud misrepresents a public audit.
- Any accuracy figure computed on `data/*.parquet` must be labelled `SIMULATED`
  and accompanied by the statement that the errors were injected by us.
