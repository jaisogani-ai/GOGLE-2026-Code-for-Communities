# TATHYON: BUILD PLAN & VERIFICATION SPECIFICATION
**Healthcare Resource Resilience Control Plane**
*Document Version: 1.0.0 | Date: September 2026*
*Author: Engineering Lead*

---

## 1. BUILD PHASES & REPOSITORY STRUCTURE

| Phase | Subsystem | Target Files | Verification Test Target |
| :--- | :--- | :--- | :--- |
| **Phase 1** | Healthcare Resource Graph | `tathyon/graph.py`, `tathyon/schema.py` | `tests/test_resource_graph.py` |
| **Phase 2** | Stockout & Surge Engine | `tathyon/stockout.py` | `tests/test_stockout_and_surge.py` |
| **Phase 3** | Resilience Resilience Scenario Engine | `tathyon/twin.py` | `tests/test_twin_planner_agents.py` |
| **Phase 4** | Response Planner & OR-Tools | `tathyon/planner.py` | `tests/test_twin_planner_agents.py` |
| **Phase 5** | Three Typed Domain Agents | `tathyon/agents.py` | `tests/test_twin_planner_agents.py` |
| **Phase 6** | REST APIs & Endpoints | `api/main.py` | `tests/test_phase6_apis.py` |
| **Phase 7** | Deterministic Hero Demo | `tathyon/hero_demo.py` | `tests/test_hero_demo.py` |
| **Phase 8** | Integration & Zero-Regression Test | All files | `pytest tests/` (161/161 passing) |

---

## 2. KEY TEST CRITERIA & HARD ASSERTIONS

1. **Phantom Inventory Rejection:**
   - Test explicitly verifies that a facility with 500 units of `claimed_quantity` but 0 units of `usable_quantity` is NEVER selected as a donor by `tathyon/planner.py`.
2. **Donor Safety Floor Preservation:**
   - Test asserts that after transfer generation, donor facility inventory remains strictly $\ge \text{SafetyFloor}$ (14 days minimum buffer).
3. **Forecast Competition Selection:**
   - Test verifies that when an intermittent series has sporadic spikes, Croston-SBA or TSB is selected over Naive only when empirical holdout MASE is strictly lower.
4. **Resilience Scenario Engine Provenance:**
   - Test verifies that every resilience scenario engine simulation payload contains `provenance: "SIMULATION"`.
5. **Human Approval Enforcement:**
   - Test verifies that executing a transfer without human credentials fails with an authentication error, and successful approval appends event `plan_approved` to the SHA-256 hash chain.
6. **Zero Test Regressions:**
   - Existing 139 baseline tests covering GFR-22, hash chain, and claim verification must continue passing without modification. Total suite count must reach 161+.
