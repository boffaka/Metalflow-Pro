from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines.dc_formulas import (
        bond_energy_kwh_t,
        corrected_bond_energy_kwh_t,
        hpgr_peripheral_speed_m_s,
        hpgr_roll_force_kn,
        hpgr_roll_surface_m2,
        hpgr_total_roll_tph,
        hpgr_unit_capacity_tph,
        installed_power_kw,
        mill_design_tph,
        rowland_ef4,
        rowland_ef5,
        rowland_f80_opt_um,
        screen_area_m2,
        shaft_power_kw,
        slurry_density_t_m3,
        slurry_volume_m3h,
        tank_diameter_m,
    )
except ImportError:  # pragma: no cover
    from engines.dc_formulas import (  # type: ignore[no-redef]
        bond_energy_kwh_t,
        corrected_bond_energy_kwh_t,
        hpgr_peripheral_speed_m_s,
        hpgr_roll_force_kn,
        hpgr_roll_surface_m2,
        hpgr_total_roll_tph,
        hpgr_unit_capacity_tph,
        installed_power_kw,
        mill_design_tph,
        rowland_ef4,
        rowland_ef5,
        rowland_f80_opt_um,
        screen_area_m2,
        shaft_power_kw,
        slurry_density_t_m3,
        slurry_volume_m3h,
        tank_diameter_m,
    )


def test_reference_general_design_throughput_formulas_are_project_independent():
    assert mill_design_tph(1517, 15) == pytest.approx(1744.55)


def test_reference_crushing_bond_and_installed_power_formula():
    energy = bond_energy_kwh_t(14, 600_000, 150_000)
    shaft = shaft_power_kw(energy, 2139.1813)
    installed = installed_power_kw(shaft, 93, 30)

    assert energy == pytest.approx(0.1807, abs=0.0001)
    assert shaft == pytest.approx(386.6, abs=0.2)
    assert installed == pytest.approx(540.4, abs=0.3)


def test_reference_ball_mill_rowland_chain_uses_hpgr_product_factor():
    hpgr_p80_um = 6000
    f80_um = hpgr_p80_um * 0.75
    p80_um = 150
    bwi = 18

    f80_opt = rowland_f80_opt_um(bwi)
    ef4 = rowland_ef4(bwi, f80_um, p80_um)
    ef5 = rowland_ef5(p80_um)
    corrected = corrected_bond_energy_kwh_t(bwi, f80_um, p80_um, 1, 1, 1, ef4, ef5, 1, 1, 1)

    assert f80_um == 4500
    assert f80_opt == pytest.approx(3399.3, abs=0.1)
    assert ef4 == pytest.approx(1.1187, abs=0.0001)
    assert ef5 == 1.0
    assert corrected == pytest.approx(13.4399, abs=0.0001)


def test_reference_hpgr_capacity_and_force_formulas():
    speed = hpgr_peripheral_speed_m_s(2.4, 18)
    capacity = hpgr_unit_capacity_tph(250, 2.4, 1.7, speed)

    assert hpgr_total_roll_tph(1744.55, 25) == pytest.approx(2180.6875)
    assert hpgr_roll_surface_m2(2.4, 1.7) == pytest.approx(4.08)
    assert speed == pytest.approx(2.2619, abs=0.0001)
    assert capacity == pytest.approx(2307.1, abs=0.1)
    assert hpgr_roll_force_kn(4.5, 2.4, 1.7) == pytest.approx(18360)


def test_reference_classification_and_cil_slurry_formulas():
    cyclone_feed_tph = 1744.55 * (1 + 300 / 100)
    qv_cyclone = slurry_volume_m3h(cyclone_feed_tph, 2.75, 65)
    density = slurry_density_t_m3(2.75, 65)

    assert qv_cyclone == pytest.approx(cyclone_feed_tph / 2.75 + cyclone_feed_tph * 35 / 65)
    assert density == pytest.approx(1 / (0.65 / 2.75 + 0.35), abs=0.0001)

    cil_qv = slurry_volume_m3h(1744.55, 2.75, 45)
    cil_volume = cil_qv * 24
    cil_unit_volume = cil_volume * 1.20 / 8
    assert tank_diameter_m(cil_unit_volume, 14) == pytest.approx(30.1, abs=0.1)


def test_reference_screen_area_formula():
    assert screen_area_m2(1283.5, 40, 0.9, 0.8, 1) == pytest.approx(44.57, abs=0.01)
