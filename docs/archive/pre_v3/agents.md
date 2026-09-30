# TATHYON AI Role Design and Prototype Boundaries

This document describes the intended contracts for six narrowly scoped roles. The repository has deterministic orchestration and fixture-backed handlers; it does not contain six trained AI agents. Some responses are hardcoded demo fixtures, and Gemini use is optional and credential-dependent. Do not use these roles as operational data sources until handlers are connected to authorized services and their outputs carry source provenance.

**Current implementation status:** tool allowlists and a deterministic dispatch loop exist. Several role handlers return static example records (including sample facility/resource values); the six role contracts below are target behavior, not proof that those integrations, persistence guarantees, real-data grounding, or Gemini calls are active. Outputs from these handlers are demo-only. Role F has no connected nodes or approved real model card; its handler must report unavailable values.

---

## 1. Core Architectural Invariant

**Never describe or implement an AI role as an autonomous decision-maker.**

In TATHYON, all state transitions, allocations, verification targeting, and escalation clocks are strictly governed by **deterministic, audited Python code**. 

### Primary Operating Principles:
1. **Server-Side Enforcement**: Tool allowlists are validated and enforced in server-side application code (`tathyon/ai_roles.py`), never left to prompt adherence alone.
2. **Zero Direct Mutability**: No AI role has direct write access to the database or authority to modify verified operational state.
3. **Mandatory Audit Trail**: Every function call request, validated argument set, tool execution result, and error is appended to the SHA-256 cryptographic event ledger with stable `[EVT-...]` references.
4. **Mandatory Advisory Notice**: Every generated explanation or recommendation displays:
   > *"AI suggestion — human decides."*
5. **No Google ADK**: Single-turn bounded tool execution loops are managed deterministically by `DeterministicRoleOrchestrator`. Multi-agent orchestration frameworks (like ADK) are excluded to prevent uncontrolled delegation cascades.

---

## 2. Role A — Intake Extraction

* **Trigger**: A new physical count sheet or inventory register lands in the ingest inbox.
* **Server-Side Tool Allowlist**:
  1. `extract_count_sheet`: Uses Gemini 2.5 Flash Vision to parse handwritten/printed numerals.
  2. `validate_schema`: Checks non-negativity, arithmetic consistency (`usable <= present`), and required keys.
  3. `quarantine_row`: Appends anomalous rows to the audit ledger and flags human review.
  4. `file_attestation_record`: Files clean rows as staged claims (`status="INTAKE_STAGED"`, `verified_state="UNVERIFIED"`).
* **Behavior & Safety Rules**:
  - Uncertain fields (<0.70 confidence) become `null`. The model **never guesses**.
  - Defends against prompt injection embedded in document text (quarantines injection attempts).
  - **Forbidden**: Cannot approve, allocate, verify, or edit verified values.
* **Data Boundary**: Accesses only the single uploaded document buffer.
* **Failure Behavior**: If unreadable, corrupted, or mathematically inconsistent, quarantines the record with a typed reason (`ARITHMETIC_ANOMALY`, `UNREADABLE_DOCUMENT`).

---

## 3. Role B — Data Quality Triage

* **Trigger**: A scheduled deterministic quality scan runs, or a newly ingested observation arrives.
* **Server-Side Tool Allowlist**:
  1. `read_observation`: Reads reported balance and timestamp for facility-resource pair.
  2. `compare_history`: Compares observation against 30-day trailing consumption baseline.
  3. `calculate_quality_flags`: Evaluates tier-1 arithmetic violations, reporting staleness (>30d), and volume anomalies.
  4. `create_review_recommendation`: Creates an advisory review ticket for human supervisors.
* **Behavior & Safety Rules**:
  - Finds duplicates, impossible values, stale records, and conflicting sources.
  - Outputs evidence-backed flags and suggested review priority (`HIGH`, `MEDIUM`, `LOW`).
  - **Forbidden**: Must not rewrite data, alter claims, or accuse a facility of fraud.
* **Data Boundary**: Reads historical facility consumption summaries only.
* **Failure Behavior**: Returns `LOW` priority advisory ticket if historical baseline is insufficient.

---

## 4. Role C — Demand Forecast Explainer

* **Trigger**: An authorized health officer inspects a facility forecast or inquires why a risk score changed.
* **Server-Side Tool Allowlist**:
  1. `read_forecast`: Reads deterministic Croston-SBA / TSB statistical forecast outputs.
  2. `read_feature_summary`: Reads causal feature weights driving the trust score.
  3. `read_data_freshness`: Checks elapsed days since last physical verification count.
  4. `explain_model_output`: Summarizes statistical model parameters and limitations.
