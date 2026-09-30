# Sovereign Adapter Interface & Federated Weight Exchange

**Status: DESIGN / SYNTHETIC DEMO ONLY.** Some local data-contract and sanitization code exists in `tathyon/federation.py`; this is not an implemented or independently verified live exchange.
**Production-readiness status: NOT PRODUCTION-READY. No real organization or country node is connected.**

See [`docs/federation-privacy.md`](federation-privacy.md) for the full threat model, privacy analysis, onboarding process, model versioning and rollback procedure, model card template, release checklist, and the list of open legal, clinical, and governance questions that must be resolved before real deployment.

This document proposes a future interface. It does not represent a program mandate, a government partnership, BRICS participation, or compliance with a cross-border digital-health standard.

---

## 1. Why a Sovereign Boundary at All

Data-sharing permissions and sovereignty requirements depend on the applicable jurisdiction, system owner, and agreement. No raw or derived data may be exchanged until those conditions and a threat model are reviewed.

The sovereign question is: **"What can be shared to strengthen collective resilience without exposing raw facility rows or patient data?"**

---

## 2. The Strict Sovereign Partition

| Item | Status | Mechanism | Audit Check |
|---|---|---|---|
| **Model Weights & Feature Importances** | **SHARED** | Observation-weighted FedAvg (`FederatedWeightPackage`) | Hash-signed payload |
| **Phantom Anomaly Library** | **SHARED** | Structural signatures (`PhantomAnomalyPattern`) | No identifiers |
| **Standardized Feature Definitions** | **SHARED** | `TRUST_FEATURES`, `BED_TRUST_FEATURES`, `PERSONNEL_TRUST_FEATURES` | Public vocabulary |
| **Raw Facility Inventory Records** | **NEVER SHARED** | Quarantined strictly within district boundary | Sanitizer hard-fails |
| **Patient Demographics & Encounters** | **NEVER SHARED** | Quarantined strictly within district boundary | Sanitizer hard-fails |
| **Personnel Names & Biometrics** | **NEVER SHARED** | Quarantined strictly within district boundary | Sanitizer hard-fails |
| **Facility Coordinates** | **NEVER SHARED** | Kept in local district GIS topology | Sanitizer hard-fails |

---

## 3. The Federated Weight Package (`FederatedWeightPackage`)

The following is a schema illustration only. It is not a real district export, trained model card, or measured metric; the sample values must never be represented as pilot evidence:

```json
{
  "package_id": "fed_pkg_8a92b1",
  "exporting_district_id": "IN_CG_BASTAR",
  "exporting_country": "INDIA",
  "resource_type": "medicine",
  "model_family": "GradientBoostingClassifier",
  "train_observation_count": 42000,
  "train_base_rate": 0.0225,
  "held_out_pr_auc": 0.9309,
  "feature_importances": {
    "t1_violations_now": 0.6137,
    "burn_inconsistency_30d": 0.3030,
    "att_last_unusable_share": 0.0506
  },
  "patterns_shared": [
    {
      "pattern_id": "PAT_MED_01",
      "pattern_name": "Batch Expiry Rollover",
      "provenance_citation": "CAG Punjab Rpt 4/2019 Para 2.1.7.2(ii)"
    }
  ],
  "package_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "sanitization_verified": true
}
```

---

## 4. Federated Aggregation (`FederatedTrustAggregator`)

Emerging or data-sparse districts that have not yet accumulated enough local audit data pull consensus feature importances and anomaly signatures from the federation.

The aggregation uses observation weighting:
$$W_f = \sum_{k=1}^K \frac{N_k}{\sum_j N_j} w_{k, f}$$

where $N_k$ is the observation count of district $k$ and $w_{k, f}$ is the local feature importance for feature $f$.

---

## 5. Security & Privacy — What Is and Is Not Implemented

1. **Pull, Not Push**: Partner nodes pull aggregates asynchronously via authenticated cryptographic handshakes. *(Network transport layer not yet implemented.)*
2. **Deterministic Cryptographic Hashing**: Every weight package is sealed with a SHA-256 digest (`package_hash`). *(Implemented.)*
3. **Leakage Gate**: `sanitize_and_export_weights` audits the exported payload with token filters, throwing `SOVEREIGN_DATA_LEAKAGE_DETECTED` if any facility ID or patient entity is detected. *(Implemented.)*
4. **Model Lifecycle State Machine**: Aggregates pass through AVAILABLE → EVALUATED → APPROVED before influencing operational decisions. *(Implemented.)*
5. **Poisoning Guard**: Delta-importance threshold quarantines incoming aggregates with suspiciously large feature shifts. *(Implemented — heuristic, not cryptographic.)*
6. **Secure Aggregation**: Cryptographic masking of individual node contributions from the coordinator. **NOT YET IMPLEMENTED.**
7. **Differential Privacy**: Calibrated noise on exported importances. **NOT YET IMPLEMENTED.** See `docs/federation-privacy.md` §4 for the full analysis.

---

## 6. Model Lifecycle

An aggregate received from the coordination service is NOT immediately operational.
It must pass through the `ModelLifecycleState` finite state machine:

```
AVAILABLE → EVALUATED → APPROVED → operational use permitted
                ↓              ↓
           QUARANTINED    ROLLED_BACK
```

See `docs/federation-privacy.md` §6 for validation criteria and rollback procedure.
