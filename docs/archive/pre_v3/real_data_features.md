# TATHYON Real-Data Feature Definitions & Availability Specification (v2.0)

## Overview & Governance Rules

This document defines all **38 causal features** implemented in the TATHYON health-resource verification prioritization engine across three operational resource modules:
1. **Medicines & Health Commodities** (18 features: `TRUST_FEATURES`)
2. **Hospital Beds & Acute Capacity** (10 features: `BED_TRUST_FEATURES`)
3. **Clinical & Allied Personnel** (10 features: `PERSONNEL_TRUST_FEATURES`)

### Strict Causal & Anti-Leakage Constraints
1. **Observed Data Only:** Features are computed strictly from unverified claimed ledgers, duty rosters, ward censuses, and historical attestation logs. Truth columns (`true_stock`, `true_usable`, `true_occupied`, `true_present_hours`) and ground-truth labels (`is_materially_wrong`) are **strictly prohibited** from feature calculation.
2. **Causal Time Horizon:** At snapshot date $t$, the feature extractor only observes ledger records dated $\le t$ and physical human attestations dated strictly $< t$. A physical verification conducted on day $t$ cannot influence the prioritization of day $t$.
3. **Missing Feature Rules:** When a real data source (such as an offline DVDMS export or district HMIS file) lacks a specific column required for a feature:
   - The feature is marked as **UNAVAILABLE / MISSING**.
   - No correlated proxy may be silently substituted without explicit documentation.
   - Missing numeric values are imputed with neutral, uninformative baselines (e.g. $0.0$ for violation counts, $1.0$ for neutral ratios, $365.0$ for uncounted series) as defined below, ensuring deterministic behavior.

---

## 1. Medicine Module (18 Features — `TRUST_FEATURES`)

These 18 features triage which facility $\times$ SKU ledger records are materially incorrect.

