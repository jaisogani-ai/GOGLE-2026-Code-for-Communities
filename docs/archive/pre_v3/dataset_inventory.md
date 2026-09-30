# TATHYON Dataset and Source Inventory

## Overview & Evaluation Standards

This document establishes the official dataset inventory for the **TATHYON Health-Resource Verification Prioritization Engine**. In accordance with TATHYON Sovereign Boundary specifications and strict provenance constraints, candidate datasets from Indian government portals, research repositories, and open data platforms were systematically inventoried, audited, and analyzed for suitability in training and evaluating prioritization models across three resource modules:
1. **Medicines & Health Commodities** (18 causal trust features)
2. **Hospital Beds & Acute Capacity** (10 causal trust features)
3. **Clinical & Allied Personnel** (10 causal trust features)

---

## Strict Ground-Truth Label Audit Rule

Under TATHYON's validation governance, the target label for supervised training is **exclusively a human-attested material discrepancy**:
$$\text{Label} \in \{0, 1\}$$
- **$1$ (Material Discrepancy Verified):** An authorized, independent human auditor physically counted stock on the facility shelf or ward census and confirmed a material discrepancy adhering to statutory **Phase 6 (P6) thresholds**:
  - **Medicines:** Relative error $> 15\%$ ($|\text{reported} - \text{physical\_present}| / \text{physical\_present} > 0.15$) OR Usable share $< 70\%$ ($\text{physical\_usable} < 0.70 \times \text{reported}$).
  - **Beds:** Free bed gap $\ge 2$ beds OR relative gap $> 15\%$ ($|\text{reported\_free} - \text{physical\_free}| / \text{total\_beds} > 0.15$).
  - **Personnel:** Absence gap $\ge 3.0$ hours OR ghost worker ($0.0$ physically verified hours present during rostered duty).
- **$0$ (Reconciled / No Discrepancy):** An authorized human physically verified that the reported figure matches reality within P6 tolerance.
- **Unlabeled / Prohibited Substitutions:** Unknown, unreviewed, imputed, AI-generated, proxy-derived, or stockout-inferred rows **are not labels** and are strictly excluded from supervised training.

---

## Comprehensive Dataset & Candidate Source Inventory

### 1. OpenStreetMap (OSM) Healthcare Infrastructure (Bastar District, Chhattisgarh)
- **Direct Source:** OpenStreetMap Overpass API (`https://overpass-api.de/api/interpreter`), cached locally at `data/osm_bastar_facilities.json`.
- **Owner / Custodian:** OpenStreetMap Community & Contributors.
- **Geography:** Bastar District, Chhattisgarh, India (Bounding Box: Lat 18.95°N–19.30°N, Lon 81.70°E–82.15°E).
- **Date Range / Retrieval:** 2026-09-20 (snapshot cached locally; live tiling supported via `tathyon/osm_facilities.py`).
- **Collection Method:** Community-contributed GPS surveys, humanitarian mapping campaigns, remote satellite tracing, and OpenGovernmentData cross-referencing.
- **Schema:**
  - `osm_type` (`node`, `way`, `relation`)
  - `osm_id` (`int64`)
  - `name` (`str`)
  - `amenity` (`hospital`, `clinic`, `pharmacy`, `doctors`)
  - `lat`, `lon` (`float64`)
  - `raw_tags` (District, State, PIN, Healthcare classification)
- **License / Terms of Use:** Open Database License (ODbL) 1.0 (Attribution-ShareAlike).
- **Download Method:** Fully accessible via Overpass API HTTP GET query; offline cached copy included in `data/`.
- **Known Limitations:** Uneven rural coverage. Does not represent an official MoHFW facility census; unmapped sub-centres or remote health posts may be omitted.
- **Human-Attested Discrepancy Labels:** **NO**. Contains geographic locations and facility metadata only. Contains zero inventory ledgers, zero shelf counts, and zero discrepancy labels.

---

### 2. National Institute of Health & Family Welfare (NIHFW) Hospital Directory
- **Direct Source:** Open Government Data Platform India (`data.gov.in`), Ministry of Health and Family Welfare (MoHFW).
- **Owner / Custodian:** National Institute of Health and Family Welfare (NIHFW), Government of India.
- **Geography:** Pan-India (All States & Union Territories, District and Sub-district breakdown).
- **Date Range:** 2018–2024 (periodic administrative releases).
- **Collection Method:** Administrative compilation from State Health Directorates and National Health Mission reporting units.
- **Schema:**
  - `StateName`, `DistrictName`, `FacilityType` (PHC, CHC, SDH, DH)
  - `FacilityName`, `Address`, `Pincode`
  - `TotalBeds`, `RuralUrbanFlag`
- **License / Terms of Use:** Government Open Data License - India (GODL-India).
- **Download Method:** Direct CSV download or REST API via `data.gov.in` using API Key.
- **Known Limitations:** Static administrative capacity; static sanctioned bed counts rather than daily operational census. Lacks daily admissions, discharges, maintenance downtime, and ward bed types (ICU, O2, isolation).
- **Human-Attested Discrepancy Labels:** **NO**. Administrative directory only. Contains no physical count audits or discrepancy labels.

