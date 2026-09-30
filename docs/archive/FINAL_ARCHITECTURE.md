# TATHYON Sovereign Healthcare Resource Resilience Control Plane — Final Architecture

**Document:** `docs/FINAL_ARCHITECTURE.md`  
**Standard:** Section 1 & Section 44 of CTO War-Room Directive  

---

## 1. Product Thesis & Foundational Truth

```
         "THE DIGITAL RECORD IS NOT THE USABLE RESOURCE."
```

In developing world healthcare systems, digital inventory databases (e.g. e-Aushadhi, DVDMS) routinely overstate operational supply by failing to subtract expired batches, cold-chain breaches, quarantined stock, and phantom records.

TATHYON determines:
1. **WHAT IS ACTUALLY USABLE?** $\to$ Physical usable state reconciliation.
2. **WHAT WILL FAIL?** $\to$ Runout hazard under intermittent Poisson/Negative Binomial consumption.
3. **WHEN WILL IT FAIL?** $\to$ Days to stockout calculated against lead-time replenishment.
4. **WHY WILL IT FAIL?** $\to$ Explainable multi-source demand fusion (historical burn + epidemic shock + footfall surge).
5. **WHAT HAPPENS TO THE NETWORK IF IT FAILS?** $\to$ Patient diversion and cascade burden on neighboring hospitals.
6. **WHAT CAN WE DO?** $\to$ Multi-objective constrained candidate response plans.
7. **WHAT ARE THE TRADEOFFS?** $\to$ Closest Donor vs Max Coverage vs Min Cost vs Max Resilience.
8. **WHO APPROVED IT?** $\to$ policy-based Chief Medical Officer signoff under GFR 2017 & Epidemic Diseases Act.
9. **WHAT ACTUALLY HAPPENED?** $\to$ Physical delivery outcome, variance, and transit loss recorded.
10. **DID THE INTERVENTION WORK?** $\to$ Runway delta measured, model calibrated, and feedback stored in proprietary data moat.

---

## 2. The 10-Stage Operational Loop

```
OBSERVE
   ↓
RECONCILE (Physical Usable State = Claimed - Phantom - Expired - Quarantined - Floor)
   ↓
UNDERSTAND (6-Dimension Facility Health Profile & Longitudinal Discrepancy Detection)
   ↓
PREDICT (Holdout MASE Competition: TSB vs Croston-SBA vs Naive Mean)
   ↓
REHEARSE (Resilience Scenario Engine 2.0 Counterfactual Simulation: Plan A vs B vs Do Nothing)
   ↓
OPTIMIZE (OR-Tools CP-SAT with Donor Safety Floor & Rejected Donor Auditing)
   ↓
APPROVE (CMO policy-based Signoff with Role-Based Access Control)
   ↓
EXECUTE (READY_FOR_SYSTEM_OF_RECORD: DVDMS / CSV / FHIR R4 Bundle Staged)
   ↓
MEASURE (Delivered Quantity, Lead-Time Variance, Post-Intervention Runway Gain)
   ↓
LEARN (Proprietary Data Moat: Supplier Reliability & Route Friction Calibration)
```

---

## 3. Real-World System Boundary & Interoperability

TATHYON does **NOT** replace state systems of record. It sits **ABOVE and BESIDE** them:

