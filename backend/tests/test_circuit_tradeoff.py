"""Tests for circuit trade-off comparison."""
import pytest

pytestmark = pytest.mark.no_db

from engines.circuit_tradeoff import compare_tradeoff_circuits


def test_compare_tradeoff_returns_five_candidates(monkeypatch):
    ore = {
        "grade_au": 1.05,
        "c_organic_pct": 0.02,
        "s_total_pct": 0.3,
        "as_ppm": 50,
        "bwi": 14.2,
        "grg_pct": 15.0,
        "leach_recovery_pct": 89.0,
        "nacn_kg_t": 0.5,
        "flot_recovery_pct": 0.0,
        "throughput_tph": 1596,
        "gold_price": 2340,
        "availability_pct": 92,
        "op_hours_day": 22,
        "mine_life_years": 14,
        "discount_rate_pct": 5,
        "bm_available": False,
    }

    def fake_strategy(pid, db_qall, db_qone):
        return {
            "scenario_source": {"mode": "template_active"},
            "tradeoff": {
                "evaluated_count": 5,
                "circuit_ids": ["X1", "X2", "X3", "X4", "X5"],
                "candidates": [{"id": "X1"}] * 5,
                "recommended": {"id": "X1"},
                "metallurgical_recommendation": {"circuit_id": "X1"},
            },
        }

    monkeypatch.setattr("engines.circuit_tradeoff.analyze_circuit_strategy", fake_strategy)

    result = compare_tradeoff_circuits("pid-1", lambda *a, **k: [], lambda *a, **k: None)
    assert result["evaluated_count"] == 5
    assert len(result["candidates"]) == 5
    assert result["recommended"] is not None
    assert result["metallurgical_recommendation"]["circuit_id"] == "X1"
    assert result["strategy_source"]["mode"] == "template_active"
