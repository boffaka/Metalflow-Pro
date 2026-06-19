"""Tests for unified circuit strategy engine."""
import pytest

pytestmark = pytest.mark.no_db

from engines.circuit_strategy import analyze_circuit_strategy


def _ore():
    return {
        "grade_au": 1.5,
        "c_organic_pct": 0.05,
        "s_total_pct": 1.1,
        "as_ppm": 80,
        "bwi": 16.5,
        "grg_pct": 18.0,
        "leach_recovery_pct": 88.0,
        "nacn_kg_t": 0.7,
        "flot_recovery_pct": 72.0,
        "throughput_tph": 950,
        "gold_price": 2300,
        "availability_pct": 92,
        "op_hours_day": 22,
        "mine_life_years": 12,
        "discount_rate_pct": 5,
        "bm_available": False,
        "dc_available": True,
        "economics_available": True,
    }


def test_strategy_prefers_active_template(monkeypatch):
    monkeypatch.setattr("engines.circuit_strategy.extract_ore_profile", lambda *_: _ore())
    monkeypatch.setattr(
        "engines.circuit_strategy.recommend_cip_cil_from_lims",
        lambda *_: {"circuit_type": "CIP", "score": 0, "confidence": "high", "reasons": []},
    )

    def fake_qone(sql, args):
        if "is_active=true" in sql:
            return {"id": "tpl-1", "name": "Template Principal"}
        return {"id": "tpl-1", "name": "Template Principal"}

    def fake_qall(sql, args):
        if "FROM circuit_operations" in sql:
            return [{"op_code": "HPGR"}, {"op_code": "BALL_MILL"}, {"op_code": "FLOTATION"}, {"op_code": "CIP"}]
        return []

    out = analyze_circuit_strategy("pid-1", fake_qall, fake_qone)
    assert out["scenario_source"]["mode"] == "template_active"
    assert out["scenario_source"]["template_id"] == "tpl-1"
    assert len(out["tradeoff"]["candidates"]) == 5
    assert out["recommendation"]["evaluated_count"] == 4
    assert out["tradeoff"]["project_id"] == "pid-1"


def test_strategy_fallback_without_template(monkeypatch):
    monkeypatch.setattr("engines.circuit_strategy.extract_ore_profile", lambda *_: _ore())
    monkeypatch.setattr("engines.circuit_strategy.recommend_cip_cil_from_lims", lambda *_: None)

    out = analyze_circuit_strategy(
        "pid-2",
        lambda *_: [],
        lambda *_: None,
    )
    assert out["scenario_source"]["mode"] == "library_fallback"
    assert out["scenario_source"]["template_id"] is None
    assert len(out["tradeoff"]["candidates"]) == 5
    assert out["recommendation"]["recommended"] is not None
