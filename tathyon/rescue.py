"""
TATHYON rescue-window engine.

The one operational question:
    "A facility will run out of a critical resource. Can the network physically
     get stock there before it does?"

    rescue_margin = time_to_failure - arrival_time      (hours)

Everything here is a deterministic function of its inputs. There is no score, no
probability and no hardcoded winner: change stock, consumption, donor quantity,
ETA, access signal or availability and the verdicts change.

Provenance rules:
  - ETA is either CALLER_SUPPLIED or a SYNTHETIC_HAVERSINE_ESTIMATE (40 km/h). No
    Routes API is called.
  - `access_risk` is a caller-supplied signal. Tathyon never derives it from
    satellite data. It only re-weights arrival time (policy multiplier below).
  - Human approval is always required; this module only recommends.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .routing import RouteIntelligenceAdapter
from .schema import now

ACCESS_DISRUPTION = "POTENTIAL ACCESS DISRUPTION"
VALID_ACCESS_SIGNALS = ("NOT_EVALUATED", "Normal", ACCESS_DISRUPTION)

# POLICY CONFIGURATION, not a measurement: a route carrying an access-disruption
# signal is planned as if it takes this much longer.
DEFAULT_ACCESS_ETA_MULTIPLIER = 1.5
DEFAULT_RESUPPLY_LEAD_TIME_DAYS = 7.0

VERDICT_FEASIBLE = "FEASIBLE"
VERDICT_TOO_LATE = "ARRIVES_AFTER_FAILURE"
VERDICT_VERIFY = "REQUIRES_VERIFICATION"
VERDICT_UNAVAILABLE = "DONOR_UNAVAILABLE"
VERDICT_NO_STOCK = "NO_TRANSFERABLE_STOCK"

STATUS_PLAN = "RECOMMENDED_PLAN"
STATUS_NO_SAFE = "NO_SAFE_RESCUE_WINDOW"
STATUS_NOT_NEEDED = "NO_RESCUE_NEEDED"
STATUS_VERIFY = "VERIFY_BEFORE_TRANSFER"


@dataclass(frozen=True)
class Recipient:
    facility_id: str
    usable_stock: float
    consumption_per_day: float
    incoming: tuple[tuple[float, float], ...] = ()   # (quantity, arrival_hours)
    resupply_lead_time_days: float = DEFAULT_RESUPPLY_LEAD_TIME_DAYS
    consumption_uncertainty: float = 0.0             # +fraction, applied pessimistically
    usable_stock_optimistic: Optional[float] = None  # upper stock estimate (data-age range); None = same as usable_stock
    bridge_hours: Optional[float] = None             # hours until the next supply that will really arrive


@dataclass(frozen=True)
class Donor:
    facility_id: str
    transferable: float
    eta_hours: Optional[float] = None                # caller-supplied route ETA
    lat: Optional[float] = None
    lon: Optional[float] = None
    handling_hours: float = 0.0
    access_risk: str = "NOT_EVALUATED"
    available: bool = True


def _validate(recipient: Recipient, donors: list[Donor]) -> None:
    if recipient.usable_stock < 0 or recipient.consumption_per_day < 0:
        raise ValueError("usable_stock and consumption_per_day must be >= 0")
    if not 0 <= recipient.consumption_uncertainty <= 5:
        raise ValueError("consumption_uncertainty must be within [0, 5]")
    for q, t in recipient.incoming:
        if q < 0 or t < 0:
            raise ValueError("incoming quantities and ETAs must be >= 0")
    for d in donors:
        if d.transferable < 0 or d.handling_hours < 0:
            raise ValueError(f"donor {d.facility_id}: negative quantity or handling time")
        if d.access_risk not in VALID_ACCESS_SIGNALS:
            raise ValueError(f"donor {d.facility_id}: access_risk must be one of {VALID_ACCESS_SIGNALS}")
        if d.eta_hours is not None and d.eta_hours < 0:
            raise ValueError(f"donor {d.facility_id}: negative eta_hours")


def time_to_failure(
    usable_stock: float,
    consumption_per_day: float,
    arrivals: tuple[tuple[float, float], ...] = (),
) -> Optional[float]:
    """Hours until stock reaches zero, counting scheduled arrivals. None = never fails."""
    rate = consumption_per_day / 24.0
    if rate <= 0:
        return None
    stock, t = usable_stock, 0.0
    for qty, eta in sorted(arrivals, key=lambda a: a[1]):
        depletes_at = t + stock / rate
        if eta >= depletes_at:
            return depletes_at                       # dead before this arrival lands
        stock = stock - rate * (eta - t) + qty
        t = eta
    return t + stock / rate


def _resolve_eta(donor: Donor, recipient_lat: Optional[float], recipient_lon: Optional[float]) -> tuple[Optional[float], str]:
    if donor.eta_hours is not None:
        return donor.eta_hours, "CALLER_SUPPLIED"
    if None in (donor.lat, donor.lon, recipient_lat, recipient_lon):
        return None, "UNAVAILABLE_NO_ETA_OR_COORDINATES"
    info = RouteIntelligenceAdapter().get_route_intelligence(
        donor.facility_id, "recipient", donor.lat, donor.lon, recipient_lat, recipient_lon
    )
    return info.eta_hours, info.data_source


@dataclass
class DonorVerdict:
    facility_id: str
    verdict: str
    reason: str
    transferable: float
    eta_hours: Optional[float]
    eta_source: str
    effective_arrival_hours: Optional[float]
    access_risk: str
    rescue_margin_hours: Optional[float]


@dataclass
class RescuePlan:
    donor: str
    quantity: float
    effective_arrival_hours: float
    rescue_margin_hours: float
    failure_hours_after_rescue: Optional[float]
    runway_added_hours: Optional[float]
    covers_need: bool
    reasoning: str


@dataclass
class RescueAssessment:
    status: str
    facility_id: str
    time_to_failure_hours: Optional[float]
    time_to_failure_pessimistic_hours: Optional[float]
    need_units: float
    donors: list[DonorVerdict]
    recommended: Optional[RescuePlan]
    alternative: Optional[RescuePlan]
    supplemental_donors: list[str] = field(default_factory=list)
    reasoning: str = ""
    provenance: str = "CALCULATED_FROM_CALLER_INPUTS"
    human_approval_required: bool = True
    generated_at: str = field(default_factory=now)

    def to_dict(self) -> dict:
        return asdict(self)


def _plan_for(
    donor: DonorVerdict, recipient: Recipient, need: float, ttf_pess: float
) -> RescuePlan:
    qty = round(min(donor.transferable, need), 3)
    arrivals = tuple(recipient.incoming) + ((qty, donor.effective_arrival_hours),)
    after = time_to_failure(recipient.usable_stock, recipient.consumption_per_day, arrivals)
    base = time_to_failure(recipient.usable_stock, recipient.consumption_per_day, tuple(recipient.incoming))
    added = None if after is None or base is None else round(after - base, 2)
    return RescuePlan(
        donor=donor.facility_id,
        quantity=qty,
        effective_arrival_hours=donor.effective_arrival_hours,
        rescue_margin_hours=round(ttf_pess - donor.effective_arrival_hours, 2),
        failure_hours_after_rescue=None if after is None else round(after, 2),
        runway_added_hours=added,
        covers_need=qty + 1e-9 >= need,
        reasoning=(
            f"{donor.facility_id} delivers {qty:g} units by {donor.effective_arrival_hours:g}h, "
            f"{round(ttf_pess - donor.effective_arrival_hours, 1):g}h before the pessimistic failure time."
        ),
    )


def assess_rescue(
    recipient: Recipient,
    donors: list[Donor],
    recipient_lat: Optional[float] = None,
    recipient_lon: Optional[float] = None,
    access_eta_multiplier: float = DEFAULT_ACCESS_ETA_MULTIPLIER,
) -> RescueAssessment:
    _validate(recipient, donors)
    if access_eta_multiplier < 1.0:
        raise ValueError("access_eta_multiplier must be >= 1.0")

    incoming = tuple(recipient.incoming)
    ttf = time_to_failure(recipient.usable_stock, recipient.consumption_per_day, incoming)
    ttf_pess = time_to_failure(
        recipient.usable_stock,
        recipient.consumption_per_day * (1.0 + recipient.consumption_uncertainty),
        incoming,
    )
    horizon_h = recipient.bridge_hours if recipient.bridge_hours is not None \
        else recipient.resupply_lead_time_days * 24.0
    # Supply only counts if it lands before the facility fails; anything later is the
    # bridge target, not a rescue.
    counted_incoming = sum(q for q, t in incoming if ttf is not None and t < ttf and t < horizon_h)
    need = max(recipient.consumption_per_day / 24.0 * horizon_h - recipient.usable_stock - counted_incoming, 0.0)

    # A "verify" window exists only when the stock itself is uncertain (data age).
    ttf_opt = ttf_pess
    if recipient.usable_stock_optimistic is not None:
        ttf_opt = time_to_failure(
            recipient.usable_stock_optimistic,
            recipient.consumption_per_day * (1.0 + recipient.consumption_uncertainty),
            incoming,
        )

    if ttf is None or ttf_pess is None or need <= 0 or ttf_pess >= horizon_h:
        return RescueAssessment(
            status=STATUS_NOT_NEEDED, facility_id=recipient.facility_id,
            time_to_failure_hours=None if ttf is None else round(ttf, 2),
            time_to_failure_pessimistic_hours=None if ttf_pess is None else round(ttf_pess, 2),
            need_units=round(need, 3), donors=[], recommended=None, alternative=None,
            reasoning="Stock plus supply that arrives before failure covers the bridge horizon. No rescue needed.",
        )

    verdicts: list[DonorVerdict] = []
    for d in donors:
        eta, src = _resolve_eta(d, recipient_lat, recipient_lon)
        eff = None
        if eta is not None:
            eff = eta * (access_eta_multiplier if d.access_risk == ACCESS_DISRUPTION else 1.0) + d.handling_hours
            eff = round(eff, 2)
        margin = None if eff is None else round(ttf_pess - eff, 2)

        if not d.available:
            v, why = VERDICT_UNAVAILABLE, "Donor marked unavailable."
        elif d.transferable <= 0:
            v, why = VERDICT_NO_STOCK, "No verified transferable stock above the donor safety floor."
        elif eff is None:
            v, why = VERDICT_UNAVAILABLE, "No ETA or coordinates supplied; cannot judge arrival."
        elif eff > (ttf_opt if ttf_opt is not None else ttf_pess):
            v, why = VERDICT_TOO_LATE, (
                f"Arrives at {eff:g}h, after failure at {ttf_pess:.1f}h.")
        elif eff > ttf_pess:
            v, why = VERDICT_VERIFY, (
                f"Arrives at {eff:g}h, inside the stock-uncertainty window "
                f"({ttf_pess:.1f}h-{ttf_opt:.1f}h). Verify recipient stock before transfer.")
        else:
            v, why = VERDICT_FEASIBLE, f"Arrives at {eff:g}h with {margin:g}h of margin."
            if d.access_risk == ACCESS_DISRUPTION:
                why += f" ETA inflated x{access_eta_multiplier:g} for the access-risk signal."
        verdicts.append(DonorVerdict(d.facility_id, v, why, d.transferable, eta, src, eff, d.access_risk, margin))

    feasible = sorted(
        (v for v in verdicts if v.verdict == VERDICT_FEASIBLE),
        key=lambda v: (v.effective_arrival_hours, -v.transferable, v.facility_id),
    )
    if not feasible:
        needs_verify = any(v.verdict == VERDICT_VERIFY for v in verdicts)
        return RescueAssessment(
            status=STATUS_VERIFY if needs_verify else STATUS_NO_SAFE, facility_id=recipient.facility_id,
            time_to_failure_hours=round(ttf, 2), time_to_failure_pessimistic_hours=round(ttf_pess, 2),
            need_units=round(need, 3), donors=verdicts, recommended=None, alternative=None,
            reasoning=(("Donor arrival falls inside the stock-uncertainty window. Verify recipient stock "
                        "physically before moving anything.") if needs_verify else
                       (f"No donor can arrive before failure at {ttf_pess:.1f}h. "
                        "Escalate to the State Drug Warehouse; outside supply is a state decision.")),
        )

    rec = _plan_for(feasible[0], recipient, need, ttf_pess)
    alt = _plan_for(feasible[1], recipient, need, ttf_pess) if len(feasible) > 1 else None
    supplemental = [] if rec.covers_need else [v.facility_id for v in feasible[1:]]
    return RescueAssessment(
        status=STATUS_PLAN, facility_id=recipient.facility_id,
        time_to_failure_hours=round(ttf, 2), time_to_failure_pessimistic_hours=round(ttf_pess, 2),
        need_units=round(need, 3), donors=verdicts, recommended=rec, alternative=alt,
        supplemental_donors=supplemental,
        reasoning=(rec.reasoning + ("" if rec.covers_need else
                   f" This covers {rec.quantity:g} of {need:g} units needed; remaining need requires "
                   "a supplemental donor or state escalation.")),
    )


@dataclass
class RescueOutcome:
    facility_id: str
    planned_quantity: float
    dispatched_quantity: float
    received_quantity: float
    damaged_quantity: float
    usable_received: float
    arrival_hours: float
    original_failure_hours: Optional[float]
    remaining_stock: float
    failure_avoided: bool
    failure_occurred: bool
    discrepancies: list[str]
    recorded_at: str = field(default_factory=now)

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate_outcome(
    recipient: Recipient,
    planned_quantity: float,
    dispatched_quantity: float,
    received_quantity: float,
    damaged_quantity: float,
    arrival_hours: float,
) -> RescueOutcome:
    """Reconcile physical delivery against the plan and decide if failure was avoided."""
    for name, v in (("planned", planned_quantity), ("dispatched", dispatched_quantity),
                    ("received", received_quantity), ("damaged", damaged_quantity), ("arrival_hours", arrival_hours)):
        if v < 0:
            raise ValueError(f"{name} must be >= 0")
    if damaged_quantity > received_quantity:
        raise ValueError("damaged cannot exceed received")

    usable_received = received_quantity - damaged_quantity
    base = time_to_failure(recipient.usable_stock, recipient.consumption_per_day, tuple(recipient.incoming))
    arrivals = tuple(recipient.incoming) + ((usable_received, arrival_hours),)
    after = time_to_failure(recipient.usable_stock, recipient.consumption_per_day, arrivals)

    # Stock at arrival time (never below zero) - if it hit zero first, the facility failed.
    rate = recipient.consumption_per_day / 24.0
    failed_before_arrival = base is not None and arrival_hours > base
    failure_occurred = failed_before_arrival or (after is not None and after <= arrival_hours)
    remaining = max(recipient.usable_stock + usable_received
                    - rate * arrival_hours, 0.0) if not failure_occurred else 0.0

    notes: list[str] = []
    if dispatched_quantity < planned_quantity:
        notes.append(f"DISPATCH_SHORT: {planned_quantity - dispatched_quantity:g} units below plan")
    if received_quantity < dispatched_quantity:
        notes.append(f"TRANSIT_LOSS: {dispatched_quantity - received_quantity:g} units unaccounted for")
    if damaged_quantity > 0:
        notes.append(f"DAMAGE: {damaged_quantity:g} units unusable")
    if failure_occurred:
        notes.append("ARRIVAL_TOO_LATE: recipient stocked out before delivery")

    return RescueOutcome(
        facility_id=recipient.facility_id, planned_quantity=planned_quantity,
        dispatched_quantity=dispatched_quantity, received_quantity=received_quantity,
        damaged_quantity=damaged_quantity, usable_received=usable_received,
        arrival_hours=arrival_hours,
        original_failure_hours=None if base is None else round(base, 2),
        remaining_stock=round(remaining, 2),
        failure_avoided=(not failure_occurred) and usable_received > 0,
        failure_occurred=failure_occurred, discrepancies=notes,
    )


def tokapal_scenario() -> dict:
    """The reference scenario. Every verdict below is computed, none is scripted."""
    recipient = Recipient("TOKAPAL_PHC", usable_stock=42.0, consumption_per_day=28.0)
    base = [
        Donor("DONOR_A", 100.0, eta_hours=72.0),
        Donor("DONOR_B", 35.0, eta_hours=8.0),
        Donor("DONOR_C", 45.0, eta_hours=11.0),
    ]
    b_down = [Donor("DONOR_B", 35.0, eta_hours=8.0, available=False) if d.facility_id == "DONOR_B" else d for d in base]
    b_risk = [Donor("DONOR_B", 35.0, eta_hours=8.0, access_risk=ACCESS_DISRUPTION) if d.facility_id == "DONOR_B" else d for d in base]
    return {
        "provenance": "SYNTHETIC_SCENARIO_COMPUTED_BY_ENGINE",
        "1_baseline": assess_rescue(recipient, base).to_dict(),
        "2_donor_b_unavailable": assess_rescue(recipient, b_down).to_dict(),
        "3_donor_b_access_risk_signal": assess_rescue(recipient, b_risk).to_dict(),
    }