* **Behavior & Safety Rules**:
  - Explains forecast inputs, time horizon, uncertainty intervals, and data freshness penalties in plain language.
  - **Forbidden**: Must not compute or invent numbers. Computation belongs 100% to `tathyon/forecast.py`. Never implies certainty.
* **Data Boundary**: Reads statistical model outputs and causal features only.
* **Failure Behavior**: If forecast data is missing, states plainly: *"Forecast model data is unavailable for this SKU."*

---

## 5. Role D — District Operations Copilot

* **Trigger**: An authorized health officer asks an operational question in the workspace.
* **Server-Side Tool Allowlist** (100% Read-Only):
  1. `query_trust_queue`: Reads prioritized facilities from the deterministic Knapsack queue.
  2. `get_facility_detail`: Looks up facility tiers, reported balances, and safety floors.
  3. `search_audit_log`: Queries the immutable SHA-256 event ledger.
  4. `get_allocation_plan`: Retrieves CP-SAT allocation proposals and rejected donors.
  5. `explain_event`: Explains cryptographic block hashes, actors, and ledger chain integrity.
* **Behavior & Safety Rules**:
  - Answers exclusively from tool results.
  - Cites `[EVT-...]` identifiers for every factual assertion.
  - Missing evidence returns standard fallback: *"I don't have that data."*
  - **Forbidden**: Explicitly refuses approval, veto, dispatch, allocation, or state modifications under TATHYON's safety policy. Configure human authority against applicable department/state procedures.
* **Data Boundary**: Read-only queries to the local district ledger and registry.
* **Failure Behavior**: Graceful fallback with zero hallucinated figures.

---

## 6. Role E — Allocation Plan Explainer

* **Trigger**: An officer opens a deterministic CP-SAT allocation plan.
* **Server-Side Tool Allowlist**:
  1. `read_allocation_plan`: Reads proposed transfer lines, fulfilled quantities, and shortfalls.
  2. `read_constraints`: Reads truck capacities and donor safety floors (14-day minimum runway).
  3. `read_facility_detail`: Inspects facility geographic location and tier.
  4. `explain_event`: Explains plan approval or rejection audit blocks.
* **Behavior & Safety Rules**:
  - Explains donor/recipient pairs, lead times, unverified stock gates, and counterfactuals.
  - **Forbidden**: Cannot edit, rank, approve, or execute transfer plans. The mathematical optimizer (`ortools`) produces the schedule.
* **Data Boundary**: Reads active plan object and facility metadata.
* **Failure Behavior**: Reports plan status as `NO_FEASIBLE_PLAN` if constraints prevent transfers.

---

## 7. Role F — Federation Model Steward

* **Trigger**: An authorized administrator reviews a federated model-round or aggregate metric.
* **Server-Side Tool Allowlist**:
  1. `read_federation_status`: Intended to read round ID, nodes, and synchronization state; currently returns not connected.
  2. `read_model_card`: Intended to read an approved model card; currently returns unavailable.
  3. `read_aggregate_metrics`: Intended to read validated outcome metrics; currently returns unavailable.
  4. `create_review_recommendation`: Creates an advisory review ticket for cross-border alignment.
* **Behavior & Safety Rules**:
  - Explains participating nodes, aggregated model weights, and structural anomaly patterns.
  - **Zero Raw Data Principle**: Strictly barred from accessing another district/country's raw inventory rows, patient records, or transaction logs.
  - **Forbidden**: Cannot trigger training rounds, publish models, or alter federation configuration.
* **Data Boundary**: Aggregated model metadata and structural pattern signatures only.
* **Failure Behavior**: Refuses raw data queries with typed refusal `RAW_DATA_ACCESS_FORBIDDEN`.

---

## 8. Cross-Cutting Security & Authorization Matrix

| Control Dimension | Role A | Role B | Role C | Role D | Role E | Role F |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Max Tool Calls / Turn** | 4 | 4 | 4 | 5 | 4 | 4 |
| **Execution Timeout** | 10.0 s | 5.0 s | 5.0 s | 5.0 s | 5.0 s | 5.0 s |
| **Direct DB Write** | No | No | No | No | No | No |
| **Direct State Mutation** | Staged Only | No | No | No | No | No |
| **Consequential Action** | Blocked | Blocked | Blocked | Blocked | Blocked | Blocked |
| **Mandatory Disclaimer** | Yes | Yes | Yes | Yes | Yes | Yes |
| **Ledger Audit Event** | `EXTRACTED` | `DECISION_MADE`| `DECISION_MADE`| `DECISION_MADE`| `DECISION_MADE`| `DECISION_MADE`|
| **Blocked Call Logged** | `FLAGGED` | `FLAGGED` | `FLAGGED` | `FLAGGED` | `FLAGGED` | `FLAGGED` |
