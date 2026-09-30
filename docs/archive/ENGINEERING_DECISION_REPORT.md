# TATHYON Internal Engineering Decision Report (CTO Pre-Build Gate)

**Document:** `docs/ENGINEERING_DECISION_REPORT.md`  
**Date:** 2026-09-20  
**Role:** Founding CTO & Principal Healthcare Supply-Chain Architect  
**Status:** APPROVED FOR IMPLEMENTATION  

---

## 1. Current Architecture
TATHYON is currently structured as:
- **Core Package (`tathyon/`):** 38 modules spanning `schema.py` (immutable events, verified state read projections), `store.py` (append-only SHA-256 event store), `graph.py` (bipartite resource state engine), `forecast.py` & `demand.py` (TSB / Croston holdout backtesting + multi-source demand fusion), `stockout.py` (runway & hazard evaluation), `twin.py` (Resilience Scenario Engine 2.0 multi-plan counterfactual simulation), `planner.py` & `optimize.py` (OR-Tools CP-SAT multi-objective redistribution planner), `policy.py` (policy-based policy constraints), `cases.py` (durable `ResilienceCase` lifecycle engine), `adapters.py` (DVDMS, CSV, REST, HL7 FHIR R4), `shipment.py` & `data_moat.py` (closed-loop outcome tracking and supplier SLA metrics), and `benchmark.py` (empirical model selection gate).
- **API Surface (`api/main.py`):** 36 REST endpoints serving health, event ledger, graph state, risk queues, twin runs, candidate plans, CMO approval, exports, outcomes, cases, and benchmarks.
- **Frontend (`web/`):** Unified 6-screen triage interface (`web/index.html`), PWA field attestation console (`web/field.html`), and technical auditor workspace (`web/workspace.html`).
- **Test Suite (`tests/`):** 29 test files, 262 passing tests.

---

## 2. What Is Good (To Preserve & Protect)
1. **The Physical Usable State Invariant:** $\text{Usable} = \max(\min(\text{Observed}, \text{Claimed}) - \text{Expired} - \text{Quarantined} - \text{Floor}, 0)$. This is the most defensible intellectual property in the system.
2. **Deterministic Optimizer Gate:** Consequential stock rebalancing is strictly solved by OR-Tools CP-SAT with donor safety floor guarantees and explicit rejection reason logging.
3. **The Empirical Forecaster Gate:** Intermittent demand is modeled via Teunter-Syntetos-Babai (TSB) competed against Croston-SBA and Naive on holdout MASE.
4. **Append-Only Tamper-Evident Ledger:** Implemented in `tathyon/store.py` with strict separation of duties.
5. **Durable Operational Case Engine:** Alerts transition into `ResilienceCase` tracking from `DETECTED` to `CLOSED`.
6. **Execution Boundary Discipline:** Payloads badged `READY_FOR_SYSTEM_OF_RECORD: GENERATED — NOT EXECUTED`.

---

## 3. What Is Wrong (To Correct)
1. **Misleading Legal Authority Claims:** Previous reports claimed a "14-day donor safety floor is national policy-based law." Fact-check: It is an operational NHM/IPHS guideline/SOP, not an Act of Parliament. It must be configurable ($k_{\text{safety}} \in [7, 30]\text{ days}$).
2. **Exaggerated Cold-Chain Statistics:** The claim that "19% of vaccines are destroyed on tarmac" is an unsupported conflation of temperature excursion rates (14–34%) with physical destruction.
3. **Unfair Incumbent Framing:** Claiming "e-Aushadhi cannot transfer medicines" is false. DVDMS natively supports inter-facility transfer vouchers. TATHYON's moat is **decision intelligence, usable truth reconciliation, and network simulation**, not the database table.
4. **UI Disconnect:** The frontend (`web/index.html`) still shows "162 Tests Passing" and lacks a dedicated visual console for the newly introduced `ResilienceCase` lifecycle.

---

## 4. What Must Be Removed
1. **Unscented Kalman Filter (UKF) & MARL references:** Fully excised from active loops.
2. **AI Chatbots & Conversational Wrappers:** Completely barred from decision and inventory flows.
3. **Decorative 3D Visualizations:** No Three.js/MapLibre spinning globes.
4. **Autonomous Government DB Mutation:** Strict adherence to human-in-the-loop staging.

---

## 5. What Must Be Redesigned
1. **Planner Safety Floor Config:** Expose `safety_floor_days` dynamically in `POST /plans/generate` rather than hardcoding 14 days.
2. **UI Navigation:** Add dedicated `Cases` navigation tab and viewer in `web/index.html`.
3. **Export UI:** Add direct "Export FHIR R4 Bundle" button in the Plan Console.
4. **Cold-Chain Quality Gate:** Transition temperature breaches into an evidence-based `QUARANTINE` state awaiting shake-test / VVM stage confirmation.

---

## 6. What Must Be Researched
1. Real-world C-DAC DVDMS XML/JSON transfer indent schemas (verified: standard indent voucher structure).
2. NHM buffer stock calculations ($\text{ROL} = \text{AMC} \times \text{LeadTime} + \text{Buffer}$).
3. DPDP Act 2023 applicability to public health logistics (verified: Section 3 exempts non-personal logistics data).

