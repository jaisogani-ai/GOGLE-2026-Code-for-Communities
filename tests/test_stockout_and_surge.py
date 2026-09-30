"""
Tests for Phase 2: Stockout Forecast + Surge Engine.

Verifies:
1. Forecast competition across Naive, Seasonal Naive, Croston-SBA, and TSB based on MASE.
2. Naive is allowed to win whenever it scores lowest (no artificial ML bias).
3. P(stockout) and expected stockout date calculations against verified usable stock.
4. Explanatory driver attribution (exposing phantom inventory).
5. Tabular CUSUM surge detection and alert tier escalation (WATCH -> ELEVATED -> HIGH -> CRITICAL).
"""
import numpy as np
import pytest

from tathyon.graph import create_default_resource_graph
from tathyon.stockout import (
    StockoutPredictor,
    SurgeDetectionEngine,
    benchmark_forecast_models,
)


def test_01_forecast_competition_benchmark():
    """Verify holdout MASE competition selects the empirical winner without artificial bias."""
    # Synthetic flat intermittent series where naive-mean is optimal
    flat_series = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0] * 5
    bench = benchmark_forecast_models(flat_series, holdout_days=7)

    assert bench.best_model in ("naive-mean", "seasonal-naive")
    assert "scores_mase" in bench.to_dict()
    assert bench.selected_daily_rate == pytest.approx(10.0, 0.5)


def test_02_tsb_and_croston_competition():
    """Verify Croston and TSB are properly evaluated on intermittent series."""
    # Lumpy intermittent series: lots of zeros with occasional bursts
    intermittent_series = [0.0, 0.0, 15.0, 0.0, 0.0, 0.0, 20.0, 0.0, 0.0, 12.0] * 4
    bench = benchmark_forecast_models(intermittent_series, holdout_days=10)

    assert len(bench.scores_mase) == 4
    assert "TSB" in bench.scores_mase
    assert "Croston-SBA" in bench.scores_mase
    assert bench.selected_daily_rate > 0.0


def test_03_stockout_prediction_against_usable_stock():
    """Verify stockout prediction correctly flags high P(stockout) when usable stock is zero/low."""
    graph = create_default_resource_graph()
    predictor = StockoutPredictor(graph)

    # 1. CHC_RURAL_NORTH has claimed=200, but usable=0 (expired)
    pred_chc = predictor.predict_stockout(
        facility_id="CHC_RURAL_NORTH",
        resource_id="MED_ANTI_RABIES_VACCINE",
    )
    assert pred_chc.p_stockout == 1.0
    assert pred_chc.days_to_stockout == 0.0
    assert pred_chc.usable_stock == 0.0
    assert any("PHANTOM_INVENTORY_EXPOSED" in d for d in pred_chc.drivers)

    # 2. District Central Depot has 500 verified usable vials (velocity 12/day -> ~41 days)
    pred_dwh = predictor.predict_stockout(
        facility_id="DWH_DISTRICT_DEPOT_01",
        resource_id="MED_ANTI_RABIES_VACCINE",
    )
    assert pred_dwh.p_stockout < 0.05
    assert pred_dwh.days_to_stockout > 35.0
    assert pred_dwh.expected_shortage_quantity == 0.0


def test_04_surge_detection_cusum_alert_tiers():
    """Verify CUSUM escalates from WATCH -> ELEVATED -> HIGH -> CRITICAL as consumption surges."""
    graph = create_default_resource_graph()
    surge_engine = SurgeDetectionEngine(graph)

    baseline_rate = 5.0

    # 1. Stable consumption within bounds -> WATCH
    stable_history = [5.0, 4.0, 6.0, 5.0, 5.0, 4.0, 5.0]
    alert_watch = surge_engine.detect_surge("PHC_REMOTE_EAST", "MED_ANTI_RABIES_VACCINE", stable_history, baseline_rate)
    assert alert_watch.tier == "WATCH"

    # 2. Moderate +25% consumption drift -> ELEVATED
    drift_history = [6.0, 6.5, 7.0, 6.0, 6.5, 7.0]
    alert_elevated = surge_engine.detect_surge("PHC_REMOTE_EAST", "MED_ANTI_RABIES_VACCINE", drift_history, baseline_rate)
    assert alert_elevated.tier in ("ELEVATED", "HIGH")

    # 3. Severe +120% dog-bite surge -> CRITICAL
    severe_surge = [11.0, 12.0, 14.0, 13.0, 15.0, 16.0, 15.0]
    alert_crit = surge_engine.detect_surge("PHC_REMOTE_EAST", "MED_ANTI_RABIES_VACCINE", severe_surge, baseline_rate)
    assert alert_crit.tier in ("HIGH", "CRITICAL")
    assert alert_crit.residual_pct > 100.0
    assert "CRITICAL" in alert_crit.tier or "HIGH" in alert_crit.tier
