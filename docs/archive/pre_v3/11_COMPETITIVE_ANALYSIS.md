# 11 — Competitive Analysis

This document compares Tathyon against the systems that already occupy this
space. It is written to find where Tathyon is *not* differentiated, because that
is the part a reviewer will find anyway.

---

## 0. Verification caveat, stated before anything else

A GitHub repository at `github.com/Saatvik6/healthgrid` describes itself in its
own README as **"Winner of Google Cloud X Hack2Skill 'Build with AI: Code for
Communities'"** (Smart Health track).

**The winning entry's identity is asserted by that repository's own README and
has not been independently confirmed against an official results announcement.**

What is independently corroborated is only this: the repository exists and is
public. Its claim to have won is a self-description. It may be accurate; nothing
here depends on whether it is.

Accordingly, the comparison below treats that project **on its verifiable
content only** — what its repository states it implements — and draws no
conclusion from the award claim. Referring to it as "the winning entry" would
import an unverified fact into an analysis whose entire premise is that
unverified facts should not be acted on. It is referred to as **HealthGrid**.

The same standard applies in the other direction: none of Tathyon's numbers are
external validation either. All Tathyon data is synthetic; there is no pilot, no
deployment, no partnership and no revenue (`14_LIMITATIONS.md`).

---

## 1. What HealthGrid implements, as its repository describes it

- A **district command centre** with per-facility risk scoring, weighted:
  medicines 40%, staffing 25%, beds 15%, surge 10%, diagnostics 10%.
- **Burn-rate stockout forecasting** — consumption rate projected to a stockout
  date.
- **Guarded transfer recommendations**, with two explicit guards: a transfer may
  not exceed **40% of donor stock**, and the donor must retain **more than 14
  days of cover**.
- **Gemini root-cause narration** over the computed situation.
- **Hindi voice input.**
- A **Firestore audit trail**.
- It positions itself as **"the decision layer on top of"** HMIS / DVDMS / eVIN.

This is a coherent and well-scoped product. The guards in particular are not
decorative — a donor-fraction cap and a donor-cover floor are exactly the two
constraints a naive transfer optimiser omits, and Tathyon implements the same
two (`max_donor_fraction = 0.40`, plus a per-facility safety floor). On
optimisation guardrails, HealthGrid and Tathyon have converged independently,
and Tathyon claims no advantage there.

The difference is upstream of all of it. HealthGrid computes risk, forecasts
stockouts, narrates causes and recommends transfers **against the numbers the
source systems report**. It is a decision layer on top of HMIS/DVDMS/eVIN and
says so. Tathyon's position is that the number reported by those systems is the
thing in question: the register says FUNCTIONAL for 100% of assets while 49.2%
are actually functional (divergence 50.8%), and vendors self-report 97.9% mean
uptime against a 95% SLA with no independent check. A decision layer sitting on
top of that inherits it.

That is a difference in **where the system places its trust boundary**, not a
difference in quality. It is also a difference that only matters if the
underlying data is in fact unreliable — which is an empirical claim about the
real world that Tathyon has not tested, because Tathyon has no real data either.

---

## 2. Comparison table

