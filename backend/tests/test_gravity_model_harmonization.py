"""Gravity model shared by mass_balance_engine and ore_to_bullion."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines import mass_balance_engine as mb
    from backend.engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params
    from backend.engines.ore_to_bullion.circuits.gravity import simulate_gravity
    from backend.engines.ore_to_bullion.stream import Stream
except ImportError:
    from engines import mass_balance_engine as mb
    from engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params
    from engines.ore_to_bullion.circuits.gravity import simulate_gravity
    from engines.ore_to_bullion.stream import Stream


def test_catalog_default_recovery_matches_formula():
    """GRAVITE_KNELSON defaults: GRG 35%, slip 30%, Knelson 50%, ILR 95%."""
    gp = resolve_gravity_params({
        "grg_pct": 35.0,
        "gravity_slip_pct": 30.0,
        "gravity_knelson_recovery_pct": 50.0,
        "gravity_ilr_recovery_pct": 95.0,
    })
    assert plant_gravity_recovery_pct(gp) == pytest.approx(4.9875, rel=1e-3)


def test_legacy_gravity_grg_recovery_pct_maps_to_knelson_not_ore_grg():
    gp = resolve_gravity_params({"gravity_grg_recovery_pct": 60.0, "gravity_slip_pct": 30.0})
    assert gp.knelson_unit_recovery_pct == 60.0
    assert gp.grg_pct == 35.0


def test_simulation_gravity_rec_alias():
    gp = resolve_gravity_params({
        "gravity_grg": 35.0,
        "gravity_slip": 30.0,
        "gravity_rec": 50.0,
        "gravity_ilr": 95.0,
    })
    assert gp.knelson_unit_recovery_pct == 50.0


def test_mass_balance_and_simulator_same_plant_recovery():
    dc = {
        "grg_pct": 35.0,
        "gravity_slip_pct": 30.0,
        "gravity_knelson_recovery_pct": 50.0,
        "gravity_ilr_recovery_pct": 95.0,
        "gravity_mass_pull_pct": 0.2,
        "gravity_flush_m3h": 2.0,
        "plant_h_per_d": 22.1,
    }
    pp = {"target_tph": 500.0, "ore_sg": 2.75, "gold_grade": 1.5}
    carry = {"bm_product_tph": 500.0, "bm_product_pct_sol": 35.0, "au_gt": 1.5}

    mb._gen_gravity_recovery(pp, dc, carry)

    stream = Stream.from_feed(tph=500.0, au=1.5, sg=2.75, pct_sol=35.0, p80=75.0)
    sim = simulate_gravity(stream, dc)
    gp = resolve_gravity_params(dc)
    expected = plant_gravity_recovery_pct(gp)

    assert sim["mass_balance"]["gravity_recovery_pct"] == pytest.approx(expected, abs=0.01)
    assert sim["mass_balance"]["tails_au_g_t"] == pytest.approx(
        1.5 * (1 - expected / 100.0), abs=1e-3
    )