| # | Feature Name | Tier | Canonical Formula / Calculation | Real-Data Source Field | Real Availability Status & Fallback Policy |
|---|---|---|---|---|---|
| 1 | `t1_violations_now` | Tier 1 (Hard) | Sum of row-local hard arithmetic, negative consumption, expiry integrity, and chronological contradictions on date $t$. | `AvailableStock`, `ClosingBalance`, `Receipt`, `Issue`, `ExpiryDate` | **High**. Computed from standard daily transaction rows. If receipts/issues missing, arithmetic check defaults to 0. |
| 2 | `t1_violation_days_30d` | Tier 1 (Hard) | Count of days in trailing 30-day window carrying at least one Tier 1 violation. | Rolling sum of `t1_violations_now > 0` over 30 days. | **High**. Requires $\ge 30$ days of daily ledger history. If history $< 30$ days, calculated over available history. |
| 3 | `t1_arithmetic_days_30d` | Tier 1 (Hard) | Days in last 30 where $\text{closing} \neq \text{opening} + \text{receipts} - \text{issues}$. | `OpeningBalance`, `ReceiptQty`, `IssuedQty`, `ClosingBalance` | **Medium**. Available if DVDMS export contains opening/closing ledger. If only static stock is dumped, defaults to 0. |
| 4 | `t1_expiry_integrity_now` | Tier 1 (Hard) | $1$ if batch recorded with conflicting expiry dates historically or missing expiry on active stock; $0$ otherwise. | `BatchNo`, `ExpiryDate` | **High**. Extracted from batch tracking records. Missing expiry flag triggers 1.0. |
| 5 | `t2_weak_signal_days_30d`| Tier 2 (Weak) | Count of days in last 30 carrying weak signals: quantity outlier, round-number balance ($Q \pmod{50} = 0$), or retro entry lag $> 20$d. | `ClosingBalance`, `EntryDate`, `TransactionDate` | **Medium**. Requires transaction timestamps. If entry date missing, entry lag check is skipped. |
| 6 | `t2_qty_z_now` | Tier 2 (Weak) | Absolute z-score of reported stock against series' own trailing 28-day mean and standard deviation: $|S_t - \mu_{28}| / \sigma_{28}$. | `AvailableStock` trailing 28 days | **High**. Requires time series. For cold start ($<5$ records), defaults to 0.0. |
| 7 | `t2_entry_lag_log` | Tier 2 (Weak) | $\log(1 + \text{days between event date and ledger entry date})$. | `DateOfTransaction`, `DateOfEntry` | **Medium**. Present in system audit trails. If export omits entry timestamp, defaults to 0.0. |
| 8 | `t2_expired_but_stocked` | Tier 2 (Weak) | $1$ if reported expiry date $< t$ and reported stock $> 0$; $0$ otherwise. | `ExpiryDate`, `AvailableStock`, Snapshot Date $t$ | **High**. Universally extractable when expiry date is recorded. |
| 9 | `gp_days_since_attestation`| Causal Staleness | Days elapsed since the last recorded physical human count strictly before $t$. ($365.0$ if never attested). | Physical Attestation Log / Stock-Taking Register | **Requires Pilot Log**. If facility has no recorded human count, set to $365.0$ days. |
| 10| `gp_unobserved_ratio` | Gamma-Poisson | $\mathbb{E}[\text{unobserved consumption since last count}] / \max(\text{reported stock}, 1.0)$, derived from Gamma-Poisson posterior. | Daily issue velocity from ledger + Attestation Log | **High** (computed via `verify.posterior_unobserved`). Uses historical consumption prior. |
| 11| `gp_uncertainty_ratio` | Gamma-Poisson | Spread $(Q_{0.90} - \text{mean}) / \max(\text{reported stock}, 1.0)$ of the Gamma-Poisson posterior over unobserved window. | Daily issue velocity from ledger + Attestation Log | **High**. Quantifies tail risk of hidden stock depletion. |
| 12| `att_count` | Audit History | Cumulative count of independent physical inspections conducted on this SKU at this facility before $t$. | Physical Attestation Log | **Requires Pilot Log**. Count of signed inspection events. Defaults to 0 if no inspection system configured. |
| 13| `att_never` | Audit History | $1.0$ if `att_count == 0`; $0.0$ otherwise. | Physical Attestation Log | **Requires Pilot Log**. Identifies unverified cold-start series. |
| 14| `att_last_record_gap` | Audit History | $|\text{reported stock} - \text{physical present}| / \max(\text{physical present}, 1.0)$ at the most recent prior inspection. | Prior Physical Attestation Record | **Requires Pilot Log**. Captures historical unreliability. Defaults to 0.0 if never counted. |
| 15| `att_last_unusable_share`| Audit History | $(\text{physical present} - \text{physical usable}) / \max(\text{physical present}, 1.0)$ at most recent prior inspection. | Prior Physical Attestation Record | **Requires Pilot Log**. Quantifies historical shelf spoilage/expiry. Defaults to 0.0 if never counted. |
| 16| `burn_inconsistency_30d`| Flow Balance | $|(S_t - S_{t-30}) - (\sum_{30} \text{receipts} - \sum_{30} \text{issues})| / \max(S_t, 1.0)$. Stock moved without transaction. | Rolling 30d issues, receipts, closing balances | **High**. Catches unrecorded leakage or book adjustments. Defaults to 0.0 if $<30$d history. |
| 17| `burn_recon_gap_since_count`| Flow Balance | $|S_t - (S_{\text{att}} + \sum_{\text{since}} \text{receipts} - \sum_{\text{since}} \text{issues})| / \max(S_t, 1.0)$. | Attestation Log + Rolling transaction log | **Requires Pilot Log**. Reconciliation gap since last physical baseline. Defaults to 0.0 if never counted. |
| 18| `burn_issue_rate_ratio` | Burn Trend | $\text{mean}(\text{issues}_{7\text{d}}) / \max(\text{mean}(\text{issues}_{30\text{d}}), 1\text{e-}5)$. Acceleration of consumption. | Rolling 7d vs 30d issues | **High**. Captures demand surges or abnormal burn. Defaults to 1.0 if no consumption recorded. |

---

## 2. Hospital Bed Module (10 Features — `BED_TRUST_FEATURES`)

These 10 features identify wards and facilities where reported available bed capacity diverges from real, staffed, infection-free physical beds.

