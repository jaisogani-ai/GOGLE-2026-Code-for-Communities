# TATHYON Transformation Plan
## From Hackathon Prototype → National-Scale Healthcare Supply-Chain Resilience OS

---

## Executive Summary

The current TATHYON repository is a **technically impressive prototype** with:
- 262 passing tests
- Working end-to-end pipeline on synthetic data
- Honest limitations documentation
- Real OR-Tools CP-SAT optimization
- Real federated learning (FedAvg/FedProx)
- Real resilience scenario engine simulation
- Real verification gate with phantom inventory prevention

**Critical gaps for demonstration deployment:**
1. **No real data integrations** — only synthetic parquet adapters
2. **No national-scale architecture** — single-district toy graph
3. **No supplier network** — missing manufacturer→distributor→warehouse→facility
4. **No route intelligence** — Euclidean distance only, no road network/weather/flood risk
5. **No cold-chain product model** — VVM, excursion handling, quality assessment workflow
6. **No specialized chains** — blood, vaccine, oxygen, snakebite, rabies have distinct rules
7. **No hospital resilience view** — ICU consumables, oxygen, blood, bed capacity, procedure demand
8. **Simulated BRICS federation** — not sovereign adapters with real cross-border governance
9. **No offline-first field workflow** — queue/validate/sync/deduplicate/reconcile/acknowledge
10. **No data quality engine** — provenance, freshness, verification status per data point
11. **No case management lifecycle** — 10-stage durable case tracking
12. **Prototype auth only** — X-Role header, no signatures, no device keys, no delegation
13. **No mobile client** — offline evidence capture contract exists server-side only

---

## Phase 1: Core Architecture Redesign (Week 1-2)

### 1.1 National-Scale Configuration Layer
```
COUNTRY CONFIGURATION
    ↓
HEALTH SYSTEM CONFIGURATION (India: Central → State → District → Facility)
    ↓
POLICY CONFIGURATION (safety floors, attestation windows, approval authorities)
    ↓
RESOURCE MODEL (medicines, vaccines, equipment, beds, personnel, blood, oxygen)
    ↓
LOCAL IDENTIFIERS (LGD codes, HFR IDs, state-specific facility codes)
    ↓
LOCAL INTEGRATIONS (e-Aushadhi, DVDMS, eVIN, state LMIS, HMIS)
    ↓
COMMON RESILIENCE ENGINE (forecast → risk → twin → optimize → approve → outcome)
```

**Files to create:**
- `tathyon/country.py` — Country configuration registry
- `tathyon/health_system.py` — Health system hierarchy per country
- `tathyon/policy.py` — Policy configuration per health system level
- `tathyon/identifiers.py` — Local identifier mapping (LGD, HFR, state codes)
- `tathyon/integrations/` — Adapter framework for real systems

### 1.2 Real Ingest Adapters (Replace Synthetic Parquet)
**Priority adapters:**
1. `eAushadhiAdapter` — District warehouse → facility stock, indents, receipts
2. `DVDMSAdapter` — State medical corporation procurement, tenders, supplier data
3. `eVINAdapter` — Vaccine stock + temperature at cold chain points
4. `HMISAdapter` — Facility consumption, patient footfall, disease surveillance
5. `ABDM_HFR_Adapter` — Facility registry, master data
6. `StateLMISAdapter` — State-specific logistics platforms

**Interface contract:**
```python
class IngestAdapter(Protocol):
    def fetch_claims(self, since: datetime, facility_ids: list[str]) -> list[Claim]
    def fetch_supplier_data(self) -> list[Supplier]
    def fetch_facility_master(self) -> list[Facility]
    def push_transfer_order(self, sor_payload: dict) -> str  # returns SOR reference
```

### 1.3 Supplier Network Graph
**Entities:** Manufacturer → Distributor → Warehouse → Transporter → Facility
**Track:** capacity, lead_time, reliability, quality, geographic_exposure, concentration_risk
**Detect:** single-supplier, single-warehouse, single-route, single-region dependencies

**Files:** `tathyon/supplier_graph.py`, `tathyon/supplier_intelligence.py`

---

