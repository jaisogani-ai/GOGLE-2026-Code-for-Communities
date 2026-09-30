# 15 — Startup Strategy

**Status of this document.** Every commercial statement below is a hypothesis.
There is no revenue, no customer, no letter of intent, no pilot, no partnership
and no user. Nothing here has been validated by anyone, by any accelerator, by
any programme, or by any market. A strategy document written before first
revenue is a set of falsifiable guesses, and it is written here so that the
guesses can be checked against reality later rather than quietly revised
afterwards.

The evidence discipline of `10_IMPACT_METHODOLOGY.md` applies: a figure from a
published audit describes the size of a problem and may never be multiplied by a
figure from our synthetic generator to produce a savings claim or a market size.

---

## 1. The commercial wedge

**Independent verification of physical health-asset claims that money is paid
against.**

Not "supply chain visibility". Not "AI for public health". Not "data quality".
The wedge is narrower than all three, and the narrowness is the point.

The wedge exists wherever these three conditions hold simultaneously:

1. **A claim is made about a physical thing** — this ventilator is functional,
   this facility holds 795 units of salbutamol, this cold chain held temperature.
2. **Money moves on the strength of that claim** — an AMC instalment, a
   reimbursement, a replenishment order, a performance-linked grant tranche, a
   policy-based certificate that unlocks a budget line.
3. **The party making the claim benefits from it being accepted**, and no
   independent party has looked.

Condition 3 is where the value is. In the synthetic corpus this system models,
the asset register says FUNCTIONAL for 100% of assets while 49.2% are actually
functional — a divergence of 50.8% — and vendors self-report 97.9% mean uptime
against a 95% SLA. Every vendor beats their contract; nobody checks. That
structure is not a data-quality problem. It is a **verification-of-counterparty
problem**, and verification of counterparty is something people have always paid
for.

The comparison that makes the wedge legible is not another software product. It
is the pre-shipment inspection industry — SGS, Bureau Veritas, Intertek. Those
businesses exist because a buyer will not pay against a seller's own assertion
about physical goods. Tathyon's claim is that the same structure exists in public
health procurement and that nobody has built the software-first version of it.

**Explicitly not the wedge:** anomaly detection (DHIS2 ships it free —
`11_COMPETITIVE_ANALYSIS.md` §3), demand forecasting (commoditised, and our own
pooled result loses to the naive mean), and district dashboards (crowded, and
the buyer is someone who wants to look at data rather than someone who has to pay
against it).

---

## 2. First buyer hypothesis

**HYPOTHESIS — not validated, not tested with any buyer.**

The first buyer is **not** the facility, and **not** the health ministry. It is
the party that is **currently paying against unverified claims and is
accountable when that goes wrong.**

Ranked by how sharply the pain sits on one budget line:

| Rank | Candidate buyer | Why they might pay | Why they might not |
|---|---|---|---|
| 1 | **A state biomedical equipment maintenance programme (BEMMP-type) office** | Pays AMC instalments against vendor-reported uptime with no independent check. An audit finding lands on this office, not the vendor. Verification is directly substitutable for the inspection they are already supposed to do under GFR Rule 213. | The same office may prefer not to know. A verification layer that produces evidence of past overpayment creates a problem for the person who commissioned it. **This is the strongest single objection to the whole business.** |
| 2 | **A development-finance or multilateral grant-maker with disbursement-linked indicators** | Already pays third-party verification agents in cash to confirm results before releasing tranches. The budget line exists and is externally mandated. Substituting a software-plus-evidence workflow for a consultant field visit is an argument about cost, not about whether verification is needed. | Procurement cycles measured in years. They may insist the verifier be a named accredited firm rather than software. |
| 3 | **A state health-society finance or internal-audit function** | Owns the CAG exposure. Verification is their job description. | Small discretionary budget; may lack authority to mandate facility behaviour. |
| 4 | **A private hospital chain's group internal audit** | Faster to sell to, real budget, no procurement cycle. Cycle counting across sites is an existing practice they pay people to do. | Loses the organisational-boundary property that is Tathyon's actual differentiator (`11_COMPETITIVE_ANALYSIS.md` §4) — inside one company, SAP already blocks posting against an uncounted bin, and has for decades. |
| 5 | **A health insurer / claims payer** | Largest eventual market, strongest structural fit with the wedge. | Furthest from anything built. Not a first buyer. |

