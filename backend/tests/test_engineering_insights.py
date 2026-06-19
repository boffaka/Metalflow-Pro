"""Tests for engineering readiness & digital-twin fidelity heuristics."""
from __future__ import annotations

import pytest
from unittest.mock import patch

pytestmark = pytest.mark.no_db

try:
    from services.engineering_insights import compute_digital_twin_fidelity, compute_engineering_readiness
except ImportError:  # pragma: no cover
    from backend.services.engineering_insights import (
        compute_digital_twin_fidelity,
        compute_engineering_readiness,
    )


def _mock_qone_sequence(rows: list[dict | None]):
    """Return a callable that cycles through rows for each qone call."""
    it = iter(rows)

    def _qone(_sql, _params=None):
        try:
            return next(it)
        except StopIteration:
            return None

    return _qone


@patch("services.engineering_insights._regclass_exists", return_value=True)
@patch("services.engineering_insights.qone")
def test_readiness_perfect_score(mock_qone, _mock_reg):
    """Toutes les cibles « plein score » atteintes → 100."""
    mock_qone.side_effect = _mock_qone_sequence(
        [
            {"id": "tpl-1"},
            {"n": 12},
            {"n": 6},
            {"n": 25},
            {"n": 30},
            {"n": 20, "n_recent": 20},
            {"ok": 1},
        ]
    )
    out = compute_engineering_readiness("p1")
    assert out["score"] == 100
    assert out["earned"] == out["possible"] == 100.0
    assert not out["missing_gate_ids"]
    assert out["weights_version"]
    assert all("fraction" in g for g in out["gates"])


@patch("services.engineering_insights._regclass_exists", return_value=True)
@patch("services.engineering_insights.qone")
def test_readiness_empty_project(mock_qone, _mock_reg):
    mock_qone.side_effect = _mock_qone_sequence(
        [
            None,
            {"n": 0},
            {"n": 0},
            {"n": 0},
            {"n": 0, "n_recent": 0},
            None,
        ]
    )
    out = compute_engineering_readiness("p1")
    assert out["score"] == 0
    assert len(out["missing_gate_ids"]) >= 3


@patch("services.engineering_insights._regclass_exists", return_value=True)
@patch("services.engineering_insights.qone")
def test_fidelity_weighted_includes_lims(mock_qone, _mock_reg):
    mock_qone.side_effect = _mock_qone_sequence(
        [
            {"n": 5},
            {"n": 10},
            {"n": 4, "nk": 2},
            {"n": 1},
            {"n": 40},
        ]
    )
    out = compute_digital_twin_fidelity("p1")
    assert out["kind"] == "digital_twin_fidelity"
    assert 0 <= out["score"] <= 100
    assert "lims_test_chain" in out["components"]
    assert abs(sum(out["weights"].values()) - 1.0) < 1e-6