## Phase 2: Core Vertical Slice Deepening (Week 2-4)

### 2.1 Route Intelligence (Replace Haversine)
- Integrate OSRM / OpenStreetMap for real road networks
- Weather/flood/landslide risk from IMD / state disaster management
- Cold-chain feasibility per route segment
- Vehicle availability tracking
- Multi-modal transport (road, rail, air for remote)

**Files:** `tathyon/route_intelligence.py`, `tathyon/transport.py`

### 2.2 Cold-Chain Product-Specific Model
Per-product configuration:
- Required temperature range (2-8°C, -15 to -25°C, -70 to -80°C)
- VVM type and discard criteria
- Shake test protocol for freeze-sensitive
- Excursion handling: QUARANTINE → QUALITY_ASSESSMENT → AUTHORIZED_DECISION → USABLE/INVALIDATED
- Logger data integration (Berlinger Fridge-tag, ELPRO LIBERO, Sensitech TempTale)
- Manual inspection workflow

**Files:** `tathyon/cold_chain.py`, `tathyon/vvm.py`

### 2.3 Specialized Supply Chains
Each with distinct operational rules:
- **Vaccines** — eVIN integration, VVM, open vial policy, bundling
- **Blood** — eRaktKosh integration, shelf-life, cross-matching, component separation
- **Oxygen** — PSA plant status, cylinder tracking, concentrator availability
- **Snakebite** — ASV distribution, polyvalent vs monovalent, cold-chain
- **Rabies** — PEP/PrEP protocols, HRIG availability, cold-chain
- **Maternal** — Oxytocin, misoprostol, magnesium sulfate, cold-chain
- **Diagnostics** — RDT, GeneXpert cartridges, sample transport

**Files:** `tathyon/specialized_chains/`

### 2.4 Hospital Resilience View
Beyond PHC/CHC:
- ICU-critical consumables (sedatives, paralytics, vasopressors, antibiotics)
- Oxygen (liquid, cylinders, PSA, concentrators)
- Blood availability (whole, components, apheresis)
- Emergency supplies (trauma, burn, antidote)
- Bed capacity (ICU, HDU, ward, isolation)
- Procedure demand (surgeries, dialysis, ventilator days)
- Staff attendance (doctors, nurses, anesthetists, perfusionists) — *opt-in, privacy-preserving*

**Files:** `tathyon/hospital_resilience.py`

### 2.5 Offline-First Field Workflow
```
LOCAL (device) 
    ↓
QUEUE (IndexedDB / SQLite)
    ↓
VALIDATE (client-side schema, nonce freshness, perceptual hash)
    ↓
SYNC (background, retry with exponential backoff)
    ↓
DEDUPLICATE (client_event_id idempotency + artifact_hash + perceptual_hash)
    ↓
RECONCILE (server-side merge, conflict resolution)
    ↓
ACKNOWLEDGE (server receipt → client clears queue)
```

**Test scenarios:** offline, reconnect, duplicate event, conflicting event, stale event, replay, device clock error

**Files:** `tathyon/offline.py`, mobile client (React Native / Flutter / PWA)

### 2.6 Data Quality Engine
Every data point gets:
- source, timestamp, freshness, confidence, provenance, verification_status

**States:** VERIFIED, PROVISIONAL, STALE, CONFLICTED, QUARANTINED, MISSING, SYNTHETIC

**Freshness:** resource/context specific (not universal 48h)

**Files:** `tathyon/data_quality.py`

### 2.7 Case Management Lifecycle
```
DETECTED → INVESTIGATING → PLANNING → AWAITING_APPROVAL → APPROVED 
    → READY_FOR_SYSTEM_OF_RECORD → EXECUTING → DELIVERED → MEASURED → CLOSED
```

Preserves: timeline, evidence, decisions, rejected_options, approval, execution, outcome, lessons_learned

**Files:** `tathyon/cases.py` (extend existing), `tathyon/case_workflow.py`

---

## Phase 3: BRICS Federation & Scale (Week 4-6)

### 3.1 Sovereign Federation Architecture
Each country has:
- LOCAL DATA (never leaves)
- LOCAL MODELS (trained locally)
- LOCAL POLICY (sovereign)
- LOCAL IDENTIFIERS (LGD, CNES, SIS, etc.)
- LOCAL COMPUTE (in-country)

