# backend/tests/test_engines_comminution.py
"""Unit tests for comminution engine — Bond energy law, SAG, HPGR."""
import math, pytest

def get_engine():
    try:
        from backend.engines.comminution import (
            bond_ball_mill_energy, sag_mill_power, hpgr_specific_energy,
            total_comminution_energy
        )
    except ImportError:
        from engines.comminution import (
            bond_ball_mill_energy, sag_mill_power, hpgr_specific_energy,
            total_comminution_energy
        )
    return bond_ball_mill_energy, sag_mill_power, hpgr_specific_energy, total_comminution_energy

def test_bond_energy_typical_gold_plant():
    bond, *_ = get_engine()
    W = bond(wi=14.0, p80_um=75.0, f80_um=3000.0)
    assert 10.0 <= W <= 25.0, f"Bond energy {W} kWh/t out of typical range"

def test_bond_energy_formula():
    bond, *_ = get_engine()
    wi, p80, f80 = 14.0, 100.0, 4000.0
    expected = wi * 10.0 * (1.0 / math.sqrt(p80) - 1.0 / math.sqrt(f80))
    result = bond(wi=wi, p80_um=p80, f80_um=f80)
    assert abs(result - expected) < 0.001

def test_bond_energy_rejects_zero_p80():
    bond, *_ = get_engine()
    with pytest.raises((ValueError, ZeroDivisionError)):
        bond(wi=14.0, p80_um=0.0, f80_um=3000.0)

def test_sag_power_returns_positive():
    _, sag, *_ = get_engine()
    P = sag(spi_kwh_t=10.0, tph=500.0)
    assert P > 0

def test_sag_power_scales_with_throughput():
    _, sag, *_ = get_engine()
    P1 = sag(spi_kwh_t=10.0, tph=500.0)
    P2 = sag(spi_kwh_t=10.0, tph=1000.0)
    assert P2 > P1

def test_hpgr_energy_typical_range():
    *_, hpgr, _ = get_engine()
    Ecs = hpgr(mih_kwh_t=4.5, f80_um=25000.0, p80_um=3000.0)
    assert 1.0 <= Ecs <= 15.0, f"HPGR Ecs {Ecs} out of typical range"

def test_total_energy_sums_components():
    *_, total = get_engine()
    result = total(e_sag=5000.0, e_bm=3000.0, e_hpgr=0.0, e_isamill=0.0, e_aux=500.0)
    assert abs(result - 8500.0) < 0.01
