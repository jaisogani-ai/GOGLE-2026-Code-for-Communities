# TATHYON (तथ्य) — ANTIGRAVITY SOURCE OF TRUTH
## Healthcare Resource Resilience Control Plane
*Document Reference: `docs/17_ANTIGRAVITY_SOURCE_OF_TRUTH.md`*  
*Supersedes: `docs/00_LOCKED_DECISION.md`*

---

## 1. PRODUCT THESIS

**Tathyon predicts which public-health facility will run out of a critical resource, shows what that failure does to the network, and produces the safest human-approved redistribution plan.**

TATHYON is NOT primarily:
- a trust layer
- an audit product
- a verification product
- an agent safety harness
- an ERP / HMS
- a chatbot

The verification and trust kernel is **input quality infrastructure** underneath the product.

---

## 2. THE PRIMARY PRODUCT LOOP

```
DATA FUSION
    ↓
HEALTHCARE RESOURCE GRAPH
    ↓
DEMAND INTELLIGENCE
    ↓
STOCKOUT / SURGE PREDICTION
    ↓
EMERGENCY RESILIENCE Resilience Scenario Engine
    ↓
RESPONSE PLANNER (CONSTRAINED OPTIMIZATION)
    ↓
HUMAN APPROVAL
    ↓
EXECUTION ADAPTER
    ↓
OUTCOME RECORDING
    ↓
FEDERATED LEARNING
```

---

## 3. SACROSANCT INVARIANTS

1. **AI NEVER decides physical truth.**
2. **Gemini NEVER counts inventory.**
3. **AI NEVER signs an attestation.**
4. **AI NEVER approves payment.**
5. **AI NEVER autonomously dispatches healthcare resources.**
6. **Unverified state defaults to unsafe.**
7. **Human attestation is mandatory.**
8. **Human authority remains responsible for consequential action.**
9. **Break-glass emergency override must remain available.**
10. **Every state-changing operation must be auditable.**
11. **Existing systems of record remain authoritative.**
12. **TATHYON is a sidecar/gate, not a replacement ERP.**
13. **Unverified inventory must NEVER become donor inventory.**

---

## 4. PHASED BUILD ARCHITECTURE

- **PHASE 1 — HEALTHCARE RESOURCE GRAPH**: Facility hierarchy (PHC, CHC, DH, DWH, SWH), Resource hierarchy (Medicine, Vaccine, Bed, Equipment, Personnel), Claimed vs. Usable State separation.
- **PHASE 2 — STOCKOUT + SURGE ENGINE**: Forecast competition (Naive, Seasonal Naive, Croston-SBA, TSB), $P(\text{stockout})$, CUSUM anomaly detection, Alert tiers (WATCH, ELEVATED, HIGH, CRITICAL).
- **PHASE 3 — RESILIENCE Resilience Scenario Engine**: Deterministic simulation of shocks (Demand +20%, +50%, +100%, Warehouse down, Lead time $\times 2$, Outbreak uplift), comparing BASELINE vs. TATHYON RESPONSE. Explicit `SIMULATION` labeling.
- **PHASE 4 — RESPONSE PLANNER (OR-TOOLS)**: Constrained mixed-integer programming for cross-facility redistribution under safety floors and cold-chain constraints.
- **PHASE 5 — THREE TYPED AGENTS**: `Supply Intelligence Agent` (SupplyBrief), `Surge Investigation Agent` (SurgeDossier), `Response Planning Agent` (PlanDraft). Zero autonomous execution or agent-to-agent chatter.
- **PHASE 6 — UNIFIED REST API**: Graph, risk, forecast, twin, plan generation, human approval, outcomes, and federation endpoints.
- **PHASE 7 — HERO DEMONSTRATION**: End-to-end deterministic execution of rabies vaccine demand surge, resilience scenario engine shock, response plan approval, and federated learning simulation.
- **PHASE 8 — STITCH UI INTEGRATION**: Clean, high-impact operator interface answering: What will fail? When? Why? What happens if we do nothing? What should we do? Who approves?
