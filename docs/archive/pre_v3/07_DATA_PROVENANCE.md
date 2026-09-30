# 07 — Data Provenance

## The one sentence that governs this document

**Every row of data in this repository is SYNTHETIC. Not one record describes a
real facility, a real consignment, a real asset, or a real person.**

What is *not* fabricated is the **taxonomy of failure modes and their approximate
incidence**, which is taken from named paragraphs of published Comptroller &
Auditor General of India performance audits and from NHSRC's biomedical equipment
programme reporting. Those documents are real, public, and cited precisely below.
The generator reproduces the *shape* of the failures those audits found; it does
not reproduce their data, which is not published at record level and which we do
not hold.

Any statement of the form "Tathyon detected X% of errors" refers to errors that
Tathyon's own generator injected. It is a statement about internal consistency of
the pipeline, not a finding about Indian public health supply chains.

## Provenance labels

The label vocabulary is enforced in code, not in prose: `tathyon/schema.py`
defines `Provenance` as a closed enum (`REAL_PUBLIC`, `REAL_USER_PROVIDED`,
`SYNTHETIC`, `SIMULATED`, `DERIVED`, `HYBRID`) and every `Claim`, `Evidence` and
`VerifiedState` object carries one. The enum docstring states the rule: *never
silently mix these; the UI must surface SYNTHETIC.*

| Label | Meaning in this project |
|---|---|
| `REAL_PUBLIC` | A published document or figure, cited to paragraph level. Used for the audit anchors and policy-based rules, never for records. |
| `SYNTHETIC` | Fabricated by `tathyon/generator.py` from a seeded RNG. All four data tables. |
| `DERIVED` | Computed from other tables in this repo. Labels, features, verified states, decisions. |
| `SIMULATED`, `REAL_USER_PROVIDED`, `HYBRID` | Defined in the schema, not used by any dataset currently in the repo. |

---

## Dataset inventory

All four tables are produced by a single call to `generator.generate()` with
defaults `n_facilities=20, n_days=240, n_assets_per_facility=6, seed=7`, and
written to `data/*.parquet`. The seed is fixed, so the corpus is reproducible
byte-for-byte.

### 1. `data/facilities.parquet` — PROVENANCE: SYNTHETIC

20 rows, 7 columns. Source: `generator.generate()`, facility loop.

| Field | Content | Real or fabricated |
|---|---|---|
| `facility_id` | `FAC000`…`FAC019` | Fabricated identifier |
| `name` | `PHC A0`, `CHC B1`, … | Fabricated. Not a real facility name. |
| `tier` | `PHC` / `CHC` / `DH`, drawn at p = 0.6 / 0.3 / 0.1 | The **tier taxonomy** is real Indian public-health structure; the **mix** is an assumption of ours, not a measured national ratio. |
| `district` | `D0`…`D3` | Fabricated. Four abstract districts, no geography. |
| `opd_per_day` | 60 / 180 / 500 by tier, × U(0.7, 1.3) | Plausible order of magnitude, **not sourced**. Treat as an assumption. |
| `x`, `y` | U(0, 100) coordinates | Fabricated. A synthetic plane, not a map. Travel time is derived from these in `optimize.travel_hours()` with a 1.35 road factor and 38 km/h — both assumptions. |

### 2. `data/truth.parquet` — PROVENANCE: SYNTHETIC (ground truth)

48,000 rows (20 facilities × 10 SKUs × 240 days), 10 columns.

This is the **physical process**: what is actually on the shelf. It exists only
because this is a simulation. In deployment there is no truth table — that is the
entire reason the product exists.

Fields: `date`, `facility_id`, `sku`, `true_stock`, `true_usable`, `true_demand`,
`batch`, `expiry`, `receipt`, `issued`.

Generative assumptions, all fabricated: intermittent demand as a Bernoulli
occurrence (p = 0.45 for emergency/respiratory SKUs, 0.82 otherwise) times a
Poisson size; a ×1.6 seasonal multiplier on antibiotics and essentials over days
150–200 standing in for a monsoon/vector-borne window; replenishment triggered
every 30 days when stock falls below 12 days of cover. None of these parameters
is fitted to observed Indian data. They are chosen to produce the intermittency
regime that motivates the TSB/Croston model family.

### 3. `data/observed.parquet` — PROVENANCE: SYNTHETIC ledger + DERIVED labels

48,000 rows, 22 columns. This is **what the system of record says**, i.e. the
e-Aushadhi-analogue ledger, after pathologies have been injected.