**Cross-border exchange:** Only model weights/gradients with:
- Secure aggregation (MPC/TEE)
- Differential privacy (formal ε accounting)
- Model drift monitoring
- Governance audit trail

**Files:** `tathyon/federation/` (replace simulated with real)

### 3.2 Common Resilience Engine + Country Adapters
```
COMMON RESILIENCE ENGINE
    + COUNTRY-SPECIFIC ADAPTERS
    = BRICS DEPLOYMENT
```

Shared: forecasting, risk, twin, optimizer, approval workflow, outcome learning
Country-specific: data model, identifiers, policy, integrations, regulations

---

## Phase 4: demonstration Hardening (Week 6-8)

### 4.1 Real Authentication
- Signed assertions bound to device keys (WebAuthn / passkeys)
- Delegation records (officer → facility in-charge → pharmacist)
- Attestor keypair in hardware (Android Keystore / iOS Secure Enclave)
- Session management, revocation, audit

### 4.2 Durable Event Store
- PostgreSQL + advisory locks for append-only
- Periodic root hash publication (external anchor)
- Load-and-verify path on startup
- Partitioning by facility/district/time

### 4.3 Mobile Client
- Offline evidence capture (nonce, 3-frame burst, perceptual hash)
- Background sync queue
- Attestation signing with device-bound key
- Multi-language (Hindi, English, regional)

### 4.4 GFR-22 Renderer
- Actual PDF/document generation
- Digital signature integration
- e-Aushadhi/DVDMS API submission

---

## Immediate Next Steps (This Session)

1. **Create country/health_system/policy configuration layer**
2. **Build e-Aushadhi/DVDMS adapter interfaces** (with mock implementations for testing)
3. **Extend ResourceGraph to multi-echelon (National→State→District→Warehouse→Hospital→CHC→PHC→SubCentre)**
4. **Add supplier network graph with concentration risk detection**
5. **Implement route intelligence with OSRM integration**
6. **Build cold-chain product model with VVM/excursion workflow**
7. **Create case management lifecycle with 10-stage tracking**
8. **Add offline-first sync engine**
9. **Run adversarial tests on vertical slice**
10. **Produce final architecture review (A-N per §55)**

---

## Success Criteria (CTO Tests from §58)

| Test | Current | Target |
|------|---------|--------|
| TEST 1: Gemini removed → works? | YES (mocked) | YES |
| TEST 2: Dashboard removed → engine works? | YES | YES |
| TEST 3: e-Aushadhi removed → adapters work? | NO (no adapters) | YES |
| TEST 4: No feasible plan → tells truth? | YES (NO_FEASIBLE_PLAN) | YES |
| TEST 5: Uncertain prediction → shows uncertainty? | YES (confidence, q_alpha) | YES |
| TEST 6: Connectivity lost → field ops continue? | NO (no offline) | YES |
| TEST 7: Human rejects → can reject? | YES (403/409/400) | YES |
| TEST 8: Delivery differs → learns? | YES (outcome feedback) | YES |
| TEST 9: India→Brazil → adapts? | NO (no country config) | YES |
| TEST 10: AI removed → still valuable? | YES (optimization, gate) | YES |

---

## Files to Modify/Create (Priority Order)

### New Core Architecture
- `tathyon/country.py` — Country registry
- `tathyon/health_system.py` — Hierarchy per country
- `tathyon/policy.py` — Policy configuration
- `tathyon/identifiers.py` — LGD, HFR, state codes
- `tathyon/integrations/__init__.py` — Adapter framework
- `tathyon/integrations/eaushadhi.py` — e-Aushadhi adapter
- `tathyon/integrations/dvdms.py` — DVDMS adapter
- `tathyon/integrations/evin.py` — eVIN adapter
- `tathyon/integrations/hmis.py` — HMIS adapter
- `tathyon/integrations/abdm.py` — ABDM HFR/HPR adapter

