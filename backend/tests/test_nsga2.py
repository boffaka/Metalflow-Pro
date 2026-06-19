# backend/tests/test_nsga2.py
"""Tests for NSGA-II optimization engine."""
import pytest

def get_optimizer():
    try:
        from backend.engines.optimization import run_nsga2
    except ImportError:
        from engines.optimization import run_nsga2
    return run_nsga2


def test_nsga2_returns_pareto_front():
    run_nsga2 = get_optimizer()
    results = run_nsga2(
        base_params={
            "wi": 14.0, "spi_kwh_t": 10.0, "f80_um": 3000.0,
            "r_inf": 0.90, "k_cil": 0.35, "srt_h": 24.0,
            "tph": 500.0, "op_hours_day": 24.0, "avail_pct": 92.0, "grade_g_t": 1.5,
        },
        n_pop=10, n_gen=5  # small for test speed
    )
    assert "solutions" in results
    assert len(results["solutions"]) > 0

def test_nsga2_solutions_respect_constraints():
    run_nsga2 = get_optimizer()
    results = run_nsga2(
        base_params={
            "wi": 14.0, "spi_kwh_t": 10.0, "f80_um": 3000.0,
            "r_inf": 0.90, "k_cil": 0.35, "srt_h": 24.0,
            "tph": 500.0, "op_hours_day": 24.0, "avail_pct": 92.0, "grade_g_t": 1.5,
        },
        n_pop=10, n_gen=5
    )
    for sol in results["solutions"]:
        p80 = sol["params"].get("p80_um", 75.0)
        srt = sol["params"].get("srt_h", 24.0)
        assert 50.0 <= p80 <= 200.0, f"p80 {p80} out of bounds"
        assert 12.0 <= srt <= 48.0, f"SRT {srt} out of bounds"

def test_nsga2_objectives_are_present():
    run_nsga2 = get_optimizer()
    results = run_nsga2(
        base_params={
            "wi": 14.0, "spi_kwh_t": 10.0, "f80_um": 3000.0,
            "r_inf": 0.90, "k_cil": 0.35, "srt_h": 24.0,
            "tph": 500.0, "op_hours_day": 24.0, "avail_pct": 92.0, "grade_g_t": 1.5,
        },
        n_pop=10, n_gen=5
    )
    for sol in results["solutions"]:
        objs = sol["objectives"]
        assert "recovery_pct" in objs
        assert "energy_kwh_t" in objs