Ledger fields (SYNTHETIC): `date`, `facility_id`, `sku`, `sku_name`, `sku_class`,
`reported_stock`, `issued`, `receipt`, `batch`, `obs_expiry`, `unit_value`,
`entered_at`, `entry_lag_days`, `physically_verified`, `tier`, `district`, `opd`.

Label fields (DERIVED): `rel_err` and `label_wrong`, computed by merging
`observed` against `truth` and flagging a row as materially wrong when
`|reported − true| / true > 0.15` **or** `true_usable < reported × 0.7`. The
0.15 threshold is our definition of "would change a replenishment decision". It
is an assumption, and every reported detection metric is conditional on it.

Joined truth fields (SYNTHETIC, **leakage hazard**): the merge in
`generator.generate()` leaves `true_stock`, `true_usable` and `true_demand`
physically present as columns on `observed`. They are the label's ingredients and
must never be used as model features. `detect.FEATURES` is an explicit allow-list
of eight `f_`-prefixed columns and contains none of them, which is the mechanism
that currently prevents leakage. The generator docstring states that
`tests/test_no_leakage.py` enforces this; **that test file does not exist in the
repository at the time of writing**, so the guarantee is by construction and code
review only, not by an executing test. See `14_LIMITATIONS.md`.

The SKU catalogue (`AMX250` Amoxicillin 250 mg, `PCM500` Paracetamol 500 mg,
`ORS01` ORS sachet, `IFA100` Iron Folic Acid, `METRO4` Metronidazole 400 mg,
`CEFX500` Cefixime 500 mg, `SALB` Salbutamol inhaler, `ATNL50` Atenolol 50 mg,
`MTFM500` Metformin 500 mg, `INJDEX` Inj Dexamethasone) uses **real drug names**
that plausibly appear on an Indian essential-medicines list. The stock numbers,
batch numbers, expiry dates and unit values attached to them are fabricated. Unit
values come from `SKU_UNIT_VALUE`, a synthetic arithmetic series (`1.5 + 0.9i`),
and are **not** market prices.

### 4. `data/equipment.parquet` — PROVENANCE: SYNTHETIC

120 rows (20 facilities × 6 assets), 14 columns.

Asset catalogue: ICU Ventilator, PSA Oxygen Plant, X-Ray Unit 300 mA, Ice-Lined
Refrigerator, Autoclave, Dialysis Machine. Real equipment categories; the
`value_inr` figures (₹1.8 lakh to ₹45 lakh) are **order-of-magnitude assumptions,
not procured prices**.

Two fields encode the product thesis directly and deserve naming:

- `register_status` is hard-coded to `FUNCTIONAL` for every asset. The generator
  comment states why: the register entry is written at procurement and never
  again. This is a modelling choice representing the failure mode, not a measured
  statistic.
- `vendor_reported_uptime` is drawn U(0.962, 0.998) against an `sla_target` of
  0.95 — i.e. the dashboard always passes. The generator cites the BEMMP
  Arunachal mid-term evaluation, where a dashboard showed >99% against a 95% SLA
  while evaluators physically found equipment absent, idle in stores, or moved to
  other facilities. The *pattern* is from that evaluation; the *numbers here* are
  drawn from a uniform distribution we chose.

`true_status` is assigned from `EQUIPMENT_ANCHORS`; `label_wrong` is DERIVED as
`true_status != FUNCTIONAL`.

---

## Injected failure modes and their CAG anchors

These are the exact incidence rates in `generator.CAG_ANCHORS` and
`generator.EQUIPMENT_ANCHORS`. Each is applied per (facility, SKU) series as a
Bernoulli draw, then realised per day with a further inner probability where
noted. **The rate is anchored; the realisation is fabricated.**

