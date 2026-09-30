"""Test CSV ingest and System-of-Record staged payload boundary."""
import io
import pytest

from tathyon.adapters import (
    IngestResult,
    RowValidationError,
    parse_dvdms_csv,
    write_sor_payload,
)


def test_dvdms_csv_ingest_with_provenance_and_quantity_coercion():
    csv_data = """FacilityCode,FacilityName,District,DrugCode,DrugName,ClosingBalance,DailyConsumption
PHC_TOKAPAL,Tokapal Primary Centre,Bastar,MED_RABIES,Anti-Rabies Vaccine," 1,250 ",12.5
PHC_BASTANAR,Bastanar Primary Centre,Bastar,MED_RABIES,Anti-Rabies Vaccine,450,8.0
DH_JAGDALPUR,Jagdalpur District Hospital,Bastar,MED_RABIES,Anti-Rabies Vaccine,"3,000",25.0
"""
    result = parse_dvdms_csv(csv_data, default_provenance="CSV_IMPORT")

    assert result.total_rows == 3
    assert result.valid_count == 3
    assert result.error_count == 0
    assert result.provenance == "CSV_IMPORT"

    rec1 = result.valid_records[0]
    assert rec1.facility_id == "PHC_TOKAPAL"
    assert rec1.sku == "MED_RABIES"
    assert rec1.reported_quantity == 1250.0
    assert rec1.consumption_velocity == 12.5
    assert rec1.provenance == "CSV_IMPORT"
    assert rec1.source == "DVDMS_EXPORT"

    rec2 = result.valid_records[1]
    assert rec2.facility_id == "PHC_BASTANAR"
    assert rec2.reported_quantity == 450.0

    rec3 = result.valid_records[2]
    assert rec3.facility_id == "DH_JAGDALPUR"
    assert rec3.reported_quantity == 3000.0


def test_dvdms_csv_malformed_rows_never_crash_batch():
    """Regression test for old non-numeric-quantity bug:
    Batch contains a mix of valid rows and malformed rows (unparseable text,
    negative quantity, missing SKU, missing FacilityCode).
    Batch must process all valid rows and return typed per-row errors.
    """
    csv_data = """FacilityCode,FacilityName,District,DrugCode,ClosingBalance
PHC_VALID_1,PHC 1,Bastar,MED_OXYTOCIN,100
PHC_BAD_QTY,PHC Bad,Bastar,MED_OXYTOCIN,CORRUPTED_STRING_VALUE
PHC_VALID_2,PHC 2,Bastar,MED_OXYTOCIN," 2,500 "
,PHC Missing ID,Bastar,MED_OXYTOCIN,50
PHC_BAD_SKU,PHC Bad SKU,Bastar,,300
PHC_NEGATIVE,PHC Negative,Bastar,MED_OXYTOCIN,-45
PHC_VALID_3,PHC 3,Bastar,MED_OXYTOCIN,75.5
"""
    result = parse_dvdms_csv(csv_data)

    assert result.total_rows == 7
    assert result.valid_count == 3
    assert result.error_count == 4

    valid_fids = [r.facility_id for r in result.valid_records]
    assert valid_fids == ["PHC_VALID_1", "PHC_VALID_2", "PHC_VALID_3"]
    assert result.valid_records[1].reported_quantity == 2500.0
    assert result.valid_records[2].reported_quantity == 75.5

    error_types = [e.error_type for e in result.errors]
    assert "NON_NUMERIC_QUANTITY" in error_types
    assert "MISSING_FACILITY_ID" in error_types
    assert "MISSING_SKU" in error_types
    assert "NEGATIVE_QUANTITY" in error_types


def test_staged_sor_payload_writer_execution_boundary():
    plan_dict = {
        "plan_id": "PLAN-20260928-TEST1234",
        "resource": "MED_RABIES",
        "source": "DH_JAGDALPUR",
        "destination": "PHC_TOKAPAL",
        "quantity": 50.0,
        "distance": 32.5,
        "approved_by": "DR_SHARMA_CMO",
        "approval_role": "Chief Medical Officer",
        "transfers": [
            {
                "source": "DH_JAGDALPUR",
                "destination": "PHC_TOKAPAL",
                "resource": "MED_RABIES",
                "quantity": 50.0,
                "distance": 32.5,
                "travel_hours": 0.8,
            }
        ],
    }

    # DVDMS Staged Payload
    sor_dvdms = write_sor_payload(plan_dict, target_system="DVDMS")
    assert sor_dvdms["status"] == "READY_FOR_SYSTEM_OF_RECORD"
    assert sor_dvdms["execution_boundary"] == "PAYLOAD_STAGED_NOT_EXECUTED_EXTERNALLY"
    assert "integrity_hash" in sor_dvdms
    assert len(sor_dvdms["line_items"]) == 1
    assert sor_dvdms["line_items"][0]["allotted_quantity"] == 50.0

    # FHIR R4 Staged Bundle
    sor_fhir = write_sor_payload(plan_dict, target_system="FHIR")
    assert sor_fhir["resourceType"] == "Bundle"
    assert sor_fhir["status"] == "READY_FOR_SYSTEM_OF_RECORD"
    assert sor_fhir["execution_boundary"] == "GENERATED — NOT EXECUTED"