| # | Feature Name | Formula / Description | Real-Data Source Field | Real Availability Status & Policy |
|---|---|---|---|---|
| 1 | `t1_bed_arithmetic_violation` | $1$ if $\text{total} \neq \text{occupied} + \text{available} + \text{maintenance}$; $0$ otherwise. | Ward census: `TotalBeds`, `Occupied`, `Vacant`, `UnderMaintenance` | **High**. Available in daily HMIS/IPD registers. |
| 2 | `t1_unreported_broken_now` | $1$ if maintenance reported 0 but active bio-medical equipment work order or oxygen port repair exists; $0$ otherwise. | Bio-medical Maintenance Management System (BMMIS) work orders | **Medium**. Requires BMMIS linkage. Defaults to 0.0 if unlinked. |
| 3 | `t2_admissions_delta_gap` | $|(\text{admissions} - \text{discharges}) - \Delta\text{occupied}| / \max(\text{capacity}, 1.0)$. Flow mismatch. | IPD Admission/Discharge Log vs midnight census | **Medium**. Available if admission/discharge logs are digitized. Defaults to 0.0. |
| 4 | `t2_turnover_anomaly` | Standardized z-score of ward bed turnover rate vs facility clinical baseline. | Daily admissions / average occupied beds | **High**. Detects stagnant bed census. |
| 5 | `t2_occupancy_ratio_reported` | $\text{reported occupied beds} / \max(\text{total beds}, 1.0)$. | Ward census: Occupied / Total | **High**. Standard occupancy indicator. |
| 6 | `t2_reported_free_beds` | Raw count of claimed available beds. | Ward census: Vacant beds | **High**. Standard census metric. |
| 7 | `t2_hours_since_ward_census` | Hours elapsed since the last nurse-signed ward physical census. | Timestamp of latest nurse bed-roll signoff | **Medium**. Requires shift-log timestamp. Defaults to 24.0h if undated. |
| 8 | `t2_emergency_divert_rate_7d`| Trailing 7-day rate of emergency ambulance diversions from this facility. | Ambulance Dispatch & Emergency Registry (108 log) | **Low**. Requires state 108 ambulance dispatch linkage. Defaults to 0.0. |
| 9 | `t2_weekend_plateau_flag` | $1$ if occupancy remains identical across 48h weekend with zero logged discharges; $0$ otherwise. | Weekend census time series | **High**. Detects weekend reporting freeze. |
| 10| `t2_bed_saturation_pressure` | $\max(0.0, \text{occupancy\_ratio} - 0.85)$. Clinical stress index. | Occupancy ratio | **High**. Deterministic clinical indicator. |

---

## 3. Personnel Module (10 Features — `PERSONNEL_TRUST_FEATURES`)

These 10 features detect roster attendance anomalies, proxy attendance, and clinical service deficits.

| # | Feature Name | Formula / Description | Real-Data Source Field | Real Availability Status & Policy |
|---|---|---|---|---|
| 1 | `t1_impossible_duplicate_punch`| $1$ if employee biometric/sign-in registered at two distinct facilities on same date; $0$ otherwise. | Aadhaar-Enabled Biometric Attendance System (AEBAS) logs | **High**. Present in centralized state attendance systems. |
| 2 | `t1_zero_clinical_encounters` | $1$ if staff logged $\ge 6$h duty but zero OPD/IPD/procedure records signed in EMR/HMIS; $0$ otherwise. | Staff duty log cross-referenced with OPD consultation logs | **Medium**. Requires EMR/OPD doctor ID attribution. Defaults to 0.0. |
| 3 | `t2_clinical_volume_per_hour` | Total OPD patients examined / logged shift duty hours. | OPD slip counts / attendance shift duration | **High**. Standard clinical productivity metric. |
| 4 | `t2_punch_timestamp_regularity`| Variance of punch-in minutes over trailing 30 days ($0.0 \rightarrow$ batch proxy sign-in). | Daily punch-in timestamp minute distribution | **High**. Detects automated or proxy sign-ins. |
| 5 | `t2_consecutive_days_unverified`| Days elapsed since independent physical roll-call or supervisory inspection. | District Quality Assurance (NQAS) or supervisory visit log | **Requires Field Log**. Defaults to 90.0 days if uninspected. |
| 6 | `t2_shift_fulfillment_ratio` | Physically observed on-duty hours / rostered scheduled duty hours. | Physical verification spot-check log vs roster | **Requires Field Log**. Defaults to 1.0 (uninformative) without physical audit. |
| 7 | `t2_round_number_hours_flag` | $1$ if shift duration is exactly $8.000$ hours (indicative of manual ledger entry vs biometric punch); $0$ otherwise. | Biometric punch delta | **High**. Detects manual register entry. |
| 8 | `t2_peer_attendance_correlation`| Pearson correlation between employee absence and specific coworker absence over 90 days. | Shift attendance matrix | **Medium**. Identifies paired proxy punch rings. |
| 9 | `t2_facility_remote_hardship_z`| Standardized isolation index (travel time from district headquarters via OSRM). | Facility GIS coordinates + OSRM road travel hours | **High**. Computed using OSRM and Bastar coordinates. |
| 10| `t2_historical_unauthorized_absence_rate` | Trailing 90-day unexcused absence rate ($ \text{unexcused absences} / \text{scheduled shifts} $). | Leave register & duty roster history | **High**. Standard administrative HR indicator. |

