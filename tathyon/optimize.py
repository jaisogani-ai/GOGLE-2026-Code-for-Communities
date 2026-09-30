"""
Constrained redistribution with a verification gate.

ONE CP-SAT network solve per planning cycle:
  Variables: units of SKU s from donor d to recipient r.

Hard constraints:
  1. Donor supply <= verified usable stock above its safety floor.
     (Unverified stock is INVISIBLE to the solver — THE GATE).
  2. Recipient receipt <= forecasted shortfall + buffer.
  3. Per-cycle transport capacity (truck constraint).
  4. Cold-chain feasibility per SKU-route where applicable.
  5. Deadline constraints (rescue-margin: effective arrival <= time-to-stockout).

Objective, lexicographic:
  (a) minimize essentiality-weighted stockout-days;
  (b) minimize time-discounted expiry loss (short-dated verified stock flows to burn-rate);
  (c) minimize transport cost.

PARTIAL FULFILLMENT:
  A plan explicitly carries fulfilled_qty and shortfall_qty.
  Any shortfall sets replan_required = True.
  NO_FEASIBLE_PLAN is returned ONLY when zero units can move under constraints.
  Never a partial risky plan presented as complete, never silently dropped unmet need.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from ortools.sat.python import cp_model

from .schema import STATE_ESCALATION, VerificationState, now


@dataclass
class Facility:
    facility_id: str
    tier: str
    x: float
    y: float
    criticality: float = 1.0
    has_cold_chain: bool = True
    lat: Optional[float] = None
    lon: Optional[float] = None


@dataclass
class SourceStock:
    facility_id: str
    resource_key: str
    reported_qty: float
    verified_state: VerificationState
    q_alpha: Optional[float]          # quantity confident of having at least
    unusable_qty: float = 0.0
    safety_stock: float = 0.0
    reasons: list = field(default_factory=list)
    expiry_date: Optional[str] = None
    days_to_expiry: Optional[float] = None
    handling_hours: float = 0.0
    access_risk_multiplier: float = 1.0
    available: bool = True

    @property
    def transferable(self) -> float:
        """Only verified stock is transferable, and only the amount above the
        facility's own safety floor. Unverified stock is strictly 0.0."""
        if not self.available:
            return 0.0
        if self.verified_state != VerificationState.VERIFIED:
            return 0.0
        if self.q_alpha is None:
            return 0.0
        return max(self.q_alpha - self.safety_stock, 0.0)


@dataclass
class Need:
    facility_id: str
    resource_key: str
    shortfall: float
    days_to_stockout: float
    criticality: float = 1.0
    essentiality: float = 1.0                   # V=3, E=2, N=1 or criticality
    deadline_hours: Optional[float] = None      # Folded rescue margin deadline
    buffer: float = 0.0                         # Safety buffer
    cold_chain_required: bool = False
    lead_time_days: float = 14.0


@dataclass
class Transfer:
    from_facility: str
    to_facility: str
    resource_key: str
    qty: float
    travel_hours: float
    distance_km: float = 0.0
    cold_chain: bool = False
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    """A typed decision with explicit fulfillment accounting, never an unhandled exception."""
    allowed: bool
    status: str                                 # ALLOWED | BLOCKED_VERIFICATION_REQUIRED | NO_FEASIBLE_PLAN | NO_SAFE_SOURCE
    transfers: list[Transfer] = field(default_factory=list)
    fulfilled_qty: float = 0.0
    shortfall_qty: float = 0.0
    replan_required: bool = False
    blocked_sources: list = field(default_factory=list)
    rejected_donors: list[dict] = field(default_factory=list)
    unmet: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    remedy: Optional[str] = None
    objective: Optional[float] = None
    solver_status: Optional[str] = None
    override: dict = field(default_factory=lambda: {
        "eligible": True,
        "required_role": "medical_officer",
        "max_duration_s": 3600,
        "note": "Break-glass creates a verification obligation with a deadline. "
                "A gate that cannot be overridden gets uninstalled in week two; "
                "a gate that records every override with a name attached changes behaviour."
    })

    def to_dict(self) -> dict:
        d = asdict(self)
        d["transfers"] = [t.to_dict() if hasattr(t, "to_dict") else asdict(t) for t in self.transfers]
        return d