| Generator key | Rate | Anchor (all REAL_PUBLIC) | What the source actually says | How we translated it |
|---|---|---|---|---|
| `batch_expiry_conflict` | 0.071 | CAG Punjab Report No. 4 of 2019, **Para 2.1.7.2(ii)** | 952 of 13,322 records carried the same batch number but different expiry dates | 952/13,322 = 7.1%, applied as the per-series probability that the series is afflicted; afflicted rows then get a conflicting expiry with inner p = 0.25 |
| `fefo_violation` | 0.036 | CAG Punjab Report No. 4 of 2019, **Para 2.1.7.6(vi)** | 24,164 of 6,74,253 instances where FEFO was not followed | 24,164/674,253 = 3.6%. Present as a declared anchor; the generator's day loop does not currently implement a distinct FEFO-violation injection — the related detector `v_issue_against_expired` fires on issues against already-expired batches produced by the expiry pathway. Flagged as a gap, not a claim. |
| `retroactive_entry` | 0.045 | CAG Punjab Report No. 4 of 2019, **Para 2.1.7.6(vii)** | 6,120 offline indents entered into the system after delays of up to 947 days | We adopt 947 days as the upper bound of `lag_days ~ U(20, 947)`; the 4.5% series rate is **our assumption**, since the paragraph gives a count and a maximum delay, not a base rate |
| `absurd_unit_value` | 0.004 | CAG Maharashtra Report No. 4, IT Audit of e-Aushadhi, **Para 2.4.8.12** | A unit rate of **₹11,10,111.20** recorded for an Amoxycillin 250 mg capsule, which the audit attributes to **the software not having been properly tested** | The literal value ₹1,110,111.20 is written into afflicted rows. **This is a data-validation failure, not a purchase price.** No one paid ₹11 lakh for a capsule. Anyone reading this as a procurement fraud figure has misread the source. The 0.4% rate is our assumption. |
| `issued_never_received` | 0.030 | CAG Maharashtra Report No. 4, **Para 2.4.8.9** | 85 drugs valuing **₹1.36 crore** shown as issued but not received by PHCs | Realised as `reported_stock += issued` with inner p = 0.08 — stock the ledger believes arrived and which is not there. The 3% series rate is our assumption. |
| `verification_skipped` | 0.60 | CAG Punjab Report No. 4 of 2019, **Para 2.1.8.4** | Shortfalls of **38 to 90 per cent** in carrying out monthly stock verification; physical verification entries never captured in the system | 0.60 sits inside the published 38–90% band. Drives `physically_verified`, which drives `days_since_verified`, the single most load-bearing feature in the system. |
| `expired_counted_live` | 0.055 | CAG Telangana Report No. 4 of 2024, **Para 4.5.3** | Expired drugs valuing **₹390.26 crore** held in stock | Afflicted series continue to report expired quantity as live stock, so `true_usable < reported_stock`. The 5.5% rate is our assumption; the paragraph gives a value, not an incidence. |

### Equipment anchors

| Key | Rate | Anchor | Notes |
|---|---|---|---|
| `not_commissioned` | 0.24 | CAG West Bengal Report No. 3 of 2024, **Para 4.5** — 43 of 75 PSA oxygen plants not commissioned or not functional (57%) | The source's 57% is a **combined** not-commissioned-or-not-functional figure for PSA plants specifically. We split it into 24% not commissioned + 19% non-functional and apply it across **all** asset classes. Both the split and the generalisation are our assumptions and are not supported by the paragraph. |
| `non_functional` | 0.19 | as above, plus NHSRC BEMMP | NHSRC's Biomedical Equipment Management & Maintenance Programme reports **7,56,750 equipment items across 29,115 facilities valued at approximately ₹4,564 crore**, with **13–34% dysfunctional** depending on state. Our 19% sits inside that band. |
| `not_present` | 0.06 | No numeric anchor | **Our assumption.** Represents assets on the register that cannot be found. Flagged as unsourced. |

### policy-based rules referenced (REAL_PUBLIC, not data)

- **GFR 2017 Rule 213(1)** — annual physical verification of all fixed assets,
  with the outcome recorded in the register. This is the legal basis for the
  equipment attestation cadence; `verify.POLICY` sets
  `equipment.max_attestation_age_days = 180`, i.e. **twice** the policy-based
  minimum frequency. That is a product choice, not a legal requirement.
- **GFR 2017 Rule 211** — item-wise lists enabling verification of actual
  balances against book balances; fixed assets on **Form GFR-22**. This is the
  rule Tathyon's claim/attestation pair is designed to satisfy: the claim is the
  book balance, the attestation is the actual balance, and the event log is the
  record of the comparison.

---

## Anti-leakage discipline

The generator docstring names this as "the part most synthetic pipelines get
wrong", and the design is sound:

1. `truth` and `observed` are built as **separate tables** in the same loop.
2. The label is produced by **comparing** them, never by reading the pathology
   flags.
3. The per-series pathology dictionary `path` is a local variable and is **never
   written to any output table**. A classifier therefore cannot recover the label
   by reading an injected-flag column, because no such column is emitted.
4. Features come from `observed` only, via the explicit `detect.FEATURES`
   allow-list.

The residual risk is the one named above: `true_*` columns survive the merge onto
`observed`. Nothing currently consumes them as features, and the named enforcing
test is absent.

## Reproduction

```
python -m tathyon.generator      # writes data/*.parquet, seed=7
```

The script prints the row counts and the realised `label_wrong` rate for both the
ledger and the asset register. Those printed rates are properties of our injection
parameters. They are not estimates of anything in the world.
