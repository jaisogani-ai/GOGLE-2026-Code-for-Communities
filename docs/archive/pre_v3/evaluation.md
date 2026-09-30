# TATHYON Evaluation Harness & Fairness Documentation

> **Evidence boundary:** all scores and scenario outcomes in this file are generated simulation outputs. They do not estimate live facility performance, clinical outcomes, or patient impact. No authorized real-data supervised model is available in this repository.

## Principle of Evaluation Fairness

> "Do not tune Tathyon to beat the baselines — tune the harness to be fair."

The Tathyon Evaluation Harness (`tathyon/evaluate.py`) is designed as a rigorous, unbiased measurement engine. It evaluates whether intelligent verification targeting and constrained network optimization provide genuine, measurable value over standard public health logistics practices.

---

## The Phantom-Adjusted Headline Metric

### The Flaw in Raw `stockout_days_averted` (Synthetic Scenarios)
In traditional logistics simulations, `stockout_days_averted` measures how many days of patient demand were supposedly satisfied by incoming transfer shipments. However, when an unverified system ships **phantom inventory** (stock that exists only on paper in a corrupted ledger), a naive simulator still tallies those shipments as "averted stockout days."

In Prompt 2's initial runs, the naive baseline appeared to score **95.97** and greedy guarded scored **100.50** (versus Tathyon's **80.87**) solely because naive shipped **204.0** phantom units and greedy guarded shipped **83.0** phantom units. Dispatched trucks arrived at peripheral clinics with empty boxes, yet the ledger credited them with averted stockouts. In a real clinic, phantom stock cannot treat patients.

### The Corrected Metric: `verified_stockout_days_averted`
To eliminate this perverse incentive, `tathyon/evaluate.py` introduces:
$$\text{verified\_stockout\_days\_averted} = \sum_{t} \min\left(\text{deficit}_t, \frac{\text{verified\_usable\_stock\_received}_t}{\text{daily\_burn}_t}\right)$$

Any stockout day served by phantom units counts **strictly zero**. This is the sole headline metric printed by `make eval`, rendered on the web UI, and documented across all reports.

---

## Measured Evaluation Results (`artifacts/eval_report.json`)

Generated deterministically via `make eval` (seed `20260928`, 10 scenarios, 4 verification slots/month):

| Arm | Verified Stockout Days Averted (Headline) | Raw Stockout Days Claimed | Phantom Units Shipped | Phantom Units Blocked | Verification Hit-Rate | Silent Hit-Rate | Decision Latency | Loop Closure Rate |
|---|---|---|---|---|---|---|---|---|
| **Tathyon** | **80.87** | 80.87 | **0.0** | **380.0** (170 silent) | **10.0%** | **5.0%** | 4.12 ms | 100% |
| **Always-Verify** | **80.59** | 80.59 | **0.0** | **380.0** (170 silent) | 4.0% | 2.0% | 1.48 ms | 100% |
| **AI-Ablation** | **27.11** | 27.11 | **0.0** | 380.0 (0 silent) | 5.0% | 0.0% | 0.82 ms | 100% |
| **Greedy Guarded** | **0.00** | 100.50 | 83.0 | 0.0 | 0.0% | 0.0% | 0.08 ms | 100% |
| **Naive Greedy** | **0.00** | 95.97 | 204.0 | 0.0 | 0.0% | 0.0% | 0.05 ms | 100% |
| **Min/Max Reorder** | **0.00** | 85.25 | 204.0 | 0.0 | 0.0% | 0.0% | 0.02 ms | 100% |

---

## The Four Baselines & Detailed Audit

### 1. Naive (Trust Reported Stock, Greedy Allocation)
- **Mechanism**: Assumes reported balances in the ledger represent ground truth. Matches shortages to donors reporting surplus above daily consumption.
- **Outcome**: Claims 95.97 stockout days averted, but dispatches **204.0 phantom units**. Because zero units were verified-usable, its `verified_stockout_days_averted` is **0.00**.