**The first-buyer hypothesis to test, stated so it can fail:** rank 2 —
a grant-maker with an existing third-party-verification line item — because they
are the only candidate who has **already conceded that verification must be
independent and has already budgeted cash for it.** Everyone else must first be
persuaded that independent verification is necessary, which is a much longer
sale.

**What would falsify it:** three conversations with such organisations in which
the response is that their verification agent is mandated by a framework
agreement, is not substitutable, and that no amount of evidence quality changes
that. If verification supply is contractually locked, software does not enter.

---

## 3. Pricing hypothesis

**HYPOTHESIS — not market fact. No price has been quoted to anyone, and no
customer exists to have rejected one.**

Three structures, in order of how well they align with the wedge:

**(a) Per verified asset-event.** A price per asset or per stock position moved
into a signed `VERIFIED` state with evidence attached. Aligns revenue exactly
with the thing being sold. Aligns badly with the buyer's budgeting, which is
annual and fixed.

**(b) Annual platform fee per district, plus a verification-volume component.**
The fee covers the gate, the audit chain and the workspace; the volume component
covers verification throughput. Matches how the buyer budgets. Risks the platform
fee anchoring the conversation to a software comparison — against DHIS2, which is
free.

**(c) Percentage of value under verification.** Charging against the value of
contracts or assets whose claims are being verified. Closest to how inspection
businesses actually price, and the only structure where revenue scales with the
buyer's exposure rather than with our headcount.

**Preferred hypothesis: (b) as the entry structure, with an explicit path to
(c).** Rationale: (b) is buyable inside an existing software budget line, which
is the shortest route to a first contract; (c) is where the economics have to end
up, because it is the only one of the three that does not implicitly cap the
business at the cost of the labour it replaces.

**The anchor, and the honest problem with it.** The natural anchor is the cost of
the physical verification the buyer performs today — an inspector's time, travel
and report. If Tathyon's price is set below that, the sale is arithmetic. But
anchoring to the cost of labour means being valued as a cheaper way to do
inspection, and a cheaper way to do inspection is an inspection business. See §6,
which is the section this document exists for.

**Explicitly unknown:** what a district or a programme office would actually pay,
what the procurement route is, whether verification can be a line item at all,
and whether the buyer's willingness to pay survives the first report that shows
their own past payments were unsupported.

---

## 4. Expansion path

Each stage is entered only when the previous stage's test has passed. The path is
written as a sequence of *earned* rights, not a roadmap of features.

**Stage 0 — prototype (now).** Synthetic data, 62 passing tests, a gate that
refuses, a chain that verifies. No customer.
*Right earned:* to ask for a pilot.

**Stage 1 — one district, one asset class.** Biomedical equipment only, because
the claim is binary (functional / not) and the divergence is enormous, so the
first report is unambiguous. Medicines come second: stock is a continuous
quantity, which makes disputes about partial counts the first argument rather
than the tenth.
*Test:* does verified state change a payment decision at least once?
*Right earned:* to charge.

**Stage 2 — one district, both asset classes, paid.** Add medicine stock. The
gate blocks transfers; the counterfactual quantifies phantom units.
*Test:* does the block rate stay non-zero without break-glass overrides
absorbing it? (`06_AD.md` §5 — an override with an expired obligation is the
metric that decides whether this is real.)
*Right earned:* to sell a second district.

**Stage 3 — multi-district within one state.** The federated/hierarchical
pooling module (`05_WBS.md` G4, currently `PARTIAL`) starts to matter here: a
facility with no history borrows strength from its tier and district rather than
exposing rows. This is also where cross-district comparison becomes politically
live and where the product either gains an owner at state level or dies.
*Test:* does verification cost per asset fall between district one and district
five? (§6.)
*Right earned:* to raise on it.

