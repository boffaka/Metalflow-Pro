"""Layer 2 — handler tests with a real DB connection but a fake ctx."""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")

from backend.db import get_conn, qone
from backend.jobs.context import JobCancelled
from backend.worker.handlers.sensitivity import handle_spider, handle_tornado


class _Ctx:
    """Real-DB ctx used in handler tests. No throttling, no cancellation."""
    def __init__(self, conn, job_id, project_id, user_id):
        self.conn = conn
        self.job_id = job_id
        self.project_id = project_id
        self.user_id = user_id
        self.cancel_calls = 0
    def check_cancelled(self) -> None:
        self.cancel_calls += 1
    def report_progress(self, *a, **k) -> None:
        pass


class _CancelCtx(_Ctx):
    def __init__(self, *args, cancel_after: int = 1, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancel_after = cancel_after
    def check_cancelled(self) -> None:
        self.cancel_calls += 1
        if self.cancel_calls > self.cancel_after:
            raise JobCancelled()


def _payload():
    return {
        "base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24},
        "params_to_vary": ["p80", "cn"],
        "delta_pcts": [-5.0, 5.0],
    }


def test_handle_spider_persists_simulation_run_v2(test_project_id, admin_token):
    user_id = qone("SELECT id FROM users WHERE email = %s", (os.environ["ADMIN_EMAIL"],))["id"]
    job_id = uuid.uuid4()
    with get_conn() as conn:
        ctx = _Ctx(conn, job_id, test_project_id, user_id)
        ref = handle_spider(_payload(), ctx)
        conn.commit()
    assert ref["kind"] == "simulation_run_v2"
    row = qone("SELECT id, run_type, results FROM simulation_runs_v2 WHERE id = %s", (ref["id"],))
    assert row is not None
    assert row["run_type"] == "sensitivity_spider"
    assert "series" in row["results"]


def test_handle_tornado_persists_simulation_run_v2(test_project_id, admin_token):
    user_id = qone("SELECT id FROM users WHERE email = %s", (os.environ["ADMIN_EMAIL"],))["id"]
    job_id = uuid.uuid4()
    with get_conn() as conn:
        ctx = _Ctx(conn, job_id, test_project_id, user_id)
        ref = handle_tornado(_payload(), ctx)
        conn.commit()
    row = qone("SELECT run_type, results FROM simulation_runs_v2 WHERE id = %s", (ref["id"],))
    assert row["run_type"] == "sensitivity_tornado"
    assert "rows" in row["results"]
    assert all("rank" in r for r in row["results"]["rows"])


def test_handle_spider_atomic_on_cancel(test_project_id, admin_token):
    """If the handler raises JobCancelled, no row appears in simulation_runs_v2."""
    user_id = qone("SELECT id FROM users WHERE email = %s", (os.environ["ADMIN_EMAIL"],))["id"]
    job_id = uuid.uuid4()
    with get_conn() as conn:
        ctx = _CancelCtx(conn, job_id, test_project_id, user_id, cancel_after=1)
        with pytest.raises(JobCancelled):
            handle_spider(_payload(), ctx)
        conn.rollback()
    rows = qone(
        "SELECT count(*) AS n FROM simulation_runs_v2 "
        "WHERE project_id = %s AND params->>'__job_id' = %s",
        (test_project_id, str(job_id)),
    )
    assert rows["n"] == 0
