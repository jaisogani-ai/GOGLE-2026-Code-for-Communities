"""
Verification engine + freshness model.

Two hard design positions, both reversals of an earlier draft:

1. The record's output is a STATE MACHINE, not a score. A threshold you cannot
   defend in a CAG hearing is a threshold you will be asked to justify in one.
   A priority score still exists -- but only to order a queue under limited
   human capacity, and it is never persisted, never exported, never placed in
   an attestation.

2. Freshness is a POSTERIOR, not a floor and not a TTL. The earlier design
   subtracted "p95 of unobserved consumption" from an anchor. That is wrong:
   quantiles do not add across time windows. Poisson rates DO add, so we model
   unobserved consumption as Gamma-Poisson and get a Negative Binomial in
   closed form. The CONSUMER supplies alpha, because the consumer owns the risk:
       alpha = c_over / (c_over + c_under)
   A policy-based certificate over-claiming stock is catastrophic (alpha ~ 0.98).
   An optimizer that can re-plan is not (alpha ~ 0.6).
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from typing import Any, Optional

import numpy as np
import pandas as pd
from scipy import stats

from .schema import (
    Provenance, ResourceType, SLAContract, VerificationState, VerificationVisit,
    VerifiedState, VisitStatus, REASONS,
)
from .store import EventStore

log = logging.getLogger(__name__)

# Policy defaults. Tenant may tighten; a consumer may tighten further but never
# loosen below the tenant floor.
POLICY = {
    "version": "v1",
    "medicine": {"max_attestation_age_days": 30, "default_alpha": 0.60},
    "equipment": {"max_attestation_age_days": 180, "default_alpha": 0.90},
    "min_attestation_seconds": 20.0,   # below this is rubber-stamping
}


# ---------------------------------------------------------------------------
# Freshness: Gamma-Poisson posterior over unobserved consumption
# ---------------------------------------------------------------------------

@dataclass
class ConsumptionPrior:
    """Hierarchical: pooled over SKU x facility-tier x district, scaled by OPD.
    This is what makes cold start work *by construction* -- a series with no
    history gets a correctly wide posterior, Q_alpha collapses toward zero, and
    the system says 'verify before relying on this'. That is the right answer,
    not a bug."""
    a0: float = 2.0
    b0: float = 1.0

    @classmethod
    def fit(cls, daily_rates: np.ndarray) -> "ConsumptionPrior":
        x = np.asarray(daily_rates, dtype=float)
        x = x[np.isfinite(x)]
        if len(x) < 3 or x.mean() <= 0:
            return cls()
        m, v = float(x.mean()), float(x.var()) + 1e-9
        # method of moments on the Gamma
        b = max(m / v, 1e-3)
        a = max(m * b, 1e-3)
        return cls(a0=a, b0=b)


def posterior_unobserved(prior: ConsumptionPrior,
                         observed_days: float, observed_count: float,
                         gap_days: float) -> stats.rv_discrete:
    """Posterior predictive for consumption over an unobserved gap.

    lambda ~ Gamma(a0, b0);  x | lambda ~ Poisson(lambda * t)
    => posterior lambda ~ Gamma(a0 + observed_count, b0 + observed_days)
    => predictive over gap  ~ NegBin(n = a_post, p = b_post / (b_post + gap))

    Accepts scalars or equal-length arrays (the trust scorer evaluates many
    series at once); the arithmetic is identical either way.
    """
    a_post = prior.a0 + np.maximum(observed_count, 0.0)
    b_post = prior.b0 + np.maximum(observed_days, 0.0)
    gap = np.maximum(gap_days, 1e-6)
    p = b_post / (b_post + gap)
    return stats.nbinom(n=a_post, p=p)


def quantity_posterior(anchor_qty: float, observed_deltas: float,
                       prior: ConsumptionPrior, observed_days: float,
                       observed_count: float, gap_days: float,
                       alpha: float) -> dict:
    """Q_t = anchor - observed deltas - U_gap, with U_gap ~ NegBin.

    Returns the point estimate AND Q_alpha, the quantity we are (1-alpha)
    confident of having at least. A gate answers against Q_alpha, never the point.
    """
    nb = posterior_unobserved(prior, observed_days, observed_count, gap_days)
    u_mean = float(nb.mean())
    u_hi = float(nb.ppf(alpha))
    base = anchor_qty - observed_deltas
    return {
        "point": round(max(base - u_mean, 0.0), 1),
        "q_alpha": round(max(base - u_hi, 0.0), 1),
        "alpha": alpha,
        "unobserved_gap_days": round(gap_days, 2),
        "unobserved_expected": round(u_mean, 1),
        "model": "gamma_poisson_negbin",
    }


# ---------------------------------------------------------------------------
# Equipment: discrete-time hazard, not a TTL
# ---------------------------------------------------------------------------

def survival(months_since_verified: float, age_months: float,
             amc_active: bool, last_service_months: float) -> float:
    """P(still functional | verified functional at t0).

    A TTL throws away the failure data we actually have. A discrete-time hazard
    with a logistic link handles interval censoring and trains on maintenance
    tickets. Coefficients here are seeded from BEMMP's published 13-34%
    dysfunction band; they are re-fit from data in eval.py.
    """
    alpha0 = -3.4
    z = (alpha0
         + 0.011 * age_months
         + (0.55 if not amc_active else 0.0)
         + 0.020 * last_service_months)
    h = 1.0 / (1.0 + math.exp(-z))          # monthly hazard
    return float(max((1.0 - h) ** max(months_since_verified, 0.0), 0.0))


# ---------------------------------------------------------------------------
# The verification engine
# ---------------------------------------------------------------------------

class VerificationEngine:
    def __init__(self, store: EventStore, policy: dict = POLICY):
        self.store = store
        self.policy = policy

    def state_for(
        self,
        facility_id: str,
        resource_type: ResourceType,
        resource_key: str,
        as_of: str,
        alpha: Optional[float] = None,
        prior: Optional[ConsumptionPrior] = None,
        observed_days: float = 28.0,
        observed_count: float = 0.0,
        observed_deltas: float = 0.0,
        detector_reasons: Optional[list] = None,
    ) -> VerifiedState:
        """Project the event log into a VerifiedState. Pure function of
        (log, policy, alpha). Never reads a stored status column."""
        import pandas as pd

        claim = self.store.latest_claim(facility_id, resource_key)
        att = self.store.latest_attestation(facility_id, resource_key)
        reasons = list(detector_reasons or [])
        rt = resource_type
        kind = "medicine" if rt == ResourceType.MEDICINE else "equipment"
        alpha = alpha if alpha is not None else self.policy[kind]["default_alpha"]
        max_age = self.policy[kind]["max_attestation_age_days"]

        vs = VerifiedState(
            facility_id=facility_id, resource_type=rt, resource_key=resource_key,
            state=VerificationState.UNVERIFIED, reasons=reasons, as_of=as_of,
            policy_version=self.policy["version"], provenance=Provenance.SYNTHETIC,
        )
        if claim is not None:
            if rt == ResourceType.MEDICINE:
                vs.reported_qty = float(claim.state.get("reported_stock", 0.0))
            else:
                vs.claimed_status = claim.state.get("register_status")

        # -- no attestation at all --------------------------------------
        if att is None:
            vs.reasons = reasons + ["NO_ATTESTATION"]
            return vs

        age_s = (pd.Timestamp(as_of) - pd.Timestamp(att.observed_at)).total_seconds()
        age_days = age_s / 86400.0
        vs.staleness_s = age_s
        vs.anchor = {"attestation_id": att.attestation_id,
                     "observed_at": att.observed_at,
                     "attestor": att.attestor_id,
                     "role": att.attestor_role}

        # -- integrity checks on the attestation itself ------------------
        if att.seconds_spent < self.policy["min_attestation_seconds"]:
            vs.reasons = reasons + ["RUBBER_STAMP_SUSPECTED"]
            vs.state = VerificationState.CONFLICTED
            return vs

        if att.extraction_agreement and att.extraction_agreement.get("delta_pct", 0) > 5:
            vs.reasons = reasons + ["EXTRACTION_DISAGREEMENT"]
            vs.state = VerificationState.CONFLICTED
            return vs

        # -- stale: a read-time projection, NOT a stored state -----------
        if age_days > max_age:
            vs.reasons = reasons + ["ATTESTATION_STALE"]
            vs.state = VerificationState.UNVERIFIED
            return vs

        # -- verified ----------------------------------------------------
        if rt == ResourceType.MEDICINE:
            usable = float(att.observed.get("usable_qty", 0.0))
            present = float(att.observed.get("present_qty", usable))
            vs.verified_usable_qty = usable
            vs.unusable_qty = max(present - usable, 0.0)
            if vs.unusable_qty > 0:
                vs.reasons = vs.reasons + ["EXPIRED_STOCK_COUNTED_LIVE"]
            p = prior or ConsumptionPrior()
            vs.posterior = quantity_posterior(
                anchor_qty=usable, observed_deltas=observed_deltas, prior=p,
                observed_days=observed_days, observed_count=observed_count,
                gap_days=max(age_days, 0.0), alpha=alpha)
        else:
            st = att.observed.get("status")
            vs.verified_status = st
            if st != "FUNCTIONAL":
                code = {"NOT_PRESENT": "ASSET_NOT_PRESENT",
                        "NOT_COMMISSIONED": "ASSET_NOT_COMMISSIONED",
                        "NON_FUNCTIONAL": "ASSET_NON_FUNCTIONAL"}.get(st)
                if code:
                    vs.reasons = vs.reasons + [code]
                vs.state = VerificationState.REJECTED
                vs.survival = 0.0
                return vs
            meta = att.observed
            vs.survival = survival(
                months_since_verified=age_days / 30.0,
                age_months=meta.get("age_months", 24),
                amc_active=meta.get("amc_active", True),
                last_service_months=meta.get("last_service_months", 6))

        vs.state = VerificationState.VERIFIED
        return vs

    def generate_gfr22_certificate(
        self,
        facility_id: str,
        asset_id: str,
        as_of: str,
    ) -> dict:
        """Emits or refuses a TATHYON equipment verification record.

        Rule 213(1): 'Physical verification of Fixed Assets ... should be carried
        out at least once in a year and a certificate to this effect should be
        recorded in the Register of Fixed Assets.'
        A line CANNOT be emitted when physical state is UNVERIFIED or REJECTED.
        """
        vs = self.state_for(facility_id, ResourceType.EQUIPMENT, asset_id, as_of=as_of)
        if vs.state != VerificationState.VERIFIED or vs.verified_status != "FUNCTIONAL":
            refusal_code = vs.reasons[0] if vs.reasons else "ASSET_UNVERIFIED"
            refusal_msg = (
                f"TATHYON verification record REFUSED for asset '{asset_id}' at facility '{facility_id}'. "
                f"Physical state is {vs.state.value} ({refusal_code}). "
                "The configured policy requires a physical attestation before this record can be emitted."
            )
            return {
                "record_type": "TATHYON equipment physical verification",
                "facility_id": facility_id,
                "asset_id": asset_id,
                "statutory_rule": "GFR 2017 Rule 213(1)",
                "issued": False,
                "verification_state": vs.state.value,
                "refusal_code": refusal_code,
                "refusal_reason": refusal_msg,
                "reasons": vs.reasons,
                "as_of": as_of,
                "provenance": vs.provenance.value,
            }

        return {
            "record_type": "TATHYON equipment physical verification",
            "facility_id": facility_id,
            "asset_id": asset_id,
            "statutory_rule": "GFR 2017 Rule 213(1)",
            "issued": True,
            "verification_state": vs.state.value,
            "verified_status": vs.verified_status,
            "anchor": vs.anchor,
            "as_of": as_of,
            "provenance": vs.provenance.value,
            "attestation_ref": vs.anchor.get("attestation_id") if vs.anchor else None,
            "certified_by": vs.anchor.get("attestor") if vs.anchor else None,
            "certification_statement": (
                f"TATHYON records asset '{asset_id}' as physically present and functional based on the "
                "attestation reference shown. This record does not certify compliance with a legal requirement."
            ),
        }

    def calculate_sla_evidence_pack(
        self,
        facility_id: str,
        asset_id: str,
        as_of: str,
        contract: Optional[SLAContract] = None,
        claimed_uptime: Optional[float] = None,
    ) -> dict:
        """Calculates SLA compliance and penalty evidence pack for BEMMP contracts.

        Compares vendor self-reported uptime against physical attestation.
        Produces structured deduction and invoice payable recommendation.
        """
        vs = self.state_for(facility_id, ResourceType.EQUIPMENT, asset_id, as_of=as_of)
        claim = self.store.latest_claim(facility_id, asset_id)

        target = contract.sla_target if contract else 0.95
        base_fee = contract.monthly_base_fee_inr if contract else 150_000.0
        vendor_id = contract.vendor_id if contract else "vendor_default"
        vendor_name = contract.vendor_name if contract else "Maintenance Contractor"

        if claimed_uptime is None:
            claimed_uptime = float(claim.state.get("vendor_reported_uptime", 0.9912)) if claim else 0.9912

        conflicts = []
        if vs.state == VerificationState.REJECTED or (vs.verified_status and vs.verified_status != "FUNCTIONAL"):
            # Asset is physically broken or not commissioned despite vendor claiming uptime
            conflicts.append("SLA_UPTIME_CONTRADICTED")
            if "ASSET_NOT_COMMISSIONED" in vs.reasons:
                conflicts.append("ASSET_NOT_COMMISSIONED")

            verified_uptime = 0.0
            payable_status = "PENALTY_APPLIED"
            # Full deduction when asset is not functional or not commissioned
            penalty_inr = base_fee
            net_payable_inr = 0.0
            rationale = (
                f"Vendor claimed {claimed_uptime * 100:.2f}% uptime, but physical audit verified "
                f"asset as {vs.verified_status or 'NON_FUNCTIONAL'}. Full maintenance fee withheld under SLA penalty rules."
            )
        elif vs.state == VerificationState.VERIFIED:
            verified_uptime = claimed_uptime
            payable_status = "PAYABLE"
            penalty_inr = 0.0
            net_payable_inr = base_fee
            rationale = f"Physical attestation confirms asset is functional. Vendor uptime of {claimed_uptime * 100:.2f}% accepted."
        else:
            # UNVERIFIED
            verified_uptime = None
            payable_status = "BLOCKED_EVIDENCE_REQUIRED"
            penalty_inr = 0.0
            net_payable_inr = 0.0
            rationale = "No independent physical attestation on record. Invoice payment blocked pending physical verification."

        att = self.store.latest_attestation(facility_id, asset_id)
        evidence_refs = att.evidence_refs if att else []

        return {
            "asset_id": asset_id,
            "facility_id": facility_id,
            "contract_id": contract.contract_id if contract else f"cnt_{asset_id}",
            "vendor_id": vendor_id,
            "vendor_name": vendor_name,
            "sla_target": target,
            "vendor_claimed_uptime": claimed_uptime,
            "verified_uptime": verified_uptime,
            "verification_state": vs.state.value,
            "payable_status": payable_status,
            "base_fee_inr": base_fee,
            "penalty_inr": penalty_inr,
            "net_payable_inr": net_payable_inr,
            "conflicts": conflicts,
            "reasons": vs.reasons,
            "evidence_refs": evidence_refs,
            "attestation_anchor": vs.anchor,
            "rationale": rationale,
            "provenance": vs.provenance.value,
        }

    # -- priority: queue ordering only, never exported -------------------
    @staticmethod
    def priority(p_discrepancy: float, impact_inr: float,
                 days_since_verified: float, expected_cost_minutes: float) -> float:
        """NOT a probability of wrongness and NOT a truth claim -- which is
        exactly why it survives audit. It is a resource-allocation quantity."""
        freshness = 1.0 + math.log1p(max(days_since_verified, 0.0)) / 4.0
        return (p_discrepancy * math.log1p(impact_inr) * freshness
                / max(expected_cost_minutes, 1.0))


# ---------------------------------------------------------------------------
# Verification planning for a shortage: which donors must be counted first
# ---------------------------------------------------------------------------

BLOCKED_DONOR_REASON = "UNVERIFIED_STOCK_EXCLUDED_BY_SAFETY_GATE"


def plan_verifications(target_facility_id: str,
                       candidate_facilities: list[dict]) -> list[dict]:
    """Donor holders whose stock cannot be counted on until someone counts it.

    Every candidate that is not VERIFIED is returned with transferable_qty 0 and
    a physical-verification action. This is the verification half of a shortage
    response; `optimize.plan_response` is the transfer half and calls this.
    Pure function: reads nothing, writes nothing.
    """
    blocked = []
    for fac in candidate_facilities:
        fid = fac.get("facility_id", "")
        if fid == target_facility_id:
            continue
        v_state = str(fac.get("verification_state", "UNVERIFIED")).upper()
        if v_state == VerificationState.VERIFIED.value:
            continue
        blocked.append({
            "facility_id": fid,
            "facility_tier": fac.get("tier", "CHC"),
            "verified_state": v_state,
            "reported_stock": float(fac.get("reported_stock", 0.0)),
            "transferable_qty": 0.0,
            "blocked_reason": BLOCKED_DONOR_REASON,
            "action_required": "Dispatch physical verification task before rebalance consideration.",
        })
    return blocked


# ---------------------------------------------------------------------------
# Equipment SLA reconciliation: vendor-claimed uptime vs attested downtime
# ---------------------------------------------------------------------------

DEFAULT_SLA_PERIOD_HOURS = 720.0          # one 30-day billing month


@dataclass(frozen=True)
class SLAReconciliation:
    """Vendor uptime claim reconciled against custodian-attested downtime.

    Read-only by construction: this is a computation for the Drawing & Disbursing
    Officer to sign or refuse, never a payment instruction.
    """
    asset_id: str
    contract_id: str
    vendor_id: str
    claimed_uptime: float
    verified_uptime: Optional[float]
    attested_downtime_hours: float
    base_fee_inr: float
    penalty_inr: float
    net_payable_inr: float
    payable_status: str
    conflicts: tuple = ()
    reasons: tuple = ()
    evidence_refs: tuple = ()
    narrative: str = ""
    can_write_state: bool = False

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        for k in ("conflicts", "reasons", "evidence_refs"):
            d[k] = list(d[k])
        return d


def reconcile_equipment_sla(
    asset_id: str,
    contract: Optional[SLAContract],
    claimed_uptime: float,
    attested_downtime_hours: float,
    evidence_refs: Optional[list[str]] = None,
    total_period_hours: float = DEFAULT_SLA_PERIOD_HOURS,
) -> SLAReconciliation:
    """Penalty = shortfall below the SLA target x contractual rate, capped at the fee.

    Out-of-range inputs are refused, never clipped: a negative downtime would
    otherwise produce >100% uptime and silently unlock a penalty-free payment.
    """
    total_hours = total_period_hours if total_period_hours > 0 else DEFAULT_SLA_PERIOD_HOURS
    if not 0.0 <= claimed_uptime <= 1.0:
        raise ValueError(f"claimed_uptime must be in [0, 1], got {claimed_uptime!r}")
    if not 0.0 <= attested_downtime_hours <= total_hours:
        raise ValueError(f"attested_downtime_hours must be in [0, {total_hours}], "
                         f"got {attested_downtime_hours!r}")
    actual_uptime_hours = max(total_hours - attested_downtime_hours, 0.0)
    verified_uptime = round(actual_uptime_hours / total_hours, 4)

    fallback = SLAContract(contract_id="cntr_bemmp_2026", asset_id=asset_id,
                           vendor_id="VND_MEDTECH", vendor_name="Maintenance Contractor")
    c = contract or fallback
    shortfall_pct = max((c.sla_target - verified_uptime) * 100.0, 0.0)
    penalty_inr = min(round(shortfall_pct * c.penalty_rate_per_pct, 2), c.monthly_base_fee_inr)
    net_payable_inr = max(round(c.monthly_base_fee_inr - penalty_inr, 2), 0.0)

    uptime_delta = round(claimed_uptime - verified_uptime, 4)
    conflicts = ((f"VENDOR_OVERSTATED_UPTIME_{round(uptime_delta * 100, 1)}PCT",)
                 if uptime_delta > 0.005 else ())
    narrative = (
        f"Vendor claimed {claimed_uptime * 100:.1f}% uptime, but ground-attested "
        f"downtime ({attested_downtime_hours:.1f} hrs) indicates actual uptime "
        f"is {verified_uptime * 100:.1f}%. Discrepancy: {uptime_delta * 100:.1f}%. "
        f"Contractual SLA penalty applied: INR {penalty_inr:,.2f}."
    )
    return SLAReconciliation(
        asset_id=asset_id, contract_id=c.contract_id, vendor_id=c.vendor_id,
        claimed_uptime=claimed_uptime, verified_uptime=verified_uptime,
        attested_downtime_hours=attested_downtime_hours,
        base_fee_inr=c.monthly_base_fee_inr, penalty_inr=penalty_inr,
        net_payable_inr=net_payable_inr,
        payable_status="PENALTY_APPLIED" if penalty_inr > 0 else "PAYABLE",
        conflicts=conflicts,
        reasons=("GROUND_DOWNTIME_LOGGED_BY_CUSTODIAN",) if attested_downtime_hours > 0 else (),
        evidence_refs=tuple(evidence_refs or ()),
        narrative=narrative,
    )



# ===========================================================================
# THE CORE DECISION: which facility x SKU reports get a physical count
# ===========================================================================
#
# "Given stock reports we cannot fully trust and transfer capacity we cannot
# waste, which facilities do we verify, and which shortages do we serve first?"
#
# This section answers the first half. Every facility x SKU report is ranked by
#     value = P(materially wrong) x hidden stockout-days x essentiality
# and a 0/1 knapsack picks the set with the most value that fits the monthly
# visit budget. P comes from a TRAINED classifier (below), not hand-set weights.
#
# DOCTRINE: the value that orders this queue is never persisted, exported or
# attested. It lives in VerificationQueue.ranking (memory only). What is stored
# is a VerificationVisit, which has no score field, and EventStore refuses any
# payload carrying one.

TRAIN_SEEDS = tuple(range(1001, 1015))   # generated worlds used to fit the scorer
TEST_SEEDS = tuple(range(2001, 2005))     # held-out worlds, never seen in training
DEPLOYMENT_SEED = 7                       # the demo ledger in ./data; in neither split
SNAPSHOT_DAYS = tuple(range(42, 240, 14))  # observation days per world (day 0 = run start)
USABLE_SHARE_GRID = tuple(np.linspace(0.025, 0.975, 20))  # quantile grid for the expectation

# Hyper-parameters fixed before any evaluation and never tuned on TEST_SEEDS.
GBC_PARAMS = dict(n_estimators=200, max_depth=4, learning_rate=0.03,
                  subsample=0.8, random_state=0)

# SKU essentiality: WHO/MSH VEN classification (Vital / Essential / Non-essential).
# A POLICY input set by the district drug committee, not learned and not
# clinical advice. The 3/2/1 weights are the conventional ordinal VEN weights.
VEN_WEIGHTS = {"V": 3.0, "E": 2.0, "N": 1.0}
SKU_VEN = {
    "INJDEX": "V",    # emergency corticosteroid
    "SALB": "V",      # acute bronchospasm
    "ORS01": "V",     # child diarrhoeal dehydration
    "AMX250": "V",    # first-line childhood pneumonia
    "CEFX500": "E", "METRO4": "E", "IFA100": "E", "MTFM500": "E", "ATNL50": "E",
    "PCM500": "N",    # symptomatic relief, widely substitutable
}
DEFAULT_VEN = "E"

# Visit cost model (SYNTHETIC assumptions, published in the scorer report).
CONSEQUENCE_HORIZON_DAYS = 30.0           # one monthly verification cycle
DDW_XY = (50.0, 50.0)                     # district drug warehouse at the centre of the grid
COUNT_HOURS = 1.0                         # time to count one SKU on site
SLOT_HOURS = 4.0                          # one visit slot = half a working day
DEFAULT_ASSIGNEE = "district-verification-team"
VISIT_DUE_DAYS = 7


@dataclass(frozen=True)
class TrustScorer:
    """P(the report is materially wrong), trained on generator labels.

    Triage, never a truth machine: a high P earns a physical count; it never
    changes a stock figure, and a human count always overrules it.
    """
    model: Any
    model_name: str
    features: tuple
    train_seeds: tuple
    train_rows: int
    train_base_rate: float
    over_report_share: float       # P(physical usable < reported | materially wrong)
    usable_share_if_over: float = 0.50    # median physical usable / reported, those cases
    usable_share_quantiles: tuple = (1.0,)  # its distribution, for the expectation
    model_id: str = "TATHYON-GBC-BASELINE-v2.0 (Synthetic Baseline)"
    is_verified_real: bool = False
    provenance: str = "SYNTHETIC_CAG_BENCHMARK"
    human_approval_documented: bool = False

    def expected_hidden_stockout_days(self, reported, daily_rate) -> np.ndarray:
        """E[stockout-days a wrong report would hide], over the TRAINING
        distribution of how much of an over-report is physically usable.

        An expectation, not a plug-in median: with the median, any report with
        more than ~72 days of cover scored exactly zero and was never counted,
        although a wrong report can be almost entirely unusable (expired stock
        counted live). The held-out queue evaluation exposed that.
        """
        reported = np.asarray(reported, dtype=float)
        right = stockout_days(reported, daily_rate)
        hidden = np.mean([stockout_days(reported * q, daily_rate) - right
                          for q in self.usable_share_quantiles], axis=0)
        return self.over_report_share * hidden

    def p_wrong(self, frame: pd.DataFrame) -> np.ndarray:
        missing = [f for f in self.features if f not in frame.columns]
        if missing:
            raise ValueError(f"TRUST_FEATURES_MISSING: {missing}")
        return self.model.predict_proba(frame[list(self.features)].to_numpy(dtype=float))[:, 1]


@lru_cache(maxsize=None)
def _generated(seed: int) -> dict:
    from .generator import generate
    return generate(seed=seed)


@lru_cache(maxsize=None)
def labelled_snapshots(seed: int, snapshot_days: tuple = SNAPSHOT_DAYS) -> pd.DataFrame:
    """Observed-only features + the ground-truth label, one row per observation.
    Truth columns ride along ONLY for evaluation; they are never features."""
    from .generator import attestation_log, observation_labels, observed_only
    from .trust_features import snapshot_features
    g = _generated(seed)
    labels = observation_labels(g, list(snapshot_days))
    feats = snapshot_features(observed_only(g["observed"]), attestation_log(g), labels["date"].unique())
    out = feats.merge(labels.drop(columns=["reported_stock"]),
                      on=["date", "facility_id", "sku"], how="inner")
    out["seed"] = seed
    return out


def _fit(model_name: str, X: np.ndarray, y: np.ndarray):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    if model_name == "gbc":
        return GradientBoostingClassifier(**GBC_PARAMS).fit(X, y)
    if model_name == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(X, y)
    raise ValueError(f"UNKNOWN_MODEL: {model_name!r} (expected 'gbc' or 'logistic')")


def train_trust_scorer(train_seeds: tuple = TRAIN_SEEDS, model: str = "gbc",
                       features: Optional[tuple] = None) -> TrustScorer:
    """Fit on TRAIN_SEEDS only. GradientBoosting by default; if it cannot be
    fitted, fall back to LogisticRegression and say so in model_name."""
    from .trust_features import TRUST_FEATURES
    feats = tuple(features or TRUST_FEATURES)
    frame = pd.concat([labelled_snapshots(s) for s in train_seeds], ignore_index=True)
    y = frame["is_materially_wrong"].to_numpy(dtype=int)
    if len(np.unique(y)) < 2:
        raise ValueError("TRAINING_LABELS_DEGENERATE: training worlds contain a single class")
    X = frame[list(feats)].to_numpy(dtype=float)
    try:
        fitted, name = _fit(model, X, y), model
    except (ValueError, FloatingPointError) as exc:
        if model != "gbc":
            raise
        log.warning("GradientBoosting failed (%s); falling back to LogisticRegression", exc)
        fitted, name = _fit("logistic", X, y), "logistic (fallback)"
    wrong = frame[frame["is_materially_wrong"] == 1]
    over = wrong[wrong["true_usable"] < wrong["reported_stock"]]
    share = (over["true_usable"] / over["reported_stock"].clip(lower=1.0)).clip(0, 1)
    return TrustScorer(
        model=fitted, model_name=name, features=feats, train_seeds=tuple(train_seeds),
        train_rows=len(frame), train_base_rate=float(y.mean()),
        over_report_share=float(len(over) / max(len(wrong), 1)),
        usable_share_if_over=float(share.median()) if len(share) else 1.0,
        usable_share_quantiles=(tuple(float(q) for q in np.quantile(share, USABLE_SHARE_GRID))
                                if len(share) else (1.0,)),
    )


REAL_MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "artifacts", "models", "trust_scorer_real_pilot.joblib"
)


@lru_cache(maxsize=None)
def default_trust_scorer() -> Optional[TrustScorer]:
    """Load an approved real scorer, or return None when none is available.

    Hardening Rule:
    A real-data model artifact is served ONLY if it passes strict verification gates:
    - Must be an authenticated TrustScorer instance
    - Must have is_verified_real == True
    - Must have human_approval_documented == True
    - Must have train_rows >= 50 (no toy mock runs permitted)
    Never fit a generated benchmark model as an application startup fallback.
    """
    if os.path.isfile(REAL_MODEL_PATH):
        try:
            import joblib
            loaded = joblib.load(REAL_MODEL_PATH)
            if isinstance(loaded, TrustScorer):
                is_verified = getattr(loaded, "is_verified_real", False)
                human_approved = getattr(loaded, "human_approval_documented", False)
                train_rows = getattr(loaded, "train_rows", 0)
                if is_verified and human_approved and train_rows >= 50:
                    return loaded
                log.warning(
                    "Model artifact at %s is not verified for pilot serving (is_verified=%s, human_approved=%s, train_rows=%d); model unavailable.",
                    REAL_MODEL_PATH, is_verified, human_approved, train_rows,
                )
        except Exception as e:
            log.warning("Could not load real-data model from %s: %s; model unavailable", REAL_MODEL_PATH, e)
    return None


# ---------------------------------------------------------------------------
# Consequence and cost
# ---------------------------------------------------------------------------

def stockout_days(stock, daily_rate, horizon_days: float = CONSEQUENCE_HORIZON_DAYS) -> np.ndarray:
    """Days inside the horizon with nothing on the shelf, at a constant burn rate."""
    stock = np.maximum(np.asarray(stock, dtype=float), 0.0)
    rate = np.asarray(daily_rate, dtype=float)
    cover = np.where(rate > 0, stock / np.maximum(rate, 1e-9), np.inf)
    return np.clip(horizon_days - cover, 0.0, horizon_days)


def visit_cost_slots(x: float, y: float) -> int:
    """Half-day slots for a round trip from the district warehouse plus the count."""
    from .optimize import Facility, travel_hours
    hours = 2 * travel_hours(Facility("DDW", "DDW", *DDW_XY), Facility("F", "F", x, y)) + COUNT_HOURS
    return max(1, math.ceil(hours / SLOT_HOURS))


def verification_candidates(generated: dict, as_of: pd.Timestamp,
                            scorer: TrustScorer) -> pd.DataFrame:
    """Every facility x SKU report on `as_of`, with every ranking input exposed."""
    from .forecast import select_model
    from .generator import attestation_log, observed_only
    from .trust_features import snapshot_features
    obs = observed_only(generated["observed"])
    obs = obs[obs["date"] <= as_of]
    cand = snapshot_features(obs, attestation_log(generated), [as_of])
    cand["p_wrong"] = scorer.p_wrong(cand)
    rates = {k: select_model(g["issued"].to_numpy(dtype=float))
             for k, g in obs.groupby(["facility_id", "sku"], sort=False)}
    cand["daily_rate"] = [rates[(f, k)][0].rate for f, k in zip(cand["facility_id"], cand["sku"])]
    cand["forecast_model"] = [rates[(f, k)][1] for f, k in zip(cand["facility_id"], cand["sku"])]
    right = stockout_days(cand["reported_stock"], cand["daily_rate"])
    hidden = scorer.expected_hidden_stockout_days(cand["reported_stock"], cand["daily_rate"])
    cand["stockout_days_if_right"] = right
    cand["stockout_days_if_wrong"] = right + hidden / max(scorer.over_report_share, 1e-9)
    cand["hidden_stockout_days"] = hidden
    cand["ven"] = cand["sku"].map(SKU_VEN).fillna(DEFAULT_VEN)
    cand["essentiality"] = cand["ven"].map(VEN_WEIGHTS)
    fac = generated["facilities"].set_index("facility_id")
    cand["visit_cost"] = [visit_cost_slots(fac.at[f, "x"], fac.at[f, "y"]) for f in cand["facility_id"]]
    return cand


# ---------------------------------------------------------------------------
# Knapsack + queue
# ---------------------------------------------------------------------------

class VerificationQueue(list):
    """list[VerificationVisit], highest value first, plus the in-memory ranking.

    `ranking` holds every candidate (selected or not) and every feature that
    produced its rank, for inspection only. It is never written to the store.
    """

    def __init__(self, visits: list, ranking: pd.DataFrame, budget: int, budget_used: int):
        super().__init__(visits)
        self._ranking = ranking
        self.budget = budget
        self.budget_used = budget_used

    @property
    def ranking(self) -> pd.DataFrame:
        return self._ranking.copy()


RANKING_INPUTS = ("facility_id", "sku", "p_wrong", "hidden_stockout_days", "essentiality", "visit_cost")


def target_verifications(budget: int, candidates: Optional[pd.DataFrame] = None, *,
                         scorer: Optional[TrustScorer] = None,
                         as_of: Optional[pd.Timestamp] = None,
                         assignee: str = DEFAULT_ASSIGNEE) -> VerificationQueue:
    """Pick this month's physical counts.

    value_i = P(materially wrong)_i x hidden stockout-days_i x essentiality_i,
    maximised under sum(visit_cost_i) <= budget. With `candidates` supplied (any
    frame carrying RANKING_INPUTS) this is a pure function, testable in
    isolation. Without it, candidates come from the deployment ledger.
    """
    if isinstance(budget, bool) or not isinstance(budget, (int, np.integer)) or budget < 0:
        raise ValueError(f"budget must be a non-negative integer number of visit slots, got {budget!r}")
    if candidates is None:
        raise ValueError("AUTHORIZED_CANDIDATES_REQUIRED: provide candidates built from authorized operational records")
    missing = [c for c in RANKING_INPUTS if c not in candidates.columns]
    if missing:
        raise ValueError(f"CANDIDATES_MISSING_COLUMNS: {missing}")
    as_of = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.now(tz="UTC").normalize()

    ranked = candidates.copy()
    ranked["value"] = (ranked["p_wrong"].astype(float) * ranked["hidden_stockout_days"].astype(float)
                       * ranked["essentiality"].astype(float))
    ranked = ranked.sort_values(["value", "facility_id", "sku"],
                                ascending=[False, True, True]).reset_index(drop=True)
    ranked["rank"] = np.arange(1, len(ranked) + 1)
    from .optimize import knapsack
    chosen = knapsack(ranked["value"], ranked["visit_cost"], int(budget))
    ranked["selected"] = False
    ranked.loc[chosen, "selected"] = True

    due = (as_of + timedelta(days=VISIT_DUE_DAYS)).isoformat()
    stamp = as_of.strftime("%Y%m%d")
    visits = [VerificationVisit(visit_id=f"vv_{stamp}_{r.facility_id}_{r.sku}",
                                facility_id=r.facility_id, sku_id=r.sku, assignee=assignee,
                                due_date=due, status=VisitStatus.SCHEDULED)
              for r in ranked.loc[chosen].itertuples()]
    used = int(ranked.loc[chosen, "visit_cost"].sum()) if chosen else 0
    return VerificationQueue(visits, ranked, int(budget), used)
