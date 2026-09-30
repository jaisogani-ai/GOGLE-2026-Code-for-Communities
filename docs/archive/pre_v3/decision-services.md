# TATHYON Non-AI Deterministic Decision Services Specification

> **Evidence boundary:** the worked examples and benchmark values in this specification use synthetic scenarios. They are not measured field performance, government operational data, or patient impact.

This document details the architecture, design principles, mathematical formulation, and API contracts for TATHYON's core non-AI deterministic decision services.

---

## 1. Architectural Philosophy & Ground Rules

1. **State Machine, Not Black-Box Scoring:**
   - Operational decisions (e.g., admitting donor stock to redistribution pools) are gated by state transitions (`UNVERIFIED` $\to$ `VERIFIED`), never by opaque probabilistic thresholds alone.
2. **Deterministic & Repeatable:**
   - Every service is implemented in standard Python without autonomous LLM loops or random seeds without fixtures. Given identical inputs, outputs are byte-identical.
3. **Audit Trail on Every Consequential Action:**
   - Any state change, dispatch scheduling, human approval, or operational override is written to the immutable SHA-256 hash-chained event ledger (`EventStore`).
4. **Separation of Risk vs. Data Confidence:**
   - Risk measures the expected probability of a material physical deficit.
   - Confidence measures the quality and freshness of the source observation.
   - A missing observation is treated as high uncertainty / high verification priority, never as low risk.
5. **Human Approval (TATHYON Policy):**
   - Resource allocation and staff redeployment generate `PROPOSED` plans. No physical transfer or redeployment is ever executed from a recommendation alone.

---

## 2. Decision Services Breakdown

### Service 1: Trust Queue Service (`TrustQueueService`)
- **Location:** [`tathyon/non_ai_services.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/non_ai_services.py)
- **Goal:** Rank facilities for physical verification under a finite inspection budget.
- **Configurable Factors:**
  $$\text{Value}_i = \left( w_{\text{risk}} \cdot (\text{Risk}_i \times \text{HiddenDays}_i) + w_{\text{stale}} \cdot \text{StalenessNorm}_i + w_{\text{disc}} \cdot \text{DiscrepancyNorm}_i \right) \times \left( \frac{\text{VEN}_i}{2.0} \right) \times \frac{1}{\sqrt{\text{VisitCost}_i}}$$
- **Outputs:**
  - `expected_value`: Composite priority score for knapsack selection.
  - `risk_score`: Calibrated probability of record discrepancy $[0.0, 1.0]$.
  - `data_confidence`: Decay-adjusted confidence in report freshness $[0.05, 1.0]$.
  - `top_contributing_reasons`: Human-readable explanation list.
  - `source_facts`: Unprocessed underlying input values.
- **Non-Accusatory Rule:** Flags anomalies as statistical variance without alleging fraud or intent.

### Service 2: Verification Dispatch Service (`VerificationDispatchService`)
- **Location:** [`tathyon/non_ai_services.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/non_ai_services.py)
- **Goal:** Turn ranked triage items into assigned inspection shifts under inspector workload constraints.
- **Rules & Constraints:**
  - Inspector assignment uses deterministic least-workload scheduling with tie-breaks by sorted identifier.
  - Any task exceeding the policy threshold ($\text{Value} \ge 25.0$) or involving vital missing items automatically enters `PENDING_APPROVAL`.
  - Manual overrides by health officers are supported and write an immutable `OVERRIDDEN` event with reason and actor ID to the event ledger.

### Service 3: Deterministic Forecasting Service (`DeterministicForecastingService`)
- **Location:** [`tathyon/non_ai_services.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/non_ai_services.py)
- **Goal:** Demand and occupancy forecasting with explicit uncertainty bounds.
- **Models:**
  - Medicine: Intermittent demand forecasting via Croston-SBA / TSB with compound Poisson-Gamma prediction intervals and holdout backtesting (MASE).
  - Beds: Compartmental ward turnover model parameterized by target length-of-stay and admission rates.
- **Fail-Safe Invariants:**
  - Returns `INSUFFICIENT_DATA` if series length $< 5$.
  - Returns `STALE_DATA` warning if observation age $> 60$ days.
  - Never presents a forecast as an observed count. Every projection carries the disclaimer: *"STATISTICAL PROJECTION ONLY — Not an observed inventory count."*

### Service 4: Deterministic Resource Allocation Service (`DeterministicAllocationService`)
- **Location:** [`tathyon/non_ai_services.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/non_ai_services.py) & [`tathyon/optimize.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/optimize.py)
- **Goal:** Multi-commodity network redistribution solve under physical and policy constraints.
- **Hard Invariants:**
  1. *Unverified Stock Gate:* Unverified stock is invisible to transfers ($Q_\alpha = 0.0$).
  2. *Donor Safety Floor:* Mandatory preservation of 14 days of local demand ($Q_{\text{transferable}} = \max(0, Q_{\text{usable}} - 14 \cdot \text{burn})$).
  3. *Expiry Filter:* Stock expiring in $\le 7$ days is rejected to prevent in-transit expiry.
  4. *Truck Capacity & Cold Chain:* Respects maximum vehicle capacities and temperature compliance.
  5. *Exact Rejection Reasons:* Returns rejected donor candidates with the specific invariant that excluded them (`UNVERIFIED_STOCK_GATE`, `DONOR_SAFETY_FLOOR_VIOLATION`, `EXPIRY_DEADLINE_CONSTRAINT`).

### Service 5: Demo Safety Controls (`DemoSafetyService`)
- **Location:** [`tathyon/non_ai_services.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/non_ai_services.py)
- **Goal:** Guarantee synthetic demonstration fixtures cannot be mistaken for national live operations.
- **Controls:**
  - Mandatory banner: `[SYNTHETIC DEMO - NO LIVE OPERATIONS]`.
  - Static seeds (`seed=20260928`) ensuring repeatable demonstrations across Bastar District simulations.
  - Explicit flag: `is_live_connection: False`.