---

### 3. Development Data Lab SHRUG COVID Healthcare Capacity (DLHS-4 / Population Census)
- **Direct Source:** Development Data Lab (`devdatalab.org/covid`, DOI: 10.5281/zenodo.253523397).
- **Owner / Custodian:** Prof. Sam Asher (Johns Hopkins SAIS), Prof. Paul Novosad (Dartmouth College), Development Data Lab.
- **Geography:** 640 Districts of India (PC11 and LGD administrative boundaries).
- **Date Range:** 2011–2020 harmonized research data.
- **Collection Method:** Administrative census matching (Population Census 2011, Economic Census 2013, District Level Household Survey DLHS-4).
- **Schema:**
  - `pc11_state_id`, `pc11_district_id`
  - `tot_beds`, `pub_beds`, `priv_beds`
  - `num_doctors`, `num_nurses`
  - `rural_pop`, `urban_pop`
- **License / Terms of Use:** Creative Commons Attribution 4.0 International (CC BY 4.0).
- **Download Method:** Direct download via Zenodo / GitHub release archive (`github.com/devdatalab/covid`).
- **Known Limitations:** Cross-sectional decadal district aggregate. Does not track facility-day operational ledgers, daily stock movements, or ward-level bed statuses.
- **Human-Attested Discrepancy Labels:** **NO**. Academic census aggregation. Contains no physical verification audit labels.

---

### 4. CDAC DVDMS / e-Aushadhi Sovereign System of Record Export Shape
- **Direct Source:** State Health Procurement & Logistics Corporations (e.g. CGMSC Chhattisgarh, RMSCL Rajasthan, BMSICL Bihar) & National Informatics Centre (NIC).
- **Owner / Custodian:** State Health Departments & Ministry of Health and Family Welfare (MoHFW), Government of India.
- **Geography:** State-specific public health facilities (District Warehouses, DH, CHC, PHC, Sub-Centres).
- **Date Range:** Operational transaction log (daily rolling ledger).
- **Collection Method:** Storekeeper daily transaction entries (Goods Receipt Notes, Issue Vouchers, Dispensation logs, Physical Stock Taking modules).
- **Schema (Canonically mapped in `tathyon/adapters.py`):**
  - `facility_id` (`FacilityCode`, `StoreCode`)
  - `facility_name`, `district`, `state`
  - `sku` (`DrugCode`, `ItemCode`)
  - `drug_name`, `batch_number`, `expiry_date`
  - `reported_quantity` (`AvailableStock`, `ClosingBalance`)
  - `consumption_velocity` (`DailyConsumption`, `MonthlyConsumption`)
- **License / Terms of Use:** RESTRICTED / SOVEREIGN GOVERNMENT DATA. Not public domain. Ingestion into TATHYON operates as an offline, air-gapped sidecar via standard CSV/TSV dump exports provided by statutory custodians.
- **Download Method:** Secure authorized export by designated District Health Officer or Drug Warehouse Manager; no unauthorized scraping or API bypass permitted.
- **Human-Attested Discrepancy Labels:** **CONDITIONAL**. Standard inventory dumps reflect *claimed* system balances. Discrepancy labels exist **only when** paired with official physical verification stock-take logs (`attestation_log`) signed by an independent inspection officer where $\text{attester} \neq \text{custodian}$.
- **Audit Verification of `data/real/dvdms_ledger.csv` (2026-09-29):**
  - **Status: FILE PRESENT BUT UNVERIFIED.**
  - **Digest:** SHA-256 `8229d3e4c0343d4faedc25749f99996dbaab16c0be3f75e859d275f0ff02fcd8` (3,136,465 bytes, 18,000 rows).
  - **Origin:** Generated by `tathyon/generate_real_pilot_data.py` using Bastar OSM facility coordinates and NLEM essential drugs with simulated Poisson burn velocities and scripted audit draws (`rng.uniform < 0.28`).
  - **Audit Verdict:** This file is a benchmark simulation. It is **not** an authentic sovereign DVDMS export and contains zero genuine human attestations. It cannot be served as a real-data model. Live government connection remains `NOT_CONFIGURED`.

---

### 5. USAID SCMS / Global Health Supply Chain Shipment Pricing Dataset
- **Direct Source:** USAID Global Health Supply Chain Program via Kaggle (`jillanisofttech/supply-chain-shipment-pricing-data`, Dataset ID: 63711/124070) & USAID Data Services.
- **Owner / Custodian:** USAID Office of Inspector General & Global Health Bureau.
- **Geography:** International health supply procurement programs (Sub-Saharan Africa, South Asia, Latin America).
- **Date Range:** 2007–2015.
- **Collection Method:** Centralized supply-chain ERP purchase orders, freight tracking documents, and port-of-entry customs clearances.
- **Schema:**
  - `Country`, `Managed By`, `Fulfill Via`, `Vendor`
  - `Item Description`, `Dosage`, `Pack Price`, `Line Item Quantity`
  - `Scheduled Delivery Date`, `Delivered to Client Date`, `Delivery Recorded Date`
  - `Shipment Mode`, `Manufacturing Site`
