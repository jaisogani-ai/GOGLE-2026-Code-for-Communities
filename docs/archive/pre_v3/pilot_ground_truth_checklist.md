# TATHYON Pilot Ground-Truth Collection & Human-Attestation Checklist (P6)

## Purpose & Authority

This checklist governs the **physical ground-truth collection and human attestation procedure** for public health facilities (District Hospitals, Community Health Centres, Primary Health Centres). 

Under the proposed TATHYON pilot data policy:
- **No machine-learning model or automated system can generate ground-truth labels.**
- **No row may be assigned a discrepancy label based on stockout status, missing values, or AI inference.**
- The label `is_materially_wrong` is created **only** from an authorized human's documented physical count or inspection under the approved pilot protocol. P6 is a TATHYON pilot threshold, not a statutory threshold.

---

## 1. TATHYON Pilot Role Separation & Anti-Rubber-Stamping Policy

This section is a proposed pilot control, not a statement of statutory requirements. Confirm the applicable department and state procedures before field use. Where GFR 2017 Rule 213 applies, physical verification is conducted in the presence of the officer responsible for custody; TATHYON's separate-verifier control is an additional pilot safeguard, not a substitute for that requirement.

1. **Independent Inspector Requirement:**
   - $\text{Attester} \neq \text{Custodian}$.
   - The inspecting officer **must not** be the storekeeper, pharmacist-in-charge, or inventory custodian who entered the ledger records.
   - Authorized roles: `district_drug_inspector`, `quality_medical_officer`, `block_health_officer`, `district_verification_team`.
2. **Anti-Rubber-Stamping Minimum Time Requirement:**
   - A physical verification visit must spend **at least 20.0 seconds per SKU / resource** on site examining packaging, count integrity, and expiry dates.
   - Any attestation logged with `< 20.0` seconds is automatically flagged as invalid (`INVALID_ATTESTATION_TIME`) and quarantined.
3. **Physical Verification Scope:**
   - Blind or unanchored count: The inspector records physical units present on shelves before reviewing claimed ledger quantities.

---

## 2. Phase 6 (P6) Material-Discrepancy Thresholds

An attestation produces binary label `is_materially_wrong`:
- `is_materially_wrong = 1`: Physical reality diverges from reported ledger by at least the P6 threshold.
- `is_materially_wrong = 0`: Physical reality verifies the reported ledger within P6 tolerance.

### Module A: Medicines & Health Commodities
For each audited SKU at a facility:
$$\text{Relative Error} = \frac{|\text{Reported Quantity} - \text{Physical Present Quantity}|}{\max(\text{Physical Present Quantity}, 1.0)}$$
$$\text{Usable Share} = \frac{\text{Physical Usable Quantity}}{\max(\text{Reported Quantity}, 1.0)}$$

**P6 Condition for `is_materially_wrong = 1`:**
1. **Quantity Discrepancy:** $\text{Relative Error} > 0.15$ (discrepancy exceeds $15\%$ of actual shelf stock); **OR**
2. **Quality / Shelf Spoilage Discrepancy:** $\text{Usable Share} < 0.70$ (less than $70\%$ of reported stock is usable due to expiry, broken seal, cold-chain excursion, or water damage).

### Module B: Hospital Beds & Acute Capacity
For each audited ward bed type (General, Maternity, Pediatric, ICU/Oxygen, Isolation):
$$\text{Free Bed Gap} = |\text{Reported Vacant Beds} - \text{Physical Empty Staffed Beds}|$$

**P6 Condition for `is_materially_wrong = 1`:**
1. **Absolute Free Bed Gap:** $\text{Free Bed Gap} \ge 2$ beds; **OR**
2. **Relative Ward Capacity Gap:** $\frac{\text{Free Bed Gap}}{\max(\text{Total Ward Beds}, 1)} > 0.15$ ($>15\%$ capacity error).
*(Examples: Phantom vacant bed marked open on dashboard while physically occupied; or broken oxygen flowmeter preventing admission).*

### Module C: Clinical & Allied Personnel
For each rostered duty shift (Medical Officer, Staff Nurse, Lab Technician, Pharmacist):
$$\text{Hours Gap} = |\text{Reported Shift Hours} - \text{Physically Verified Present Hours}|$$

**P6 Condition for `is_materially_wrong = 1`:**
1. **Significant Absence:** $\text{Hours Gap} \ge 3.0\text{ hours}$; **OR**
2. **Ghost Worker / Complete Absence:** $\text{Physically Verified Present Hours} == 0.0$ (employee signed in on register/biometric but absent during clinical shift).

---

## 3. Pilot Field Attestation Data Schema (CSV / TSV)

Field inspection teams record attestations in standard CSV format (`pilot_attestations.csv`) for ingestion into the TATHYON pipeline:

```csv
attestation_id,facility_id,resource_key,inspection_timestamp,attester_id,attester_role,is_custodian,reported_quantity,physical_present_quantity,physical_usable_quantity,seconds_spent,notes,is_materially_wrong
ATT-2026-001,FAC_BASTAR_DH,MED-INJDEX,2026-10-01T10:30:00Z,OFFICER_DRUG_INSP_04,district_drug_inspector,false,500.0,320.0,320.0,145.0,"Physical count reveals 180 vial deficit",1
ATT-2026-002,FAC_BASTAR_DH,MED-ORS01,2026-10-01T10:45:00Z,OFFICER_DRUG_INSP_04,district_drug_inspector,false,1200.0,1180.0,1180.0,95.0,"Within 1.7% tolerance; verified clean",0
ATT-2026-003,FAC_TOKAPAL_CHC,MED-AMX250,2026-10-01T14:15:00Z,OFFICER_DRUG_INSP_04,district_drug_inspector,false,800.0,800.0,400.0,180.0,"400 strips expired 2026-08; unusable on shelf",1
```

### Mandatory Validation Checks Performed by Pipeline:
1. `is_custodian == false` (Must be false; self-attestation is strictly quarantined).
2. `seconds_spent >= 20.0` (Anti-rubber-stamping check).
3. `reported_quantity >= 0` and `physical_present_quantity >= 0`.
4. `physical_usable_quantity <= physical_present_quantity` (Usable stock cannot exceed total present stock).
5. `is_materially_wrong in (0, 1)`: Must match exact P6 formula evaluated against quantities.

---

## 4. Field Protocol Checklist for Inspection Officers

- [ ] **Step 1: Identity & Role Verification**
  - Verify inspection officer credentials. Ensure officer has no custodial or storekeeping duties at the facility.
- [ ] **Step 2: Unanchored Physical Shelf Count**
  - Conduct independent count of units on shelves, cold boxes, and store racks.
  - Inspect batch numbers and expiry dates directly on packaging.
  - Count separately: (a) Total physically present, and (b) Usable units (unexpired, intact cold chain).
- [ ] **Step 3: Access Claimed System Ledger**
  - Record the quantity claimed in DVDMS/e-Aushadhi register as of the inspection time.
- [ ] **Step 4: Compute P6 Threshold & Discrepancy Status**
  - Calculate relative error: $|\text{claimed} - \text{present}| / \text{present}$.
  - Calculate usable share: $\text{usable} / \text{claimed}$.
  - If error $> 15\%$ or usable $< 70\%$, classify as `is_materially_wrong = 1`.
  - Otherwise, classify as `is_materially_wrong = 0`.
- [ ] **Step 5: Sign & Timestamp**
  - Record duration in seconds spent inspecting the item.
  - Log findings in field attestation ledger and upload CSV to `data/pilot/` directory for offline ingestion.