**Stage 4 — adjacent verified claims, same engine.** Cold chain, consumables,
bed availability, staffing presence. Same loop: claim → evidence → non-custodian
attestation → signed state → gated decision. The engine does not change; the
evidence type does.

**Stage 5 — the relying-party product.** The buyer stops being the operator and
becomes the party who *relies* on the signed state: insurers, grant-makers,
auditors, procurement. This is where the organisational-boundary property becomes
the whole product rather than an architectural detail, and where the business
stops looking like an inspection service.

**The sequencing rule:** never add a claim type before the previous one has
produced a signed state that somebody paid or refused to pay against. Breadth
before a single closed loop is how verification products become dashboards.

---

## 5. Moat

Assessed honestly, including the ones that are not moats.

**Not a moat:**
- Anomaly detection. DHIS2 ships validation rules, outlier detection and a
  data-quality app, free, with a vast installed base.
- Trust gating as a pattern. Data-observability tools have shipped circuit
  breakers since 2022; SAP and Oracle WMS have blocked posting against uncounted
  bins for decades.
- Intermittent-demand forecasting. Croston 1972, Syntetos–Boylan 2005,
  Teunter–Syntetos–Babai 2011. Textbook, and our own pooled MASE loses to the
  naive mean (0.709 vs 0.699).
- The Gemini integration. Currently a mock adapter, and substitutable at a fixed
  seam by any OCR or VLM by design.
- The optimiser. Donor-fraction and donor-cover guards were independently
  implemented by at least one other team in this space with the same parameters.

**Plausible moats, in increasing order of durability:**

1. **The evidence corpus.** Every verification produces a photograph, a nonce, a
   perceptual hash, a signed attestation and a ground-truth outcome. That is
   exactly the labelled data the trust model currently lacks — today it scores
   PR-AUC 0.079 against a base rate of 0.053, which is weak, and it is weak
   because it is trained on weak supervision over synthetic data. Real
   attestations are real labels. Compounding, but slow, and it only compounds if
   verification volume is real.

2. **Being the record a third party relies on.** Once a payer disburses against
   Tathyon's signed state, switching cost is not technical — it is the
   discontinuity in the audit trail. Replayable chains are hard to leave halfway
   through a financial year. This is the strongest moat available and it does not
   exist until stage 5.

3. **Regulatory fit.** GFR 2017 Rule 213(1) already requires annual physical
   verification of fixed assets with the outcome recorded on Form GFR-22. The
   obligation exists; compliance is largely paper. Being the default machine-
   readable form of an existing policy-based duty is durable in a way that no
   feature is. It is also entirely outside our control.

4. **The refusal itself as a reference.** A verification layer's credibility is
   established by the first time it refuses something inconvenient and is upheld.
   That reputation is not copyable by a competitor shipping the same feature —
   but it is also not an asset until it has happened once, in public, to someone
   who did not want it to.

**The honest summary:** at stage 0 there is no moat. There is a design position
and a working prototype. Anyone in this competitive set could implement the gate
in a quarter. What they cannot implement in a quarter is having been relied on.

---

## 6. The core scaling test — and the central business risk

This is the section that matters more than every other section combined.

**The test:**

> The ratio **HUMAN VERIFICATION LABOUR / VERIFICATION REVENUE** must fall as
> scale increases.

Measured concretely: hours of human counting, travelling and attesting, divided
by revenue recognised against verifications, tracked per district and per
quarter. Nothing else is the metric. Not ARR, not districts live, not assets
under verification, not model accuracy.

**Why it decides what kind of company this is:**

If every additional unit of verification revenue requires a proportional
additional unit of human inspection time, then **this is an inspection-services
business.** Inspection-services businesses are real, useful, and often good
businesses — SGS, Bureau Veritas and Intertek are large and durable — but they
are valued as services businesses. **Historically they trade at roughly 2x
revenue.** A software business with the same revenue and falling marginal cost
trades at a multiple several times that.

