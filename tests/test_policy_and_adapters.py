"""
Tests for Tathyon Policy Engine, SOR Adapters, and Expanded Intelligence APIs.

Covers:
1. tathyon.policy — policy-based governance, GFR 2017 provenance, donor checks, authorization
2. tathyon.adapters — DVDMSAdapter, GenericCSVAdapter, GenericRESTAdapter, execution boundaries
"""
import pytest

from tathyon.adapters import DVDMSAdapter, GenericCSVAdapter, GenericRESTAdapter
from tathyon.graph import ResourceState
from tathyon.policy import PolicyCategory, PolicyConstraint, PolicyEngine
from tathyon.schema import now


# ==========================================================================
# 1. POLICY ENGINE TESTS
# ==========================================================================

class TestPolicyEngine:
    """Validates policy-based policy constraints and provenance."""

    def test_policy_catalog_has_statutory_provenance(self):
        engine = PolicyEngine()
        policies = engine.list_policies()
        assert len(policies) >= 6

        # Check provenance citations
        citations = {p["statutory_provenance"] for p in policies}
        assert any("General Financial Rules" in c for c in citations)
        assert any("National Health Mission" in c for c in citations)
        assert any("Drugs and Cosmetics Rules" in c for c in citations)

    def test_unverified_stock_donor_rejection(self):
        engine = PolicyEngine()
        st = ResourceState(
            facility_id="PHC_TEST",
            resource_id="MED-001",
            claimed_quantity=500.0,
            usable_quantity=0.0,  # Unverified
            consumption_velocity=10.0,
            last_attested_at=now(),
        )
        res = engine.evaluate_donor_eligibility("PHC_TEST", "MED-001", st)
        assert res["eligible"] is False
        assert any("POL-UNVERIFIED-EXCLUSION" in v for v in res["violations"])
        assert len(res["provenance_citations"]) > 0

    def test_stale_attestation_donor_rejection(self):
        engine = PolicyEngine()
        st = ResourceState(
            facility_id="PHC_TEST",
            resource_id="MED-001",
            claimed_quantity=500.0,
            usable_quantity=300.0,
            consumption_velocity=10.0,
            last_attested_at="2020-01-01T00:00:00+00:00",  # Stale
        )
        res = engine.evaluate_donor_eligibility("PHC_TEST", "MED-001", st, max_age_hours=48.0)
        assert res["eligible"] is False
        assert any("attestation age exceeds" in v for v in res["violations"])

    def test_eligible_donor_passes(self):
        engine = PolicyEngine()
        st = ResourceState(
            facility_id="PHC_TEST",
            resource_id="MED-001",
            claimed_quantity=500.0,
            usable_quantity=450.0,
            consumption_velocity=10.0,
            last_attested_at=now(),  # Fresh
        )
        res = engine.evaluate_donor_eligibility("PHC_TEST", "MED-001", st, min_safety_days=7.0)
        assert res["eligible"] is True
        assert res["transferable_quantity"] >= 380.0
        assert len(res["violations"]) == 0

    def test_approval_authorization_roles(self):
        engine = PolicyEngine()
        # Medical Officer authorized
        mo_check = engine.evaluate_approval_authorization("DR-01", "Chief Medical Officer")
        assert mo_check["authorized"] is True

        # Unauthorized role rejected
        clerk_check = engine.evaluate_approval_authorization("CLK-01", "Warehouse Clerk")
        assert clerk_check["authorized"] is False
        assert clerk_check["rejection_reason"] is not None


# ==========================================================================
# 2. ADAPTER TESTS & EXECUTION BOUNDARY
# ==========================================================================

class TestAdaptersAndExecutionBoundary:
    """Validates System of Record adapters and execution boundary tagging."""

    @pytest.fixture
    def mock_plan(self):
        return {
            "plan_id": "PLAN-2026-TEST-001",
            "source": "DWH_CENTRAL",
            "destination": "PHC_REMOTE",
            "resource": "MED_ANTI_RABIES_VACCINE",
            "quantity": 150.0,
            "distance": 45.0,
            "approved_by": "DR-CMO-001",
            "approval_role": "ChiefMedicalOfficer",
            "approval_timestamp": now(),
            "transfers": [{
                "source": "DWH_CENTRAL",
                "source_name": "District Central Warehouse",
                "destination": "PHC_REMOTE",
                "destination_name": "Remote Primary Health Centre",
                "resource": "MED_ANTI_RABIES_VACCINE",
                "quantity": 150.0,
                "distance_km": 45.0,
                "travel_hours": 1.3,
            }],
        }

    def test_dvdms_adapter(self, mock_plan):
        voucher = DVDMSAdapter.format_voucher(mock_plan)
        assert voucher["system_target"] == "DVDMS"
        assert voucher["status"] == "READY_FOR_SYSTEM_OF_RECORD"
        assert voucher["execution_boundary"] == "PAYLOAD_STAGED_NOT_EXECUTED_EXTERNALLY"
        assert len(voucher["line_items"]) == 1
        assert voucher["line_items"][0]["allotted_quantity"] == 150.0
        assert voucher["line_items"][0]["cold_chain_preservation_required"] is True
        assert "integrity_hash" in voucher

    def test_csv_adapter(self, mock_plan):
        csv_out = GenericCSVAdapter.format_csv(mock_plan)
        assert "READY_FOR_SYSTEM_OF_RECORD" in csv_out
        assert "VoucherID,PlanID,LegNumber" in csv_out
        assert "DWH_CENTRAL" in csv_out
        assert "150.0" in csv_out

    def test_rest_adapter(self, mock_plan):
        rest_out = GenericRESTAdapter.format_webhook_payload(mock_plan)
        assert rest_out["http_method"] == "POST"
        assert "X-Tathyon-Signature" in rest_out["headers"]
        assert "X-Idempotency-Key" in rest_out["headers"]
        assert rest_out["headers"]["X-Execution-Status"] == "READY_FOR_SYSTEM_OF_RECORD"
        assert rest_out["payload_body"]["plan_id"] == "PLAN-2026-TEST-001"