---

## 3. API Response Contracts

### Endpoint 1: `GET /v2/decision/trust-queue`
```json
{
  "score_version": "2.1.0",
  "calculation_timestamp": "2026-09-28T14:45:00Z",
  "budget_slots": 20,
  "items": [
    {
      "facility_id": "PHC_X",
      "facility_name": "PHC X (North Hub)",
      "resource_key": "MED-ARV-01",
      "resource_type": "MEDICINE",
      "risk_score": 0.88,
      "data_confidence": 0.18,
      "data_age_days": 41.0,
      "is_missing_data": false,
      "service_criticality": 3.0,
      "discrepancy_history_count": 2,
      "visit_cost_slots": 2,
      "expected_value": 48.84,
      "rank": 1,
      "top_contributing_reasons": [
        "Observation staleness: 41 days since last attested count exceeds 30d threshold",
        "Statistical anomaly in burn rate indicates high probability of record discrepancy (88%)",
        "Vital resource tier (VEN=V): Unobserved stockout poses acute clinical consequence"
      ],
      "source_facts": {
        "reported_stock": 5000.0,
        "daily_consumption_rate": 5.0,
        "hidden_stockout_days_projected": 18.5,
        "visit_cost_slots": 2
      },
      "score_version": "2.1.0",
      "provenance": "SYNTHETIC_TRIAGE"
    }
  ]
}
```

### Endpoint 2: `POST /v2/decision/dispatch/schedule`
```json
{
  "scheduled_tasks": [
    {
      "dispatch_id": "dsp_PHC_X_MED-ARV-01_1",
      "facility_id": "PHC_X",
      "resource_key": "MED-ARV-01",
      "assignee_id": "INSP_01",
      "assigned_slots": 2,
      "scheduled_date": "2026-09-29",
      "status": "PENDING_APPROVAL",
      "requires_approval": true,
      "priority_level": "CRITICAL",
      "travel_time_hours": 3.0,
      "workload_hours": 7.0,
      "reasons": [
        "Observation staleness: 41 days since last attested count exceeds 30d threshold"
      ],
      "audit_event_id": "evt_b82a17..."
    }
  ],
  "total_tasks": 1,
  "total_slots_budget": 20,
  "status": "SCHEDULED"
}
```

### Endpoint 3: `POST /v2/decision/forecast`
```json
{
  "resource_type": "MEDICINE",
  "resource_key": "MED-ARV-01",
  "facility_id": "PHC_1",
  "horizon_days": 14,
  "projected_daily_rate": 8.5,
  "point_forecast": 119.0,
  "lower_bound_80": 98.4,
  "upper_bound_80": 139.6,
  "stockout_risk_prob": 0.45,
  "days_to_stockout": 5.3,
  "model_family": "Croston-SBA (Intermittent Demand)",
  "backtest_mase": 0.82,
  "data_coverage_days": 30,
  "data_freshness_days": 1.0,
  "status": "SUCCESS",
  "reasons": [
    "Intermittent demand series backtested with MASE scale-free metric"
  ],
  "disclaimer": "STATISTICAL PROJECTION ONLY — Not an observed inventory count."
}
```

---

## 4. Test Verification Summary

- Total Test Suites: 47 files.
- Total Tests Passed: **465 passed in 59.26s**.
- Scope Hygiene: **6/6 passed** (`tests/test_scope_hygiene.py`).
- Evaluation Benchmark: Byte-identical reproduction:
  $$\text{verified\_stockout\_days\_averted} = 80.87 \quad \text{(synthetic benchmark only)}$$
