# 08 — Model Cards

Four models ship in this prototype. None is a learned neural network; three are
statistical models with closed-form posteriors and one is a rule ensemble. That
is deliberate. A model whose coefficients an officer can read aloud in a hearing
survives an audit; a model that cannot be read does not.

**All four are currently fitted and evaluated on SYNTHETIC data only
(`07_DATA_PROVENANCE.md`). No metric below is evidence about the real world.**

A structural caveat that applies to all four: `tathyon/eval.py` and the `tests/`
directory are referenced by module docstrings but are **empty / absent in this
repository**. Where a metric is described below as "reported", read: *the code
path that computes it exists in `forecast.evaluate()`*, or, where noted,
*specified but not yet implemented*. Numbers are not quoted here because running
them would produce numbers about our own generator, which would be misleading to
print in a model card.

---

## Model 1 — Weak-supervision rule ensemble (anomaly detection)

**Module:** `tathyon/detect.py`

### Intended use

Order a queue. Given a ledger with no human-verified labels, decide which records
a human should physically check first, under a fixed budget of human minutes. Its
output feeds `VerificationEngine.priority()`, which the module explicitly
describes as a resource-allocation quantity, *not* a probability of wrongness and
*not* a truth claim.

### Why it is not a classifier

The module docstring is blunt: on day one there are zero human-verified labels,
and "pretending otherwise would be the single most dishonest thing in this repo."
So the design is two-tier:

- **Tier 1 — hard violations.** Records that contradict *themselves*. No ground
  truth needed, so these are free labels. Implemented in `hard_violations()`:
  ledger arithmetic (`closing ≠ opening + receipts − issues`, tolerance
  `max(1.0, 2% of reported)`), negative implied consumption, same batch with
  differing expiry dates, issue against an already-expired batch, unit value above
  `ABSURD_UNIT_VALUE = 50,000`, and missing expiry on a dated commodity.
- **Tier 2 — weak signals.** Suspicious but not dispositive, computed in
  `weak_features()` from observed data only.

### Training data

None, in the supervised sense. Tier 1 is deductive. Tier 2 is *specified* as
being calibrated against tier-1 labels so its output is a probability rather than
a vibe; **that calibration step is not implemented in the repository** — the
current code emits features and reason codes, and no fitted calibration model
exists. This is the largest gap between the design and the code.

### Features (`detect.FEATURES`, eight columns)

`f_entry_lag` (log1p of days between event and data entry), `f_round_number`
(reported stock divisible by 50 — the classic fabrication tell), `f_qty_z`
(28-day rolling z-score of reported stock), `f_cons_per_opd_z` (consumption per
OPD against the series' own mean), `f_smoothness` (inverse of 14-day rolling
std of issues — real consumption is bursty, fabricated is smooth),
`f_expired_but_stocked`, `f_stale`, `f_days_since_verified`.

Every quantity feature is **facility-relative on purpose**. The code comment
states the reason: normalising globally is exactly how a model learns "big
hospital" instead of "wrong".

### Metrics reported

Specified: precision@k and recall on tier-1 hard violations, and the named
adversarial check that the ranking does not simply recover facility size or
remoteness. **Not implemented** — the detectors for that failure mode are said to
live in `eval.py`, which does not exist.

### Baselines

The natural baselines are: rank by absolute reported quantity; rank by facility
size; rank at random. None is implemented.

### Limitations

- The eight weak features are hand-chosen with hand-chosen thresholds
  (`f_qty_z > 3`, `entry_lag_days > 20`, `STALE_DAYS = 30`). None is tuned.
- `ABSURD_UNIT_VALUE = 50,000` is a single global constant across all SKUs. A
  genuinely expensive item would trip it.
- `f_round_number` on a `% 50 == 0` test will fire on legitimately round pack
  sizes.
- Tier-1 arithmetic depends on `receipt` and `issued` being recorded at all. In a
  ledger where those are simply absent, tier 1 degrades to silence.

### Failure modes

The one the module names and actively defends against: learning **"unusual"**
(large hospital, new SKU, remote PHC with lumpy supply) instead of **"wrong"**. A
second, unnamed one: a facility that fabricates *consistently* — smooth, arithmetically
consistent, promptly entered fiction — trips nothing in tier 1 and little in tier 2.
Consistent fraud is invisible to this ensemble by construction. Only physical
attestation catches it.

### Must NOT be used for

Disciplinary action, procurement blacklisting, individual performance assessment,
or any statement that a specific facility or officer *is* wrong. It flags records
for a human to look at. A flag is a question, not a finding.

---

## Model 2 — TSB / Croston-SBA intermittent demand model

**Module:** `tathyon/forecast.py`

### Intended use

Forecast the consumption rate and the lead-time demand *distribution* for a
(facility, SKU) series, to drive stockout early warning and to size the shortfall
passed to the optimizer.

### Why this family

