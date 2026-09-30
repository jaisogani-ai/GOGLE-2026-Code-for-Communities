"""
Tests for TATHYON HL7 FHIR R4 Interoperability Adapter.
"""

from tathyon.adapters import FHIRR4Adapter


def test_fhir_r4_bundle_formatting():
    plan_dict = {
        "plan_id": "PLAN-2026-HERO-001",
        "source": "CHC_JAGDALPUR",
        "source_name": "Jagdalpur Community Health Centre",
        "destination": "PHC_TOKAPAL",
        "destination_name": "Tokapal Primary Health Centre",
        "resource": "MED_ANTI_RABIES_VACCINE",
        "quantity": 180.0,
        "reason": "Outbreak emergency rebalancing under GFR Rule 22",
        "approved_by": "Dr_R_K_Verma",
        "approval_role": "Chief Medical Officer",
        "status": "APPROVED",
        "provenance": "SIMULATION",
    }

    bundle = FHIRR4Adapter.format_fhir_bundle(plan_dict)

    # 1. Top level bundle assertions
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "collection"
    assert bundle["status"] == "READY_FOR_SYSTEM_OF_RECORD"
    assert bundle["execution_boundary"] == "GENERATED — NOT EXECUTED"
    assert "A SupplyRequest is an operational request" in bundle["disclaimer"]
    assert bundle["total"] == 5

    # 2. Check each resource type in entries
    resource_types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert "Location" in resource_types
    assert "SupplyRequest" in resource_types
    assert "SupplyDelivery" in resource_types
    assert "Task" in resource_types

    # 3. SupplyRequest checks
    sr = next(e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == "SupplyRequest")
    assert sr["status"] == "active"
    assert sr["priority"] == "urgent"
    assert sr["quantity"]["value"] == 180.0
    assert sr["itemCodeableConcept"]["text"] == "MED_ANTI_RABIES_VACCINE"
