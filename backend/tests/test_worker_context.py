"""Tests for backend/worker/context.py — JobContext throttling and cancel caching."""
from __future__ import annotations

import os
import time
import uuid

import pytest

if not os.getenv("TEST_DATABASE_URL"):
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)

from db import execute, qone
from jobs import repo, JobCancelledException


@pytest.fixture
def jpu():
    pid = str(uuid.uuid4()); uid = str(uuid.uuid4())
    execute(
        "INSERT INTO users (id, email, password_hash, role, full_name) VALUES (%s, %s, 'x', 'Project Manager', 'T')",
        (uid, f"ctx-{uid[:8]}@test.dev"),
    )
    execute(
        "INSERT INTO projects (id, project_name, project_code, user_id) VALUES (%s, %s, %s, %s)",
        (pid, f"Ctx-{pid[:6]}", f"C-{pid[:6]}", uid),
    )
    yield {"project_id": pid, "user_id": uid}
    execute("DELETE FROM projects WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE id=%s", (uid,))


def _new_running_job(jpu):
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="simulate_optimize",
        project_id=jpu["project_id"], created_by=jpu["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    return job_id, worker


def test_report_progress_throttles_intermediate_writes(jpu):
    from worker.context import JobContext
    job_id, worker = _new_running_job(jpu)
    ctx = JobContext(job_id=job_id, project_id=jpu["project_id"],
                     user_id=jpu["user_id"], worker_id=worker,
                     progress_throttle_ms=500, cancel_cache_ms=500)
    ctx.report_progress(10, "step 1")
    ctx.report_progress(20, "step 2")  # within throttle window
    ctx.report_progress(30, "step 3")
    row = qone("SELECT progress FROM jobs WHERE id=%s", (job_id,))
    # Only the first call writes; throttled ones are dropped.
    assert row["progress"] == 10


def test_report_progress_writes_after_throttle_window(jpu):
    from worker.context import JobContext
    job_id, worker = _new_running_job(jpu)
    ctx = JobContext(job_id=job_id, project_id=jpu["project_id"],
                     user_id=jpu["user_id"], worker_id=worker,
                     progress_throttle_ms=50, cancel_cache_ms=500)
    ctx.report_progress(10, "a")
    time.sleep(0.1)
    ctx.report_progress(50, "b")
    assert qone("SELECT progress FROM jobs WHERE id=%s", (job_id,))["progress"] == 50


def test_report_progress_terminal_write_not_throttled(jpu):
    """Spec §7: the final terminal write (100, 'done') is NOT throttled."""
    from worker.context import JobContext
    job_id, worker = _new_running_job(jpu)
    ctx = JobContext(job_id=job_id, project_id=jpu["project_id"],
                     user_id=jpu["user_id"], worker_id=worker,
                     progress_throttle_ms=10000, cancel_cache_ms=500)
    ctx.report_progress(10, "start")
    ctx.report_progress(100, "done")  # terminal — must always write
    assert qone("SELECT progress, progress_message FROM jobs WHERE id=%s", (job_id,))["progress"] == 100


def test_check_cancelled_raises(jpu):
    from worker.context import JobContext
    job_id, worker = _new_running_job(jpu)
    ctx = JobContext(job_id=job_id, project_id=jpu["project_id"],
                     user_id=jpu["user_id"], worker_id=worker,
                     progress_throttle_ms=500, cancel_cache_ms=0)  # no cache for clarity
    repo.request_cancel(job_id)
    with pytest.raises(JobCancelledException):
        ctx.check_cancelled()


def test_check_cancelled_caches_within_window(jpu):
    """check_cancelled must not hammer the DB. With cache_ms>0, repeated calls
    in a tight loop should result in only ONE DB query within the window."""
    from worker.context import JobContext
    import jobs.repo as repo_mod

    job_id, worker = _new_running_job(jpu)
    ctx = JobContext(job_id=job_id, project_id=jpu["project_id"],
                     user_id=jpu["user_id"], worker_id=worker,
                     progress_throttle_ms=500, cancel_cache_ms=500)

    call_count = {"n": 0}
    real_is_cancel = repo_mod.is_cancel_requested
    def counting(jid):
        call_count["n"] += 1
        return real_is_cancel(jid)

    repo_mod.is_cancel_requested = counting
    try:
        for _ in range(20):
            ctx.check_cancelled()
        assert call_count["n"] == 1
    finally:
        repo_mod.is_cancel_requested = real_is_cancel