```
┌──────────────────────────────────────────────────────────────────┐
│                   GOVERNMENT SYSTEMS OF RECORD                   │
│   e-Aushadhi (CDAC) │ DVDMS (State) │ HMIS │ e-Sushrut           │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Claims & Ledgers
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                     TATHYON SOURCE ADAPTERS                      │
│   DVDMSAdapter │ GenericCSVAdapter │ GenericRESTAdapter          │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Normalized Events
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│             RESOURCE TRUTH & USABLE-STATE RECONCILER             │
│   Claimed vs Observed vs Usable vs Quarantined vs Expired        │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Verifiable Usable State
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                  RESILIENCE INTELLIGENCE ENGINE                  │
│   Demand Fusion │ Stockout Hazard │ 6-Dim Health │ Network Impact│
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Ranked Risk Queue
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                     OPERATIONAL CASE ENGINE                      │
│   ResilienceCase Lifecycle: DETECTED → CLOSED                    │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Active Case
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│           Resilience Scenario Engine 2.0 & OR-TOOLS RESPONSE PLANNER           │
│   Candidate Plans: Closest Donor, Max Coverage, Max Resilience   │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Proposed Transfers
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                     HUMAN policy-based APPROVAL                     │
│   X-Role: medical_officer │ GFR 2017 Rule 22 │ SHA-256 Seal      │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ verified Plan
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                   SYSTEM-OF-RECORD EXPORT GATE                   │
│   STATUS: READY_FOR_SYSTEM_OF_RECORD                             │
│   FORMATS: DVDMS Indent V2 │ RFC 4180 CSV │ HL7 FHIR R4 Bundle   │
│   BOUNDARY: PAYLOAD_STAGED_NOT_EXECUTED_EXTERNALLY               │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Physical Transfer & Delivery
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                     CLOSED-LOOP OUTCOME ENGINE                   │
│   Planned vs Actual Delivery │ Loss │ Lead-Time Variance         │
└─────────────────────────────────┬────────────────────────────────┘
                                  │ Feedback
                                  ▼
┌──────────────────────────────────────────────────────────────────┐
│                      PROPRIETARY DATA MOAT                       │
│   Longitudinal Discrepancies │ Supplier SLA │ Route Friction     │
└──────────────────────────────────────────────────────────────────┘
```

---

## 4. Module Map & Responsibilities

| Module | Purpose & Invariant |
|---|---|
| [`tathyon/graph.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/graph.py) | Heterogeneous bipartite facility-resource graph. Computes `phantom_quantity`, `reconciliation_gap`, and `donor_transferable_qty`. |
| [`tathyon/intelligence.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/intelligence.py) | 6-dimension facility health profile (`health_score` $[0, 1]$) and longitudinal discrepancy detector (phantom creep, shrinkage, ghost receipts). |
| [`tathyon/demand.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/demand.py) | Multi-source demand fusion engine combining consumption, footfall, seasonality, and WHO/NVBDCP typed emergency shocks. |
| [`tathyon/forecast.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/forecast.py) | Intermittent demand backtesting engine competing TSB, Croston-SBA, and Naive Mean on holdout MASE. |
| [`tathyon/stockout.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/stockout.py) | Computes days-to-stockout runway, lead-time failure probabilities, and network cascade burden scores. |
| [`tathyon/twin.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/twin.py) | Resilience Scenario Engine 2.0 multi-plan counterfactual simulator comparing Plan A vs Plan B vs Do Nothing under identical seeds. |
| [`tathyon/planner.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/planner.py) | Multi-objective OR-Tools CP-SAT response planner generating Pareto trade-off options with full rejected donor audit logging. |
| [`tathyon/policy.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/policy.py) | Grounded policy-based policy engine (GFR 2017 Rules 211/213/22, NHM Guidelines Sec 7.3, Drugs & Cosmetics Rule 65). |
| [`tathyon/cases.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/cases.py) | Durable `ResilienceCase` lifecycle engine driving failures from detection to closed-loop outcome measurement. |
| [`tathyon/adapters.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/adapters.py) | DVDMS, CSV, REST, and HL7 FHIR R4 interoperability adapters with `READY_FOR_SYSTEM_OF_RECORD` discipline. |
| [`tathyon/benchmark.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/benchmark.py) | Model selection gate and empirical benchmarking suite evaluating forecasting and allocation baselines. |
| [`tathyon/business.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/business.py) | TrustMRR B2G SaaS economics, 40x Government fiscal Scenario Calculator calculator, and FixMyItch problem catalog. |
| [`tathyon/federated.py`](file:///Users/jaisogani/Downloads/tathyon%202/tathyon/federated.py) | Sovereign cross-border DP-FedAvg machine learning with differential privacy between synthetic national silos. |
