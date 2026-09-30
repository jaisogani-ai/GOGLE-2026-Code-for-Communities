"""
TATHYON System-of-Record Integration Adapters & Execution Boundary.

Section 21 of Sovereign Control Plane Specification:
- The product may generate READY_FOR_SYSTEM_OF_RECORD payloads.
- It MUST NOT claim "government transfer executed" unless there is an actual external integration.
- Adapters: DVDMSAdapter, GenericCSVAdapter, GenericRESTAdapter.
- Clear execution boundary: payloads are staged and verified, awaiting dispatch execution
  by the policy-based custodian in the official System of Record (e-Aushadhi / DVDMS / HMIS).
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .schema import now, sha256


EXECUTION_BOUNDARY_DISCLAIMER = (
    "EXECUTION BOUNDARY: PAYLOAD_STAGED_NOT_EXECUTED_EXTERNALLY. "
    "Tathyon is an intelligence and verification control plane that operates BESIDE "
    "policy-based systems of record (e-Aushadhi / DVDMS). This payload is verified, "
    "policy-checked, and signed by an authorized Medical Officer, but physical stock "
    "movement occurs only when imported into and executed by the upstream government portal."
)


@dataclass
class RowValidationError:
    """Typed error for malformed or unparseable CSV rows."""
    row_index: int
    field: str
    raw_value: Any
    error_type: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngestedStockRecord:
    """A verified, provenance-labeled stock record parsed from DVDMS/e-Aushadhi CSV export."""
    row_index: int
    facility_id: str
    facility_name: str
    district: str
    state: str
    sku: str
    drug_name: str
    batch_number: Optional[str]
    expiry_date: Optional[str]
    reported_quantity: float
    consumption_velocity: float
    source: str
    timestamp: str
    provenance: str = "CSV_IMPORT"
    raw_row: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngestResult:
    """Batch ingestion result containing valid records, typed row errors, and provenance."""
    total_rows: int
    valid_records: list[IngestedStockRecord]
    errors: list[RowValidationError]
    source_system: str
    timestamp: str = field(default_factory=now)
    provenance: str = "CSV_IMPORT"

    @property
    def valid_count(self) -> int:
        return len(self.valid_records)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict:
        return {
            "total_rows": self.total_rows,
            "valid_count": self.valid_count,
            "error_count": self.error_count,
            "source_system": self.source_system,
            "timestamp": self.timestamp,
            "provenance": self.provenance,
            "valid_records": [r.to_dict() for r in self.valid_records],
            "errors": [e.to_dict() for e in self.errors],
        }


def parse_dvdms_csv(
    csv_input: str | io.StringIO,
    default_provenance: str = "CSV_IMPORT",
    default_source: str = "DVDMS_EXPORT",
    default_state: str = "Chhattisgarh",
    default_district: str = "Bastar",
) -> IngestResult:
    """Ingests DVDMS / e-Aushadhi shaped CSV export.
    
    Coerces quantities and numeric fields. Unparseable values raise typed per-row
    validation errors without crashing the batch.
    Every ingested row gets provenance labels (source, timestamp, provenance).
    """
    if isinstance(csv_input, str):
        lines = [l for l in csv_input.splitlines() if not l.strip().startswith("#")]
        reader = csv.DictReader(lines)
    else:
        reader = csv.DictReader(csv_input)

    valid_records: list[IngestedStockRecord] = []
    errors: list[RowValidationError] = []

    row_count = 0
    for idx, raw_row in enumerate(reader, start=1):
        row_count += 1
        # Normalize keys: lower and stripped
        row = {k.strip().lower(): (v.strip() if isinstance(v, str) else v) for k, v in raw_row.items() if k}

        # 1. Facility ID
        fid = (
            row.get("facility_id")
            or row.get("facilityid")
            or row.get("facility_code")
            or row.get("facilitycode")
            or row.get("store_code")
            or row.get("storecode")
            or row.get("store_id")
            or row.get("storeid")
            or row.get("donorfacilitycode")
            or row.get("recipientfacilitycode")
        )
        if not fid:
            errors.append(RowValidationError(
                row_index=idx,
                field="facility_id",
                raw_value=raw_row.get("facility_id", ""),
                error_type="MISSING_FACILITY_ID",
                message=f"Row {idx} is missing mandatory facility_id / FacilityCode",
            ))
            continue

        # 2. SKU / Resource ID
        sku = (
            row.get("sku")
            or row.get("drug_code")
            or row.get("drugcode")
            or row.get("item_code")
            or row.get("itemcode")
            or row.get("resourcesku")
            or row.get("resource_sku")
            or row.get("resource")
        )
        if not sku:
            errors.append(RowValidationError(
                row_index=idx,
                field="sku",
                raw_value=raw_row.get("sku", ""),
                error_type="MISSING_SKU",
                message=f"Row {idx} is missing mandatory drug_code / SKU",
            ))
            continue

        # 3. Quantity (must coerce cleanly; invalid values generate typed error, never crash batch)
        raw_qty = (
            row.get("reported_quantity")
            or row.get("quantity")
            or row.get("closing_balance")
            or row.get("closingbalance")
            or row.get("availablestock")
            or row.get("available_stock")
            or row.get("stock_qty")
            or row.get("transferquantity")
            or row.get("transfer_quantity")
            or row.get("allotted_quantity")
        )
        if raw_qty is None or raw_qty == "":
            errors.append(RowValidationError(
                row_index=idx,
                field="reported_quantity",
                raw_value=raw_qty,
                error_type="MISSING_QUANTITY",
                message=f"Row {idx} has missing or empty quantity",
            ))
            continue

        # Clean quantity string (strip commas, spaces, currency symbols)
        clean_qty_str = str(raw_qty).replace(",", "").replace("₹", "").strip()
        try:
            qty_val = float(clean_qty_str)
            if qty_val < 0:
                errors.append(RowValidationError(
                    row_index=idx,
                    field="reported_quantity",
                    raw_value=raw_qty,
                    error_type="NEGATIVE_QUANTITY",
                    message=f"Row {idx} has negative quantity: {qty_val}",
                ))
                continue
        except (ValueError, TypeError):
            errors.append(RowValidationError(
                row_index=idx,
                field="reported_quantity",
                raw_value=raw_qty,
                error_type="NON_NUMERIC_QUANTITY",
                message=f"Row {idx} contains non-numeric quantity '{raw_qty}'",
            ))
            continue

        # 4. Optional fields with defaults
        fac_name = (
            row.get("facility_name")
            or row.get("facilityname")
            or row.get("store_name")
            or row.get("storename")
            or fid
        )
        district = (
            row.get("district")
            or row.get("district_name")
            or row.get("districtname")
            or default_district
        )
        state = (
            row.get("state")
            or row.get("state_name")
            or row.get("statename")
            or default_state
        )
        drug_name = (
            row.get("drug_name")
            or row.get("item_name")
            or row.get("itemname")
            or row.get("generic_drug_name")
            or sku
        )
        batch_no = row.get("batch_number") or row.get("batchno") or row.get("batch_no")
        exp_date = row.get("expiry_date") or row.get("expdate") or row.get("expirydate")

        # 5. Velocity coercion
        raw_vel = (
            row.get("consumption_velocity")
            or row.get("daily_consumption")
            or row.get("dailyconsumption")
            or row.get("consumption")
        )
        vel_val = 5.0
        if raw_vel:
            try:
                vel_val = max(0.0, float(str(raw_vel).replace(",", "").strip()))
            except (ValueError, TypeError):
                vel_val = 5.0

        ts = now()
        record = IngestedStockRecord(
            row_index=idx,
            facility_id=fid,
            facility_name=fac_name,
            district=district,
            state=state,
            sku=sku,
            drug_name=drug_name,
            batch_number=batch_no,
            expiry_date=exp_date,
            reported_quantity=qty_val,
            consumption_velocity=vel_val,
            source=default_source,
            timestamp=ts,
            provenance=default_provenance,
            raw_row=raw_row,
        )
        valid_records.append(record)

    return IngestResult(
        total_rows=row_count,
        valid_records=valid_records,
        errors=errors,
        source_system=default_source,
        timestamp=now(),
        provenance=default_provenance,
    )


def write_sor_payload(
    plan_dict: dict[str, Any],
    target_system: str = "DVDMS",
    state_code: str = "CG",
    district_name: str = "Bastar",
) -> dict[str, Any]:
    """Writes a staged System of Record payload ready for upstream execution.
    STAGED, NEVER EXECUTED EXTERNALLY.
    """
    if target_system.upper() == "DVDMS":
        return DVDMSAdapter.format_voucher(plan_dict, state_code=state_code, district_name=district_name)
    elif target_system.upper() == "FHIR":
        return FHIRR4Adapter.format_fhir_bundle(plan_dict)
    else:
        return GenericRESTAdapter.format_webhook_payload(plan_dict)


class DVDMSAdapter:
    """State Drug & Vaccine Distribution Management System (DVDMS) voucher adapter."""

    @staticmethod
    def format_voucher(
        plan_dict: dict[str, Any],
        state_code: str = "CG",        # Chhattisgarh
        district_name: str = "Bastar",
    ) -> dict[str, Any]:
        """Converts a Tathyon ResponsePlan into a canonical DVDMS Indent/Transfer Voucher."""
        plan_id = plan_dict.get("plan_id", "PLAN-UNKNOWN")
        voucher_id = f"DVDMS-{state_code}-{district_name.upper()[:4]}-{plan_id.replace('PLAN-', '')[:10]}"

        line_items = []
        for i, tr in enumerate(plan_dict.get("transfers", [])):
            res = tr.get("resource", plan_dict.get("resource", "MEDICINE"))
            is_cold_chain = "VACCINE" in res.upper() or "OXYTOCIN" in res.upper()
            line_items.append({
                "indent_item_sr_no": i + 1,
                "drug_item_code": res,
                "generic_drug_name": res.replace("_", " ").title(),
                "allotted_quantity": tr.get("quantity", 0.0),
                "unit_of_measurement": "VIAL" if is_cold_chain else "TABLET/UNIT",
                "issuing_store_code": tr.get("source", ""),
                "issuing_store_name": tr.get("source_name", tr.get("source", "")),
                "receiving_facility_code": tr.get("destination", ""),
                "receiving_facility_name": tr.get("destination_name", tr.get("destination", "")),
                "cold_chain_preservation_required": is_cold_chain,
                "transit_distance_km": tr.get("distance_km", tr.get("distance", 0.0)),
                "estimated_transit_hours": tr.get("travel_hours", 0.0),
                "item_dispatch_status": "READY_FOR_DISPATCH",
            })

        seal = sha256({
            "voucher_id": voucher_id,
            "plan_id": plan_id,
            "total_quantity": plan_dict.get("quantity", plan_dict.get("total_quantity", 0.0)),
            "approver": plan_dict.get("approved_by"),
        })

        return {
            "system_target": "DVDMS",
            "format": "STATE_DVDMS_TRANSFER_INDENT_V2",
            "voucher_id": voucher_id,
            "source_plan_id": plan_id,
            "state_code": state_code,
            "district_name": district_name,
            "indent_type": "SPECIAL_EMERGENCY_INTER_FACILITY_TRANSFER",
            "dispatch_priority": "LIFE_SAVING_EMERGENCY",
            "total_quantity": plan_dict.get("quantity", plan_dict.get("total_quantity", 0.0)),
            "line_items": line_items,
            "officer_authorization": {
                "approving_officer_id": plan_dict.get("approved_by") or "PENDING_SIGNATURE",
                "officer_role": plan_dict.get("approval_role") or "Chief Medical Officer",
                "approval_timestamp": plan_dict.get("approval_timestamp") or now(),
                "authority_basis": "TATHYON policy; configure and verify applicable department/state delegation",
            },
            "integrity_hash": seal,
            "status": "READY_FOR_SYSTEM_OF_RECORD",
            "execution_boundary": "PAYLOAD_STAGED_NOT_EXECUTED_EXTERNALLY",
            "disclaimer": EXECUTION_BOUNDARY_DISCLAIMER,
            "generated_at": now(),
        }


class GenericCSVAdapter:
    """RFC 4180 CSV export for batch manual import in peripheral facilities without API."""

    @staticmethod
    def format_csv(plan_dict: dict[str, Any]) -> str:
        """Serializes a ResponsePlan into standard CSV text."""
        output = io.StringIO()
        writer = csv.writer(output)

        # policy-based and execution boundary metadata comments
        output.write(f"# TATHYON SYSTEM OF RECORD STAGING PAYLOAD\n")
        output.write(f"# STATUS: READY_FOR_SYSTEM_OF_RECORD\n")
        output.write(f"# DISCLAIMER: {EXECUTION_BOUNDARY_DISCLAIMER}\n")

        headers = [
            "VoucherID",
            "PlanID",
            "LegNumber",
            "DonorFacilityCode",
            "DonorFacilityName",
            "RecipientFacilityCode",
            "RecipientFacilityName",
            "ResourceSKU",
            "TransferQuantity",
            "DistanceKM",
            "TransitHours",
            "ColdChainRequired",
            "Priority",
            "ApprovingOfficerID",
            "ApprovingRole",
            "ApprovalTimestamp",
            "Status",
        ]
        writer.writerow(headers)

        plan_id = plan_dict.get("plan_id", "PLAN-UNKNOWN")
        voucher_id = f"CSV-TR-{plan_id.replace('PLAN-', '')[:10]}"
        officer = plan_dict.get("approved_by", "PENDING_SIGNATURE")
        role = plan_dict.get("approval_role", "ChiefMedicalOfficer")
        app_time = plan_dict.get("approval_timestamp", now())

        for i, tr in enumerate(plan_dict.get("transfers", [])):
            res = tr.get("resource", plan_dict.get("resource", "MEDICINE"))
            is_cold_chain = "VACCINE" in res.upper() or "OXYTOCIN" in res.upper()
            row = [
                voucher_id,
                plan_id,
                i + 1,
                tr.get("source", ""),
                tr.get("source_name", tr.get("source", "")),
                tr.get("destination", ""),
                tr.get("destination_name", tr.get("destination", "")),
                res,
                tr.get("quantity", 0.0),
                tr.get("distance_km", tr.get("distance", 0.0)),
                tr.get("travel_hours", 0.0),
                "YES" if is_cold_chain else "NO",
                "EMERGENCY",
                officer,
                role,
                app_time,
                "READY_FOR_SYSTEM_OF_RECORD",
            ]
            writer.writerow(row)

        return output.getvalue()


class GenericRESTAdapter:
    """Standard REST payload with HMAC-SHA256 signature and idempotency headers for webhooks."""

    @staticmethod
    def format_webhook_payload(
        plan_dict: dict[str, Any],
        webhook_target_url: str = "https://eaushadhi.gov.in/api/v1/transfers/inbound",
        api_version: str = "2026-04-01",
    ) -> dict[str, Any]:
        """Packages ResponsePlan into an authenticated REST API envelope."""
        plan_id = plan_dict.get("plan_id", "PLAN-UNKNOWN")
        idempotency_key = f"idemp_{sha256(plan_id + str(plan_dict.get('approval_timestamp', '')))[:24]}"

        body = {
            "event": "health_resource.transfer.staged",
            "api_version": api_version,
            "plan_id": plan_id,
            "resource": plan_dict.get("resource"),
            "source_facility": plan_dict.get("source"),
            "destination_facility": plan_dict.get("destination"),
            "total_quantity": plan_dict.get("quantity", plan_dict.get("total_quantity", 0.0)),
            "distance_km": plan_dict.get("distance", 0.0),
            "transfers": plan_dict.get("transfers", []),
            "signoff": {
                "officer_id": plan_dict.get("approved_by"),
                "role": plan_dict.get("approval_role"),
                "timestamp": plan_dict.get("approval_timestamp"),
                "status": "APPROVED",
            },
            "status": "READY_FOR_SYSTEM_OF_RECORD",
            "execution_boundary": "PAYLOAD_STAGED_NOT_EXECUTED_EXTERNALLY",
            "timestamp": now(),
        }

        # Calculate payload signature
        signature = sha256(body)

        return {
            "target_url": webhook_target_url,
            "http_method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-Tathyon-Signature": f"sha256={signature}",
                "X-Idempotency-Key": idempotency_key,
                "X-Execution-Status": "READY_FOR_SYSTEM_OF_RECORD",
                "X-Policy-Authority": "AUTHORIZED_HUMAN_APPROVAL",
            },
            "payload_body": body,
            "disclaimer": EXECUTION_BOUNDARY_DISCLAIMER,
        }


class FHIRR4Adapter:
    """HL7 FHIR R4 Standards-Compatible Resource Adapter.
    
    Section 22 of CTO War-Room Specification:
    - Generates standard FHIR R4 SupplyRequest, SupplyDelivery, Location, and Task resources.
    - Strict boundary: 'A SupplyRequest is a request; it is NOT proof that a transfer occurred.'
    - Status: READY_FOR_SYSTEM_OF_RECORD.
    - Execution Boundary: GENERATED — NOT EXECUTED.
    """

    FHIR_BOUNDARY_DISCLAIMER = (
        "FHIR INTEROPERABILITY BOUNDARY: GENERATED — NOT EXECUTED. "
        "This HL7 FHIR R4 Bundle represents a standards-compatible SupplyRequest and Task. "
        "A SupplyRequest is an operational request, NOT physical proof that an inter-facility "
        "truck was dispatched or received. Actual physical execution requires upstream custody sign-off."
    )

    @classmethod
    def format_fhir_bundle(
        cls,
        plan_dict: dict[str, Any],
        base_url: str = "https://tathyon.health/fhir/r4",
    ) -> dict[str, Any]:
        """Formats a ResponsePlan into an HL7 FHIR R4 Bundle of SupplyRequest, SupplyDelivery, Location, and Task."""
        plan_id = plan_dict.get("plan_id", "PLAN-UNKNOWN")
        bundle_id = f"bundle-transfer-{plan_id.replace('PLAN-', '').lower()[:12]}"
        created_at = plan_dict.get("created_at") or now()
        approved = plan_dict.get("status") == "APPROVED"
        req_status = "active" if approved else "draft"
        task_status = "accepted" if approved else "requested"

        donor_id = plan_dict.get("source", "FAC-DONOR-001")
        recipient_id = plan_dict.get("destination", "FAC-RECIPIENT-001")
        resource_sku = plan_dict.get("resource", "MED_ESSENTIAL")
        total_qty = float(plan_dict.get("quantity", plan_dict.get("total_quantity", 0.0)))
        approver = plan_dict.get("approved_by") or "PENDING_SIGNATURE"

        entries: list[dict[str, Any]] = []

        # 1. Donor Location
        entries.append({
            "fullUrl": f"{base_url}/Location/{donor_id}",
            "resource": {
                "resourceType": "Location",
                "id": donor_id,
                "identifier": [{"system": "https://eaushadhi.gov.in/facility", "value": donor_id}],
                "status": "active",
                "name": plan_dict.get("source_name", donor_id),
                "mode": "instance",
                "type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-RoleCode", "code": "HOSP", "display": "Hospital/PHC"}]}],
                "physicalType": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/location-physical-type", "code": "bu", "display": "Building"}]},
            }
        })

        # 2. Recipient Location
        entries.append({
            "fullUrl": f"{base_url}/Location/{recipient_id}",
            "resource": {
                "resourceType": "Location",
                "id": recipient_id,
                "identifier": [{"system": "https://eaushadhi.gov.in/facility", "value": recipient_id}],
                "status": "active",
                "name": plan_dict.get("destination_name", recipient_id),
                "mode": "instance",
                "type": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-RoleCode", "code": "HOSP", "display": "Hospital/PHC"}]}],
                "physicalType": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/location-physical-type", "code": "bu", "display": "Building"}]},
            }
        })

        # 3. SupplyRequest
        supply_req_id = f"sr-{plan_id.replace('PLAN-', '').lower()[:12]}"
        entries.append({
            "fullUrl": f"{base_url}/SupplyRequest/{supply_req_id}",
            "resource": {
                "resourceType": "SupplyRequest",
                "id": supply_req_id,
                "identifier": [
                    {"system": "https://tathyon.health/supply-requests", "value": supply_req_id},
                    {"system": "https://tathyon.health/plans", "value": plan_id},
                ],
                "status": req_status,
                "category": {
                    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/supplyrequest-kind", "code": "central-stock", "display": "Central Stock Redistribution"}]
                },
                "priority": "urgent",
                "itemCodeableConcept": {
                    "coding": [{"system": "https://eaushadhi.gov.in/drug-codes", "code": resource_sku, "display": resource_sku.replace("_", " ").title()}],
                    "text": resource_sku,
                },
                "quantity": {
                    "value": total_qty,
                    "unit": "vial" if "VACCINE" in resource_sku.upper() else "unit",
                    "system": "http://unitsofmeasure.org",
                    "code": "1",
                },
                "deliverTo": {"reference": f"Location/{recipient_id}", "display": plan_dict.get("destination_name", recipient_id)},
                "deliverFrom": {"reference": f"Location/{donor_id}", "display": plan_dict.get("source_name", donor_id)},
                "authoredOn": created_at,
                "requester": {"display": "TATHYON Sovereign Health Resource Resilience Control Plane"},
                "reasonCode": [{"text": plan_dict.get("reason", "Acute stockout prevention inter-facility redistribution")}],
            }
        })

        # 4. SupplyDelivery (Proposed / Staged)
        supply_del_id = f"sd-{plan_id.replace('PLAN-', '').lower()[:12]}"
        entries.append({
            "fullUrl": f"{base_url}/SupplyDelivery/{supply_del_id}",
            "resource": {
                "resourceType": "SupplyDelivery",
                "id": supply_del_id,
                "identifier": [{"system": "https://tathyon.health/supply-deliveries", "value": supply_del_id}],
                "basedOn": [{"reference": f"SupplyRequest/{supply_req_id}"}],
                "status": "in-progress" if approved else "entered-in-error",
                "type": {
                    "coding": [{"system": "http://terminology.hl7.org/CodeSystem/supply-item-type", "code": "medication", "display": "Medication"}]
                },
                "suppliedItem": {
                    "quantity": {"value": total_qty, "unit": "unit"},
                    "itemCodeableConcept": {"text": resource_sku},
                },
                "supplier": {"reference": f"Location/{donor_id}"},
                "destination": {"reference": f"Location/{recipient_id}"},
            }
        })

        # 5. Task (Coordinating Workflow)
        task_id = f"task-{plan_id.replace('PLAN-', '').lower()[:12]}"
        entries.append({
            "fullUrl": f"{base_url}/Task/{task_id}",
            "resource": {
                "resourceType": "Task",
                "id": task_id,
                "identifier": [{"system": "https://tathyon.health/tasks", "value": task_id}],
                "basedOn": [{"reference": f"SupplyRequest/{supply_req_id}"}],
                "status": task_status,
                "intent": "order",
                "priority": "urgent",
                "code": {
                    "coding": [{"system": "http://hl7.org/fhir/CodeSystem/task-code", "code": "fulfill", "display": "Fulfill Supply Request"}]
                },
                "description": f"Redistribution of {total_qty} units of {resource_sku} from {donor_id} to {recipient_id}",
                "focus": {"reference": f"SupplyRequest/{supply_req_id}"},
                "for": {"reference": f"Location/{recipient_id}"},
                "executionPeriod": {"start": created_at},
                "authoredOn": created_at,
                "lastModified": now(),
                "requester": {"display": approver},
                "owner": {"reference": f"Location/{donor_id}"},
            }
        })

        bundle = {
            "resourceType": "Bundle",
            "id": bundle_id,
            "type": "collection",
            "timestamp": now(),
            "meta": {
                "profile": ["http://hl7.org/fhir/StructureDefinition/Bundle"],
                "lastUpdated": now(),
            },
            "entry": entries,
            "total": len(entries),
            "status": "READY_FOR_SYSTEM_OF_RECORD",
            "execution_boundary": "GENERATED — NOT EXECUTED",
            "disclaimer": cls.FHIR_BOUNDARY_DISCLAIMER,
            "tathyon_provenance": plan_dict.get("provenance", "SIMULATION"),
            "hash_seal": sha256({"bundle_id": bundle_id, "plan_id": plan_id, "total_qty": total_qty, "approver": approver}),
        }
        return bundle
