# backend/tests/test_engines_flotation.py
"""Unit tests for flotation engine."""
import math

import pytest


def get_engine():
    try:
        from backend.engines.flotation import flotation_recovery, mass_pull, concentrate_grade
    except ImportError:
        from engines.flotation import flotation_recovery, mass_pull, concentrate_grade
    return flotation_recovery, mass_pull, concentrate_grade


def test_recovery_typical_sulphide_gold():
    rec, *_ = get_engine()
    R = rec(r_max=0.95, k=0.8, tau_min=15.0)
    assert R > 0.85


def test_recovery_formula_first_order():
    rec, *_ = get_engine()
    r_max, k, tau = 0.90, 0.5, 20.0
    expected = r_max * (1.0 - math.exp(-k * tau))
    result = rec(r_max=r_max, k=k, tau_min=tau)
    assert abs(result - expected) < 1e-9


def test_recovery_at_zero_retention_time():
    rec, *_ = get_engine()
    R = rec(r_max=0.95, k=0.8, tau_min=0.0)
    assert abs(R) < 1e-9


def test_mass_pull_returns_positive():
    _, mp, _ = get_engine()
    mp_pct = mp(collector_g_t=30.0, frother_g_t=15.0, air_flow_factor=1.0)
    assert mp_pct > 0


def test_concentrate_grade():
    *_, cg = get_engine()
    grade = cg(feed_grade_g_t=2.0, recovery=0.90, mass_pull_pct=5.0)
    assert grade > 2.0


def test_concentrate_grade_accepts_recovery_percent():
    *_, cg = get_engine()
    g_frac = cg(feed_grade_g_t=2.0, recovery=0.90, mass_pull_pct=5.0)
    g_pct = cg(feed_grade_g_t=2.0, recovery=90.0, mass_pull_pct=5.0)
    assert abs(g_frac - g_pct) < 1e-6


def test_flotation_rmax_as_percent_matches_fraction():
    rec, *_ = get_engine()
    a = rec(r_max=0.88, k=0.5, tau_min=10.0)
    b = rec(r_max=88.0, k=0.5, tau_min=10.0)
    assert abs(a - b) < 1e-9