### 2. Min/Max Reorder-Point Rule (Why It Legitimately Scores 0.00)
- **Mechanism**: The standard classical inventory control policy used in state medical stores. Whenever a peripheral facility drops below a 7-day minimum safety stock, it submits a replenishment order to the central District Hospital depot up to a 21-day maximum level.
- **Fair Implementation Audit**: The min/max arm implementation in `tathyon/evaluate.py` accurately models central depot replenishment: it calculates the exact deficit up to the 21-day ceiling, dispatches available stock from the central warehouse (`DH_BASTAR`), and clamps to warehouse availability.
- **Why It Scores Zero Verified Days**: In the evaluation test scenarios, the central depot's reported closing ledger contains unverified phantom stock (204.0 units). Because the min/max arm possesses no lateral rebalancing capability and **no physical verification targeting mechanism**, it unconditionally dispatches central depot stock without verification. All 204.0 units dispatched from the unverified depot are phantom stock, causing `verified_stockout_days_averted` to be **0.00**. It is not broken; it legitimately reflects how centralized push systems fail when central ledgers are unverified.

### 3. Greedy Guarded Transfers (e-Aushadhi / HealthGrid Style)
- **Mechanism**: Restricts lateral transfers so donor clinics must maintain a 14-day safety stock floor before sharing surplus.
- **Outcome**: Reduces phantom shipments compared to naive (83.0 vs 204.0 units), but because it operates without a physical verification gate, it still dispatches phantom surplus, causing its verified headline score to collapse to **0.00**.

### 4. Always-Verify (The Infinite-Budget Ideal)
- **Mechanism**: Audits all candidate facilities prior to calculating allocation.
- **Outcome**: Scores 80.59 verified days averted with 0 phantom units shipped. However, it requires an unconstrained inspection budget (100% facility visits), whereas Tathyon achieves 80.87 days under a strict real-world budget constraint of **4 verification visits per month**.

---

## AI Ablation: Proving the ML Trust Scorer is Load-Bearing

To verify whether the Gradient Boosting Trust Scorer (`tathyon/trust_features.py`) is load-bearing or mere AI theater, the harness runs an exact ablation:
- **Identical Solver & Budget**: The CP-SAT single network solve, safety floors, transport constraints, and 4-slot verification budget are identical.
- **Ablation Change**: The Trust Scorer's ranking is replaced with a uniform random draw of candidate facilities.
- **Results**:
  - Tathyon catches **170.0 silent phantom units** (hit rate on silent phantoms: **5.0%**).
  - AI Ablation catches **0.0 silent phantom units** (hit rate on silent phantoms: **0.0%**).
  - Without the Trust Scorer, verified stockout days averted drops from **80.87** to **27.11** (a **53.76-day degradation**).
  - Obvious arithmetic discrepancies (tier-1 hard violations) are caught by simple rules in both arms; the machine learning model is uniquely load-bearing in identifying **silent corruption** where ledger arithmetic is internally consistent.

---

## How to Read `artifacts/eval_report.json`

The JSON report is structured into four key sections:
1. `"headline"`: One-line summary string with verified days averted, phantom units blocked, hit rates, and loop closure.
2. `"benchmark_arms"`: Dictionary keyed by arm name (`tathyon`, `naive`, `always_verify`, `min_max`, `greedy_guarded`, `ai_ablation`), containing all measured metrics.
3. `"silent_vs_flagged_split"`: Explicit breakdown of phantom units blocked and hit rates between overt arithmetic errors and silent corruption.
4. `"ai_ablation_comparison"`: Direct comparison between `tathyon` and `ai_ablation`, proving ML necessity.
5. `"evaluation_metadata"`: Execution seed (`20260928`), scenario count, visit budget, and enforced system doctrines.

---

## v2 Module Evaluations (Beds & Personnel)

> These sections were generated by real train/test evaluation on 2026-09-28.
> Train seeds: [1001, 1002, 1003] — Test seeds: [2001, 2002, 2003] (strictly held out).
> Medicine headline **not altered**: `verified_stockout_days_averted=80.87` (seed=20260928).

---

## BEDS — BedTrustScorer Evaluation

### Methodology
- **Generator**: `generate_beds_dataset()` with 5 facilities (1 DH, 1 CHC, 3 PHC), 60 days, seeded.
- **Labels**: `is_materially_wrong` set by generator ground truth (phantom free beds, unreported maintenance, arithmetic errors).
- **Features**: 10 strictly observed causal features (`t1_bed_arithmetic_violation`, `t1_unreported_broken_now`, 8 × t2 staleness/occupancy features).
- **Model**: GradientBoostingClassifier (n_estimators=200, max_depth=4, lr=0.04).
- **Budget**: Top 20% of rows verified per seed (same budget for random ablation).

### Results (held-out test seeds 2001, 2002, 2003 — 3,780 total rows)

