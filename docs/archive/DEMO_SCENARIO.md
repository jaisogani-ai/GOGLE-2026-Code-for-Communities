# TATHYON Final 3-Minute Hero Demo Scenario

**Document:** `docs/DEMO_SCENARIO.md`  
**Standard:** Section 39 & Section 40 of CTO War-Room Directive  

---

## 1. Demo Parameters & Setup

* **Geography:** Bastar District, Chhattisgarh (Aspirational / Tribal Health District).
* **Facilities Involved:**
  * **Shortage Facility:** `PHC_TOKAPAL` (Tokapal Primary Health Centre — rural, acute trauma & animal bite catchment).
  * **Surplus Donor Facility:** `CHC_JAGDALPUR` (Jagdalpur Community Health Centre — sub-divisional hub, 28 km away).
  * **Rejected Donor Facility:** `PHC_BASTANAR` (Bastanar Primary Health Centre — evaluated but rejected to protect safety floor).
* **Target Commodity:** `MED_ANTI_RABIES_VACCINE` (Life-saving anti-rabies vaccine vials).
* **Emergency Shock:** Acute rabies cluster surge (+120% demand acceleration) combined with a 14-day delay in central warehouse replenishment.

---

## 2. The 10-Step Demo Narrative (Exact Script)

### Step 1: "The System Says We Have Stock" (0:00 – 0:18)
* **Presenter:** "Open the state inventory portal or DVDMS. What does it show? Tokapal PHC reports 200 vials of Anti-Rabies Vaccine. On paper, the district looks completely safe."
* **Screen:** `GET /graph` showing `claimed_quantity: 200.0`.

### Step 2: "TATHYON Shows What Is Actually Usable" (0:18 – 0:38)
* **Presenter:** "Now look at TATHYON's Resource Truth Engine. When the pharmacist counted physical stock yesterday, 120 vials were expired, and 40 vials were missing book discrepancies. Real usable stock is not 200—it is exactly **40 vials**."
* **Screen:** `GET /graph` highlighting `usable_quantity: 40.0`, `phantom_inventory: 40.0`, `expired_quantity: 120.0`.

### Step 3: "This Facility Will Fail in 2.8 Days" (0:38 – 0:58)
* **Presenter:** "Under normal burn of 14 vials/day, 40 vials lasts 2.8 days. But with an active animal bite surge in the tribal block, demand is accelerating. The central warehouse supply truck is 18 days away. TSB intermittent forecasting computes a **94.2% probability of total stockout** within 72 hours."
* **Screen:** `GET /risk` displaying ranked card: `PHC_TOKAPAL`, `P(Stockout) = 0.94`, `Runway: 2.8d`, `Severity: CRITICAL`.
* **Action:** An operational case `CASE-2026-0089` is automatically created.

### Step 4: "If Nothing Happens, The Network Impact Is..." (0:58 – 1:18)
* **Presenter:** "What happens if Tokapal stocks out? Patients are turned away and diverted 28 km down rural roads to Jagdalpur, overwhelming the referral emergency ward and triggering secondary shortages across the district."
* **Screen:** `POST /twin/run` showing Baseline (Do Nothing): 42 diverted patients, 14 days of unmet demand, red cascade risk.

### Step 5: "These Are The Feasible Responses" (1:18 – 1:40)
* **Presenter:** "Can we transfer stock from neighboring facilities? TATHYON explores candidate donors using OR-Tools CP-SAT. Bastanar PHC has 30 vials, but TATHYON **rejects** it because donating would drop Bastanar below its own mandatory 14-day safety floor."
* **Screen:** `POST /plans/generate` displaying candidate plans and the **Rejected Donors Audit Table**:
  * `PHC_BASTANAR`: `REJECTED — BELOW_SAFETY_FLOOR (Buffer would drop to 4 days)`.
  * `CHC_JAGDALPUR`: `ELIGIBLE — 180 usable vials, 28km distance`.

### Step 6: "This Plan Preserves Donor Safety While Covering Tokapal" (1:40 – 2:00)
* **Presenter:** "TATHYON generates Plan A (Closest Donor): Transfer 60 vials from Jagdalpur to Tokapal. Tokapal recovers 14.5 days of runway. Jagdalpur retains 120 vials—preserving 21 days of reserve. Zero donor safety compromise."
* **Screen:** Counterfactual Twin Plan Comparison (Plan A vs Plan B vs Do Nothing).

### Step 7: "Authorized Human Approval" (2:00 – 2:20)
* **Presenter:** "Nothing moves autonomously. TATHYON presents the transfer ticket to Chief Medical Officer Dr. Verma. Under GFR 2017 Rule 22 and Section 2(1) of the Epidemic Diseases Act, CMO signs off."
* **Screen:** `POST /plans/{id}/approve` with `X-Role: medical_officer`. Immutable hash chain event committed.

### Step 8: "Ready For System-of-Record" (2:20 – 2:35)
* **Presenter:** "TATHYON does not hack the state database. It generates verified, ready-to-import payloads: a state DVDMS Transfer Indent Voucher, an RFC 4180 CSV, and a standards-compliant HL7 FHIR R4 Bundle—all stamped `READY_FOR_SYSTEM_OF_RECORD`."
* **Screen:** `GET /plans/{id}/export/dvdms` and `GET /plans/{id}/fhir`.

### Step 9: "Actual Delivery & Outcome Verification" (2:35 – 2:50)
* **Presenter:** "The physical courier arrives. 58 vials received in good condition, 2 broken in transit. Tokapal storekeeper logs physical receipt."
* **Screen:** `POST /outcomes` recording actual delivery: 58 vials, 3.2 hours transit time. Tokapal usable inventory immediately updates from 40 to 98 vials.

### Step 10: "This Outcome Becomes New Operational Data" (2:50 – 3:00)
* **Presenter:** "The loop closes. The delivery loss and route delay are recorded in TATHYON's proprietary data moat, training future route friction scores and supplier reliability ratings. This is the operating system for healthcare resource resilience."
* **Screen:** `GET /cases/{id}` showing status `CLOSED`, outcome verified, feedback integrated.

---

## 3. Demo Truth & Boundary Checklist

| Assertion | Verification Standard |
|---|---|
| No fake government API connection | Payload explicitly stamped `READY_FOR_SYSTEM_OF_RECORD: PAYLOAD_STAGED_NOT_EXECUTED_EXTERNALLY`. |
| No fake live BRICS cross-border feed | Cross-border federation labeled `SIMULATED_FEDERATION`. |
| No AI hallucinated numbers | Inventory arithmetic and CP-SAT optimization executed deterministically. |
| Reproducible execution | Seed `42`, deterministic graph generation, sub-second API response. |