The difference is not a matter of positioning, branding or what the pitch deck
says. It is entirely determined by whether that ratio falls. A company whose
revenue grows linearly with the number of inspectors it employs is an inspection
company that ships software, regardless of what it calls itself.

**This is the single most important business risk, and it is not a market risk or
a competitive risk — it is structural.** Every other risk in this document (the
first buyer declining, the price anchoring low, a competitor shipping the gate)
is survivable and recoverable. This one determines the category the company is
in, and it is decided by the product's architecture rather than by execution.

**The mechanisms by which the ratio could fall — each a hypothesis:**

1. **Targeting.** The trust model orders the verification queue so that a fixed
   verification budget catches more wrong records. Current measured effect:
   roughly **37% fewer verifications than random** to catch half the wrong
   records, on synthetic data, with a model at PR-AUC 0.079 against a 0.053 base
   rate. That is a genuine effect and a small one. If this improves with real
   attestation labels (§5 moat 1), the ratio falls. If it does not, it does not.
2. **Transcription.** Gemini transcribing what the human wrote removes data
   entry, not counting. Counting is most of the labour. This helps at the margin
   and cannot be the main mechanism.
3. **Reuse of verified state.** One physical count satisfying several relying
   parties — the payer, the auditor, the optimiser, the certificate — divides
   the same labour across more revenue. **This is the mechanism with the highest
   ceiling**, and it is exactly what stage 5 of the expansion path is for.
4. **Sensor evidence.** Where a sensor can substitute for a human — temperature,
   power draw indicating whether a ventilator has ever run — the labour goes to
   zero for that claim type. eVIN already demonstrates this for cold chain. Not
   available for stock counts.
5. **Decay of the need.** If verification changes behaviour, the divergence rate
   falls, and less verification is needed per unit of assurance. This is the most
   attractive mechanism and the hardest to distinguish from the gate quietly
   being routed around.

**The falsification condition, stated in advance:** if, after three districts and
four quarters, verification hours per unit of verification revenue are flat or
rising, the correct conclusion is that this is an inspection business with a
software front end. The honest response is to say so, price and raise
accordingly, and stop describing it as a software company — not to redefine the
metric.

---

## 7. Risks, ranked

| # | Risk | Severity | Current evidence |
|---|---|---|---|
| 1 | Verification labour scales linearly with revenue (§6) | Existential to the category | Unmeasured. No verification has ever been performed by a real user. |
| 2 | The buyer does not want to know. A verification layer that documents past overpayment is a liability to whoever commissioned it. | Existential to the sale | Unmeasured. Zero buyer conversations. |
| 3 | The gate is routed around — break-glass overrides absorb the block rate and obligations are never discharged | Existential to the product | Monitoring designed (`06_AD.md` §5); never observed in the field |
| 4 | Procurement cycles outlast the company | High | Unmeasured |
| 5 | An incumbent (DHIS2 ecosystem, a large LMIS vendor, an ERP) ships the gate | Medium | They have not, but nothing prevents it |
| 6 | The trust model stays weak on real data | Medium | PR-AUC 0.079 vs 0.053 base rate on synthetic data. It beats the staleness baseline at 0.047; it is not strong. |
| 7 | Data-residency or regulatory constraints force on-premise deployment | Low–Medium | Architecturally anticipated (`06_AD.md` §2.1) |

---

## 8. What is explicitly not claimed

- No revenue. No customer. No letter of intent. No pilot. No partnership.
- No accelerator, programme or investor has validated any part of this, and no
  such validation is implied anywhere in this repository.
- No market size is stated. Published audit findings describe the scale of the
  problem; they may not be multiplied by our synthetic detection rates to produce
  a savings figure or a TAM (`10_IMPACT_METHODOLOGY.md`).
- No claim that verification improves health outcomes. The causal chain from
  verified stock to patient outcome is long, and nothing in this repository
  measures any link in it.
- The system has never been used by anyone other than its authors.
