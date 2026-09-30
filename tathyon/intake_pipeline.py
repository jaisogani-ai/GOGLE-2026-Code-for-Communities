"""DVDMS / e-Aushadhi-shaped stock export intake.

    CSV / TSV -> schema validation -> row quarantine -> provenance
              -> normalization -> StockRow (fed to the workspace)

This is a FILE-SHAPED adapter. It is not a live integration with any state
DVDMS or e-Aushadhi deployment; those systems' export columns vary by state, so
header aliases below are configuration, not a claim about a specific portal.

Nothing here repairs a bad row. A row is either accepted exactly as written or
quarantined with typed reasons that the ledger records.
"""
from __future__ import annotations

import csv
import io
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

MAX_UPLOAD_BYTES = 2_000_000
MAX_ROWS = 20_000
MAX_CELL_CHARS = 200
MAX_FUTURE_SKEW = timedelta(hours=24)

# Canonical column -> accepted header spellings (lower-cased, stripped).
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "facility_id": ("facility_id", "facilitycode", "facility_code", "store_id", "storecode"),
    "sku": ("sku", "drugcode", "drug_code", "item_code", "itemcode"),
    "reported_qty": ("reported_qty", "closing_balance", "availablestock", "available_stock", "closing_stock"),
    "issues": ("issues", "issuedqty", "issued_qty", "issue_qty"),
    "receipts": ("receipts", "receiptqty", "receipt_qty"),
    "report_date": ("report_date", "date", "as_on_date", "stock_date"),
    "period_days": ("period_days", "days_in_period"),
    "batch_no": ("batch_no", "batchno", "batch"),
    "expiry_date": ("expiry_date", "expirydate", "expiry"),
    "sku_name": ("sku_name", "drugname", "drug_name", "item_name", "itemname"),
    "cold_chain": ("cold_chain", "coldchain", "cold_chain_required"),
    "ved": ("ved", "ved_class", "ved_category"),
}
REQUIRED = ("facility_id", "sku", "reported_qty", "issues", "report_date")
NUMERIC = ("reported_qty", "issues", "receipts", "period_days")
# Leading characters a spreadsheet may execute as a formula (CSV injection).
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class IntakeError(ValueError):
    """The whole file is rejected (not a per-row quarantine)."""


@dataclass(frozen=True)
class StockRow:
    row_number: int
    facility_id: str
    sku: str
    reported_qty: float
    daily_consumption: float
    report_date: str
    batch_no: Optional[str]
    expiry_date: Optional[str]
    receipts: float
    issues: float
    period_days: float
    sku_name: Optional[str] = None
    cold_chain: Optional[bool] = None
    ved: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IntakeReport:
    source_filename: str
    delimiter: str
    total_rows: int = 0
    accepted: list[StockRow] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        reasons: dict[str, int] = {}
        for q in self.quarantined:
            for r in q["reasons"]:
                reasons[r] = reasons.get(r, 0) + 1
        return {
            "source_filename": self.source_filename,
            "delimiter": "TAB" if self.delimiter == "\t" else self.delimiter,
            "total_rows": self.total_rows,
            "accepted_rows": len(self.accepted),
            "quarantined_rows": len(self.quarantined),
            "quarantine_reasons": reasons,
        }


def _resolve_headers(fieldnames: list[str]) -> dict[str, str]:
    """Map canonical column -> header present in the file."""
    present = {h.strip().lower(): h for h in fieldnames if h is not None}
    resolved: dict[str, str] = {}
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            if alias in present:
                resolved[canonical] = present[alias]
                break
    return resolved


def _sniff_delimiter(text: str, requested: Optional[str]) -> str:
    if requested in (",", "\t", ";", "|"):
        return requested
    first = text.split("\n", 1)[0]
    return "\t" if first.count("\t") > first.count(",") else ","


def _parse_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("non-finite")
    return value


