"""Project analysis API."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.no_db

try:
    import backend.routes.analysis as analysis_mod
except ImportError:
    import routes.analysis as analysis_mod

get_project_analysis = analysis_mod.get_project_analysis
_leach_avg = analysis_mod._leach_avg
_grg_avg = analysis_mod._grg_avg


def test_leach_avg_prefers_48h():
    tests = {"d1": [{"leach_rec_48h_pct": 96.5, "leach_rec_24h_pct": 80.0}]}
    assert _leach_avg(tests) == pytest.approx(96.5)


def test_grg_avg_from_c2():
    tests = {"c2": [{"grg_rec_pct": 34.9}]}
    assert _grg_avg(tests) == pytest.approx(34.9)


@patch.object(analysis_mod, "_load_lims_tests")
def test_get_project_analysis_shape(mock_load):
    mock_load.return_value = {
        "a1": [{"au_g_t": 0.67}],
        "b1": [{"bwi_kwh_t": 16.3}],
        "c2": [{"grg_rec_pct": 34.9}],
        "d1": [{"leach_rec_48h_pct": 96.5}],
        "g1": [{"au_recovery_pct": 90.8}],
    }
    out = get_project_analysis("proj-1", user=MagicMock())
    assert out["project_id"] == "proj-1"
    assert len(out["b1"]) == 1
    assert out["route_metallurgique"]["direct"] == pytest.approx(96.5)
    assert "formula_references" in out