def travel_hours(a: Facility, b: Facility, kmph: float = 38.0) -> float:
    km = math.hypot(a.x - b.x, a.y - b.y) * 1.35
    return km / kmph


def travel_distance_km(a: Facility, b: Facility) -> float:
    return math.hypot(a.x - b.x, a.y - b.y) * 1.35


# ---------------------------------------------------------------------------
# ONE CP-SAT Network Solve Per Planning Cycle
# ---------------------------------------------------------------------------

def optimise(
    facilities: dict[str, Facility],
    sources: list[SourceStock],
    needs: list[Need],
    max_travel_hours: float = 6.0,
    max_cycle_truck_capacity: float = 5000.0,
    per_route_truck_capacity: Optional[float] = None,
    max_donor_fraction: float = 0.40,
    max_cold_chain_hours: float = 8.0,
    time_limit_s: float = 5.0,
) -> Decision:
    """ONE CP-SAT network solve per planning cycle over all donors, recipients, and SKUs.
    
    Hard constraints:
      - Donor supply <= verified usable stock above safety floor (unverified is invisible)
      - Recipient receipt <= forecasted shortfall + buffer
      - Transport capacity (the truck constraint)
      - Cold-chain feasibility per SKU-route
      - Deadline constraints (effective travel <= deadline_hours)
    
    Objective (lexicographic):
      (a) Minimize essentiality-weighted stockout-days
      (b) Minimize time-discounted expiry loss
      (c) Minimize transport cost
    """
    total_shortfall = sum(n.shortfall for n in needs)

    # Validate facility registry
    unknown = sorted(
        {s.facility_id for s in sources if s.facility_id not in facilities}
        | {n.facility_id for n in needs if n.facility_id not in facilities}
    )
    if unknown:
        raise ValueError(
            f"UNKNOWN_FACILITY: {unknown} not present in facility registry. "
            "A transfer cannot be planned against a facility with no location."
        )

    # 1. THE VERIFICATION GATE (Runs before solver)
    eligible_sources: list[SourceStock] = []
    blocked_sources: list[dict] = []
    rejected_donors: list[dict] = []

    for s in sources:
        if s.transferable > 0:
            eligible_sources.append(s)
        else:
            reason = "UNVERIFIED" if s.verified_state != VerificationState.VERIFIED else (
                "BELOW_SAFETY_FLOOR" if (s.q_alpha or 0.0) <= s.safety_stock else (
                    "DONOR_UNAVAILABLE" if not s.available else "NO_TRANSFERABLE_STOCK"
                )
            )
            blocked_sources.append({
                "facility_id": s.facility_id,
                "resource_key": s.resource_key,
                "reported_qty": s.reported_qty,
                "verified_state": s.verified_state.value if hasattr(s.verified_state, "value") else str(s.verified_state),
                "q_alpha": s.q_alpha,
                "unusable_qty": s.unusable_qty,
                "reasons": s.reasons + [reason],
            })
            rejected_donors.append({
                "facility_id": s.facility_id,
                "resource_key": s.resource_key,
                "reason": reason,
                "detail": f"State={s.verified_state}, Q_alpha={s.q_alpha}, safety_floor={s.safety_stock}",
            })

    if not eligible_sources and needs:
        return Decision(
            allowed=False,
            status="BLOCKED_VERIFICATION_REQUIRED",
            fulfilled_qty=0.0,
            shortfall_qty=total_shortfall,
            replan_required=True,
            blocked_sources=blocked_sources,
            rejected_donors=rejected_donors,
            unmet=[n.facility_id for n in needs],
            reasons=sorted({r for s in sources for r in s.reasons}) or ["NO_ATTESTATION"],
            remedy="Dispatch verification tasks to the listed source facilities. "
                   "No transfer may be authorised against unverified stock.",
        )

    # 2. Build Decision Variables and Route Feasibility
    model = cp_model.CpModel()
    SCALE = 10  # 0.1 unit granularity

    # pairs: (donor_idx, need_idx) -> metadata & variables
    pairs: dict[tuple[int, int], dict[str, Any]] = {}

    for si, s in enumerate(eligible_sources):
        fac_s = facilities[s.facility_id]
        for ni, n in enumerate(needs):
            if s.resource_key != n.resource_key:
                continue
            if s.facility_id == n.facility_id:
                continue

            fac_n = facilities[n.facility_id]
            dist_km = travel_distance_km(fac_s, fac_n)
            base_hours = travel_hours(fac_s, fac_n)
            eff_hours = base_hours * s.access_risk_multiplier + s.handling_hours

            # Cold chain feasibility
            if n.cold_chain_required:
                if not (fac_s.has_cold_chain and fac_n.has_cold_chain):
                    rejected_donors.append({
                        "facility_id": s.facility_id,
                        "resource_key": s.resource_key,
                        "recipient": n.facility_id,
                        "reason": "COLD_CHAIN_INCOMPATIBLE",
                        "detail": "Facility lacks cold chain storage.",
                    })
                    continue
                if eff_hours > max_cold_chain_hours:
                    rejected_donors.append({
                        "facility_id": s.facility_id,
                        "resource_key": s.resource_key,
                        "recipient": n.facility_id,
                        "reason": "COLD_CHAIN_TRANSIT_EXCEEDED",
                        "detail": f"Transit {eff_hours:.1f}h exceeds max cold chain duration {max_cold_chain_hours:.1f}h.",
                    })
                    continue

            # Deadline constraint (folded rescue margin)
            if n.deadline_hours is not None:
                if eff_hours > n.deadline_hours:
                    rejected_donors.append({
                        "facility_id": s.facility_id,
                        "resource_key": s.resource_key,
                        "recipient": n.facility_id,
                        "reason": "ARRIVES_AFTER_DEADLINE",
                        "detail": f"Effective transit {eff_hours:.1f}h exceeds deadline {n.deadline_hours:.1f}h.",
                    })
                    continue

            # Standard transit cutoff
            if eff_hours > max_travel_hours and n.deadline_hours is None:
                rejected_donors.append({
                    "facility_id": s.facility_id,
                    "resource_key": s.resource_key,
                    "recipient": n.facility_id,
                    "reason": "EXCEEDS_MAX_TRAVEL_HOURS",
                    "detail": f"Transit {eff_hours:.1f}h exceeds max allowed {max_travel_hours:.1f}h.",
                })
                continue

            max_possible = min(s.transferable, n.shortfall + n.buffer)
            if per_route_truck_capacity is not None:
                max_possible = min(max_possible, per_route_truck_capacity)
            cap = int(max_possible * SCALE)
            if cap <= 0:
                continue

            x_var = model.NewIntVar(0, cap, f"x_{si}_{ni}")
            u_var = model.NewBoolVar(f"u_{si}_{ni}")
            model.Add(x_var > 0).OnlyEnforceIf(u_var)
            model.Add(x_var == 0).OnlyEnforceIf(u_var.Not())

            pairs[(si, ni)] = {
                "var": x_var,
                "used": u_var,
                "travel_hours": eff_hours,
                "distance_km": dist_km,
                "cold_chain": n.cold_chain_required,
                "source": s,
                "need": n,
            }

    if not pairs:
        return Decision(
            allowed=False,
            status="NO_FEASIBLE_PLAN",
            fulfilled_qty=0.0,
            shortfall_qty=total_shortfall,
            replan_required=True,
            blocked_sources=blocked_sources,
            rejected_donors=rejected_donors,
            unmet=[n.facility_id for n in needs],
            reasons=["NO_FEASIBLE_PLAN: all routes violate constraints (travel/deadline/cold-chain/floor)"],
            remedy="No verified source meets operational constraints. " + STATE_ESCALATION,
        )

    # 3. Hard Constraints
    # Donor capacity constraint: sum_r x_{d, r, s} <= transferable(d, s) * max_donor_fraction
    for si, s in enumerate(eligible_sources):
        outs = [p["var"] for (a, _), p in pairs.items() if a == si]
        if outs:
            donor_limit = int(s.transferable * max_donor_fraction * SCALE)
            model.Add(sum(outs) <= donor_limit)

    # Recipient receipt constraint: sum_d x_{d, r, s} <= shortfall + buffer
    unmet_vars: dict[int, Any] = {}
    for ni, n in enumerate(needs):
        ins = [p["var"] for (_, b), p in pairs.items() if b == ni]
        need_units = int(n.shortfall * SCALE)
        buffer_units = int(n.buffer * SCALE)
        u = model.NewIntVar(0, need_units, f"unmet_{ni}")
        unmet_vars[ni] = u
        if ins:
            # sum(ins) can cover up to shortfall + buffer, but unmet is shortfall - min(receipt, shortfall)
            receipt_var = model.NewIntVar(0, need_units + buffer_units, f"rcpt_{ni}")
            model.Add(receipt_var == sum(ins))
            model.Add(sum(ins) <= need_units + buffer_units)
            # Unmet shortfall is capped at need_units
            model.Add(u >= need_units - receipt_var)
            model.Add(u >= 0)
        else:
            model.Add(u == need_units)

    # Truck constraint (per-cycle transport capacity)
    all_x = [p["var"] for p in pairs.values()]
    model.Add(sum(all_x) <= int(max_cycle_truck_capacity * SCALE))

    # 4. Lexicographic / Multi-Objective Formulation
    # Coerce ALL coefficients to Python int before multiplying CP-SAT variables
    W_STOCKOUT = 100_000
    W_EXPIRY = 50
    W_TRAVEL = 5
    W_MOVE = 50

    obj_terms = []

    # (a) Minimize essentiality-weighted stockout-days
    for ni, n in enumerate(needs):
        urgency = int(n.essentiality * 100 / max(n.days_to_stockout, 0.5))
        coeff_stockout = int((W_STOCKOUT * urgency) // 100)
        obj_terms.append(unmet_vars[ni] * coeff_stockout)

    # (b) Minimize time-discounted expiry loss (reward moving short-dated stock)
    # (c) Minimize transport cost
    for (si, ni), p in pairs.items():
        s = p["source"]
        # Short-dated stock discount: stock expiring sooner has higher bonus when transferred
        if s.days_to_expiry is not None and s.days_to_expiry > 0:
            expiry_bonus = max(1, int(60 - min(s.days_to_expiry, 60)))
        else:
            expiry_bonus = 0

        travel_cost = int(p["travel_hours"] * W_TRAVEL)
        net_var_coeff = int(travel_cost - (expiry_bonus * W_EXPIRY // 10))
        obj_terms.append(p["var"] * net_var_coeff)
        obj_terms.append(p["used"] * int(W_MOVE))

    model.Minimize(sum(obj_terms))

    # 5. Solve CP-SAT
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1  # Deterministic, avoids pthread/absl conflicts
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Decision(
            allowed=False,
            status="NO_FEASIBLE_PLAN",
            fulfilled_qty=0.0,
            shortfall_qty=total_shortfall,
            replan_required=True,
            blocked_sources=blocked_sources,
            rejected_donors=rejected_donors,
            unmet=[n.facility_id for n in needs],
            reasons=[f"SOLVER_INFEASIBLE: {status_name}"],
            remedy="Solver found no feasible redistribution under strict safety constraints.",
        )

    # 6. Extract Transfers and Accounting
    transfers: list[Transfer] = []
    total_fulfilled = 0.0

    for (si, ni), p in pairs.items():
        val = solver.Value(p["var"])
        q = val / SCALE
        if q > 0:
            s, n = p["source"], p["need"]
            total_fulfilled += q
            exp_note = f", expiry_in={s.days_to_expiry:.0f}d" if s.days_to_expiry else ""
            transfers.append(Transfer(
                from_facility=s.facility_id,
                to_facility=n.facility_id,
                resource_key=s.resource_key,
                qty=round(q, 1),
                travel_hours=round(p["travel_hours"], 2),
                distance_km=round(p["distance_km"], 1),
                cold_chain=p["cold_chain"],
                rationale=(
                    f"Verified source (Q_alpha={s.q_alpha}{exp_note}) -> "
                    f"Recipient {n.facility_id} (shortfall {n.shortfall:.1f}, "
                    f"{n.days_to_stockout:.1f}d to stockout)"
                ),
            ))

    total_fulfilled = round(total_fulfilled, 1)
    shortfall_qty = round(max(total_shortfall - total_fulfilled, 0.0), 1)
    replan_required = shortfall_qty > 0

    if total_fulfilled == 0:
        return Decision(
            allowed=False,
            status="NO_FEASIBLE_PLAN",
            fulfilled_qty=0.0,
            shortfall_qty=total_shortfall,
            replan_required=True,
            blocked_sources=blocked_sources,
            rejected_donors=rejected_donors,
            unmet=[n.facility_id for n in needs],
            reasons=["NO_FEASIBLE_PLAN: 0 units could be transferred under constraints"],
            remedy="Zero units could be safely transferred without violating floors or deadlines.",
        )

    unmet_facilities = [
        needs[ni].facility_id for ni, u in unmet_vars.items()
        if solver.Value(u) > 0
    ]

    return Decision(
        allowed=True,
        status="ALLOWED",
        transfers=transfers,
        fulfilled_qty=total_fulfilled,
        shortfall_qty=shortfall_qty,
        replan_required=replan_required,
        blocked_sources=blocked_sources,
        rejected_donors=rejected_donors,
        unmet=unmet_facilities,
        objective=round(float(solver.ObjectiveValue()), 2),
        solver_status=status_name,
        reasons=[] if not replan_required else ["PARTIAL_FULFILLMENT_REPLAN_REQUIRED"],
    )


# ---------------------------------------------------------------------------
# The Counterfactual Arm (Spine of Tathyon Evidence)
# ---------------------------------------------------------------------------

def naive_optimise(
    facilities: dict[str, Facility],
    sources: list[SourceStock],
    needs: list[Need],
    **kw,
) -> Decision:
    """What naive systems do: trust reported stock blindly.
    
    Identical constraints, identical solver. The ONLY difference is that
    unverified stock is marked as verified with reported_qty.
    """
    naive_sources = [
        SourceStock(
            facility_id=s.facility_id,
            resource_key=s.resource_key,
            reported_qty=s.reported_qty,
            verified_state=VerificationState.VERIFIED,   # naive assumes true
            q_alpha=s.reported_qty,                      # naive uses reported
            unusable_qty=0.0,
            safety_stock=s.safety_stock,
            reasons=[],
            expiry_date=s.expiry_date,
            days_to_expiry=s.days_to_expiry,
            handling_hours=s.handling_hours,
            access_risk_multiplier=s.access_risk_multiplier,
            available=s.available,
        )
        for s in sources
    ]
    d = optimise(facilities, naive_sources, needs, **kw)
    d.status = "NAIVE_" + d.status
    return d


def counterfactual(
    facilities: dict[str, Facility],
    sources: list[SourceStock],
    needs: list[Need],
    **kw,
) -> dict[str, Any]:
    """Runs Naive vs Tathyon vs Do-Nothing on identical data and calculates phantom units averted."""
    naive = naive_optimise(facilities, sources, needs, **kw)
    tath = optimise(facilities, sources, needs, **kw)

    verified_qty = {
        (s.facility_id, s.resource_key): (s.q_alpha or 0.0) if s.verified_state == VerificationState.VERIFIED else 0.0
        for s in sources
    }

    phantom = 0.0
    for t in naive.transfers:
        avail = verified_qty.get((t.from_facility, t.resource_key), 0.0)
        phantom += max(t.qty - avail, 0.0)

    total_need = sum(n.shortfall for n in needs)
    do_nothing = {
        "status": "DO_NOTHING",
        "fulfilled_qty": 0.0,
        "shortfall_qty": total_need,
        "stockouts": len(needs),
        "transfers": [],
    }

    return {
        "do_nothing": do_nothing,
        "naive": naive.to_dict(),
        "tathyon": tath.to_dict(),
        "phantom_units_naive_would_move": round(phantom, 1),
        "naive_transfer_count": len(naive.transfers),
        "tathyon_transfer_count": len(tath.transfers),
        "sources_blocked_by_gate": len(tath.blocked_sources),
        "replan_required": tath.replan_required,
    }


# ---------------------------------------------------------------------------
# Shortage Response Candidate Screening
# ---------------------------------------------------------------------------

DEFAULT_TRAVEL_HOURS = 2.0


@dataclass(frozen=True)
class ResponseCandidates:
    """Donor screen for one shortage behind the verification gate."""
    sku: str
    target_facility_id: str
    needed_qty: float
    candidates: tuple = ()
    unverified_blocked: tuple = ()
    total_verified_available: float = 0.0
    recommendation: str = ""
    can_write_state: bool = False

    def to_dict(self) -> dict:
        return {
            "sku": self.sku,
            "target_facility_id": self.target_facility_id,
            "needed_qty": self.needed_qty,
            "candidates": list(self.candidates),
            "unverified_blocked": list(self.unverified_blocked),
            "total_verified_available": self.total_verified_available,
            "recommendation": self.recommendation,
            "can_write_state": self.can_write_state,
        }


def plan_response(
    sku: str,
    target_facility_id: str,
    needed_qty: float,
    candidate_facilities: list[dict],
) -> ResponseCandidates:
    from .verify import plan_verifications
    candidates = []
    for fac in candidate_facilities:
        fid = fac.get("facility_id", "")
        if fid == target_facility_id:
            continue
        if str(fac.get("verification_state", "UNVERIFIED")).upper() != VerificationState.VERIFIED.value:
            continue
        q_alpha = fac.get("q_alpha")
        transferable = max((float(q_alpha) if q_alpha is not None else 0.0)
                           - float(fac.get("safety_stock", 0.0)), 0.0)
        if transferable <= 0:
            continue
        candidates.append({
            "facility_id": fid,
            "facility_tier": fac.get("tier", "CHC"),
            "verified_state": VerificationState.VERIFIED.value,
            "reported_stock": float(fac.get("reported_stock", 0.0)),
            "q_alpha": float(q_alpha),
            "transferable_qty": transferable,
            "travel_hours": float(fac.get("travel_hours", DEFAULT_TRAVEL_HOURS)),
            "status": "ELIGIBLE_FOR_REBALANCE",
        })
    candidates.sort(key=lambda c: (c["travel_hours"], -c["transferable_qty"]))
    blocked = plan_verifications(target_facility_id, candidate_facilities)
    total_verified = sum(c["transferable_qty"] for c in candidates)
    return ResponseCandidates(
        sku=sku,
        target_facility_id=target_facility_id,
        needed_qty=needed_qty,
        candidates=tuple(candidates),
        unverified_blocked=tuple(blocked),
        total_verified_available=total_verified,
        recommendation=_response_recommendation(total_verified, needed_qty, len(candidates), blocked),
    )


def _response_recommendation(total_verified: float, needed_qty: float,
                             n_candidates: int, blocked: list[dict]) -> str:
    if total_verified >= needed_qty:
        return (f"Sufficient verified stock found: {total_verified:.0f} units available across "
                f"{n_candidates} facilities to satisfy deficit of {needed_qty:.0f} units. "
                f"Proceed to OR-Tools constrained optimizer for human DDO sign-off.")
    blocked_units = sum(b["reported_stock"] for b in blocked)
    if total_verified > 0:
        return (f"Partial verified stock found: {total_verified:.0f} of {needed_qty:.0f} units available. "
                f"{len(blocked)} facilities hold {blocked_units:.0f} "
                f"unverified units currently blocked by Tathyon safety gate.")
    return (f"Zero verified stock available for transfer in region. "
            f"{len(blocked)} facilities hold reported stock but are UNVERIFIED. "
            f"Emergency verification dispatches recommended.")


# ---------------------------------------------------------------------------
# Exact 0/1 Knapsack (Used by verify.py target_verifications)
# ---------------------------------------------------------------------------

def knapsack(values, costs, capacity: int) -> list[int]:
    """Exact 0/1 knapsack by dynamic programming."""
    values = np.asarray(values, dtype=float)
    costs = np.asarray(costs, dtype=int)
    if capacity < 0 or len(values) == 0:
        return []
    best = np.zeros(capacity + 1)
    keep = np.zeros((len(values), capacity + 1), dtype=bool)
    for i, (v, c) in enumerate(zip(values, costs)):
        if v <= 0 or c > capacity:
            continue
        with_item = best[: capacity + 1 - c] + v
        better = with_item > best[c:] + 1e-12
        keep[i, c:] = better
        best[c:] = np.where(better, with_item, best[c:])
    chosen, cap = [], capacity
    for i in range(len(values) - 1, -1, -1):
        if keep[i, cap]:
            chosen.append(i)
            cap -= int(costs[i])
    return sorted(chosen)
