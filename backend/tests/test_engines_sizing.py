# backend/tests/test_engines_sizing.py
"""Unit tests for equipment sizing engine."""
import math, pytest

def get_sizing():
    try:
        from backend.engines.equipment_sizing import (
            size_ball_mill, size_flotation, size_thickener,
            size_cil_tanks, size_ew_cells, apply_lang_factors
        )
    except ImportError:
        from engines.equipment_sizing import (
            size_ball_mill, size_flotation, size_thickener,
            size_cil_tanks, size_ew_cells, apply_lang_factors
        )
    return size_ball_mill, size_flotation, size_thickener, size_cil_tanks, size_ew_cells, apply_lang_factors


def test_ball_mill_kokoya():
    """Kokoya: Wi=14, tph=1517, P80=75µm, F80=3000µm."""
    size_bm, *_ = get_sizing()
    result = size_bm(wi=14.0, tph=1517.0, p80_um=75.0, f80_um=3000.0)
    assert "power_kw" in result
    assert "diameter_m" in result
    assert result["power_kw"] > 10_000  # Kokoya scale plant needs >10 MW
    assert result["diameter_m"] > 3.0   # physically reasonable minimum

def test_ball_mill_power_scales_with_throughput():
    size_bm, *_ = get_sizing()
    r1 = size_bm(wi=14.0, tph=500.0, p80_um=75.0, f80_um=3000.0)
    r2 = size_bm(wi=14.0, tph=1000.0, p80_um=75.0, f80_um=3000.0)
    assert r2["power_kw"] > r1["power_kw"]

def test_flotation_cell_volume():
    _, size_fl, *_ = get_sizing()
    result = size_fl(q_m3h=400.0, srt_min=15.0, v_unit_m3=100.0)
    assert "n_cells" in result
    assert "v_total_m3" in result
    assert result["n_cells"] >= 1
    assert result["v_total_m3"] > 0

def test_thickener_diameter():
    _, _, size_th, *_ = get_sizing()
    result = size_th(tpd=3600.0, ua_m2_t_d=0.08, n_units=1)
    assert "diameter_m" in result
    assert 10.0 <= result["diameter_m"] <= 65.0

def test_cil_tanks():
    *_, size_cil, _, _ = get_sizing()
    result = size_cil(q_m3h=600.0, srt_h=24.0, n_tanks=8)
    assert "v_per_tank_m3" in result
    assert "d_tank_m" in result
    assert result["n_tanks"] == 8

def test_ew_cells():
    *_, size_ew, _ = get_sizing()
    result = size_ew(oz_per_day=500.0, j_cath_a_m2=200.0, a_cath_m2=1.0, cathodes_per_cell=30)
    assert "n_cells" in result
    assert result["n_cells"] >= 1

def test_lang_factors_increase_equipment_cost():
    *_, lang = get_sizing()
    equipment_cost = 1_000_000.0
    result = lang(equipment_cost)
    assert isinstance(result, dict)
    assert "total_capex_usd" in result
    assert "tic_usd" in result
    assert "lang_factor" in result
    total = result["total_capex_usd"]
    assert total > equipment_cost * 1.5  # Lang factor always increases cost significantly


def test_sag_mill_sizing():
    try:
        from backend.engines.equipment_sizing import size_sag_mill
    except ImportError:
        from engines.equipment_sizing import size_sag_mill
    result = size_sag_mill(spi_kwh_t=10.0, tph=1517.0)
    assert "power_kw" in result
    assert "diameter_m" in result
    assert result["power_kw"] == 1517.0 * 10.0  # P = SPI × tph