---

## Summary of Feature Availability Across Real Data Regimes

```
Module        Total Features   Extractable from Daily Ledger   Requires Physical Audit Log   Requires External Linkage
----------------------------------------------------------------------------------------------------------------------
Medicines           18                      12                             6 (Attestation)              0
Beds                10                       7                             1 (Ward Audit)               2 (BMMIS/108)
Personnel           10                       7                             2 (Roll-Call)                1 (OPD EMR)
----------------------------------------------------------------------------------------------------------------------
TOTAL               38                      26                             9                            3
```

---

## Explicit Missing Feature & Substitution Policy

In accordance with TATHYON anti-hallucination standards:
1. **Never Invent Data:** When a source system export omits an operational field, TATHYON **never** fabricates a surrogate proxy row or imputes dynamic values based on ungrounded heuristics.
2. **Deterministic Uninformative Baselines:** Missing features must strictly adopt the following statutory defaults:

### Medicines Module Substitutions:
- **`gp_days_since_attestation` (Missing Attestation):** Defaults to `365.0` days (maximum unobserved penalty for unverified stock).
- **`att_count` / `att_never`:** Defaults to `0` and `1.0` (marking series as uninspected cold-start).
- **`att_last_record_gap` / `att_last_unusable_share`:** Defaults to `0.0` (neutral, no prior error history).
- **`burn_recon_gap_since_count`:** Defaults to `0.0` (cannot compute physical drift without a physical baseline count).
- **`t1_arithmetic_days_30d` (Static Stock Dump):** If export provides only closing balances without daily receipts/issues, arithmetic mismatch defaults to `0.0`.
- **`t2_entry_lag_log` (Omitted Transaction Timestamp):** Defaults to `0.0` (log(1 + 0)).
- **`t2_qty_z_now` (Cold-Start History < 5 Days):** Defaults to `0.0`.
- **`burn_issue_rate_ratio` (Zero Recorded Consumption):** Defaults to `1.0` (neutral velocity ratio).

### Beds Module Substitutions:
- **`t1_unreported_broken_now` (Unlinked BMMIS):** Defaults to `0.0` (no verified work order).
- **`t2_hours_since_ward_census` (Undated Shift Census):** Defaults to `24.0` hours.
- **`t2_emergency_divert_rate_7d` (Unlinked 108 Emergency Dispatch):** Defaults to `0.0`.
- **`t2_admissions_delta_gap` (Undigitized Admission/Discharge):** Defaults to `0.0`.

### Personnel Module Substitutions:
- **`t1_zero_clinical_encounters` (Unlinked OPD EMR Consultation Slips):** Defaults to `0.0`.
- **`t2_consecutive_days_unverified` (No Supervisory Audit Log):** Defaults to `90.0` days.
- **`t2_shift_fulfillment_ratio` (No Physical Spot-Check):** Defaults to `1.0` (neutral attendance assumption).

All substitutions are logged in the Ingestion Manifest and Data Card; models trained on subsets with missing fields must document which features were active during training.
