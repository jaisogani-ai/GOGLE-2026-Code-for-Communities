r"""
Stockout Forecasting & Surge Detection Engine.
BUILD PHASE 2: STOCKOUT FORECAST + SURGE ENGINE

Implements:
1. Forecast Competition & Benchmarking:
   - Naive (Sample Mean)
   - Seasonal Naive (7-day periodic)
   - Croston-SBA (Syntetos-Boylan bias-corrected)
   - TSB (Teunter-Syntetos-Babai dual-parameter)
   - Evaluated by holdout MASE (Mean Absolute Scaled Error); no artificial bias.
2. Stockout Risk Projection:
   - P(stockout within lead time)
   - Expected stockout date
   - Expected shortage quantity
   - Demand over lead time
   - Drivers analysis
   - Confidence score
3. Surge Detection:
   - Forecast Residuals: e_t = y_t - \hat{y}_t
   - District CUSUM (Cumulative Sum Control Chart)
   - Tiered Output: WATCH | ELEVATED | HIGH | CRITICAL
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

from .forecast import (
    TSBFit,
    fit_croston_sba,
    fit_tsb,
    mase,
    naive_rate,
    seasonal_naive,
)
from .graph import HealthcareResourceGraph, ResourceState
from .schema import EventType, Provenance, now, sha256


@dataclass
class ForecastBenchmark:
    """Rigorous comparison of the 4 candidate intermittent demand models."""
    best_model: str
    scores_mase: dict[str, float]
    selected_daily_rate: float
    tsb_template: TSBFit
    provenance: str = "SYNTHETIC_BENCHMARK"

    def to_dict(self) -> dict:
        return {
            "best_model": self.best_model,
            "scores_mase": {k: round(v, 4) if np.isfinite(v) else 999.0 for k, v in self.scores_mase.items()},
            "selected_daily_rate": round(self.selected_daily_rate, 2),
            "provenance": self.provenance,
        }


@dataclass
class StockoutPrediction:
    """Comprehensive stockout risk assessment."""
    facility_id: str
    resource_id: str
    p_stockout: float                           # P(demand > usable_stock over lead time)
    days_to_stockout: float
    expected_stockout_date: str                 # ISO-8601 UTC date
    expected_shortage_quantity: float
    demand_over_lead_time: float
    usable_stock: float
    claimed_stock: float
    lead_time_days: float
    drivers: list[str]                          # Causal explanatory factors
    confidence: float                           # [0.0 - 1.0]
    forecast_model: str
    network_impact: dict = field(default_factory=dict)
    as_of: str = field(default_factory=now)
    provenance: str = "SIMULATION"

    @property
    def winning_model(self) -> str:
        return self.forecast_model

    @property
    def daily_velocity(self) -> float:
        if self.lead_time_days > 0:
            return round(self.demand_over_lead_time / self.lead_time_days, 2)
        return 0.0

    @property
    def risk(self) -> str:
        if self.usable_stock <= 0 or self.p_stockout >= 0.85 or self.days_to_stockout <= 3.0:
            return "CRITICAL"
        if self.p_stockout >= 0.60 or self.days_to_stockout <= 7.0:
            return "HIGH"
        if self.p_stockout >= 0.30 or self.days_to_stockout <= 14.0:
            return "MEDIUM"
        return "LOW"

    def to_dict(self) -> dict:
        return {
            "facility_id": self.facility_id,
            "resource_id": self.resource_id,
            "p_stockout": round(self.p_stockout, 3),
            "stockout_probability": round(self.p_stockout, 3),
            "days_to_stockout": round(self.days_to_stockout, 1),
            "expected_stockout_date": self.expected_stockout_date,
            "expected_shortage_quantity": round(self.expected_shortage_quantity, 1),
            "demand_over_lead_time": round(self.demand_over_lead_time, 1),
            "usable_stock": round(self.usable_stock, 1),
            "claimed_stock": round(self.claimed_stock, 1),
            "lead_time_days": self.lead_time_days,
            "drivers": self.drivers,
            "confidence": round(self.confidence, 3),
            "forecast_model": self.forecast_model,
            "failure_risk": {
                "p_stockout": round(self.p_stockout, 3),
                "days_to_stockout": round(self.days_to_stockout, 1),
                "shortage_quantity": round(self.expected_shortage_quantity, 1),
                "urgency": self.risk,
            },
            "network_impact": self.network_impact,
            "as_of": self.as_of,
            "provenance": self.provenance,
        }


@dataclass
class SurgeAlert:
    """CUSUM and residual-based outbreak/surge alert."""
    tier: str                                   # "WATCH", "ELEVATED", "HIGH", "CRITICAL"
    facility_id: str
    resource_id: str
    district: str
    cusum_score: float
    residual_pct: float                         # % excess demand vs baseline
    baseline_velocity: float
    current_velocity: float
    affected_facilities_count: int
    message: str
    timestamp: str = field(default_factory=now)
    provenance: str = "SIMULATION"

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "facility_id": self.facility_id,
            "resource_id": self.resource_id,
            "district": self.district,
            "cusum_score": round(self.cusum_score, 2),
            "residual_pct": round(self.residual_pct, 1),
            "baseline_velocity": round(self.baseline_velocity, 1),
            "current_velocity": round(self.current_velocity, 1),
            "affected_facilities_count": self.affected_facilities_count,
            "message": self.message,
            "timestamp": self.timestamp,
            "provenance": self.provenance,
        }


# --------------------------------------------------------------------------
# Forecast Competition & Benchmarking
# --------------------------------------------------------------------------

def benchmark_forecast_models(
    history: list[float],
    holdout_days: int = 14,
) -> ForecastBenchmark:
    """Competes Naive, Seasonal Naive, Croston-SBA, and TSB on held-out MASE.
    
    CRITICAL RULE:
    The competition selects strictly by empirical holdout MASE.
    If naive-mean wins, naive-mean ships. No artificial bias.
    """
    y = np.asarray(history, dtype=float)
    tsb_template = fit_tsb(y)

    if len(y) < holdout_days + 7:
        rate = naive_rate(y)
        return ForecastBenchmark(
            best_model="naive-mean",
            scores_mase={"naive-mean": 1.0, "TSB": 1.0, "Croston-SBA": 1.0, "seasonal-naive": 1.0},
            selected_daily_rate=rate,
            tsb_template=tsb_template,
        )

    tr, val = y[:-holdout_days], y[-holdout_days:]
    tsb_tr = fit_tsb(tr)
    croston_tr = fit_croston_sba(tr)
    naive_tr = naive_rate(tr)
    s_naive_tr = seasonal_naive(tr)

    candidates = {
        "naive-mean": np.full(len(val), naive_tr),
        "seasonal-naive": np.full(len(val), s_naive_tr),
        "Croston-SBA": np.full(len(val), croston_tr.rate),
        "TSB": np.full(len(val), tsb_tr.rate),
    }

    scores = {}
    for name, pred in candidates.items():
        score = mase(val, pred, tr)
        scores[name] = score if np.isfinite(score) else 999.0

    best = min(scores, key=scores.get)

    # Refit the winner on the full historical series
    if best == "naive-mean":
        full_rate = naive_rate(y)
    elif best == "seasonal-naive":
        full_rate = seasonal_naive(y)
    elif best == "Croston-SBA":
        full_rate = fit_croston_sba(y).rate
    else:
        full_rate = fit_tsb(y).rate

    return ForecastBenchmark(
        best_model=best,
        scores_mase=scores,
        selected_daily_rate=full_rate,
        tsb_template=tsb_template,
    )


# --------------------------------------------------------------------------
# Stockout Predictor
# --------------------------------------------------------------------------

class StockoutPredictor:
    """Predicts stockout probability and shortages across the Resource Graph."""

    def __init__(self, graph: HealthcareResourceGraph):
        self.graph = graph

    def predict_stockout(
        self,
        facility_id: str,
        resource_id: str,
        history: Optional[list[float]] = None,
        as_of: Optional[str] = None,
    ) -> StockoutPrediction:
        """Calculates P(stockout), expected date, and shortage quantity over lead time.
        
        CRITICAL RULE:
        Evaluates risk exclusively against verified USABLE stock, exposing phantom inventory.
        """
        eval_time = as_of or now()
        st = self.graph.get_resource_state(facility_id, resource_id)

        # Baseline defaults if state missing
        usable_qty = st.usable_quantity if st else 0.0
        claimed_qty = st.claimed_quantity if st else 0.0
        lead_time = st.lead_time if st else 7.0
        daily_velocity = st.consumption_velocity if (st and st.consumption_velocity is not None) else 5.0

        # Check for registered demand series if history not explicitly provided
        if not history and (facility_id, resource_id) in self.graph.demand_series:
            ds = self.graph.demand_series[(facility_id, resource_id)]
            if ds and len(ds.consumption) >= 10:
                history = ds.consumption

        # Forecast competition
        if history and len(history) >= 10:
            benchmark = benchmark_forecast_models(history)
            rate = max(benchmark.selected_daily_rate, 0.1)
            model_name = benchmark.best_model
            dist = benchmark.tsb_template.lead_time_distribution(int(max(lead_time, 1)))
            demand_lead = rate * lead_time
            p_stockout = float(1.0 - dist.cdf(usable_qty)) if usable_qty > 0 else 1.0
            p_stockout = float(np.clip(p_stockout, 0.0, 1.0))
            days_left = usable_qty / rate if rate > 0 else 999.0
            expected_shortage = max(demand_lead - usable_qty, 0.0)
        else:
            rate = daily_velocity
            model_name = "prior-rate-velocity"
            if rate <= 0.0:
                p_stockout = 0.0
                days_left = 999.0 if usable_qty > 0 else 0.0
                demand_lead = 0.0
                expected_shortage = 0.0
            else:
                rng = np.random.default_rng(42)
                synth_hist = np.maximum(rng.normal(rate, max(rate * 0.25, 1.0), 28), 0.1)
                tsb_temp = fit_tsb(synth_hist)
                dist = tsb_temp.lead_time_distribution(int(max(lead_time, 1)))
                demand_lead = rate * lead_time
                p_stockout = float(1.0 - dist.cdf(usable_qty)) if usable_qty > 0 else 1.0
                p_stockout = float(np.clip(p_stockout, 0.0, 1.0))
                days_left = usable_qty / rate if rate > 0 else 999.0
                expected_shortage = max(demand_lead - usable_qty, 0.0)

        # Compute stockout date
        try:
            base_dt = datetime.fromisoformat(eval_time.replace("Z", "+00:00"))
        except Exception:
            base_dt = datetime.now(timezone.utc)
        stockout_dt = base_dt + timedelta(days=days_left)
        expected_stockout_date = stockout_dt.strftime("%Y-%m-%d")

        # Driver attribution
        drivers = []
        if usable_qty <= 0:
            drivers.append("ZERO_USABLE_STOCK_ON_HAND")
        if claimed_qty > usable_qty:
            drivers.append(f"PHANTOM_INVENTORY_EXPOSED ({claimed_qty - usable_qty:.0f} units expired or unverified)")
        if days_left < lead_time:
            drivers.append(f"BUFFER_BELOW_REPLENISHMENT_LEAD_TIME ({days_left:.1f}d < {lead_time:.0f}d)")
        if rate > daily_velocity * 1.25:
            drivers.append("DEMAND_VELOCITY_ACCELERATION")
        if not drivers:
            drivers.append("ROUTINE_CONSUMPTION_WITHIN_SAFETY_WINDOW")

        confidence = 0.94 if (history and len(history) >= 28) else 0.82

        # Compute causal downstream network cascade impact
        cascade_eval = self.graph.evaluate_network_cascade(facility_id, resource_id)
        ref_pressure = cascade_eval.get("referral_pressure", {})
        hub_id = ref_pressure.get("hub_id") or "DISTRICT_HUB"
        hub_name = ref_pressure.get("hub_name") or "District Referral Hub"
        dist_km = self.graph.distance_km(facility_id, hub_id) if hub_id in self.graph.facilities else 25.0
        diverted_daily = round(rate * 0.8, 1) if p_stockout >= 0.5 else 0.0

        network_impact = {
            "downstream_hub_id": hub_id,
            "downstream_hub_name": hub_name,
            "distance_km": round(dist_km, 1),
            "diverted_patients_daily": diverted_daily,
            "cascade_burden_score": cascade_eval.get("cascade_burden_score", 0.0),
            "cascade_risk_score": cascade_eval.get("cascade_burden_score", 0.0),
            "status": "CASCADE_SURGE_RISK" if (p_stockout >= 0.6 or cascade_eval.get("secondary_shortages")) else "NOMINAL",
            "direct_impact": cascade_eval.get("direct_impact"),
            "secondary_shortages": cascade_eval.get("secondary_shortages", []),
            "referral_pressure": ref_pressure,
            "cascade_burden_formula": cascade_eval.get("cascade_burden_formula"),
            "interpretation": cascade_eval.get("interpretation"),
        }

        return StockoutPrediction(
            facility_id=facility_id,
            resource_id=resource_id,
            p_stockout=p_stockout,
            days_to_stockout=days_left,
            expected_stockout_date=expected_stockout_date,
            expected_shortage_quantity=expected_shortage,
            demand_over_lead_time=demand_lead,
            usable_stock=usable_qty,
            claimed_stock=claimed_qty,
            lead_time_days=lead_time,
            drivers=drivers,
            confidence=confidence,
            forecast_model=model_name,
            network_impact=network_impact,
            as_of=eval_time,
        )

    predict = predict_stockout



# --------------------------------------------------------------------------
# Surge Detection Engine (CUSUM + Residuals)
# --------------------------------------------------------------------------

class SurgeDetectionEngine:
    """Detects regional demand surges using Forecast Residuals and District CUSUM."""

    def __init__(self, graph: HealthcareResourceGraph):
        self.graph = graph

    def detect_surge(
        self,
        facility_id: str,
        resource_id: str,
        recent_consumption: list[float],
        baseline_rate: float,
        k_sigma: float = 0.5,
        h_threshold: float = 3.5,
    ) -> SurgeAlert:
        """Runs tabular CUSUM control chart on forecast residuals:
          e_t = y_t - baseline
          C_t = max(0, C_{t-1} + (e_t - k))
        """
        fac = self.graph.facilities.get(facility_id)
        district = fac.district if fac else "District Central"

        y = np.asarray(recent_consumption, dtype=float)
        if len(y) == 0:
            return SurgeAlert(
                tier="WATCH",
                facility_id=facility_id,
                resource_id=resource_id,
                district=district,
                cusum_score=0.0,
                residual_pct=0.0,
                baseline_velocity=baseline_rate,
                current_velocity=baseline_rate,
                affected_facilities_count=1,
                message="No recent consumption data to analyze",
            )

        current_velocity = float(np.mean(y[-3:])) if len(y) >= 3 else float(np.mean(y))
        sigma = max(float(np.std(y)), baseline_rate * 0.30, 1.0)
        slack = k_sigma * sigma

        # Compute CUSUM series
        cusum = 0.0
        for val in y:
            residual = val - baseline_rate
            cusum = max(0.0, cusum + (residual - slack))

        residual_pct = ((current_velocity - baseline_rate) / max(baseline_rate, 1.0)) * 100.0

        # Tier classification
        norm_cusum = cusum / sigma
        if (norm_cusum >= h_threshold * 2.0 and residual_pct >= 75.0) or residual_pct >= 100.0:
            tier = "CRITICAL"
            msg = f"CRITICAL REGIONAL OUTBREAK: Demand velocity surged +{residual_pct:.1f}% above baseline (CUSUM {norm_cusum:.2f}). Rapid stockout imminent."
        elif norm_cusum >= h_threshold or residual_pct >= 40.0:
            tier = "HIGH"
            msg = f"HIGH SURGE DETECTED: Sustained consumption anomaly of +{residual_pct:.1f}% across facility (CUSUM {norm_cusum:.2f})."
        elif norm_cusum >= h_threshold * 0.5 or residual_pct >= 15.0:
            tier = "ELEVATED"
            msg = f"ELEVATED CONSUMPTION: Early warning positive drift +{residual_pct:.1f}% above seasonal mean."
        else:
            tier = "WATCH"
            msg = "STABLE: Consumption variance within normal stochastic control bounds."

        # Count district facilities affected
        affected_count = 1
        for (fid, rid), st in self.graph.states.items():
            if rid == resource_id and fid != facility_id:
                other_fac = self.graph.facilities.get(fid)
                if other_fac and other_fac.district == district and st.risk in ("HIGH", "CRITICAL"):
                    affected_count += 1

        return SurgeAlert(
            tier=tier,
            facility_id=facility_id,
            resource_id=resource_id,
            district=district,
            cusum_score=norm_cusum,
            residual_pct=residual_pct,
            baseline_velocity=baseline_rate,
            current_velocity=current_velocity,
            affected_facilities_count=affected_count,
            message=msg,
        )