- **License / Terms of Use:** U.S. Government Open Data (Public Domain / CC0).
- **Download Method:** Kaggle API / Direct Web Download.
- **Known Limitations:** National and international import procurement records. Does not describe Indian facility-level peripheral distribution, daily PHC/CHC store balances, or physical shelf audits.
- **Human-Attested Discrepancy Labels:** **NO**. Tracks delivery delays and landed freight costs; contains no facility shelf reconciliation or material discrepancy ground truth.

---

### 6. Comptroller & Auditor General of India (CAG) Performance Audit Reports
- **Direct Source:** Comptroller and Auditor General of India (e.g., Report No. 2 of 2022 — Government of Jharkhand on Public Health Infrastructure & Medicine Procurement; Report No. 5 of 2017 on National Health Mission).
- **Owner / Custodian:** Comptroller & Auditor General of India, Constitutional Audit Authority.
- **Geography:** State-specific (Jharkhand, Chhattisgarh, Madhya Pradesh, Odisha, Uttar Pradesh, etc.).
- **Date Range:** Published annually across multi-year audit cycles (e.g. 2014–2019 sample audits).
- **Collection Method:** Constitutional test-checks, physical verification sampling by audit inspection teams, and reconciliation of store physical counts against DVDMS ledgers.
- **Schema:** Published parliamentary/assembly reports containing aggregated error rates, anonymized facility audit findings, case studies of expired drugs held in active inventory, and uncalibrated cold-chain assets.
- **License / Terms of Use:** Public Statutory Report (Free to cite and anchor policy; raw operational ledgers not released to public).
- **Download Method:** Official portal download (`cag.gov.in`).
- **Known Limitations:** High-level audit summaries; does not provide machine-readable, continuous row-level time-series datasets of daily facility ledgers.
- **Human-Attested Discrepancy Labels:** **INFORMATIONAL ANCHORS ONLY**. Provided the statistical incidence anchors for TATHYON's error taxonomy (15% relative stock mismatch, 18% expired on shelf, 30% ghost duty gap), but cannot serve as raw training rows.

---

## Candidate Dataset Summary Matrix

| Source / Dataset Name | Owner / Custodian | Module | Geographic Scope | Open License? | Real Ledger? | Human Attested P6 Labels? | Operational Training Eligibility |
|---|---|---|---|---|---|---|---|
| **OSM Healthcare Bastar** | OpenStreetMap Community | Infra / Facilities | Bastar, Chhattisgarh | Yes (ODbL) | No (Geo only) | No | **Approved for Geo / Routing only** |
| **NIHFW Hospital Directory** | MoHFW / NIHFW | Beds / Facilities | Pan-India | Yes (GODL-India) | No (Directory) | No | **Approved for Facility Baseline** |
| **SHRUG COVID Platform** | Asher & Novosad (DDL) | Beds / Personnel | 640 Districts | Yes (CC BY 4.0) | No (Census agg) | No | **Approved for Prior Distribution** |
| **DVDMS / e-Aushadhi Export** | State Health Logistics Corp | Medicines | State / District | Sovereign Restr. | Yes (Ledger CSV) | Requires Audit Log | **Eligible IF paired with Field Attestations** |
| **USAID SCMS Global Logistics**| USAID | Logistics | Global / Multi-country | Yes (Public Domain) | No (Shipment POs)| No | **Ineligible (Wrong Geography & Granularity)**|
| **CAG Performance Audits** | CAG India | All | Selected States | Yes (Govt Report) | Aggregate only | Aggregate only | **Eligible as Policy Anchors; Ineligible as Rows**|

---

## Kaggle CLI Local Setup Instructions

If a practitioner or field operator wishes to inspect external research datasets via the official Kaggle CLI, they must configure local authentication without committing credentials into code:

1. **Install Kaggle CLI:**
   ```bash
   pip install kaggle
   ```
2. **Download API Token:**
   - Log into [Kaggle](https://www.kaggle.com).
   - Navigate to **Account Settings** $\rightarrow$ **API** $\rightarrow$ Click **Create New Token**.
   - A file named `kaggle.json` will be downloaded to your machine.
3. **Place in Local Secure Directory:**
   ```bash
   mkdir -p ~/.kaggle
   mv ~/Downloads/kaggle.json ~/.kaggle/
   chmod 600 ~/.kaggle/kaggle.json
   ```
4. **Verification:**
   ```bash
   kaggle datasets list -s "hospital inventory"
   ```
*Note: In compliance with TATHYON security rules, never commit `kaggle.json` into this repository or paste API credentials into Python scripts.*
