"""Unit tests for Pareto comparison helpers."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

from backend.compute.pareto_compare import compare_pareto_results, pareto_metrics


def test_pareto_metrics_empty():
    assert pareto_metrics([])["count"] == 0


def test_compare_two_fronts():
    ra = {
        "pareto_front": [
            {"expected_recovery": 90.0, "expected_energy": 12.0, "p80": 75},
            {"expected_recovery": 88.0, "expected_energy": 10.0, "p80": 80},
        ]
    }
    rb = {
        "pareto_front": [
            {"expected_recovery": 91.0, "expected_energy": 11.0, "p80": 76},
        ]
    }
    out = compare_pareto_results(ra, rb)
    assert out["pareto_a"]["count"] == 2
    assert out["pareto_b"]["count"] == 1
    assert "points_a_not_dominated_by_b" in out
