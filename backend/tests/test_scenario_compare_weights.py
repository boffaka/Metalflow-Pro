"""Scenario comparison weight normalization."""
import pytest

pytestmark = pytest.mark.no_db

try:
    from routes.scenarios import _merge_compare_weights, _SCENARIO_EVAL_WEIGHTS, _evaluation_brief_markdown
except ImportError:
    from backend.routes.scenarios import _merge_compare_weights, _SCENARIO_EVAL_WEIGHTS, _evaluation_brief_markdown


def test_merge_compare_weights_normalizes():
    w = _merge_compare_weights({"recovery": 1.0, "energy": 1.0})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["recovery"] > w["environment"]


def test_merge_compare_weights_includes_economic_default():
    w = _merge_compare_weights({})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["economic"] == pytest.approx(_SCENARIO_EVAL_WEIGHTS["economic"])


def test_merge_compare_weights_all_zero_returns_defaults():
    w = _merge_compare_weights({k: 0.0 for k in _SCENARIO_EVAL_WEIGHTS})
    for k in _SCENARIO_EVAL_WEIGHTS:
        assert w[k] == pytest.approx(_SCENARIO_EVAL_WEIGHTS[k])


def test_evaluation_brief_markdown_empty():
    md = _evaluation_brief_markdown("p1", [])
    assert "p1" in md
    assert "Aucun scénario" in md


def test_evaluation_brief_markdown_with_rows():
    rows = [
        {
            "id": "s1",
            "scenario_name": "Base",
            "scenario_id": "s1",
            "overall_score": 72.5,
            "recovery_pct": 91.0,
            "economic_score": 60.0,
            "results_json": {
                "score_breakdown": {"weighted_points": {"recovery": 30}},
                "evaluation_insights": ["Ajuster le broyage"],
            },
        }
    ]
    md = _evaluation_brief_markdown("proj-x", rows)
    assert "Base" in md
    assert "72.5" in md
    assert "recovery" in md
    assert "broyage" in md
