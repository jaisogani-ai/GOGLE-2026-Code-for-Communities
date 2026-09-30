"""
Notifications driven by real events.

The directive is explicit: "notifications must originate from actual backend
events. No fake notification animations." So this is a small class that reads
the event log, applies deterministic rules to detect the six named alert
categories, and returns them structured -- no cron, no timer, no random.

Called by the API's /notifications endpoint. Every alert cites the event
offset(s) that produced it, so an operator asking "why did this fire?" gets a
specific answer, not a colour.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from .schema import EventType, VerificationState


ALERT_CATEGORIES = [
    "CRITICAL_STOCKOUT_RISK",
    "UNVERIFIED_CRITICAL_INVENTORY",
    "EXPIRY_RISK",
    "EQUIPMENT_VERIFICATION_REQUIRED",
    "CONFLICTING_RECORDS",
    "TRANSFER_BLOCKED",
]

SEVERITY = {
    "CRITICAL_STOCKOUT_RISK": "CRITICAL",
    "UNVERIFIED_CRITICAL_INVENTORY": "HIGH",
    "TRANSFER_BLOCKED": "HIGH",
    "CONFLICTING_RECORDS": "HIGH",
    "EXPIRY_RISK": "WATCH",
    "EQUIPMENT_VERIFICATION_REQUIRED": "WATCH",
}


@dataclass
class Alert:
    category: str
    severity: str
    facility_id: str
    resource_key: str
    resource_type: str
    title: str
    detail: str
    source_events: list          # event offsets that triggered this alert
    recommended_action: str

    def to_dict(self) -> dict:
        return asdict(self)


def _last_state(events: list, key: tuple) -> Optional[str]:
    """Latest verification-relevant state hint for one resource by looking at
    which event type came last."""
    for e in reversed(events):
        if (e.facility_id, e.resource_key) != key:
            continue
        if e.event_type in (EventType.ATTESTED, EventType.EVIDENCE_CAPTURED,
                            EventType.RECONCILED):
            return "verified"
        if e.event_type == EventType.REJECTED:
            return "rejected"
        if e.event_type == EventType.FLAGGED:
            return "flagged"
    return None


def build(store, registry=None) -> list[dict]:
    """Scan the event log and emit the alerts a district officer needs.

    Reads the store as it stands. Idempotent -- running it twice yields the
    same list. Does not mutate state; there is no "acknowledge" write path
    here, because acknowledgement is a workflow decision this module should
    not make on its own.
    """
    events = store.events
    seen_by_resource: dict[tuple, list] = {}
    for e in events:
        seen_by_resource.setdefault((e.facility_id, e.resource_key), []).append(e)

    alerts: list[Alert] = []

    for (fid, key), evs in seen_by_resource.items():
        latest = evs[-1]
        rtype = latest.resource_type.value

        # -- TRANSFER_BLOCKED : a decision event that refused the transfer -----
        for e in evs:
            if e.event_type == EventType.DECISION_MADE and (
                e.payload.get("status", "").startswith("BLOCKED")
                or e.payload.get("allowed") is False
            ):
                alerts.append(Alert(
                    category="TRANSFER_BLOCKED", severity="HIGH",
                    facility_id=fid, resource_key=key, resource_type=rtype,
                    title=f"Transfer blocked for {key} at {fid}",
                    detail=(f"Status: {e.payload.get('status','?')}. "
                            f"Reasons: {', '.join(e.payload.get('reasons') or [])}."),
                    source_events=[e.offset],
                    recommended_action=(
                        "Dispatch verification to the source facility, or, if urgent, "
                        "invoke break-glass -- which will create a verification "
                        "obligation with a deadline.")))
                break

        # -- CONFLICTING_RECORDS : reconciled disagreement flagged ------------
        for e in evs:
            if e.event_type == EventType.RECONCILED and e.payload.get(
                    "resolution") in ("EXTRACTION_DISAGREEMENT", "NONCE_MISMATCH"):
                alerts.append(Alert(
                    category="CONFLICTING_RECORDS", severity="HIGH",
                    facility_id=fid, resource_key=key, resource_type=rtype,
                    title=f"Conflicting evidence for {key} at {fid}",
                    detail=e.payload.get("resolution",""),
                    source_events=[e.offset],
                    recommended_action="Route to human adjudication."))
                break

        # -- REJECTED CERTIFICATE : direct signal, regardless of subsequent state
        for e in evs:
            if e.event_type == EventType.REJECTED and e.payload.get("issued") is False:
                alerts.append(Alert(
                    category=("EQUIPMENT_VERIFICATION_REQUIRED"
                              if rtype == "equipment" else "CONFLICTING_RECORDS"),
                    severity="HIGH",
                    facility_id=fid, resource_key=key, resource_type=rtype,
                    title=f"Certificate refused for {key} at {fid}",
                    detail=(f"{e.payload.get('certificate','certificate')} refused: "
                            f"{e.payload.get('refusal_reason','')}"),
                    source_events=[e.offset],
                    recommended_action="Escalate for physical re-verification."))
                break

        # -- UNVERIFIED_CRITICAL_INVENTORY : flagged and never attested -------
        has_attest = any(e.event_type == EventType.ATTESTED for e in evs)
        has_flag = any(e.event_type == EventType.FLAGGED for e in evs)
        state_hint = _last_state(evs, (fid, key))
        if has_flag and not has_attest:
            offsets = [e.offset for e in evs if e.event_type == EventType.FLAGGED]
            alerts.append(Alert(
                category="UNVERIFIED_CRITICAL_INVENTORY", severity="HIGH",
                facility_id=fid, resource_key=key, resource_type=rtype,
                title=f"Unverified inventory at {fid}/{key}",
                detail=(f"Ledger flagged by the detector; no attestation on record. "
                        f"Downstream systems must not treat this as usable stock."),
                source_events=offsets,
                recommended_action=("Dispatch a verification task to the facility "
                                    "in-charge (not the store custodian).")))

        # -- EQUIPMENT_VERIFICATION_REQUIRED : same, but for an asset ---------
        if rtype == "equipment" and state_hint in ("flagged", None):
            latest_flag = next((e.offset for e in reversed(evs)
                                if e.event_type == EventType.FLAGGED), None)
            if latest_flag is not None:
                alerts.append(Alert(
                    category="EQUIPMENT_VERIFICATION_REQUIRED", severity="WATCH",
                    facility_id=fid, resource_key=key, resource_type=rtype,
                    title=f"Equipment verification required at {fid}",
                    detail=(f"Asset {key} carries a claim from a source without "
                            f"independent verification. A GFR-22 line cannot be "
                            f"emitted from this."),
                    source_events=[latest_flag],
                    recommended_action=("Field verifier: photograph the asset with "
                                        "the server-issued nonce in frame.")))

    # -- CRITICAL_STOCKOUT_RISK and EXPIRY_RISK come from the registry if we
    # have one attached (avoids re-doing the forecasting in this module). ----
    if registry is not None:
        for (fid, key), s in getattr(registry, "_series", {}).items():
            if s.get("days_to_stockout") is not None and s["days_to_stockout"] < 7:
                alerts.append(Alert(
                    category="CRITICAL_STOCKOUT_RISK", severity="CRITICAL",
                    facility_id=fid, resource_key=key, resource_type="medicine",
                    title=f"Critical stockout risk at {fid}/{key}",
                    detail=(f"Projected days-to-stockout under the selected demand "
                            f"scenario: {s['days_to_stockout']:.1f}."),
                    source_events=[], recommended_action=(
                        "Verify source stock in the district; run the optimizer "
                        "against verified state only.")))
            if s.get("expired_units", 0) > 0:
                alerts.append(Alert(
                    category="EXPIRY_RISK", severity="WATCH",
                    facility_id=fid, resource_key=key, resource_type="medicine",
                    title=f"Expiry exposure at {fid}/{key}",
                    detail=(f"{s['expired_units']:.0f} unit(s) past expiry and "
                            f"still counted in the ledger."),
                    source_events=[], recommended_action=(
                        "Segregate expired stock and post an adjustment.")))

    # dedupe by (category, facility, resource) -- newer sighting wins
    dedup: dict[tuple, Alert] = {}
    for a in alerts:
        dedup[(a.category, a.facility_id, a.resource_key)] = a
    ordered = sorted(dedup.values(),
                     key=lambda a: (["CRITICAL","HIGH","WATCH"].index(a.severity), a.category))
    return [a.to_dict() for a in ordered]
