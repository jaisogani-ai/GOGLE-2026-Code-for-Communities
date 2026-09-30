# Evaluation — what was measured, how, and what it does not show

Everything on this page is **SYNTHETIC**: seeded simulated districts. No result here is field evidence or a
claim about real facilities. Reproduce with `make eval` and `make scorer` (deterministic seeds).

## 1. Allocation benchmark (`tathyon/evaluate.py`, seed 20260928, 10 scenarios, 4 visit slots each)

Fixes made in this audit before trusting any number:

| Problem found | Fix |
|---|---|
| Headline `verified_stockout_days_averted` credits only verified donors, so arms that never verify score 0 **by construction** | Kept as a column, removed from the headline |
| "Phantom units blocked" was credited to `tathyon`, `always_verify`, `ai_ablation` **by arm name** | Now measured from behaviour for every arm: phantom exposure − phantom units actually shipped |
| `decision_latency_ms` was a **hard-coded table** (e.g. `tathyon: 4.12`) | Removed (reported as `null`); wall-clock latency is machine-dependent |
| A partly-phantom shipment credited **zero** stockout-days, even for the real units delivered | Real delivered units now credited for every arm |
| "500× lift" message divided by a 1e-4 floor | Replaced with raw numbers |

Results after the fixes (all arms scored by identical rules):

| Arm | Stockout-days averted | Phantom units shipped | Wasted trips | Verification visits |
|---|---:|---:|---:|---:|
| naive (trust every report) | **103.47** | 204 | 5 | 0 |
| greedy_guarded | 100.50 | 83 | 2 | 0 |
| min_max (central reorder) | 92.75 | 204 | 5 | 0 |
| **TATHYON** | 80.87 | **0** | **0** | 20 |
| always_verify | 80.59 | 0 | 0 | 50 |
| ai_ablation (random targeting, same budget) | 27.11 | 0 | 0 | 20 |

**Reading it honestly**

- TATHYON **loses on raw stockout-days** to trust-the-report arms. They move more stock, including stock from
  honest donors nobody has counted yet; TATHYON refuses to plan on uncounted stock.
- The simulator does **not** charge a wasted trip any delay or cost, which overstates the naive arms' advantage.
  We did not change this to make TATHYON win; it is a stated limitation.
- What TATHYON measurably buys: **zero phantom shipments and zero wasted trips**, the same outcome as counting
  everything (80.87 vs 80.59) with **60% fewer visits** (20 vs 50), and targeting matters: the same 20 visits
  chosen at random avert 27.11.
- The operational product therefore offers the counterfactual explicitly: `VERIFY_THEN_TRANSFER` uses an unverified
  donor as *contingent* supply when the recipient's runway allows a count first — recovering part of the stock the
  pure verified-only arm leaves unused, without dispatching against an uncounted report.

## 2. Trust scorer (`tathyon/trust_eval.py`) — ML audit

- Split by **world** (train seeds 1001–1014, test 2001–2004, 12,000 test observations); features are
  observation-time only (`tests/test_no_leakage.py`).
- PR-AUC **0.931** vs base rate 0.023. Hit rate at 20 visits: scorer **0.228**, best deterministic rule (tier-1
  hard violations) **0.198**, random 0.024, oldest-count-first 0.019, ceiling 0.232.
- Verdict: the ML scorer beats the best transparent rule by ~15% **on synthetic data only**. There are no real,
  human-attested labels, so the model is **not served** (`GET /api/model/status`). The live queue uses transparent
  signals (count age, report staleness, district outlier) and an exact knapsack over units-at-stake per verifier-hour.

## 3. Beds and personnel scorers (synthetic)

| Scorer | PR-AUC | Note |
|---|---:|---|
| Beds | 0.875 (base 0.123) | Synthetic generator only |
| Personnel | **1.000** | A perfect score means the generator makes the label trivially separable. **Not evidence of anything.** |

Neither is served. The live product applies a history-gated robust-z advisory (`anomaly_detection.py`) to uploaded
bed/attendance reports, labelled "Statistical anomaly signal — human decides".

## 4. Closed-loop tests (engine behaviour, not impact)

`tests/test_workspace_loop.py` drives a synthetic test fixture through propose → approve → phantom count →
invalidation → replan → delayed/partial receipt → outcome → second replan, and checks replay equality and tamper
detection. `tests/test_intake_and_api_v3.py` drives the same loop over HTTP with role enforcement.

## 5. What would count as real evidence

A pilot district exporting DVDMS data weekly, with verifiers counting the queue's picks plus a random control sample,
measuring: phantom-shipment rate, count hit rate vs random, stockout-days at recipients, visits per averted stockout.
