"""POX/BIOX, grinding loop, O2B compare bridge, compile GRG warnings (no DB)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

from engines.op_model_registry import resolve_op_model, is_refractory_pretreatment
from engines.process_simulator import (
    _make_stream,
    _simulate_ball_cyclone_loop,
    _sim_refractory_pretreatment,
)
from engines.simulation_bridge import (
    compare_rigorous_with_o2b,
    gravity_grg_warning_if_missing,
    build_o2b_inputs,
)


def test_refractory_ops_map_to_pretreatment_model():
    for op in ("BIOX", "POX", "ROASTING", "UFG"):
        assert resolve_op_model(op) == "refractory_pretreatment"
        assert is_refractory_pretreatment(op)


def test_refractory_pretreatment_emits_metallurgical_alert():
    stream = _make_stream(1000.0, 1.5, 65.0, 5000.0)
    warnings: list[str] = []
    result = _sim_refractory_pretreatment(stream, {}, {}, {}, warnings, op_code="POX")
    assert result["model"] == "refractory_passthrough"
    assert result["product_stream"]["solids_tph"] == 1000.0
    assert any("POX" in w for w in warnings)
    assert "metallurgical_alert" in result


def test_ball_cyclone_loop_converges_recirculation():
    feed = _make_stream(500.0, 2.0, 65.0, 3000.0)
    warnings: list[str] = []
    product, ops, energy, _, _ = _simulate_ball_cyclone_loop(
        {"op_code": "BALL_MILL", "label": "BM"},
        {"op_code": "HYDROCYCLONE", "label": "CY"},
        feed,
        {},
        {},
        {"circ_load_pct": 250.0},
        warnings,
    )
    assert len(ops) == 2
    assert ops[0]["op_code"] == "BALL_MILL"
    assert ops[1]["performance"]["inner_iterations"] >= 1
    assert ops[1]["performance"]["recirculation_tph"] > 0
    assert product["solids_tph"] < feed["solids_tph"]
    assert energy > 0


def test_gravity_grg_warning_when_missing():
    warns = gravity_grg_warning_if_missing(
        ["GRAVITE_KNELSON", "CIL"], sim_has_grg=False, dc_has_grg=False,
    )
    assert len(warns) == 1
    assert warns[0]["code"] == "GRAVITY_GRG_MISSING"

    assert gravity_grg_warning_if_missing(["CIL"], False, False) == []
    assert gravity_grg_warning_if_missing(["GRAVITE_KNELSON"], True, False) == []


def test_build_o2b_inputs_from_template_ops():
    feed, config = build_o2b_inputs(
        "proj",
        "tpl",
        ["HPGR", "BALL_MILL", "HYDROCYCLONE", "GRAVITE_KNELSON", "CIL"],
        {"target_tph": 1200.0, "gold_grade_g_t": 1.8},
        {"gravity_grg": 40.0, "gravity_slip": 35.0, "gravity_rec": 55.0},
        {},
    )
    assert feed.feed_rate_tph == 1200.0
    assert config.gravity_enabled is True
    assert config.grg_pct == 40.0
    assert config.grinding_type == "hpgr_ball"
    assert config.leaching_type == "cil"


def test_compare_rigorous_with_o2b_delta():
    class _FakeO2B:
        overall_recovery_pct = 90.0
        annual_gold_oz = 100000.0
        total_energy_kwh_t = 12.0

    cmp = compare_rigorous_with_o2b(
        {"overall": {"total_recovery_pct": 88.0, "annual_gold_oz": 95000.0, "total_energy_kwh_t": 11.0}},
        _FakeO2B(),
    )
    assert cmp["recovery_pct"]["delta_pct"] is not None
    assert cmp["recovery_pct"]["process_simulator"] == 88.0
