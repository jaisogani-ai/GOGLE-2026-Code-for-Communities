# TATHYON Product Wedge: Critical Medicine Resilience

**Document:** `docs/PRODUCT_WEDGE.md`  
**Standard:** Section 36 & Section 37 of CTO War-Room Directive  

---

## 1. The Startup Product Interrogation

| Startup Question | TATHYON Architectural Answer |
|---|---|
| **WHO USES IT?** | District Drug Storekeeper, Block Medical Officers, and District Epidemiologist. |
| **WHO APPROVES?** | Chief Medical Officer (CMO) or District Collector via authenticated role `medical_officer`. |
| **WHO PAYS?** | State Health Society / National Health Mission (NHM) Program Implementation Plan (PIP) under Health System Strengthening budget head. |
| **WHAT FAILURE DOES IT PREVENT?** | Stockout of life-saving critical medicines (Anti-Snake Venom, Rabies Vaccine, Oxytocin, IV Fluids, Antibiotics) during routine and surge crises. |
| **WHAT WORKFLOW DOES IT OWN?** | The transition from **Failure Hazard Alert $\to$ Usable Reconciliation $\to$ Counterfactual Simulation $\to$ policy-based Inter-Facility Transfer Voucher $\to$ Outcome Feedback**. |
| **WHAT DATA DOES IT ACCUMULATE?** | Longitudinal claim-reality gaps (phantom inventory rates by facility), supplier lead-time variances, route transit friction, and post-delivery outcome feedback. |
| **WHY DOES THAT DATA BECOME MORE VALUABLE?** | Every physical delivery outcome trains the proprietary data moat: the platform learns which suppliers default, which routes are delayed by rain, and which facilities chronically over-report stock, improving future forecast precision. |
| **WHAT EXISTING SYSTEM DOES IT SIT BESIDE?** | Sits beside state ERPs (e-Aushadhi / DVDMS / HMIS). TATHYON ingests claims, provides intelligence, and exports verified payloads. |

---

## 2. The Primary Product Wedge (V1)

```
        CRITICAL MEDICINE RESILIENCE IN ACUTE CARE PERIPHERIES
```

Rather than attempting to track every pencil, bandage, and bed across an entire nation on day one, TATHYON drives laser-focused adoption on the **Top 15 Life-Saving Emergency Therapeutics**:

1. `MED_ANTI_SNAKE_VENOM` (Polyvalent Anti-Snake Venom Serum)
2. `MED_ANTI_RABIES_VACCINE` (Purified Chick Embryo Cell Vaccine)
3. `MED_OXYTOCIN` (Obstetric hemorrhage prevention; strict cold-chain $2^\circ\text{C}-8^\circ\text{C}$)
4. `MED_AMOXICILLIN` (Pediatric respiratory infection dispersible tablets)
5. `MED_PARACETAMOL_IV` (Pediatric fever / sepsis management)
6. `MED_RINGER_LACTATE` (Emergency fluid resuscitation)
7. `MED_ARTESUNATE` (Severe falciparum malaria injectables)
8. `MED_MAGNESIUM_SULPHATE` (Eclampsia management in primary labor rooms)
9. `MED_CEFTRIAXONE` (Broad-spectrum neonatal sepsis injectable)
10. `MED_ZINC_ORS` (Pediatric diarrheal dehydration kits)
11. `MED_INSULIN_REGULAR` (Diabetes ketoacidosis stabilization; cold chain)
12. `MED_RABIES_IMMUNOGLOBULIN` (Category III animal bite infiltration)
13. `MED_DEXAMETHASONE` (Acute respiratory distress / anaphylaxis)
14. `MED_ADRENALINE` (Anaphylactic shock auto-injectable vials)
15. `MED_MISOPROSTOL` (Postpartum hemorrhage second-line defense)

---

## 3. The Hero Workflow Architecture

```
1. UNVERIFIED CLAIM
   [e-Aushadhi claims: PHC Tokapal has 200 vials of Anti-Rabies Vaccine]
         │
         ▼
2. PHYSICAL USABLE RECONCILIATION
   [Pharmacist counts physically: 40 vials unexpired, 120 expired, 40 phantom gap]
   [Tathyon sets usable_quantity = 40.0; isolates 160 phantom/expired units]
         │
         ▼
3. DEMAND HAZARD & PREDICTION
   [TSB intermittent model predicts: 14 vials/day consumption, runway = 2.8 days]
   [Next scheduled central replenishment is 18 days away → P(Stockout) = 0.94]
         │
         ▼
4. RESILIENCE CASE OPENED
   [ResilienceCase CASE-2026-0089 created; status: DETECTED → PLANNING]
         │
         ▼
5. EMERGENCY SHOCK OVERLAY
   [Dengue surge + Stray dog bite cluster declared; demand multiplier = 2.2x]
   [Runway collapses to 1.3 days; network impact projects 40 diverted patients]
         │
         ▼
6. DONOR DISCOVERY & CONSTRAINED PLANNING
   [OR-Tools CP-SAT evaluates candidate donors within 100km radius]
   [Rejects PHC Bastanar: stock below 14d safety buffer]
   [Selects CHC Jagdalpur: 180 usable vials, 21d buffer remaining after transfer]
         │
         ▼
7. COUNTERFACTUAL Resilience Scenario Engine
   [Runs side-by-side simulation: Plan A covers 14d; Do-Nothing leads to 40 unserved patients]
         │
         ▼
8. policy-based HUMAN APPROVAL
   [CMO Dr. Verma authenticates via X-Role: medical_officer]
   [Signs transfer order under Epidemic Diseases Act 1897 & GFR 2017 Rule 22]
         │
         ▼
9. SYSTEM-OF-RECORD PAYLOAD
   [Emits verified DVDMS Indent V2 & HL7 FHIR R4 Bundle: READY_FOR_SYSTEM_OF_RECORD]
         │
         ▼
10. PHYSICAL RECEIPT & CLOSED-LOOP LEARNING
   [Delivery arrived in 3.2 hours; 58 vials received intact; 2 broken in transit]
   [Tathyon logs delivery variance, increments recipient usable stock, and records route friction]
```

---

## 4. Expansion Architecture: The 4 Horizons

```
Horizon 1: Critical Emergency Medicines & Vaccines (Active Wedge)
      ↓
Horizon 2: Equipment Maintenance & BEMMP Up-Time Verification (SLA Invoice Penalties)
      ↓
Horizon 3: Blood Bank & ICU Emergency Bed Availability (District Network Rebalancing)
      ↓
Horizon 4: Cross-State & BRICS Bilateral Federated Pandemic Surveillance (Sovereign DP-FedAvg)
```
