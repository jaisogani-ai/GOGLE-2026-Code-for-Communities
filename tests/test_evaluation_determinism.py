"""Tests for evaluation harness determinism and reproducibility.

Ensures that run_evaluation produces byte-identical outputs given identical seeds.
"""
import json
import os
import tempfile
import pytest

from tathyon.evaluate import generate_evaluation_scenarios, run_evaluation


def test_evaluation_reproducibility_and_determinism():
    """Given the same seed, run_evaluation must produce byte-identical JSON reports."""
    seed = 20260928

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f1, \
         tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f2:
        path1 = f1.name
        path2 = f2.name

    try:
        rep1 = run_evaluation(seed=seed, output_path=path1)
        rep2 = run_evaluation(seed=seed, output_path=path2)

        bytes1 = open(path1, "rb").read()
        bytes2 = open(path2, "rb").read()

        assert bytes1 == bytes2, "Evaluation report is not byte-identical across runs!"
        assert rep1.headline == rep2.headline
    finally:
        if os.path.exists(path1):
            os.remove(path1)
        if os.path.exists(path2):
            os.remove(path2)


def test_all_baselines_and_ablation_run():
    """Verify all 4 baselines and AI ablation arm run and populate report."""
    rep = run_evaluation(seed=42, output_path=tempfile.mktemp(suffix=".json"))
    arms = rep.benchmark_arms

    expected_arms = {"tathyon", "naive", "always_verify", "min_max", "greedy_guarded", "ai_ablation"}
    assert set(arms.keys()) == expected_arms

    # Verify silent phantom split exists
    assert "tathyon_hit_rate_silent" in rep.silent_vs_flagged_split
    assert "ablation_hit_rate_silent" in rep.silent_vs_flagged_split
    assert "silent_phantom_units_blocked" in rep.silent_vs_flagged_split
    assert "flagged_phantom_units_blocked" in rep.silent_vs_flagged_split

    # Verify AI ablation comparison
    assert "stockout_days_degradation_without_ai" in rep.ai_ablation_comparison
    assert "honest_assessment" in rep.ai_ablation_comparison