### Extended Core
- `tathyon/supplier_graph.py` — Supplier network
- `tathyon/supplier_intelligence.py` — Reliability, concentration risk
- `tathyon/route_intelligence.py` — OSRM, weather, flood risk
- `tathyon/transport.py` — Vehicle, multi-modal
- `tathyon/cold_chain.py` — Product-specific cold chain
- `tathyon/vvm.py` — VVM tracking, excursion handling
- `tathyon/specialized_chains/` — Vaccines, blood, oxygen, etc.
- `tathyon/hospital_resilience.py` — Hospital view
- `tathyon/offline.py` — Offline-first sync
- `tathyon/data_quality.py` — Per-point data quality
- `tathyon/case_workflow.py` — 10-stage case lifecycle
- `tathyon/auth.py` — Real authentication
- `tathyon/federation/` — Real BRICS federation

### Enhanced Existing
- `tathyon/graph.py` — Multi-echelon, country config
- `tathyon/schema.py` — Extended for new resource types
- `tathyon/planner.py` — Multi-echelon optimization
- `tathyon/twin.py` — National-scale twin
- `tathyon/api/main.py` — Real auth, new endpoints
- `web/index.html` — Multi-level navigation

---

## Research Anchors (Verified)

| Domain | Source | Key Finding |
|--------|--------|-------------|
| e-Aushadhi/DVDMS | Multiple state portals (UP, Odisha, Rajasthan, Jharkhand) | Covers purchase, supply, distribution, inventory across warehouses, hospitals, CHCs, PHCs |
| eVIN | WHO/UNDP assessments, 23 states, 27,000+ cold chain points | 80%+ stockout reduction, event-driven temp monitoring, VVM integration |
| State Medical Corps | RMSCL, KSMSC, TNMSC, AMSCL tenders | Centralized procurement, rate contracts, empanelment, quality testing, penalty clauses |
| ABDM HFR/HPR | Official sandbox docs, API specs | Facility registry, professional registry, consent, gateway, FHIR-ish |
| Cold Chain | WHO immunization handbook, CDSCO Schedule M | 2-8°C standard, VVM, excursion response, IQ/OQ/PQ validation |
| BRICS Health | WHO SEARO, country MoH sites | Common problems: shortages, epidemics, rural access, supplier concentration |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Adapter complexity | Start with mock adapters, incremental real integration |
| National scale performance | Partition event store, async processing, caching |
| BRICS data governance | Legal review per country, sovereign compute requirement |
| Offline sync conflicts | CRDT-like merge, last-writer-wins with audit trail |
| Cold-chain model complexity | Product-specific config, not code; validation rules as data |
| Hospital scope creep | Only resilience-relevant resources, not full HIS |

---

## Resource Allocation

| Role | Focus |
|------|-------|
| Architect (me) | Core architecture, country config, adapter framework |
| Backend Engineer | Ingest adapters, supplier graph, route intelligence |
| ML Engineer | Forecasting, federation, cold-chain models |
| Frontend Engineer | Multi-level UI, offline mobile client |
| DevOps | Durable store, auth, monitoring, deployment |
| Domain Expert | Policy config, specialized chains, regulatory review |
| QA/Red Team | Adversarial testing, benchmarking, security audit |

---

## Timeline

| Week | Deliverable |
|------|-------------|
| 1 | Country/health-system/policy config layer + mock adapters |
| 2 | Multi-echelon ResourceGraph + supplier network + route intelligence |
| 3 | Cold-chain model + specialized chains + hospital resilience |
| 4 | Offline-first sync + case management + data quality engine |
| 5 | Real BRICS federation + real authentication |
| 6 | Mobile client + GFR-22 renderer + durable store |
| 7 | Adversarial testing + benchmarking + red-teaming |
| 8 | Final architecture review + demo scenario + documentation |

---

## Definition of Done

Vertical slice works end-to-end for **one medicine at one PHC** with:
- Real e-Aushadhi/DVDMS adapter (mocked but interface-complete)
- Verified usable stock → demand forecast → risk → twin simulation → optimization → human approval → SOR payload → outcome recording → learning
- Offline field attestation → sync → reconciliation
- Case lifecycle tracked through all 10 stages
- All 10 CTO tests pass

Then expand: district → state → national → BRICS.