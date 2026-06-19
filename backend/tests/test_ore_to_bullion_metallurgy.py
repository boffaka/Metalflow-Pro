"""Metallurgical sanity checks for ore-to-bullion circuit engines."""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.no_db

from engines.ore_to_bullion.constants import bond_energy, flotation_recovery, leach_recovery
from engines.ore_to_bullion.models import CircuitConfig, FeedParameters
from engines.ore_to_bullion.orchestrator import run_simulation
from engines.ore_to_bullion.stream import Stream
from engines.ore_to_bullion.circuits.gravity import simulate_gravity
from engines.ore_to_bullion.circuits.leaching import simulate_leaching


def test_grinding_p80_um_accepts_mm_decimal_mistake():
    cc = CircuitConfig(grinding_target_p80_um=0.075)
    assert cc.grinding_target_p80_um == pytest.approx(75.0)


def test_bond_energy_increases_with_fineness():
    e_coarse = bond_energy(14.0, 150.0, 2000.0)
    e_fine = bond_energy(14.0, 75.0, 2000.0)
    assert e_fine > e_coarse > 0


def test_flotation_recovery_first_order():
    r = flotation_recovery(90.0, 1.5, 20.0)
    assert 0 < r < 90.0


def test_leach_recovery_matches_target_at_srt():
    target = 92.0
    srt = 24.0
    k = -math.log(1 - target / 100.0) / srt
    assert abs(leach_recovery(k, srt) - target) < 0.5


def test_gravity_recovery_bounded_and_mass_balance():
    stream = Stream.from_feed(tph=500.0, au=1.5, sg=2.75, pct_sol=35.0, p80=75.0)
    result = simulate_gravity(
        stream,
        {
            "grg_pct": 35.0,
            "gravity_slip_pct": 30.0,
            "gravity_knelson_recovery_pct": 50.0,
            "gravity_ilr_recovery_pct": 95.0,
            "ilr_recovery_pct": 95.0,
        },
    )
    mb = result["mass_balance"]
    rec = mb["gravity_recovery_pct"]
    assert rec == pytest.approx(4.9875, rel=1e-3)
    assert mb["tails_au_g_t"] < stream.au_g_t
    assert mb["tails_au_g_t"] == pytest.approx(
        stream.au_g_t * (1 - rec / 100.0), rel=1e-3
    )


def test_leaching_pregnant_solution_mg_l_reasonable():
    stream = Stream.from_feed(tph=500.0, au=1.5, sg=2.75, pct_sol=35.0, p80=75.0)
    result = simulate_leaching(
        stream,
        {
            "leaching_recovery_pct": 92.0,
            "leaching_srt_h": 24.0,
            "leaching_pct_solids": 45.0,
            "ore_sg": 2.75,
            "operating_hours_day": 22.1,
        },
    )
    mb = result["mass_balance"]
    assert 0.1 < mb["pregnant_solution_mg_l"] < 50.0
    assert mb["carbon_loading_g_t"] > 0


def test_orchestrator_recovery_matches_mass_balance():
    feed = FeedParameters(
        feed_rate_tph=500.0,
        gold_grade_g_t=1.5,
        bwi_kwh_t=14.0,
        target_recovery_pct=92.0,
    )
    config = CircuitConfig(
        gravity_enabled=True,
        flotation_enabled=False,
        leaching_recovery_pct=92.0,
    )
    result = run_simulation(feed, config)
    assert result.overall_recovery_pct > 85.0
    assert result.annual_gold_oz > 0
    leach = next(cr for cr in result.circuit_results if cr.circuit_name == "Leaching")
    adr = next(cr for cr in result.circuit_results if cr.circuit_name == "ADR")
    assert adr.mass_balance["carbon_loading_g_t"] == pytest.approx(
        leach.mass_balance["carbon_loading_g_t"], rel=0.01
    )
