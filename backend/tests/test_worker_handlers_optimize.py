from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")

from backend.db import get_conn, qone
from backend.worker.handlers.simulate_optimize import handle_optimize


class _Ctx:
    def __init__(self, conn, job_id, project_id, user_id):
        self.conn = conn
        self.job_id = job_id
        self.project_id = project_id
        self.user_id = user_id
    def check_cancelled(self) -> None: pass
    def report_progress(self, *a, **k) -> None: pass


def test_handle_optimize_persists_run(test_project_id):
    user_id = qone("SELECT id FROM users WHERE email = %s",
                   (os.environ["ADMIN_EMAIL"],))["id"]
    payload = {
        "base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24},
        "grid": {"p80": [70, 75], "cn": [300, 350], "do": [7], "srt": [24]},
    }
    with get_conn() as conn:
        ctx = _Ctx(conn, uuid.uuid4(), test_project_id, user_id)
        ref = handle_optimize(payload, ctx)
        conn.commit()
    row = qone("SELECT run_type, results FROM simulation_runs_v2 WHERE id = %s", (ref["id"],))
    assert row["run_type"] == "simulate_optimize"
    assert "pareto_front" in row["results"]
    assert "recommended_optimum" in row["results"]
