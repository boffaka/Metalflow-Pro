# backend/tests/test_engines_leaching.py
"""Unit tests for Leach (LIMS D1) kinetic leaching engine."""
import math, pytest

def get_engine():
    try:
        from backend.engines.leaching import (
            cil_recovery, fit_kinetic_params, pregnant_solution_grade, annual_gold_oz
        )
    except ImportError:
        from engines.leaching import (
            cil_recovery, fit_kinetic_params, pregnant_solution_grade, annual_gold_oz
        )
    return cil_recovery, fit_kinetic_params, pregnant_solution_grade, annual_gold_oz

def test_cil_recovery_at_infinite_time_approaches_r_inf():
    cil_recovery, *_ = get_engine()
    R = cil_recovery(r_inf=0.92, k=0.5, srt_h=48.0)
    assert R > 0.90

def test_cil_recovery_at_zero_time_is_zero():
    cil_recovery, *_ = get_engine()
    R = cil_recovery(r_inf=0.92, k=0.5, srt_h=0.0)
    assert abs(R) < 0.001

def test_cil_recovery_never_exceeds_r_inf():
    cil_recovery, *_ = get_engine()
    for srt in [1, 6, 12, 24, 48, 96]:
        R = cil_recovery(r_inf=0.88, k=0.3, srt_h=float(srt))
        assert R <= 0.88 + 1e-9, f"Recovery {R} exceeds R∞ at SRT={srt}h"

def test_cil_recovery_formula():
    cil_recovery, *_ = get_engine()
    r_inf, k, srt = 0.90, 0.4, 24.0
    expected = r_inf * (1.0 - math.exp(-k * srt))
    result = cil_recovery(r_inf=r_inf, k=k, srt_h=srt)
    assert abs(result - expected) < 1e-9

def test_fit_kinetic_params_returns_k_and_r_inf():
    _, fit, *_ = get_engine()
    times = [1.0, 6.0, 12.0, 24.0, 48.0]
    recoveries = [0.90 * (1.0 - math.exp(-0.3 * t)) for t in times]
    k, r_inf = fit(times_h=times, recoveries=recoveries)
    assert abs(k - 0.3) < 0.05
    assert abs(r_inf - 0.90) < 0.05

def test_pregnant_solution_grade_formula():
    *_, psg, _ = get_engine()
    grade = psg(feed_grade_g_t=2.0, tph=100.0, recovery=0.90)
    assert grade > 0
    assert abs(psg(2.0, 100.0, 90.0) - grade) < 1e-6


def test_cil_recovery_accepts_r_inf_percent():
    cil_recovery, *_ = get_engine()
    a = cil_recovery(r_inf=0.92, k=0.5, srt_h=48.0)
    b = cil_recovery(r_inf=92.0, k=0.5, srt_h=48.0)
    assert abs(a - b) < 1e-9

def test_annual_gold_oz_kokoya():
    *_, annual = get_engine()
    # Kokoya ~3.3 Mtpa at 92% availability => ~409 tph; 1.5 g/t Au, 90% Leach recovery
    oz = annual(tph=409.0, op_hours_day=24.0, avail_pct=92.0, grade_g_t=1.5, recovery=0.90)
    assert 130_000 <= oz <= 200_000, f"Annual oz {oz:,.0f} outside expected range"
