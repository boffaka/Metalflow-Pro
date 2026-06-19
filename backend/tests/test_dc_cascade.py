"""Tests for DC cascade recalculation engine."""
import math
import pytest

pytestmark = pytest.mark.no_db

try:
    from engines.dc_cascade import (
        load_dag,
        topological_sort,
        get_downstream_nodes,
        compute_formula,
        cascade_recalculate,
    )
except ImportError:
    from backend.engines.dc_cascade import (
        load_dag,
        topological_sort,
        get_downstream_nodes,
        compute_formula,
        cascade_recalculate,
    )


def test_load_dag():
    dag = load_dag()
    assert "nodes" in dag
    assert "inputs" in dag
    assert len(dag["nodes"]) >= 20


def test_topological_sort():
    dag = load_dag()
    order = topological_sort(dag)
    # Every node should appear exactly once
    assert len(order) == len(dag["nodes"])
    assert len(set(order)) == len(order)
    # Every dependency should come before the dependent node
    idx = {k: i for i, k in enumerate(order)}
    for key, node in dag["nodes"].items():
        for dep in node["depends_on"]:
            if dep in idx:  # skip inputs
                assert idx[dep] < idx[key], f"{dep} should come before {key}"


def test_get_downstream_nodes():
    dag = load_dag()
    # Changing target_tph should cascade to many nodes
    downstream = get_downstream_nodes(dag, ["target_tph"])
    assert "leach_feed_tph" in downstream
    assert "bm_power_kw" in downstream
    assert "annual_gold_oz" in downstream


def test_compute_bond_power():
    result = compute_formula("bond_power_bm", {
        "target_tph": 1596,
        "avg_bwi": 14.2,
        "bm_f80_um": 2000,
        "avg_p80_um": 75,
        "mech_efficiency": 0.85,
    })
    # Bond: W = 10 * Wi * (1/sqrt(P80) - 1/sqrt(F80))
    # W = 10 * 14.2 * (1/sqrt(75) - 1/sqrt(2000)) = 10 * 14.2 * (0.1155 - 0.02236) ≈ 13.22 kWh/t
    # Power = W * tph / eff = 13.22 * 1596 / 0.85 ≈ 24,834 kW
    assert 20_000 < result < 30_000


def test_compute_bond_power_accepts_percent_efficiency_and_install_margin():
    """Ball Mill installed power follows Bond/Rowland units used in the PDC.

    The design criteria catalog stores motor efficiency as a percent (95), not
    as a fraction (0.95). Installed motor power must therefore normalize the
    percent and include the installation margin used by mining PFS/FS criteria.
    """
    result = compute_formula("bond_power_bm", {
        "target_tph": 1596,
        "avg_bwi": 14.2,
        "bm_f80_um": 2000,
        "avg_p80_um": 75,
        "mech_efficiency": 95,
        "bm_install_margin_pct": 10,
    })
    assert 24_000 < result < 25_000


def test_compute_leach_feed_with_flotation():
    result = compute_formula("leach_feed", {
        "target_tph": 1596,
        "flot_mass_pull_pct": 17.0,
        "has_flotation": True,
    })
    assert abs(result - 271.3) < 1.0


def test_compute_leach_feed_without_flotation():
    result = compute_formula("leach_feed", {
        "target_tph": 1596,
        "flot_mass_pull_pct": 17.0,
        "has_flotation": False,
    })
    assert result == 1596.0


def test_compute_slurry_density():
    result = compute_formula("slurry_density", {
        "ore_sg": 2.75,
        "cil_pct_solids": 45.0,
    })
    # SG = 1 / ((0.45/2.75) + (0.55/1.0)) = 1 / (0.1636 + 0.55) = 1.401
    assert 1.3 < result < 1.5


def test_compute_annual_production():
    result = compute_formula("annual_production", {
        "target_tph": 1596,
        "gold_grade_g_t": 1.5,
        "avg_au_recovery_pct": 89.0,
        "availability_pct": 92.0,
        "operating_hours_day": 24.0,
    })
    # annual_t = 1596 * 24 * 365 * 0.92 = 12,846,067 t/y
    # gold = 12,846,067 * 1.5 * 0.89 = 17,149,489 g
    # oz = 17,149,489 / 31.1035 = 551,428 oz
    assert 500_000 < result < 600_000


def test_cascade_recalculate_simple():
    """Change target_tph and verify downstream values update."""
    dag = load_dag()
    current_values = {
        "target_tph": 1596,
        "avg_bwi": 14.2,
        "sag_p80_mm": 2.0,
        "avg_p80_um": 75,
        "mech_efficiency": 0.85,
        "flot_mass_pull_pct": 17.0,
        "has_flotation": True,
        "ore_sg": 2.75,
        "cil_pct_solids": 45.0,
        "cil_srt_h": 24.0,
        "max_vol_per_tank": 1200.0,
        "cil_hd_ratio": 1.0,
        "pc_css_mm": 140.0,
        "sc_css_mm": 35.0,
        "bm_circ_load_pct": 250.0,
        "gold_grade_g_t": 1.5,
        "avg_au_recovery_pct": 89.0,
        "availability_pct": 92.0,
        "operating_hours_day": 24.0,
        "avg_nacn_kg_t": 0.5,
        "avg_cao_kg_t": 1.2,
        "avg_unit_area": 0.08,
        "thickener_safety_factor": 1.15,
        "thickener_max_diameter_m": 45.0,
        "underflow_pct_solids": 55.0,
        "evap_factor": 0.015,
        "energy_rate_usd_kwh": 0.08,
    }
    # Source map: which values are Manual (should not be overwritten)
    source_map = {k: "input" for k in current_values}

    updates, alerts = cascade_recalculate(
        dag=dag,
        current_values=current_values,
        source_map=source_map,
        changes=[{"key": "target_tph", "value": 1700}],
    )
    # leach_feed_tph should change: 1700 * 0.17 = 289
    leach_update = next((u for u in updates if u["key"] == "leach_feed_tph"), None)
    assert leach_update is not None
    assert abs(leach_update["new"] - 289.0) < 1.0