| | **Problem solved** | **Data** | **Verification** | **Forecast** | **Risk** | **Optimisation** | **Human approval** | **Audit** | **Offline** | **AI** | **Differentiator** | **Limitation** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **HealthGrid** | District situational awareness and guarded redistribution | HMIS / DVDMS / eVIN feeds, taken as reported | None — data is an input, not a subject | Burn-rate to stockout date | Weighted facility score (meds 40 / staff 25 / beds 15 / surge 10 / diag 10) | Guarded transfers: ≤40% of donor stock, donor retains >14 days cover | Recommendation to a human | Firestore trail | Not stated | Gemini root-cause narration; Hindi voice input | Breadth — beds, staffing and surge in one district view, with language access | Explicitly a decision layer on top of the source systems, so it inherits their accuracy. Narration over unverified state can make a wrong number more persuasive. |
| **e-Aushadhi / DVDMS** | Drug inventory and procurement — the **system of record** | Its own transactional ledger | Periodic physical verification as a manual process outside the software | Reorder levels, consumption reports | Stock-out and expiry reports | Indent and issue workflow | Full workflow approvals | Transaction log | Facility-level entry, connectivity-dependent | None | It is the book of record; everything else is a sidecar to it | The book is the claim. Discrepancy between book and physical stock is precisely what audits keep finding, and the software has no independent evidence of physical reality. |
| **DHIS2** | Health management information, aggregate reporting | Aggregate and tracker data | **Ships validation rules, outlier detection (min-max, z-score, modified z-score) and a data-quality app** | Basic projections | Indicator-based | None | Approval workflow on data sets | Full audit of data-value changes | Android capture app with genuine offline support | Optional analytics add-ons | The global default. Enormous installed base, and a mature data-quality toolchain already in the box. | Detects *statistical* implausibility in reported aggregates. It has no concept of physical evidence, no attestation object, and nothing downstream refuses to act on a flagged value. |
| **OpenLMIS** | Logistics management for health supply chains | Requisition and stock-management data | Stock-card reconciliation, physical-inventory entry | Consumption-based forecasting | Stock-status indicators | Allocation and requisition logic | Requisition approval hierarchy | Transaction history | Offline-capable clients in some implementations | None core | Open-source, purpose-built LMIS with a real implementer community | Physical inventory is a data-entry event, not an evidenced one. A recorded count and a verified count are the same object. |
| **mSupply** | Warehouse and dispensary management, widely deployed in LMICs | Its own transactional store | Stocktake module with variance reporting | Consumption forecasting, AMC | Stock alerts | Transfer and requisition management | Role-based authorisation | Transaction log | **Strong offline operation — a design priority** | None core | Battle-tested offline behaviour in genuinely low-connectivity settings | Stocktake variance is reported, not gated. A variance does not stop a downstream transfer. |
| **eVIN / Logistimo** | Vaccine stock and cold-chain visibility | Handheld entry at the facility + temperature loggers | **Sensor-based verification of temperature** — genuine independent evidence, for one variable | Consumption and stock projections | Cold-chain and stock alerts | Indent support | Supervisor workflow | Event history | Designed for intermittent connectivity | None core | The closest existing system to independent physical evidence: the temperature logger is a sensor, not a claim | The evidence covers temperature, not quantity or existence. Stock counts remain human-entered and unverified. |
| **Hospital ERP cycle-counting (SAP EWM, Oracle WMS, Ariba-class modules)** | Enterprise inventory accuracy | ERP master and transactional data | **Cycle counting with tolerance thresholds; bins can be blocked pending count** | Demand planning modules | ABC classification | Full WMS optimisation | Approval hierarchies, segregation of duties | Complete audit trail, policy-based-grade | Rarely | Increasingly embedded | Decades of mature practice. **Blocking posting against an uncounted bin is not new — this has shipped for decades.** | Operates entirely **inside one organisation's four walls**. The counter, the approver, the system and the beneficiary are all the same legal entity. There is no external relying party. |
| **Tathyon** | Deciding whether a reported operational state is trustworthy **before** anything acts on it | Claims ingested from a system of record, treated as claims | **The core function.** Evidence capture + human attestation + state machine (`VERIFIED / UNVERIFIED / CONFLICTED / REJECTED / OVERRIDDEN`); attester may not be the custodian, enforced at write time | TSB / Croston-SBA with ADI-based selection (Syntetos–Boylan 1.32) | Priority score used **only** to order the verification queue — never persisted, never exported, never placed in an attestation | OR-Tools CP-SAT over `Q_alpha`, the quantity confident of having *at least*; ≤40% donor fraction; donor safety floor | Mandatory for consequential actions; break-glass always succeeds and creates a deadlined verification obligation | Append-only hash-chained event store; replay reproduces state exactly; tamper returns the breaking offset | Evidence capture designed offline-first (Firestore local cache in the target topology) | Gemini transcribes what the human wrote — **the AI never counts**; low-confidence cells render blank-to-fill, never pre-filled | A low-trust claim **blocks a downstream optimiser** and routes to physical verification; the resulting signed state is what a third party relies on | All data synthetic. Trust model is weak (PR-AUC 0.079 vs 0.053 base rate). Gemini adapter is a mock. Event store is in-process. Nothing deployed, no pilot, no user. |

---

## 3. What is table stakes and must not be sold as novelty

**"We detect bad data" is table stakes.** DHIS2 — the most widely deployed health
information system in the world — already ships validation rules, outlier
detection and a dedicated data-quality app, and has for years. Any pitch whose
core claim is "we find anomalies in health data" is describing a feature that
the incumbent gives away in the base install. Tathyon's detectors are not a
differentiator and are not presented as one.