PHC drug demand is intermittent — many zero days. That is precisely the regime
Croston-family methods were built for, and a regime where a deep sequence model
has nothing to offer on a few hundred points. **TSB (Teunter–Syntetos–Babai) is
the default over Croston** for a stated reason: Croston's estimate is biased and
never decays, so a discontinued or substituted drug keeps forecasting demand
forever. TSB updates the demand *probability* every period and fixes that.
Croston with the Syntetos–Boylan bias correction (`fit_croston_sba`) is retained
as a comparison arm, described in the code as "the minimum acceptable variant".

### Training data

`true_demand` per (facility, SKU) series from the synthetic corpus — 240 daily
points per series, of which the training window is everything up to the cut.
Series with fewer than 40 training points or fewer than 7 test points are skipped.

### Features

The demand series only. `fit_tsb(y, alpha=0.10, beta=0.05)` maintains two
exponentially-smoothed states: `demand_size` (expected size given demand occurs)
and `demand_prob`. `rate = size × prob`. The smoothing constants are
**unfitted defaults**.

### Output

Not a point. `TSBFit.lead_time_distribution(horizon)` returns a compound
Bernoulli × size distribution over the lead time, moment-matched to a Gamma, which
yields `P(demand > stock)` — the quantity the hazard model and the safety-stock
calculation actually need.

### Metrics reported

**MASE** (scale-free and defined on zero-demand days; denominator is the in-sample
naive-1 MAE), **pinball loss at τ = 0.90**, and **empirical coverage at q90**.
`forecast.evaluate()` computes all three, reported as medians, and splits them
three ways: all series, seen facilities, and a disjoint holdout facility set.

**MAPE is deliberately not reported.** It is undefined on zeros and is, in the
module's words, the first thing a sharp reviewer attacks.

### Baselines

Three, all computed in the same evaluation: `naive_rate` (training mean),
`seasonal_naive` (mean of the last 7 days), and Croston-SBA. MASE < 1.0 beats the
naive-1 benchmark.

### Evaluation split

A random row split would leak a facility's own ledger across train and test and
inflate everything. So: **temporal holdout** (last 30 days) **plus a disjoint
25% facility holdout** to test generalisation to unseen sites. This is the
correct protocol and it is implemented.

### Limitations

- Fitted on synthetic demand generated by a Bernoulli×Poisson process. TSB is
  close to the correct model family for that process, so **the evaluation is
  partly self-fulfilling**. Good MASE here is weak evidence about real PHC series.
- The generator's ×1.6 monsoon bump over days 150–200 is the only seasonality
  present, and TSB has no seasonal component — so the seasonal-naive baseline is
  the informative comparison, not the naive one.
- Gamma moment-matching of a compound Bernoulli is an approximation that degrades
  for very low demand probability.
- `alpha` and `beta` are global constants, not per-series fitted.

### Failure modes

A step change in demand (new programme, outbreak, facility catchment change) is
tracked only as fast as α and β allow. A newly stocked SKU with no history gets a
near-zero rate and will under-warn.

### Must NOT be used for

Procurement quantity decisions without a human reviewing the series, national
demand aggregation, or any epidemiological inference. It forecasts a facility's
drawdown of a SKU; it says nothing about disease.

---

## Model 3 — Gamma-Poisson freshness posterior

**Module:** `tathyon/verify.py` — `ConsumptionPrior`, `posterior_unobserved()`,
`quantity_posterior()`

### Intended use

Answer the question a verification system must answer and a TTL cannot: *given
that a human physically counted 400 units eleven days ago, how much is there
now, and how confident are we of a floor?*

### Why a posterior and not a TTL or a floor

The docstring records this as a reversal of an earlier draft. The earlier design
subtracted "p95 of unobserved consumption" from the anchor. That is wrong —
**quantiles do not add across time windows.** Poisson rates do add, so unobserved
consumption is modelled as Gamma-Poisson, giving a Negative Binomial predictive
in closed form:

```
λ ~ Gamma(a₀, b₀);  x | λ ~ Poisson(λt)
⇒ λ | data ~ Gamma(a₀ + count, b₀ + days)
⇒ U_gap    ~ NegBin(n = a_post, p = b_post / (b_post + gap))
Q_t = anchor − observed_deltas − U_gap
```

The function returns both `point` (using the posterior mean) and `q_alpha` (using
`ppf(alpha)`). **A gate answers against `q_alpha`, never the point estimate and
never the reported figure.**

### The α convention

α is supplied by the **consumer**, because the consumer owns the risk:
`α = c_over / (c_over + c_under)`. A policy-based certificate that over-claims stock
is catastrophic, so α ≈ 0.98. An optimizer that can re-plan is not, so α ≈ 0.6.
`verify.POLICY` sets defaults of 0.60 for medicine and 0.90 for equipment. A
tenant may tighten; a consumer may tighten further but never loosen below the
tenant floor.

### Training data

