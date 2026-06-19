"""Industry-standard metallurgical recovery formulas."""
from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines.metallurgical_formulas import (
        ROUTE_GRG_TEST_TO_PLANT_FACTOR,
        bond_specific_energy_kwh_t,
        combined_gravity_leach_recovery_pct,
        concentrate_leach_recovery_pct,
        gravity_flotation_leach_recovery_pct,
        lims_grg_test_to_plant_gravity_pct,
        plant_gravity_from_grg_characterization_pct,
        resolve_route_gravity_recovery_pct,
        route_recovery_estimates,
    )
except ImportError:
    from engines.metallurgical_formulas import (
        ROUTE_GRG_TEST_TO_PLANT_FACTOR,
        bond_specific_energy_kwh_t,
        combined_gravity_leach_recovery_pct,
        concentrate_leach_recovery_pct,
        gravity_flotation_leach_recovery_pct,
        lims_grg_test_to_plant_gravity_pct,
        plant_gravity_from_grg_characterization_pct,
        resolve_route_gravity_recovery_pct,
        route_recovery_estimates,
    )


def test_combined_gravity_leach_standard_example():
    assert combined_gravity_leach_recovery_pct(20.0, 90.0) == pytest.approx(92.0)


def test_concentrate_leach_no_arbitrary_uplift():
    # 90.8 % flot × 96.5 % lix on concentrate = 87.62 % (not ×1.05)
    assert concentrate_leach_recovery_pct(90.8, 96.5) == pytest.approx(87.62, abs=0.02)


def test_gravity_flotation_leach_serial():
    # R_g=25, R_f=90, R_lix=96.5 → 25 + 0.75×0.90×96.5 = 90.13
    assert gravity_flotation_leach_recovery_pct(25.0, 90.0, 96.5) == pytest.approx(90.13, abs=0.05)


def test_lims_grg_test_scale_up():
    assert lims_grg_test_to_plant_gravity_pct(34.9, plant_factor=0.70) == pytest.approx(24.43, abs=0.02)


def test_plant_gravity_from_grg_content():
    # GRG 35 %, slip 30 %, Knelson auto 52.5 %, ILR 95 % → ~5.24 %
    assert plant_gravity_from_grg_characterization_pct(35.0) == pytest.approx(5.24, abs=0.05)


def test_bond_energy_typical():
    # Wi=14 kWh/t, F80=1000 µm, P80=75 µm (Bond standard units)
    e = bond_specific_energy_kwh_t(14.0, 1000.0, 75.0)
    assert e is not None
    assert 10.0 < e < 13.0


def test_route_estimates_golden_mine_profile():
    est = route_recovery_estimates(
        leach_whole_ore_pct=96.5,
        grg_lims_avg_pct=34.9,
        flotation_pct=90.8,
        plant_factor=ROUTE_GRG_TEST_TO_PLANT_FACTOR,
    )
    r_g = 34.9 * ROUTE_GRG_TEST_TO_PLANT_FACTOR
    assert est["gravity_plant_pct"] == pytest.approx(r_g, abs=0.1)
    assert est["direct"] == pytest.approx(96.5)
    assert est["grav_leach"] == pytest.approx(
        combined_gravity_leach_recovery_pct(r_g, 96.5), abs=0.05
    )
    assert est["flot_leach"] == pytest.approx(87.62, abs=0.05)
    assert est["grav_leach"] > est["direct"]


def test_resolve_route_uses_sim_when_available():
    r = resolve_route_gravity_recovery_pct(
        34.9,
        {
            "gravity_grg": 35.0,
            "gravity_slip": 30.0,
            "gravity_rec": 50.0,
            "gravity_ilr": 95.0,
        },
    )
    assert r == pytest.approx(4.99, abs=0.05)
