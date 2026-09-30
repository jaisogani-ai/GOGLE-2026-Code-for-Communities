# TATHYON Privacy-Preserving Federation — Architecture, Threat Model & Model Card

**Document status:** DESIGN AND FUTURE-WORK SPECIFICATION.
This document describes the intended architecture for a privacy-preserving
federation capability. **Federation is NOT production-ready in this repository.**
The repository contains the data-contract layer, sanitization gate, model-lifecycle
state machine, and a clearly-labelled synthetic demo. No real multi-country or
multi-organization environment is connected or approved for operational use.

---

## Contents

1. [What Is and Is Not Implemented](#1-what-is-and-is-not-implemented)
2. [Architecture and Data-Flow Diagram](#2-architecture-and-data-flow-diagram)
3. [Threat Model](#3-threat-model)
4. [Privacy Analysis — Secure Aggregation and Differential Privacy](#4-privacy-analysis)
5. [Participant Onboarding and Trust Assumptions](#5-participant-onboarding-and-trust-assumptions)
6. [Model Versioning, Validation, and Rollback](#6-model-versioning-validation-and-rollback)
7. [Safeguards Against Known Failure Modes](#7-safeguards-against-known-failure-modes)
8. [Country-Boundary Enforcement Plan](#8-country-boundary-enforcement-plan)
9. [Incident Response](#9-incident-response)
10. [Model Card Template](#10-model-card-template)
11. [Release Checklist](#11-release-checklist)
12. [Demo Mode](#12-demo-mode)
13. [Open Governance, Legal, Clinical, and Privacy Questions](#13-open-questions-requiring-jurisdiction-specific-review)

---

## 1. What Is and Is Not Implemented

### Implemented and tested (`tathyon/federation.py`, `tests/test_federation.py`)

| Component | Status | Where |
|---|---|---|
| `FederatedWeightPackage` — unit of sovereign exchange | **IMPLEMENTED** | `federation.py` |
| `sanitize_and_export_weights` — PII/facility-ID hard-fail gate | **IMPLEMENTED** | `federation.py` |
| `FederatedTrustAggregator` — observation-weighted FedAvg with quorum/min-obs guards | **IMPLEMENTED** | `federation.py` |
| `PhantomAnomalyPattern` — structural signatures without identifiers | **IMPLEMENTED** | `federation.py` |
| `ModelLifecycleState` FSM — AVAILABLE → EVALUATED → APPROVED | **IMPLEMENTED** | `federation.py` |
| `ModelRegistry` — versioning, rollback, poisoning delta-importance guard | **IMPLEMENTED** | `federation.py` |
| `SyntheticFederationDemo` — deterministic two-node demo, clearly labelled | **IMPLEMENTED** | `federation.py` |
| Limitations disclosure in every aggregate response | **IMPLEMENTED** | `federation.py` |

### Not yet implemented (future work)

| Component | Reason not implemented |
|---|---|
| Secure aggregation (cryptographic masking) | Requires key-management infrastructure and HSM integration not present in the repository |
| Differential privacy (ε-δ noise on exported weights) | Requires calibrated sensitivity analysis per feature, which depends on real training data distribution |
| Network transport layer (mTLS, node certificates) | Requires a coordination service outside this single-process demo |
| Real country-node onboarding | Requires executed legal agreements, data protection agreements, and sovereign IT review |
| Coordinator service | Requires a separate process with its own audit log and identity store |
| k-anonymity guard on pattern library | Requires minimum-k facility count per contributing pattern |
| Byzantine-fault-tolerant aggregation | FedAvg is not Byzantine-robust beyond the delta threshold; Krum or median-based aggregation not implemented |

> **Reader guidance:** The absence of secure aggregation and differential privacy is
> not a gap that will be closed by future code alone. Both require jurisdiction-specific
> policy decisions about acceptable ε (privacy loss budget), utility trade-offs, and
> legal authorization. These are recorded in §13.

---

## 2. Architecture and Data-Flow Diagram

### 2.1 System Roles

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  LOCAL NODE  (one per participating country or district)                    │
│                                                                             │
│  ┌─────────────┐   ┌──────────────────┐   ┌──────────────────────────────┐ │
│  │  Raw local  │   │  Local Trust     │   │  Sanitization Gate           │ │
│  │  data store │──▶│  Scorer training │──▶│  sanitize_and_export_weights │ │
│  │  (STAYS     │   │  (local only)    │   │  (hard-fail on PII tokens)   │ │
│  │   HERE)     │   └──────────────────┘   └──────────────┬───────────────┘ │
│  └─────────────┘                                         │                 │
│                                                          │ FederatedWeightPackage │
│  ┌────────────────────────────────────────────────────── │ ───────────────┐│
│  │  ModelRegistry                                        │               ││
│  │  ┌────────────┐   ┌───────────────┐   ┌─────────────▼──────────────┐ ││
│  │  │ AVAILABLE  │──▶│  EVALUATED    │──▶│  APPROVED                  │ ││
│  │  │ (received) │   │  (validated)  │   │  (authorized by officer)   │ ││
│  │  └────────────┘   └───────────────┘   └────────────────────────────┘ ││
│  │         │                │                                             ││
│  │         ▼                ▼                                             ││
│  │    QUARANTINED      QUARANTINED (poisoning guard or audit trip)       ││
│  └─────────────────────────────────────────────────────────────────────── ┘│
└─────────────────────────────────────────────────────────────────────────────┘
          │ (outbound only, via authenticated channel — NOT YET IMPLEMENTED)
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│  COORDINATION SERVICE  (not yet implemented — described as future work)     │
│                                                                             │
│  • Receives FederatedWeightPackages from authorized nodes only             │
│  • Runs FederatedTrustAggregator (quorum ≥ 2, obs ≥ 5,000 enforced)       │
│  • Publishes aggregate to all participating nodes (read-only push)         │
│  • Maintains coordination audit log (separate from local audit logs)       │
│  • No access to any local raw data store                                   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Data-Flow: What Moves and What Does Not

```
LOCAL NODE                         COORDINATION SERVICE
──────────────────────────────     ──────────────────────────────────────────
[Raw facility observations]  ─── NEVER CROSSES BOUNDARY ─── ✗
[Patient records]            ─── NEVER CROSSES BOUNDARY ─── ✗
[Event-store payloads]       ─── NEVER CROSSES BOUNDARY ─── ✗
[Facility coordinates]       ─── NEVER CROSSES BOUNDARY ─── ✗
[Fitted model binary]        ─── NEVER CROSSES BOUNDARY ─── ✗

[Feature importances]        ──────── sanitized ──────────→ Coordinator
[Anomaly pattern library]    ──────── no IDs ─────────────→ Coordinator

                                      ↓  FedAvg aggregate (read-only)
[Aggregate weights]          ←──────── push ────────────── Coordinator
```

### 2.3 Three-State Lifecycle Gate

```
              Aggregate arrives
                     │
                     ▼
             ┌───────────────┐
             │   AVAILABLE   │ ← Coordinator publishes here
             │  read-only    │   No operational use permitted
             └──────┬────────┘
                    │  local validation passes
                    ▼
             ┌───────────────┐
             │   EVALUATED   │ ← Automated local checks pass
             │  research only│   Not yet authorized
             └──────┬────────┘
                    │  authorized officer explicitly releases
                    ▼
             ┌───────────────┐
             │   APPROVED    │ ← Operational use permitted
             │  + checklist  │   Local forecasts may use aggregate
             └──────┬────────┘
                    │
           ┌────────┴──────────┐
           ▼                   ▼
    ROLLED_BACK           QUARANTINED
  (officer action)      (poisoning guard
                         or audit event)
```

---

## 3. Threat Model

### 3.1 Scope

The federation capability shares only model weights and structural anomaly
signatures. The primary threats are therefore:

1. **Data exfiltration** — raw facility data leaks into shared objects.
2. **Model poisoning** — a malicious or compromised node submits weights that
   degrade aggregate accuracy or bias decisions toward a particular outcome.
3. **Data leakage through the aggregate** — when the number of nodes is small,
   individual node importances may be inferred from the aggregate.
4. **Unauthorized operational use** — an aggregate in AVAILABLE or EVALUATED
   state influences operational forecasts or allocations before local validation.
5. **Impersonation / replay** — a node submits a forged or replayed package.

### 3.2 Assets

| Asset | Value | Primary threat |
|---|---|---|
| Raw facility inventory records | Patient-facing operational | Direct exfiltration |
| Local trust scores and allocations | Decision-making | Unauthorized influence from unapproved aggregate |
| Coordinator's node registry | Participation metadata | Nation-state adversary |
| Individual node weights (before aggregation) | Research-sensitive | Inference from small-n aggregate |

### 3.3 Adversaries

| Adversary | Capability | Goal |
|---|---|---|
| Compromised participating node | Can submit any weight package | Model poisoning; inflate risk of specific facilities in partner countries |
| Dishonest coordinator | Sees all submitted packages before aggregation | Learn individual node importances; selectively withhold packages |
| External network attacker | Can intercept traffic between node and coordinator | Replay attack; man-in-the-middle |
| Malicious insider at local node | Has raw data access and model-training capability | Encode facility-level information in feature importances |
| Regulatory or nation-state adversary | Legal compulsion | Force disclosure of node identity or contribution |

### 3.4 Controls Implemented in This Repository

| Threat | Control | Where | Strength |
|---|---|---|---|
| Raw data exfiltration | `sanitize_and_export_weights` forbidden-token gate | `federation.py` | **Strong** — hard-fail, 100% rejection |
| Model poisoning (obvious) | Delta-importance threshold guard in `ModelRegistry.register_available` | `federation.py` | **Moderate** — heuristic, not cryptographic |
| Unauthorized operational use | `ModelLifecycleState` FSM; `current_operational_aggregate()` returns `None` until APPROVED | `federation.py` | **Strong** — state-machine enforced |
| Unverified package ingestion | `FederatedTrustAggregator.add_package` rejects `sanitization_verified=False` | `federation.py` | **Moderate** — flag is set by the sanitizer; not independently verified |
| Small-n inference risk | `MIN_OBSERVATIONS=5000` and `MIN_QUORUM=2` enforced before aggregation | `federation.py` | **Partial** — threshold is conservative but not a provable privacy bound |
| Release without checklist | `release_checklist_completed` required in `transition_to_approved` | `federation.py` | **Process control** — not cryptographic |

### 3.5 Controls NOT Yet Implemented (and Why This Matters)

| Threat | Missing Control | Risk if Deployed Without |
|---|---|---|
| Coordinator learning individual node contributions | Secure aggregation (cryptographic masking) | A dishonest coordinator learns every node's exact importances |
| Aggregate leaking node data | Differential privacy (calibrated noise) | With ≤3 nodes, simple algebraic subtraction recovers a node's exact importances |
| Package impersonation / replay | mTLS + package sequence numbers + node certificate pinning | Forged packages accepted as legitimate |
| Node-level inference from published aggregate | k-anonymity minimum-node guard beyond MIN_QUORUM | An adversary watching successive aggregates can track a single node's drift |

**This threat model must be reviewed by a qualified data-protection officer and
security architect before any real country node is onboarded.**

---

## 4. Privacy Analysis

### 4.1 What Is NOT Claimed

TATHYON's federation module does **not** claim:

- **Differential privacy**: No Laplace or Gaussian noise is added to exported
  feature importances. Individual node contributions are not ε-δ private.
- **Secure aggregation**: Individual node packages are visible to the coordinator
  in plaintext before aggregation.
- **Information-theoretic privacy**: With small node counts, the aggregate may
  allow reconstruction of individual node importances.

### 4.2 Secure Aggregation — Future Work

Secure aggregation (e.g., the Bonawitz et al. 2017 protocol) would prevent the
coordinator from learning individual node weights. It requires:

- A key-agreement protocol between all participating nodes (e.g., Diffie-Hellman
  over an authenticated channel).
- Pairwise masking: each pair of nodes exchanges random masks that cancel in
  the aggregate.
- Dropout handling: nodes that drop mid-round require a secret-sharing scheme.

**Why not yet implemented:** Requires HSM-backed key storage, a coordination
protocol distinct from weight exchange, and jurisdiction-specific approval for
cross-border cryptographic key exchange.

**If you deploy federation without secure aggregation:** Document this explicitly
in the model card (see §10), inform all participating nodes, and do not represent
the system as providing coordination-server privacy.

### 4.3 Differential Privacy — Future Work

Differential privacy (DP) would bound the influence of any single training
observation on the published feature importances. The standard approach for
tree-based models is to add calibrated Laplace or Gaussian noise with sensitivity
Δf = (max importance − min importance) / n, where n is the local training count.

**Why not yet implemented:** DP calibration requires:
1. A chosen ε (privacy loss budget), agreed upon and disclosed to all parties.
2. Sensitivity analysis for each feature importance, which is distribution-specific.
3. A utility-privacy trade-off analysis showing the noised importances still
   improve over local-only operation.
4. Jurisdiction-specific legal confirmation that DP constitutes "anonymization"
   for the applicable data protection framework.

**Utility trade-off caution:** Adding DP noise when `train_observation_count` is
small (e.g., a district with 500 observations) may make the exported importances
so noisy as to be worse than a local-only prior. Do not apply DP without measuring
this trade-off on representative data.

---

## 5. Participant Onboarding and Trust Assumptions

### 5.1 Trust Assumptions (Explicit)

The current implementation assumes:

1. **Node integrity**: Each participating node runs the reference implementation
   of `sanitize_and_export_weights` without modification. A node that bypasses
   the gate can submit arbitrary importances.
2. **Coordinator honesty**: The coordinator is a trusted-but-curious server: it
   runs aggregation correctly but may observe individual packages. No cryptographic
   guarantee prevents a dishonest coordinator from learning per-node contributions.
3. **Transport integrity**: Packages arrive unmodified. Without mTLS, a
   man-in-the-middle can substitute or replay packages.
4. **Jurisdiction agreement**: Participating nodes have obtained the necessary
   legal authorizations in their own jurisdiction for sharing model weights.

### 5.2 Onboarding Checklist (Future Process — Not Yet Automatable)

Before a new country or organization node is admitted to the federation:

- [ ] Executed data-sharing agreement covering what is shared, what is not, and
      who bears liability for violations.
- [ ] Legal opinion from the node's jurisdiction on whether model weight exchange
      constitutes personal data transfer under the applicable data protection law.
- [ ] Clinical governance review: which resource types and facility tiers will
      contribute data to training. What exclusions apply (e.g., paediatric wards,
      specialist hospitals)?
- [ ] Node IT security assessment: is the local data store adequately protected
      from exfiltration before the sanitization gate is reached?
- [ ] Schema compatibility review: do the local feature vocabulary and resource
      category definitions match the canonical schema? (See §7.4.)
- [ ] Key exchange (when secure aggregation is implemented).
- [ ] Test package submission with synthetic data before live onboarding.
- [ ] Minimum viable observation count confirmed (≥ 5,000 for the first round).

---

## 6. Model Versioning, Validation, and Rollback

### 6.1 Version Numbering

Each aggregate produced by `FederatedTrustAggregator.aggregate()` receives a
`version_id` (prefixed `fedv_`) from `ModelRegistry.register_available()`.
The `schema_version` field on each `FederatedWeightPackage` must match the
canonical feature vocabulary version. Mismatched schema versions should be
rejected before aggregation.

### 6.2 Local Validation Before Approval

After an aggregate arrives and is registered in AVAILABLE state, the local node
must run validation before human approval is possible. Minimum checks:

| Check | Pass criterion | Action on fail |
|---|---|---|
| Local PR-AUC with federated prior | ≥ local-only PR-AUC − 0.02 | Do not transition; flag for review |
| Calibration (Brier score) | Brier ≤ local-only Brier + 0.01 | Do not transition |
| Fairness — PR-AUC by facility tier (PHC vs CHC vs DH) | No tier's PR-AUC drops > 5pp | Quarantine if violated |
| Feature importance sign consistency | No feature flips direction vs. local model | Flag for review |

These checks are defined as policy but not yet wired to automated evaluation
in `tathyon/`. They must be added before real federation deployment.

### 6.3 Rollback Procedure

1. An authorized officer (CMO/DHO) calls `ModelRegistry.rollback()` with a reason.
2. `current_operational_aggregate()` immediately returns the previous APPROVED
   version (or `None` if none exists).
3. All local forecasts and trust scores must re-derive from the previous
   APPROVED aggregate or from the local-only scorer within one operational cycle.
4. The rollback event is appended to the local audit ledger with the officer ID
   and timestamp.

**The system must never silently revert.** The UI must display the rollback reason
and the identity of the operative aggregate version.

### 6.4 Model Card Per Version

Each APPROVED version must have a model card filled in (template in §10).
The model card is stored alongside the version record and is immutable once
the version is APPROVED.

---

## 7. Safeguards Against Known Failure Modes

### 7.1 Model Poisoning

**Control implemented:** `ModelRegistry._check_poisoning_guard()` computes the
absolute difference in each feature importance between the incoming aggregate
and the current APPROVED version. If any feature shifts by more than 30 percentage
points, the incoming version is quarantined automatically.

**Limitations:**
- A sophisticated adversary who controls multiple nodes and knows the threshold
  can spread poisoning across multiple rounds, staying just below 30pp per round.
- The threshold is not derived from a formal robustness proof. It is a conservative
  heuristic that should be tuned based on observed feature drift in production.
- After each round, local validation (§6.2) provides a second layer of detection.

**Recommended additional control (not yet implemented):** Byzantine-robust
aggregation (e.g., Krum, trimmed mean, or coordinate-wise median) instead of
plain FedAvg. These methods require knowing the maximum fraction of Byzantine
nodes in advance.

### 7.2 Participant Dropout

**Control implemented:** `MIN_QUORUM = 2`. If fewer than 2 nodes submit packages,
`aggregate()` raises `QUORUM_NOT_MET` and no aggregate is produced.

**What this does not cover:** If a consistently contributing node drops out for
multiple rounds, the aggregate drifts to represent the remaining nodes only.
Local validation (PR-AUC check) should catch degradation.

**Recommended additional control:** A coordinator-side "expected contributors"
list. Alert if a node that has previously contributed fails to submit for N
consecutive rounds.

### 7.3 Non-IID Data

Observation-weighted FedAvg down-weights data-sparse nodes but does not correct
for systematic distributional differences between nodes (e.g., different disease
prevalence, different procurement systems, different facility-tier mixes).

**What this means in practice:** If a high-observation node has a structurally
different relationship between features and the outcome (e.g., because its
resource distribution system is centralized while others are decentralized),
its importances will dominate the aggregate without being representative.

**Current mitigation:** The `held_out_pr_auc` per node is included in the
aggregate, enabling local validation to detect when the aggregate performs
worse than the local-only model.

**Recommended additional control (not yet implemented):**
- Stratified aggregation by facility tier (PHC vs. CHC vs. DH) separately.
- Distribution-shift detection before weight submission (MMD or KL-divergence
  on feature histograms, without sharing raw observations).

### 7.4 Incompatible Facility and Resource Definitions

Feature importances are only meaningful if all nodes use the same feature
vocabulary and the same resource category definitions.

**Control implemented:** `schema_version` field on `FederatedWeightPackage`.
The aggregator does not yet enforce that all packages in a round share the same
`schema_version`. This must be added before production deployment.

**Incompatibility examples:**
- Node A defines "staff present" as biometric punch-in; Node B defines it as
  roster entry. The same feature name means different things.
- Node A's "medicine stock" includes vaccines; Node B's does not.
- Node A's `burn_inconsistency_30d` uses a 30-day rolling window; Node B uses 28.

**Required pre-onboarding step (§5.2):** Schema compatibility review and a
binding commitment to the canonical feature vocabulary in the data-sharing agreement.

### 7.5 Data Leakage Through Small Aggregates

**Control implemented:** `MIN_OBSERVATIONS = 5,000` and `MIN_QUORUM = 2`.
These are conservative minimums, not privacy guarantees.

**With exactly 2 nodes:** A coordinator that knows one node's package can
subtract it from the aggregate to recover the other node's exact importances.
This is why secure aggregation (§4.2) is needed before production deployment
with small node counts.

**Recommendation:** Do not publish aggregates with fewer than 5 nodes until
secure aggregation is implemented.

---

## 8. Country-Boundary Enforcement Plan

**Invariant (enforced in code):** No country node can query another country's raw
facility records through the federation interface.

**How this is enforced:**

1. The `sanitize_and_export_weights` gate is the only path from raw data to the
   shared interface. It is a pure function with no database query capability.
2. The `FederatedTrustAggregator` operates on `FederatedWeightPackage` objects
   only. It has no reference to any data store.
3. The `ModelRegistry` stores only aggregated results (dicts), not the originating
   packages.
4. The coordination service (when implemented) must be designed with a strict
   API contract: it accepts weight packages and returns aggregates. It must have
   no read access to any participant's local data store.

**What raw-data access control is still needed at the infrastructure level
(not yet implemented):**

- Network segmentation: the local data store should not be reachable from the
  coordination service or from any other node's network.
- Audit logging at the data store level: every read of the raw store that feeds
  the training pipeline should be logged.
- Authorization tokens: the sanitization function should be callable only by
  an authorized training pipeline service account, not by arbitrary processes.

---

## 9. Incident Response

### 9.1 Poisoning Alert

Trigger: `ModelRegistry.register_available()` returns a version in QUARANTINED
state due to the delta-importance guard.

Response:
1. Immediately notify the local federation security contact and the coordinator.
2. Do not transition the quarantined version to any other state.
3. Review the offending features and compare against all recent packages from
   each contributing node.
4. If the source node is identified as compromised, revoke its authorization
   to submit packages and notify its sovereign jurisdiction.
5. Produce a post-incident report within 72 hours.

### 9.2 Data Leakage Alert

Trigger: `sanitize_and_export_weights` raises `SOVEREIGN_DATA_LEAKAGE_DETECTED`.

Response:
1. The export pipeline is already blocked (hard-fail). No package leaves the node.
2. Log the event to the local audit ledger with the offending feature name.
3. Review the training pipeline to identify how the entity-identifier entered
   the feature set.
4. Do not resume submissions until the root cause is resolved and the training
   pipeline is re-audited.

### 9.3 Rollback

Trigger: Post-approval degradation detected in local PR-AUC monitoring or
clinical outcome monitoring.

Response:
1. Call `ModelRegistry.rollback()` with a meaningful reason.
2. Verify that `current_operational_aggregate()` returns the previous version
   (or `None`).
3. Recompute all affected trust scores and forecasts using the previous version.
4. Notify the coordinator that this node has rolled back.
5. Investigate the cause before accepting the next round's aggregate.

---

## 10. Model Card Template

Use this template for each APPROVED federated aggregate version.
One model card per `version_id`. Fill in all fields. Fields left blank delay approval.

```
TATHYON FEDERATED AGGREGATE MODEL CARD
Version: <version_id>
Resource type: <MEDICINE | BED | PERSONNEL>
Schema version: <v1 | ...>
Approval date: <ISO8601 UTC>
Approved by: <officer_id>
Card prepared by: <name, role>
Card review date: <ISO8601 UTC>

─────────────────────────────────────────────────────────────────
A. INTENDED USE
─────────────────────────────────────────────────────────────────
Intended use:
  Provide a federated prior for the local Trust Scorer's feature
  importances in resource-constrained districts with insufficient
  local training data (< N observations, where N is defined by
  local policy). The aggregate supplements but does not replace
  local training.

Authorized decision domains:
  - Trust queue ranking (which facility-resource pairs to verify first)
  ONLY. Not authorized for:
  - Direct allocation decisions.
  - Clinical triage or patient prioritization.
  - Performance evaluation of individual staff or facilities.
  - Legal or regulatory proceedings.

─────────────────────────────────────────────────────────────────
B. EXCLUSIONS
─────────────────────────────────────────────────────────────────
This aggregate MUST NOT be applied to:
  - [ ] Facilities outside the participating jurisdictions.
  - [ ] Resource types not covered by the training data.
  - [ ] Real-time clinical decision support.
  - [ ] Automated allocation without human approval.
  - [ ] Any facility in a jurisdiction that has not executed the
        data-sharing agreement.

─────────────────────────────────────────────────────────────────
C. TRAINING AND EVALUATION WINDOWS
─────────────────────────────────────────────────────────────────
Training window (all nodes): <start> to <end>
Hold-out evaluation window: <start> to <end>
Schema version: <v1 | ...>
Total federated observations: <number>
Node count: <number>
Participating countries / districts: <list — anonymized if required>

─────────────────────────────────────────────────────────────────
D. METRICS
─────────────────────────────────────────────────────────────────
Mean node PR-AUC (held-out): <value>
Local PR-AUC with this aggregate: <value>
Local PR-AUC without aggregate (baseline): <value>
Improvement: <value> percentage points
Calibration Brier score: <value>
Fairness — PR-AUC by facility tier:
  PHC: <value>
  CHC: <value>
  DH: <value>
Notes: <any anomalies, data-quality warnings>

─────────────────────────────────────────────────────────────────
E. COUNTRY PARTICIPATION
─────────────────────────────────────────────────────────────────
[ ] All participating nodes have executed a data-sharing agreement.
[ ] Schema compatibility confirmed for all nodes.
[ ] Legal opinion received for each jurisdiction.
Participating jurisdictions: <list>
Excluded jurisdictions (and reason): <list>

─────────────────────────────────────────────────────────────────
F. PRIVACY AND SECURITY
─────────────────────────────────────────────────────────────────
Secure aggregation applied: YES / NO
  If NO: Coordinator can observe individual node contributions.
  Disclose this to all participants.

Differential privacy applied: YES / NO
  If YES:
    ε (epsilon): <value>
    δ (delta): <value>
    Noise mechanism: Laplace / Gaussian
    Sensitivity per feature: <list>
    Utility trade-off analysis: <reference to analysis doc>
  If NO: Individual node importances are not ε-δ private.
  Disclose this to all participants.

Min-observation guard: YES — 5,000 total observations required.
Quorum guard: YES — minimum 2 nodes required.

─────────────────────────────────────────────────────────────────
G. LIMITATIONS
─────────────────────────────────────────────────────────────────
- Trained on synthetic or limited real data. Performance on
  out-of-distribution facilities is unknown.
- Observation-weighted FedAvg does not correct for systematic
  distributional differences between nodes (non-IID data).
- No secure aggregation: coordinator sees per-node contributions.
- No differential privacy: node importances are not ε-δ private.
- The delta-importance poisoning guard is a heuristic, not a
  provable robustness bound.
- Feature vocabulary must be identical across all nodes; schema
  mismatch invalidates the aggregate.
- This card covers model weights only. Clinical and operational
  outcomes are governed by separate evaluation processes.

─────────────────────────────────────────────────────────────────
H. APPROVAL STATUS
─────────────────────────────────────────────────────────────────
Lifecycle state: APPROVED
Date of transition from EVALUATED to APPROVED: <ISO8601 UTC>
Approving officer: <officer_id, name, role>
Release checklist completed: YES
Valid until (if applicable): <date or "until next round">

Rollback trigger conditions:
  - Local PR-AUC drops > 2pp versus baseline.
  - Any clinical outcome monitoring alert.
  - Any poisoning alert from subsequent round.
  - Jurisdiction withdraws participation.
```

---

## 11. Release Checklist

The following checklist must be completed and confirmed before calling
`ModelRegistry.transition_to_approved()`. The `release_checklist_completed=True`
flag is a statement that all items below have been addressed.

- [ ] Model card (§10) fully completed and reviewed by the approving officer.
- [ ] Evaluation report attached to the version record.
- [ ] Local PR-AUC with aggregate ≥ local-only baseline − 0.02.
- [ ] Calibration (Brier score) within tolerance.
- [ ] Fairness check: no facility tier's PR-AUC drops > 5pp.
- [ ] Privacy disclosure reviewed: secure aggregation and DP status documented
      and communicated to all participating nodes.
- [ ] Schema version confirmed identical across all contributing packages.
- [ ] All contributing nodes have valid data-sharing agreements in force.
- [ ] Poisoning guard passed (version not in QUARANTINED state).
- [ ] Rollback plan documented: what triggers rollback, who authorizes it.
- [ ] Audit ledger entry created for the approval event.

---

## 12. Demo Mode

The synthetic federation demo (`run_synthetic_federation_demo()` in
`tathyon/federation.py`) demonstrates the correct workflow using two synthetic
nodes named `DEMO_NODE_IN_ALPHA` and `DEMO_NODE_ZA_BETA` from countries
`DEMO_COUNTRY_A` and `DEMO_COUNTRY_B`.

**Safeguards against confusion with real data:**

| Safeguard | Implementation |
|---|---|
| `is_demo=True` on every package | Hard-coded in demo function |
| `demo_label='[SYNTHETIC DEMO — NOT REAL PARTICIPATION DATA]'` in aggregate | Set in `FederatedTrustAggregator.aggregate()` when all nodes are demo |
| `provenance='SYNTHETIC_DEMO_ONLY'` in `SyntheticFederationDemoResult` | Hard-coded |
| Country names begin with `DEMO_` prefix | Enforced by test `test_synthetic_demo_no_real_country_names` |
| No real country codes (INDIA, SOUTH_AFRICA, etc.) in demo output | Verified by test |

**What the demo does NOT demonstrate:**
- Real mTLS node authentication.
- Real secure aggregation.
- Real differential privacy.
- Real country-level data governance.

The demo is suitable for showing the lifecycle workflow and the data-contract
boundaries. It is not suitable for evaluating privacy guarantees.

---

## 13. Open Questions Requiring Jurisdiction-Specific Review

These questions must be answered by qualified legal, clinical, privacy, and
governance experts in each participating jurisdiction before real federation
is deployed. They are recorded here to prevent premature deployment.

### Legal and Data Protection

1. Does sharing model weights (feature importances) constitute a transfer of
   personal data under the applicable data protection law (DPDP Act 2023,
   POPIA, LGPD, PIPL, GDPR)? The answer is jurisdiction-specific and may
   depend on whether individual-level information can be reconstructed.
2. Which legal gateway applies for cross-border model weight transfer (adequacy
   decision, standard contractual clauses, binding corporate rules)?
3. Who bears liability if a federated aggregate contributes to a harmful
   allocation decision in a partner jurisdiction?
4. What data retention obligations apply to the weight packages and aggregate
   records on the coordinator?

### Clinical Governance

5. Which resource types and facility tiers are in scope for federation? Are
   paediatric or maternal care facilities subject to additional restrictions?
6. What is the acceptable margin of performance degradation (vs. local-only
   operation) before the aggregate must be withdrawn?
7. Does use of a federated prior constitute a "medical device" or "clinical
   decision support software" under applicable health-technology regulation
   (e.g., EU MDR, India CDSCO, South Africa SAHPRA)?

### Privacy Engineering

8. What is the acceptable ε for differential privacy in each jurisdiction?
   (Different regulators have different views on what constitutes "anonymization.")
9. At what minimum node count is the aggregate safe to publish without secure
   aggregation?
10. How should the schema version control and feature vocabulary be governed
    across sovereign jurisdictions with different procurement systems?

### Governance and Incident Response

11. Who is the designated point of contact for a poisoning incident in each
    jurisdiction? What is the cross-border notification obligation?
12. What governance body approves the addition or removal of a participating
    node? What is the dispute resolution mechanism?
13. How are successive rounds governed? Who defines the training window, the
    feature vocabulary updates, and the MIN_QUORUM for each round?

---

*Document maintained by the TATHYON engineering team. All claims in this document
describe design intent and future work. No real multi-country environment is
operational as of the date of this document. Treat federation metrics as synthetic
unless an explicit authorization record from an approved jurisdiction exists.*