`ConsumptionPrior.fit()` takes an array of daily rates and does **method of
moments on the Gamma** (`b = m/v`, `a = mb`), falling back to a weak default
prior `Gamma(2.0, 1.0)` when fewer than three finite observations or a
non-positive mean. The prior is documented as hierarchical — pooled over
SKU × facility-tier × district, scaled by OPD — but **the pooling is not
implemented**; `fit()` currently takes whatever array it is handed.

### Cold start

Stated as a by-construction property, and it is: a series with no history gets a
correctly wide posterior, `q_alpha` collapses toward zero, and the system says
"verify before relying on this". That is the right answer, not a bug — an
unverifiable quantity should be unusable.

### Metrics reported

None computed in the repository. The metrics this model requires are
**calibration** ones: empirical coverage of `q_alpha` (does the true quantity
exceed `q_alpha` at the promised rate?) and the realised over/under-claim cost.
Neither is implemented.

### Baselines

The baseline this model must beat is the one it replaces: a fixed TTL ("verified
counts expire after 30 days"). Not implemented as a comparison.

### Limitations

- Assumes consumption is Poisson with a stable rate over the gap. Real drawdown
  is bursty and its rate shifts with OPD; the Gamma prior absorbs some but not
  all of that.
- The model accounts for **consumption** during the gap. It does not model theft,
  breakage, unrecorded transfers, or expiry occurring inside the gap — all of
  which reduce usable stock and none of which are Poisson consumption.
- `observed_days` defaults to 28.0 and `observed_count` to 0.0 in
  `state_for()`. Called without those arguments, the posterior is dominated by
  the weak default prior.

### Must NOT be used for

A policy-based stock certificate at default α. The whole point of the α convention
is that a certificate needs a much higher α than the 0.60 medicine default, and
nothing in the code enforces that a certificate-generating consumer asks for it.

---

## Model 4 — Discrete-time equipment hazard

**Module:** `tathyon/verify.py` — `survival()`

### Intended use

Estimate `P(still functional | verified functional at t₀)` for a single asset, to
decide whether an asset last seen working 14 months ago can be counted on today.

### Why a hazard and not a TTL

Stated in the docstring: a TTL throws away the failure data we actually have. A
discrete-time hazard with a logistic link handles interval censoring — which is
the real data shape, since assets are checked at irregular intervals — and can be
trained on maintenance tickets when they exist.

### Form

```
z = −3.4 + 0.011·age_months + 0.55·(no AMC) + 0.020·last_service_months
h = σ(z)                                     # monthly hazard
S = (1 − h)^months_since_verified
```

### Training data

**None. The four coefficients are hand-seeded, not fitted.** The docstring says
they are "seeded from BEMMP's published 13–34% dysfunction band" and "re-fit from
data in `eval.py`" — `eval.py` does not exist. Treat these numbers as a prior
that produces the right order of magnitude, not as estimates.

### Features

Four: months since last verification, asset age in months, whether an AMC
(annual maintenance contract) is active, and months since last service. All four
are available in the synthetic equipment table.

### Metrics reported

None. The correct metrics for a survival model — time-dependent AUC, Brier score
at fixed horizons, calibration-in-the-large against observed failures — require
longitudinal failure data that does not exist here.

### Baselines

GFR 2017 Rule 213(1)'s annual physical verification is the de-facto baseline
policy: "an asset verified within the last 12 months counts". The hazard model's
claim is that it allocates verification effort better than a uniform annual sweep.
**That claim is untested.**

### Limitations

- It is a smooth function of four covariates. Real equipment failure in PHCs is
  frequently non-gradual: a power surge, a missing consumable, a departed trained
  operator, an expired warranty. None is in the feature set.
- The strongest real-world predictor — whether anyone at the facility is trained
  to operate the item — is absent because it is not in any register.
- `survival()` is short-circuited to 0.0 whenever an attestation reports a
  non-functional status, which is correct behaviour but means the model only ever
  runs on the optimistic branch.

### Failure modes

Systematic over-optimism for asset classes needing consumables or utilities (PSA
plants need power and a trained operator; an autoclave needs water). The model
will report high survival for an asset that has been unusable for a year for a
reason it cannot see.

### Must NOT be used for

Deciding not to physically verify an asset. Its legitimate use is ranking *which*
assets to verify first under a fixed budget. GFR 2017 Rule 213(1) requires annual
physical verification regardless of what any model says, and a model output is
not a substitute for a policy-based check.

---

## Cross-cutting: what the AI does not do

**The AI never counts.** Vision-language models score roughly 0.23–0.58 on object
counting tasks. Tathyon therefore assigns counting to the human and transcription
to the model: the human counts the shelf and writes the number, the model
transcribes what the human wrote, and `Attestation.extraction_agreement` records
whether the two agree. A `delta_pct > 5` disagreement moves the record to
`CONFLICTED` rather than resolving it in the model's favour. There is no model in
this repository that estimates a quantity from an image, and adding one would
break the design.
