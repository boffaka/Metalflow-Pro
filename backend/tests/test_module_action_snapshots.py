"""API tests for module save/delete snapshots (circuit strategy, simulation, GMIE)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.no_db


def test_monolithic_action_bar_config_for_target_modules():
    html = (Path(__file__).resolve().parents[1] / "MetalFlowPro_v3_1.html").read_text(encoding="utf-8")
    assert 'save: "csSaveSnapshot()"' in html
    assert 'save: "mdSaveWorkspace()"' in html
    assert 'save: "gmieSaveSnapshot()"' in html
    assert "function mdSaveWorkspace()" in html
    assert "function gmieSaveSnapshot()" in html
    assert 'cfg.delete' in html or "cfg.delete)" in html


@patch("backend.routes.circuit_optimizer.execute")
@patch("backend.routes.circuit_optimizer.qone")
def test_put_strategy_snapshot(mock_qone, mock_execute):
    from backend.routes.circuit_optimizer import put_strategy_snapshot

    mock_qone.return_value = None
    mock_execute.return_value = {}
    user = {"id": "u1"}
    out = put_strategy_snapshot("pid-1", {"recommendation": {"recommended": "cil"}}, user=user)
    assert out["ok"] is True
    assert "saved_at" in out
    mock_execute.assert_called_once()


@patch("backend.routes.metallurgical_decision.execute")
@patch("backend.routes.metallurgical_decision.qone")
@patch("backend.routes.metallurgical_decision._normalize_levers")
def test_delete_simulation_runs(mock_norm, mock_qone, mock_execute):
    from backend.routes.metallurgical_decision import delete_simulation_runs

    mock_norm.side_effect = lambda pid, raw: raw
    mock_qone.return_value = {"n": 3}
    mock_execute.return_value = {}
    out = delete_simulation_runs("pid-1", user={"id": "u1"})
    assert out["deleted_runs"] == 3
    assert mock_execute.call_count == 2


@patch("backend.routes.geomet_intelligence.persist_gade_run")
@patch("backend.routes.geomet_intelligence._resolve_domain_result")
def test_save_geomet_snapshot(mock_resolve, mock_persist):
    from backend.routes.geomet_intelligence import save_geomet_snapshot

    mock_resolve.return_value = {"status": "ok", "domains": [{"domain_id": 1}]}
    mock_persist.return_value = {"id": "run-1", "computed_at": "2026-05-24T00:00:00Z"}
    out = save_geomet_snapshot("pid-1", user={"id": "u1"})
    assert out["ok"] is True
    assert out["persisted_run_id"] == "run-1"
