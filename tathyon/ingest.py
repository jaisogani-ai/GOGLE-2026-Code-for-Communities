"""
Tathyon Ingestion Connectors: Parsing legacy healthcare data drops.

Transforms standard CSV exports from CDAC e-Aushadhi / DVDMS and
concessionaire BEMMP maintenance systems into normalized, immutable
Tathyon Claim and SLAContract objects.

DESIGN POSITION:
Tathyon NEVER replaces the legacy system of record. It operates upstream as an
adjudication harness, ingesting claims from these systems, issuing audit nonces,
and verifying physical truth before actions execute.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Optional

from .schema import (
    Claim,
    Provenance,
    ResourceType,
    SLAContract,
    new_id,
    now,
)


def parse_eaushadhi_stock_csv(
    csv_content: str,
    source_system: str = "e-Aushadhi/DVDMS",
    source_actor: str = "warehouse_export",
) -> list[Claim]:
    """Parse a standard e-Aushadhi facility stock ledger CSV export.
    
    Expected CSV columns (flexible matching):
    facility_id, sku (or drug_code), drug_name, batch, expiry_date,
    reported_stock (or closing_balance), unit_price (optional)
    """
    claims: list[Claim] = []
    reader = csv.DictReader(io.StringIO(csv_content.strip()))
    
    for row in reader:
        # Normalize key names
        normalized = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items() if k}
        
        facility_id = (
            normalized.get("facility_id")
            or normalized.get("facility")
            or normalized.get("location_code")
            or "UNKNOWN_FACILITY"
        )
        sku = (
            normalized.get("sku")
            or normalized.get("drug_code")
            or normalized.get("item_code")
            or normalized.get("drug_name")
            or "UNKNOWN_SKU"
        )
        
        try:
            qty_str = (
                normalized.get("reported_stock")
                or normalized.get("closing_balance")
                or normalized.get("quantity")
                or normalized.get("stock")
                or "0"
            )
            reported_qty = float(qty_str)
        except ValueError:
            reported_qty = 0.0

        batch = normalized.get("batch") or normalized.get("batch_no") or "UNKNOWN_BATCH"
        expiry = normalized.get("expiry_date") or normalized.get("expiry") or ""

        claim_state = {
            "quantity": reported_qty,
            "batch": batch,
            "expiry_date": expiry,
            "drug_name": normalized.get("drug_name", sku),
            "unit": normalized.get("unit", "tablets/vials"),
        }

        claims.append(
            Claim(
                claim_id=new_id("claim_eaushadhi"),
                facility_id=facility_id,
                resource_type=ResourceType.MEDICINE,
                resource_key=sku,
                state=claim_state,
                source_system=source_system,
                source_actor=source_actor,
                effective_at=normalized.get("date") or now(),
                ingested_at=now(),
                provenance=Provenance.REAL_USER_PROVIDED,
            )
        )

    return claims


def parse_bemmp_contracts_csv(csv_content: str) -> list[SLAContract]:
    """Parse BEMMP equipment maintenance contracts CSV.
    
    Expected CSV columns:
    contract_id, asset_id, vendor_id, vendor_name, sla_target,
    monthly_base_fee_inr, penalty_rate_per_pct
    """
    contracts: list[SLAContract] = []
    reader = csv.DictReader(io.StringIO(csv_content.strip()))

    for row in reader:
        normalized = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items() if k}
        contract_id = normalized.get("contract_id") or new_id("cntr")
        asset_id = normalized.get("asset_id") or "UNKNOWN_ASSET"
        vendor_id = normalized.get("vendor_id") or "VND_DEFAULT"
        vendor_name = normalized.get("vendor_name") or "Biomedical Maintenance Concessionaire"

        try:
            sla_target = float(normalized.get("sla_target", 0.95))
        except ValueError:
            sla_target = 0.95

        try:
            monthly_base = float(normalized.get("monthly_base_fee_inr", 150_000.0))
        except ValueError:
            monthly_base = 150_000.0

        try:
            penalty_rate = float(normalized.get("penalty_rate_per_pct", 10_000.0))
        except ValueError:
            penalty_rate = 10_000.0

        contracts.append(
            SLAContract(
                contract_id=contract_id,
                asset_id=asset_id,
                vendor_id=vendor_id,
                vendor_name=vendor_name,
                sla_target=sla_target,
                monthly_base_fee_inr=monthly_base,
                penalty_rate_per_pct=penalty_rate,
            )
        )

    return contracts


def parse_bemmp_downtime_claims_csv(csv_content: str) -> list[dict[str, Any]]:
    """Parse maintenance ticket logs / invoice claims submitted by BEMMP vendors.
    
    Expected CSV columns:
    asset_id, facility_id, claimed_uptime, vendor_ticket_id, reported_breakdown_hours
    """
    records: list[dict[str, Any]] = []
    reader = csv.DictReader(io.StringIO(csv_content.strip()))

    for row in reader:
        normalized = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items() if k}
        asset_id = normalized.get("asset_id", "")
        facility_id = normalized.get("facility_id", "")
        
        try:
            claimed_uptime = float(normalized.get("claimed_uptime", 1.0))
        except ValueError:
            claimed_uptime = 1.0

        try:
            downtime_hours = float(normalized.get("reported_breakdown_hours", 0.0))
        except ValueError:
            downtime_hours = 0.0

        records.append({
            "asset_id": asset_id,
            "facility_id": facility_id,
            "claimed_uptime": claimed_uptime,
            "vendor_ticket_id": normalized.get("vendor_ticket_id", ""),
            "reported_breakdown_hours": downtime_hours,
            "period": normalized.get("period", "CURRENT_QUARTER"),
        })

    return records
