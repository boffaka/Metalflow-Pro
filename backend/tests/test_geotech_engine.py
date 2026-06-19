# backend/tests/test_geotech_engine.py
import math
import pytest

try:
    from engines.geotech import (
        bishop_factor_of_safety,
        tsf_volume_capacity,
        tsf_raise_height,
        compute_aba,
        classify_pag,
        classify_ard_risk,
    )
except ImportError:
    from backend.engines.geotech import (
        bishop_factor_of_safety,
        tsf_volume_capacity,
        tsf_raise_height,
        compute_aba,
        classify_pag,
        classify_ard_risk,
    )


def test_bishop_stable_slope():
    # Well-drained, gentle slope: FS should be > 1.5
    fs_static, fs_seismic = bishop_factor_of_safety(
        slope_angle_deg=30.0,
        slope_height_m=10.0,
        cohesion_kpa=20.0,
        friction_angle_deg=35.0,
        gamma_kn_m3=20.0,
        pore_pressure_ratio=0.0,
    )
    assert fs_static > 1.5
    assert fs_seismic < fs_static


def test_bishop_unstable_slope():
    # Steep slope with high pore pressure: FS should be < 1.3
    fs_static, _ = bishop_factor_of_safety(
        slope_angle_deg=60.0,
        slope_height_m=20.0,
        cohesion_kpa=5.0,
        friction_angle_deg=20.0,
        gamma_kn_m3=22.0,
        pore_pressure_ratio=0.4,
    )
    assert fs_static < 1.3


def test_bishop_compliance_flags():
    # FS >= 1.3 static = compliant
    fs_static, fs_seismic = bishop_factor_of_safety(
        slope_angle_deg=25.0,
        slope_height_m=8.0,
        cohesion_kpa=30.0,
        friction_angle_deg=38.0,
        gamma_kn_m3=19.0,
        pore_pressure_ratio=0.1,
    )
    assert fs_static >= 1.3  # must pass static requirement


def test_tsf_volume_capacity():
    # 1,000 t at density 1.3 t/m3 = ~769.23 m3
    vol = tsf_volume_capacity(total_tailings_t=1000.0, deposition_density_t_m3=1.3)
    assert abs(vol - 769.23) < 1.0


def test_tsf_raise_height():
    # volume / (area × 10000) = height in metres
    h = tsf_raise_height(annual_volume_m3=50000.0, embankment_area_ha=5.0)
    assert abs(h - 1.0) < 0.01


def test_compute_aba_values():
    # AP = sulfide_S% × 31.25; NNP = NP - AP; NPR = NP/AP
    ap, nnp, npr = compute_aba(sulfide_s_pct=2.0, np_kg_caco3_t=40.0)
    assert abs(ap - 62.5) < 0.01
    assert abs(nnp - (40.0 - 62.5)) < 0.01
    assert abs(npr - 40.0 / 62.5) < 0.001


def test_classify_pag_positive():
    assert classify_pag(nnp=-25.0, npr=0.5) == "PAG"


def test_classify_pag_uncertain():
    assert classify_pag(nnp=5.0, npr=1.5) == "Uncertain"


def test_classify_pag_non_pag():
    assert classify_pag(nnp=30.0, npr=3.0) == "Non-PAG"


def test_classify_ard_risk_critical():
    assert classify_ard_risk(pag_pct=80.0) == "Critical"


def test_classify_ard_risk_high():
    assert classify_ard_risk(pag_pct=55.0) == "High"


def test_classify_ard_risk_medium():
    assert classify_ard_risk(pag_pct=25.0) == "Medium"


def test_classify_ard_risk_low():
    assert classify_ard_risk(pag_pct=5.0) == "Low"
