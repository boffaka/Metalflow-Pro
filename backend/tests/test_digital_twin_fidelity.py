# backend/tests/test_digital_twin_fidelity.py
"""Unit tests for digital-twin fidelity index (pure logic, no DB)."""
import pytest

pytestmark = pytest.mark.no_db

try:
    from routes.simulation_innovations import compute_twin_fidelity_from_snapshot
except ImportError:
    from backend.routes.simulation_innovations import compute_twin_fidelity_from_snapshot


def test_fidelity_empty_project_low_score():
    s = {
        "has_flowsheet": False,
        "node_count": 0,
        "has_bullion": False,
        "nodes_missing_lims": 0,
        "nodes_unlinked_equipment": 0,
        "last_run_id": None,
        "last_run_status": None,
        "last_run_has_results": False,
        "lims_sample_count": 0,
        "node_outputs_count": 0,
    }
    r = compute_twin_fidelity_from_snapshot(s)
    assert r["score"] < 50
    assert r["grade"] in ("C", "D")
    assert len(r["factors"]) == 5


def test_fidelity_mature_project_high_score():
    s = {
        "has_flowsheet": True,
        "node_count": 12,
        "has_bullion": True,
        "nodes_missing_lims": 0,
        "nodes_unlinked_equipment": 0,
        "last_run_id": "uuid",
        "last_run_status": "completed",
        "last_run_has_results": True,
        "lims_sample_count": 40,
        "node_outputs_count": 30,
    }
    r = compute_twin_fidelity_from_snapshot(s)
    assert r["score"] >= 85
    assert r["grade"] == "A"