---

## 7. True Problem Statement
> **"In public health networks across developing nations, central procurement databases overstate peripheral medicine availability by masking expired, quarantined, and phantom stock. When seasonal epidemics strike, peripheral PHCs stock out, referral hospitals become overwhelmed, and Chief Medical Officers lack the simulation tools, constraint solvers, and audit indemnity needed to safely rebalance resources across district boundaries."**

---

## 8. True Product Wedge
**Critical Medicine & Seasonal Surge Resilience in District Public Health Networks.**  
Focused on the **Top 15 Life-Saving Therapeutics** (Anti-Snake Venom, Rabies Vaccine, Oxytocin, IV Fluids, ACT Antimalarials, Sepsis Antibiotics).

---

## 9. Final Architecture
`DATA SOURCES` $\to$ `ADAPTERS` $\to$ `RESOURCE TRUTH` $\to$ `DEMAND FUSION` $\to$ `INTERMITTENT FORECAST` $\to$ `FAILURE HAZARD` $\to$ `Resilience Scenario Engine 2.0` $\to$ `OR-TOOLS CP-SAT` $\to$ `OPERATIONAL CASE` $\to$ `CMO APPROVAL` $\to$ `STAGING EXPORT (DVDMS/FHIR)` $\to$ `DELIVERY OUTCOME` $\to$ `DATA MOAT` $\to$ `SOVEREIGN FEDERATION`.

---

## 10. Final User Workflow
1. Storekeeper/Doctor sees acute failure risk card in Risk Console.
2. Operational case opens (`ResilienceCase: DETECTED`).
3. Physical count reconciles physical truth; phantom stock isolated.
4. Resilience Scenario Engine projects 7-day consequence of doing nothing.
5. OR-Tools generates candidate plans; rejected donors audited.
6. CMO signs off via authenticated role `medical_officer`.
7. Staging export generated: DVDMS Transfer Indent & FHIR R4 Bundle.
8. Physical courier delivers stock; storekeeper counts receipt.
9. Delivery variance logged; downstream runway updated; case marked `CLOSED`.

---

## 11. Data Model
* **Immutable Write Events:** `Claim`, `Evidence`, `Attestation`, `StateEvent` (sealed with SHA-256).
* **Derived Read Projections:** `VerifiedState`, `ResourceState`, `FacilityState`.
* **Operational Case:** `ResilienceCase` with `CaseStatus` state machine.
* **Interoperability:** `DVDMSVoucher`, `FHIRR4Bundle` (`SupplyRequest`, `SupplyDelivery`, `Task`, `Location`).

---

## 12. AI / ML Model Selection
* **Forecasting:** Teunter-Syntetos-Babai (TSB) selected via dynamic holdout MASE competition.
* **Multi-Source Fusion:** Clinical WHO/NVBDCP multipliers + footfall elasticity.
* **GenAI (Gemini):** Bounded text summarization of CMO memos only. Zero inventory arithmetic.

---

## 13. Optimization Design
OR-Tools CP-SAT multi-objective integer programming:
- Minimize unmet demand.
- Minimize travel distance $\times$ quantity.
- Enforce hard donor safety floor ($k_{\text{safety}} \times \text{burn}$).
- Enforce cold-chain Form 20-B transport licenses.

---

## 14. Governance Model
- Strict RBAC: Only `medical_officer` role can approve transfers.
- Break-glass overrides create mandatory audit obligations.
- All plans stamped `READY_FOR_SYSTEM_OF_RECORD`.

---

## 15. Integration Boundary
TATHYON operates **BESIDE** government systems. It exports verified, signed, GFR-compliant payloads for manual or webhook staging. No direct unauthorized DB mutation.

---

## 16. Offline Architecture
- Mobile PWA stores attestations locally in IndexedDB.
- Offline event queue with cryptographic nonces and client UUIDs.
- Idempotent sync upon reconnection.

---

## 17. Security Model
- Tamper-evident SHA-256 hash-chained ledger.
- Separation of duties: Custodians cannot attest their own inventory.
- Photographic burst nonces defeat replay attacks.

---

## 18. Testing Strategy
- Adversarial tests (negative stock, future dates, NaN values).
- Causal integration tests (changing inventory alters risk and plan).
- High-scale performance tests (100 facilities, 1,000 resources, 10,000 events in $<1.0\text{s}$).

---

## 19. Demo Scenario
Single 3-minute story: Bastar District, Tokapal PHC, Anti-Rabies Vaccine, monsoon surge, 40 usable vials vs 200 claimed, Bastanar donor rejected, Jagdalpur donor approved, DVDMS & FHIR exported, delivery verified.

---

## 20. Known Limitations
1. demonstration authentication requires hardware PKI dongles (e-Sign/FIDO2).
2. Live state NIC integration requires formal state sandbox MoU.
3. Rural blackspots require SMS/USSD fallback.