**Trust gating as a generic pattern is not novel either.** Two independent
lineages have shipped it:

- **Data observability tools have shipped circuit breakers since 2022.** Monte
  Carlo, Great Expectations / dbt tests in CI, Soda and similar tools all
  support halting a downstream pipeline when a data quality check fails. "Bad
  data stops the pipeline" is a solved and commoditised pattern.
- **SAP and Oracle WMS have blocked posting against uncounted bins for
  decades.** Cycle counting with a blocking tolerance is standard enterprise
  warehouse practice and predates the entire modern data-quality industry.

Anyone claiming to have invented "don't act on data you don't trust" is either
unfamiliar with these or hoping the audience is. Tathyon claims neither.

Similarly not novel: intermittent-demand forecasting (Croston 1972, Syntetos–
Boylan 2005, Teunter–Syntetos–Babai 2011 — all textbook), constrained transfer
optimisation with donor guards (HealthGrid implements the same two guards),
hash-chained append-only logs, and VLM transcription.

---

## 4. Tathyon's honest differentiator

Stated as narrowly as it can be stated while remaining true:

> **Tathyon is the only system in this table where a low-trust claim BLOCKS a
> downstream optimiser and routes to physical verification, and where the
> resulting signed state is what a third party relies on.**

Each clause is doing work, and the last one is doing most of it:

1. **Blocks, rather than flags.** DHIS2 flags. mSupply reports variance. eVIN
   alerts. In each case a human may act on the flagged value anyway, and the
   software does not object. In Tathyon the unverified stock is not eligible for
   transfer; **the solver never sees it**. The gate is not a solver constraint,
   so replacing the solver cannot weaken it.

2. **Routes to physical verification.** The block is not terminal. It emits a
   remedy: a specific facility, a specific resource, a specific evidence request.
   A gate that only refuses generates resentment; a gate that refuses and hands
   back the next action generates a count.

3. **The signed state is what a third party relies on.** This is the clause the
   ERP lineage does not have. SAP blocks a posting for **its own** subsequent
   transaction, inside one company. Tathyon's verified state is produced by one
   organisation (the facility, whose in-charge attests) and consumed by another
   (the district, the payer, the auditor) as the basis for paying money or
   emitting a policy-based certificate. The attester may not be the custodian —
   enforced at write time — precisely because the two parties are not the same
   party. Asking the person who wrote the register to sign that their own books
   were wrong is a structural conflict, and that conflict only exists across an
   organisational boundary.

**The novelty is therefore not the pattern. It is the closed loop across an
organisational boundary in this domain:** claim from one party → detection →
evidence and attestation by a non-custodian → signed verified state → a gated
decision by a second party → refusal with a remedy → audit trail a third party
can replay. No system in the table above closes that loop. Most do not attempt
to, because most are operating inside a single organisation where the loop is
unnecessary.

That is a modest claim. It is also the only one the code supports.

---

## 5. Where Tathyon is behind

Written out because a comparison table that only flatters its author is not an
analysis.

| Against | Tathyon is behind on |
|---|---|
| DHIS2 | Installed base, implementer community, breadth of the data model, maturity of the data-quality toolchain, actual offline Android client in demonstration use |
| mSupply, eVIN | Proven offline operation in real low-connectivity facilities. Tathyon's offline behaviour is designed and demonstrated, not field-tested. |
| OpenLMIS, e-Aushadhi | Being an actual logistics system. Tathyon is a sidecar and deliberately cannot replace the system of record. |
| HealthGrid | Breadth (beds, staffing, surge), Hindi voice input, and a working Gemini integration — Tathyon's Gemini adapter is a mock interface. |
| SAP / Oracle WMS | Every non-functional property: durability, scale, identity, authorisation, compliance certification, support. |
| All of them | **Having ever been used by anyone.** |

---

## 6. The claim that would falsify the differentiator

If a district runs Tathyon and the gate's block rate falls to near zero while
physical divergence stays high, the gate is being routed around — through
break-glass overrides whose obligations are never discharged, or through
verification queues that grow without being worked. In that case the
differentiator is theatre, the system is a slower DHIS2, and the honest response
is to say so. The monitoring in `06_AD.md` §5 is designed to detect exactly this,
and the break-glass-with-expired-obligation count is the specific metric that
decides it.