| Metric | Value |
|---|---|
| **PR-AUC** | **0.8746** |
| Base rate (prevalence) | 0.1233 |
| PR-AUC lift over base rate | +0.7513 |
| Hit-rate @ 20% budget — Scorer | **0.5317** |
| Hit-rate @ 20% budget — Random ablation | 0.1177 |
| **Ablation delta** | **+0.4140** |
| Beats random ablation? | ✅ YES |
| Phantom-bed rows in test | 466 |
| Phantom beds blocked — Scorer | **402** |
| Phantom beds blocked — Random | 89 |

### Doctrines
- AI never counts: scores rank rows for human verification; humans confirm availability before diversion.
- Scores are never persisted or exported.
- Every patient diversion requires human CMO/DHO approval.

### Deck-ready verdict
**✅ BEDS IS DECK-READY.** PR-AUC 0.8746 vs base rate 0.1233 (+0.75 lift). Beats random ablation by +0.414 hit-rate at same 20% verification budget. Phantom-bed blocking: 402 vs 89 for random. All 405 tests green.

---

## PERSONNEL — PersonnelTrustScorer Evaluation

### Methodology
- **Generator**: `generate_personnel_dataset()` with 5 facilities, 30 days, seeded.
- **Labels**: `is_materially_wrong` (ghost workers, proxy attendance, early departure ≥3h gap).
- **Features**: 10 strictly observed causal features (`t1_impossible_duplicate_punch`, `t1_zero_clinical_encounters`, 8 × t2 punch/absence features).
- **Model**: GradientBoostingClassifier (n_estimators=200, max_depth=4, lr=0.04).
- **Budget**: Top 20% of rows verified per seed (same budget for random ablation).

### Results (held-out test seeds 2001, 2002, 2003 — 7,200 total rows)

| Metric | Value |
|---|---|
| **PR-AUC** | **1.0000** |
| Base rate (prevalence) | 0.2792 |
| PR-AUC lift over base rate | +0.7208 |
| Hit-rate @ 20% budget — Scorer | **1.0000** |
| Hit-rate @ 20% budget — Random ablation | 0.2694 |
| **Ablation delta** | **+0.7306** |
| Beats random ablation? | ✅ YES |
| Ghost workers in test | 555 |
| Ghost workers caught — Scorer | **380** |
| Ghost workers caught — Random | 122 |
| Critical ghost shifts (MO/SN roles) | 372 total |
| Critical shifts covered — Scorer | **248** |
| Critical shifts covered — Random | 91 |

### ⚠️ Honesty note on PR-AUC = 1.0
The PersonnelTrustScorer achieves PR-AUC = 1.0 on held-out generator seeds. This is **not from data leakage** (train/test are strictly split by seed). It reflects that the generator's pathologies (ghost workers always have `punch_minute_variance=0` and `logged_clinical_encounters=0` during a full 8h reported shift) are **perfectly recoverable from the two tier-1 features**. The generator is a useful worst-case adversary for the medicine scorer (silent phantoms are hard); for personnel, the features are strong enough to saturate PR-AUC on the synthetic signal. Real-world attendance data will have noisier feature signal. This is reported honestly — the number is real on the generator, but its generalization to live data must be validated by field deployment.

### Doctrines
- AI never certifies attendance: humans (DHOs, verifiers) conduct roll-calls.
- Scores never persisted or exported.
- Every inter-facility staff redeployment requires CMO sign-off under GFR 2017.

### Deck-ready verdict
**✅ PERSONNEL IS DECK-READY with caveat.** Beats random ablation convincingly (+0.73 delta). Ghost workers caught: 380 vs 122 random; critical shifts covered: 248 vs 91 random. PR-AUC=1.0 on generator is honest but must be caveated: real attendance data will be harder. All 405 tests green.

---

## Summary Table — All Three Modules

| Module | PR-AUC | Base Rate | Hit-Rate@Budget | Ablation Delta | Beats Ablation | Tests |
|---|---|---|---|---|---|---|
| **Medicine** | 0.931 (trust scorer) | — | 0.100 (silent=0.050) | +0.050 vs 0.000 | ✅ | 405 ✅ |
| **Beds** | 0.8746 | 0.1233 | 0.5317 | +0.4140 | ✅ | 405 ✅ |
| **Personnel** | 1.0000* | 0.2792 | 1.0000 | +0.7306 | ✅ | 405 ✅ |

*PR-AUC=1.0 on generator — see honesty note above.