def _parse_date(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _validate_row(row_number: int, raw: dict[str, str], cols: dict[str, str],
                  known_facilities: Optional[set[str]], known_skus: Optional[set[str]],
                  as_of: datetime) -> tuple[Optional[StockRow], list[str]]:
    reasons: list[str] = []
    cell = {c: (raw.get(h) or "").strip() for c, h in cols.items()}

    for value in raw.values():
        text = value if isinstance(value, str) else ""
        if len(text) > MAX_CELL_CHARS:
            reasons.append("CELL_TOO_LONG")
            break
    for c in ("facility_id", "sku", "batch_no"):
        if cell.get(c, "").startswith(FORMULA_PREFIXES):
            reasons.append("FORMULA_INJECTION_SUSPECTED")
            break

    missing = [c for c in REQUIRED if not cell.get(c)]
    if missing:
        reasons.append("MISSING_REQUIRED_FIELD")

    numbers: dict[str, float] = {}
    for c in NUMERIC:
        if not cell.get(c):
            continue
        try:
            numbers[c] = _parse_float(cell[c])
        except ValueError:
            reasons.append("UNPARSEABLE_NUMBER")
            continue
        if numbers[c] < 0:
            reasons.append("NEGATIVE_QUANTITY")
    period = numbers.get("period_days", 1.0)
    if "period_days" in numbers and period <= 0:
        reasons.append("NON_POSITIVE_PERIOD")

    report_dt: Optional[datetime] = None
    if cell.get("report_date"):
        try:
            report_dt = _parse_date(cell["report_date"])
            if report_dt > as_of + MAX_FUTURE_SKEW:
                reasons.append("FUTURE_DATED")
        except ValueError:
            reasons.append("UNPARSEABLE_DATE")
    expiry: Optional[str] = None
    if cell.get("expiry_date"):
        try:
            expiry = _parse_date(cell["expiry_date"]).date().isoformat()
        except ValueError:
            reasons.append("UNPARSEABLE_EXPIRY_DATE")

    ved = (cell.get("ved") or "").upper() or None
    if ved and ved not in ("V", "E", "N"):
        reasons.append("INVALID_VED_CLASS")
    cold_raw = (cell.get("cold_chain") or "").lower()
    cold = None if not cold_raw else cold_raw in ("1", "true", "yes", "y")
    if cell.get("sku_name", "").startswith(FORMULA_PREFIXES):
        reasons.append("FORMULA_INJECTION_SUSPECTED")
    fac, sku = cell.get("facility_id", ""), cell.get("sku", "")
    if fac and known_facilities is not None and fac not in known_facilities:
        reasons.append("UNKNOWN_FACILITY")
    if sku and known_skus is not None and sku not in known_skus:
        reasons.append("UNKNOWN_SKU")

    if reasons:
        return None, sorted(set(reasons))
    issues = numbers.get("issues", 0.0)
    return StockRow(
        row_number=row_number, facility_id=fac, sku=sku,
        reported_qty=numbers["reported_qty"],
        daily_consumption=round(issues / period, 4),
        report_date=report_dt.isoformat(),  # type: ignore[union-attr]
        batch_no=cell.get("batch_no") or None, expiry_date=expiry,
        receipts=numbers.get("receipts", 0.0), issues=issues, period_days=period,
        sku_name=(cell.get("sku_name") or None), cold_chain=cold, ved=ved,
    ), []


def _safe_raw(raw: dict) -> dict:
    """Quarantine copy: bounded, and never re-emitted as an executable cell."""
    out = {}
    for k, v in list(raw.items())[:20]:
        text = str(v if v is not None else "")[:MAX_CELL_CHARS]
        out[str(k)[:60]] = ("'" + text) if text.startswith(FORMULA_PREFIXES) else text
    return out


def parse_stock_export(content: str, source_filename: str, *,
                       known_facilities: Optional[set[str]] = None,
                       known_skus: Optional[set[str]] = None,
                       delimiter: Optional[str] = None,
                       as_of: Optional[datetime] = None) -> IntakeReport:
    """Validate a stock export. Raises IntakeError when the FILE is unusable;
    otherwise returns accepted rows plus typed per-row quarantine."""
    if not source_filename or "/" in source_filename or "\\" in source_filename or ".." in source_filename:
        raise IntakeError("INVALID_SOURCE_FILENAME")
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise IntakeError("FILE_TOO_LARGE")
    text = content.lstrip("﻿").strip()
    if not text:
        raise IntakeError("EMPTY_FILE")

    delim = _sniff_delimiter(text, delimiter)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    cols = _resolve_headers(reader.fieldnames or [])
    missing = [c for c in REQUIRED if c not in cols]
    if missing:
        raise IntakeError(f"MISSING_COLUMNS:{','.join(missing)}")

    now_ = as_of or datetime.now(timezone.utc)
    report = IntakeReport(source_filename=source_filename, delimiter=delim)
    seen: set[tuple[str, str, str]] = set()
    for idx, raw in enumerate(reader, start=1):
        if idx > MAX_ROWS:
            raise IntakeError("TOO_MANY_ROWS")
        report.total_rows += 1
        row, reasons = _validate_row(idx, raw, cols, known_facilities, known_skus, now_)
        if row is not None:
            key = (row.facility_id, row.sku, row.report_date)
            if key in seen:
                row, reasons = None, ["DUPLICATE_ROW"]
            else:
                seen.add(key)
        if row is None:
            report.quarantined.append({"row_number": idx, "reasons": reasons, "raw": _safe_raw(raw)})
        else:
            report.accepted.append(row)
    return report


FACILITY_REQUIRED = ("facility_id", "name", "tier", "lat", "lon")
FACILITY_TIERS = {"PHC", "CHC", "SC", "DISTRICT_HOSPITAL", "SUB_DISTRICT_HOSPITAL", "DISTRICT_WAREHOUSE",
                  "HOSPITAL", "CLINIC", "UNCLASSIFIED"}


def parse_facility_registry(content: str, source_filename: str) -> tuple[list[dict], list[dict]]:
    """facility_id,name,tier,lat,lon[,block,has_cold_chain,custodian_id] -> (accepted, quarantined)."""
    if not source_filename or any(c in source_filename for c in "/\\") or ".." in source_filename:
        raise IntakeError("INVALID_SOURCE_FILENAME")
    if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise IntakeError("FILE_TOO_LARGE")
    text = content.lstrip("﻿").strip()
    if not text:
        raise IntakeError("EMPTY_FILE")
    reader = csv.DictReader(io.StringIO(text), delimiter=_sniff_delimiter(text, None))
    headers = {h.strip().lower() for h in reader.fieldnames or [] if h}
    missing = [c for c in FACILITY_REQUIRED if c not in headers]
    if missing:
        raise IntakeError(f"MISSING_COLUMNS:{','.join(missing)}")
    accepted: list[dict] = []
    quarantined: list[dict] = []
    seen: set[str] = set()
    for idx, raw in enumerate(reader, start=1):
        if idx > MAX_ROWS:
            raise IntakeError("TOO_MANY_ROWS")
        row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        reasons = []
        if any(row.get(c, "").startswith(FORMULA_PREFIXES) for c in ("facility_id", "name", "custodian_id")):
            reasons.append("FORMULA_INJECTION_SUSPECTED")
        if any(not row.get(c) for c in FACILITY_REQUIRED):
            reasons.append("MISSING_REQUIRED_FIELD")
        lat = lon = 0.0
        try:
            lat, lon = float(row.get("lat", "")), float(row.get("lon", ""))
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                reasons.append("COORDINATES_OUT_OF_RANGE")
        except ValueError:
            reasons.append("UNPARSEABLE_COORDINATES")
        if row.get("tier") and row["tier"].upper() not in FACILITY_TIERS:
            reasons.append("UNKNOWN_TIER")
        if row.get("facility_id") in seen:
            reasons.append("DUPLICATE_FACILITY")
        if reasons:
            quarantined.append({"row_number": idx, "reasons": sorted(set(reasons)), "raw": _safe_raw(raw)})
            continue
        seen.add(row["facility_id"])
        accepted.append({"facility_id": row["facility_id"][:60], "name": row["name"][:120],
                         "tier": row["tier"].upper(), "lat": lat, "lon": lon, "block": row.get("block") or None,
                         "has_cold_chain": row.get("has_cold_chain", "true").lower() not in ("0", "false", "no"),
                         "custodian_id": row.get("custodian_id") or None,
                         "provenance": "USER_SUPPLIED_UNVERIFIED"})
    return accepted, quarantined


OSM_TIER = {"hospital": "HOSPITAL", "clinic": "CLINIC"}


def facilities_from_osm(doc: dict, district: Optional[str] = None, state: Optional[str] = None) -> list[dict]:
    """Real public facility locations from an OpenStreetMap Overpass extract (ODbL).

    OSM tells us a facility exists at a place. It does not tell us public/private
    status, tier, cold chain or stock; those stay unknown until a registry says so.
    """
    out, seen = [], set()
    for f in doc.get("facilities", []):
        try:
            lat, lon = float(f["lat"]), float(f["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        fid = f"OSM-{f.get('osm_type', 'node')[0].upper()}{f.get('osm_id')}"
        if fid in seen or not f.get("name"):
            continue
        seen.add(fid)
        tags = f.get("raw_tags", {})
        out.append({"facility_id": fid, "name": str(f["name"])[:120],
                    "tier": OSM_TIER.get(f.get("amenity", ""), "UNCLASSIFIED"),
                    "lat": lat, "lon": lon, "block": district or tags.get("addr:district") or tags.get("addr:city"),
                    "district": district, "state": state,
                    "has_cold_chain": None, "custodian_id": None,
                    "provenance": "REAL_PUBLIC_OSM",
                    "source_url": f.get("osm_url"), "source_fetched_at": doc.get("fetched_at"),
                    "source_fetch_status": doc.get("fetch_status"),
                    "attribution": doc.get("attribution", "© OpenStreetMap contributors, ODbL")})
    return out
