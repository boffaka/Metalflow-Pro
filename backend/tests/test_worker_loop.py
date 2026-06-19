"""Tests for backend/worker/loop._process_one_job — the synchronous core."""
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
        (uid, f"loop-{uid[:8]}@test.dev"),
    )
    execute(
        "INSERT INTO projects (id, project_name, project_code, user_id) VALUES (%s, %s, %s, %s)",
        (pid, f"Loop-{pid[:6]}", f"L-{pid[:6]}", uid),
    )
    yield {"project_id": pid, "user_id": uid}
    execute("DELETE FROM projects WHERE id=%s", (pid,))
    execute("DELETE FROM users WHERE id=%s", (uid,))


def test_process_one_job_dispatches_and_marks_success(jpu, monkeypatch):
    from worker import loop, registry
    expected_ref = {"kind": "simulation_run_v2", "id": str(uuid.uuid4())}
    def fake_handler(payload, ctx):
        ctx.report_progress(50, "halfway")
        return expected_ref
    monkeypatch.setitem(registry.JOB_HANDLERS, "sensitivity_spider", fake_handler)
    worker_id = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider", project_id=jpu["project_id"],
        created_by=jpu["user_id"], payload={"x": 1},
    )
    job = repo.pickup_one(worker_id)
    loop._process_one_job(job, worker_id)
    row = qone("SELECT status, result_ref, finished_at FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "success"
    assert row["result_ref"] == expected_ref


def test_process_one_job_marks_failed_on_exception(jpu, monkeypatch):
    from worker import loop, registry
    def boom(payload, ctx):
        raise RuntimeError("bang")
    monkeypatch.setitem(registry.JOB_HANDLERS, "sensitivity_spider", boom)
    worker_id = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider", project_id=jpu["project_id"],
        created_by=jpu["user_id"], payload={},
    )
    job = repo.pickup_one(worker_id)
    loop._process_one_job(job, worker_id)
    row = qone("SELECT status, error FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "failed"
    assert "bang" in row["error"]


def test_process_one_job_marks_cancelled_on_jobcancelled_exc(jpu, monkeypatch):
    from worker import loop, registry
    def cancellable(payload, ctx):
        raise JobCancelledException("from handler")
    monkeypatch.setitem(registry.JOB_HANDLERS, "simulate_optimize", cancellable)
    worker_id = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="simulate_optimize", project_id=jpu["project_id"],
        created_by=jpu["user_id"], payload={},
    )
    job = repo.pickup_one(worker_id)
    loop._process_one_job(job, worker_id)
    row = qone("SELECT status, error FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "cancelled"
    assert row["error"] is None  # cancellation is not an error


def test_process_one_job_short_circuits_if_cancel_already_set(jpu, monkeypatch):
    """Spec §7: queued+cancelled jobs go straight to cancelled without invoking handler."""
    from worker import loop, registry
    called = {"n": 0}
    def should_not_run(payload, ctx):
        called["n"] += 1
        return {"kind": "simulation_run_v2", "id": "x"}
    monkeypatch.setitem(registry.JOB_HANDLERS, "sensitivity_spider", should_not_run)
    worker_id = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider", project_id=jpu["project_id"],
        created_by=jpu["user_id"], payload={},
    )
    repo.request_cancel(job_id)  # before pickup
    job = repo.pickup_one(worker_id)
    loop._process_one_job(job, worker_id)
    assert called["n"] == 0
    assert qone("SELECT status FROM jobs WHERE id=%s", (job_id,))["status"] == "cancelled"


def test_process_one_job_unknown_kind_marks_failed(jpu):
    from worker import loop
    worker_id = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider", project_id=jpu["project_id"],
        created_by=jpu["user_id"], payload={},
    )
    job = repo.pickup_one(worker_id)
    # Force an unknown kind that no Chunk-3 handler will ever register.
    job["kind"] = "__unregistered_test_kind__"
    loop._process_one_job(job, worker_id)
    row = qone("SELECT status, error FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "failed"
    assert "no handler" in row["error"].lower()


def test_run_loop_picks_up_and_processes_then_exits(jpu, monkeypatch):
    """End-to-end: spin run() in a thread, INSERT a job, observe it complete, signal stop."""
    import threading
    from worker import loop, registry
    expected_ref = {"kind": "simulation_run_v2", "id": str(uuid.uuid4())}
    def fast_handler(payload, ctx):
        ctx.report_progress(100, "done")
        return expected_ref
    monkeypatch.setitem(registry.JOB_HANDLERS, "sensitivity_spider", fast_handler)

    stop_event = threading.Event()
    th = threading.Thread(target=loop.run, kwargs={"stop_event": stop_event,
                                                     "reaper_interval_seconds": 1000})
    th.start()
    try:
        # Insert a job; the LISTEN/poll path should pick it up within ~2s.
        job_id = repo.insert_job(
            kind="sensitivity_spider", project_id=jpu["project_id"],
            created_by=jpu["user_id"], payload={},
        )
        deadline = time.time() + 5
        while time.time() < deadline:
            row = qone("SELECT status FROM jobs WHERE id=%s", (job_id,))
            if row["status"] == "success":
                break
            time.sleep(0.05)
        assert qone("SELECT status FROM jobs WHERE id=%s", (job_id,))["status"] == "success"
    finally:
        stop_event.set()
        th.join(timeout=10)
        assert not th.is_alive(), "loop did not exit on stop_event"


def test_run_loop_heartbeat_refreshes_running_job(jpu, monkeypatch):
    """While a slow handler runs, the heartbeat thread refreshes last_heartbeat_at."""
    import threading
    from worker import loop, registry
    handler_started = threading.Event()
    handler_release = threading.Event()
    def slow_handler(payload, ctx):
        handler_started.set()
        handler_release.wait(timeout=10)
        return {"kind": "simulation_run_v2", "id": str(uuid.uuid4())}
    monkeypatch.setitem(registry.JOB_HANDLERS, "simulate_optimize", slow_handler)

    stop_event = threading.Event()
    th = threading.Thread(
        target=loop.run,
        kwargs={"stop_event": stop_event, "reaper_interval_seconds": 1000,
                "heartbeat_interval_seconds": 1},
    )
    th.start()
    try:
        job_id = repo.insert_job(
            kind="simulate_optimize", project_id=jpu["project_id"],
            created_by=jpu["user_id"], payload={},
        )
        assert handler_started.wait(timeout=5), "handler never ran"
        h1 = qone("SELECT last_heartbeat_at FROM jobs WHERE id=%s", (job_id,))["last_heartbeat_at"]
        time.sleep(2.5)
        h2 = qone("SELECT last_heartbeat_at FROM jobs WHERE id=%s", (job_id,))["last_heartbeat_at"]
        assert h2 > h1, "heartbeat did not refresh during running handler"
    finally:
        handler_release.set()
        stop_event.set()
        th.join(timeout=10)
