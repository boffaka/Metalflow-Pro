"""Tests for GMIE GADE persistence layer."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines import geomet_storage as gs
except ImportError:
    import engines.geomet_storage as gs


@patch("backend.engines.geomet_storage.execute")
@patch("backend.engines.geomet_storage.qone")
def test_load_active_gade_run(mock_qone, mock_execute):
    mock_qone.return_value = {
        "id": "run-1",
        "config_json": {"feature_weights": {"au_recovery_pct": 3.0}},
        "result_json": {"status": "ok", "domains": [{"domain_id": 0}]},
        "computed_at": "2026-05-23T12:00:00Z",
    }
    out = gs.load_active_gade_run("proj-1")
    assert out["status"] == "ok"
    assert out["persisted_run_id"] == "run-1"


@patch("backend.engines.geomet_storage.sync_geomet_domains_table")
@patch("backend.engines.geomet_storage._db_release")
@patch("backend.engines.geomet_storage._db_conn")
def test_persist_gade_run(mock_conn, mock_release, mock_sync):
    """persist_gade_run uses a transactional connection; mock at that level."""
    expected_row = {"id": "run-2", "computed_at": "2026-05-23T12:00:00Z"}

    # Build a mock cursor that returns the expected row on fetchone()
    mock_cur = MagicMock()
    mock_cur.__enter__ = lambda s: mock_cur
    mock_cur.__exit__ = MagicMock(return_value=False)
    mock_cur.fetchone.return_value = expected_row

    mock_db = MagicMock()
    mock_db.cursor.return_value = mock_cur
    mock_conn.return_value = mock_db

    result = {"status": "ok", "domains": [{"domain_id": 0, "domain_name": "Ox-Soft"}]}
    row = gs.persist_gade_run("proj-1", {}, result, user_id="user-1")
    assert row["id"] == "run-2"
    mock_db.commit.assert_called_once()
    mock_sync.assert_called_once()


@patch("backend.routes.geomet_intelligence.persist_gade_run")
@patch("backend.routes.geomet_intelligence.auto_cluster_domains")
def test_auto_domain_persists(mock_cluster, mock_persist):
    try:
        from backend.routes import geomet_intelligence as gi
    except ImportError:
        import routes.geomet_intelligence as gi

    from starlette.requests import Request as StarletteRequest
    _scope = {"type": "http", "method": "POST", "headers": [], "query_string": b"", "path": "/"}

    mock_cluster.return_value = {"status": "ok", "domains": [{"domain_id": 0}]}
    mock_persist.return_value = {"id": "run-3", "computed_at": "2026-05-23T12:00:00Z"}
    gi._domain_cache.clear()
    out = gi.run_auto_domain(StarletteRequest(_scope), "proj-1", body=None, user={"id": "u1"})
    assert out["persisted_run_id"] == "run-3"
    mock_persist.assert_called_once()
