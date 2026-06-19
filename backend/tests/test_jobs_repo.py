"""Layer 1 tests: backend/jobs/repo.py — pure DB-level."""
from __future__ import annotations

import json
import os
import time
import uuid

import pytest

if not os.getenv("TEST_DATABASE_URL"):
    pytest.skip("TEST_DATABASE_URL not set", allow_module_level=True)

from db import execute, qone


@pytest.fixture
def project_and_user():
    """Create an isolated project + user; cleaned up via projects CASCADE."""
    pid = str(uuid.uuid4())
    uid = str(uuid.uuid4())
    execute(
        "INSERT INTO users (id, email, password_hash, role, full_name) "
        "VALUES (%s, %s, 'x', 'Project Manager', 'Test User')",
        (uid, f"jobs-test-{uid[:8]}@test.dev"),
    )
    execute(
        "INSERT INTO projects (id, project_name, project_code, user_id) "
        "VALUES (%s, %s, %s, %s)",
        (pid, f"Jobs-{pid[:6]}", f"J-{pid[:6]}", uid),
    )
    yield {"project_id": pid, "user_id": uid}
    execute("DELETE FROM projects WHERE id = %s", (pid,))
    execute("DELETE FROM users WHERE id = %s", (uid,))


def test_insert_job_returns_uuid(project_and_user):
    from jobs import repo
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={"foo": "bar"},
    )
    assert isinstance(job_id, str)
    assert uuid.UUID(job_id)
    row = qone("SELECT * FROM jobs WHERE id = %s", (job_id,))
    assert row["status"] == "queued"
    assert row["progress"] == 0
    assert row["payload"] == {"foo": "bar"}
    assert row["worker_id"] is None
    assert row["cancel_requested"] is False


def test_pickup_one_picks_queued_marks_running(project_and_user):
    from jobs import repo
    worker_id = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_tornado",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    picked = repo.pickup_one(worker_id)
    assert picked["id"] == job_id
    assert picked["status"] == "running"
    assert picked["worker_id"] == worker_id
    assert picked["started_at"] is not None
    assert picked["last_heartbeat_at"] is not None


def test_pickup_one_returns_none_when_empty(project_and_user):
    from jobs import repo
    # Ensure no queued jobs exist for this project (purge any leftovers)
    execute("DELETE FROM jobs WHERE project_id = %s", (project_and_user["project_id"],))
    assert repo.pickup_one(uuid.uuid4().hex) is None


def test_pickup_one_skips_running(project_and_user):
    from jobs import repo
    # Hard purge of any leftover queued jobs so the assertion is meaningful.
    execute("DELETE FROM jobs")
    worker_a = uuid.uuid4().hex
    worker_b = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    a = repo.pickup_one(worker_a)
    assert a["id"] == job_id
    b = repo.pickup_one(worker_b)
    # The only queued job is now running; B must get nothing.
    assert b is None


def test_pickup_one_concurrent_no_double_pick(project_and_user):
    """FOR UPDATE SKIP LOCKED guarantees two workers never get the same job.

    We simulate by running two threads that each call pickup_one with
    short delays; with N=10 jobs and 2 workers, total picked = 10 with no overlap.
    """
    import threading
    from jobs import repo
    job_ids = [
        repo.insert_job(
            kind="sensitivity_spider",
            project_id=project_and_user["project_id"],
            created_by=project_and_user["user_id"],
            payload={"i": i},
        )
        for i in range(10)
    ]
    picked_a, picked_b = [], []

    def drain(worker_id, bucket):
        while True:
            r = repo.pickup_one(worker_id)
            if r is None:
                return
            bucket.append(r["id"])

    ta = threading.Thread(target=drain, args=(uuid.uuid4().hex, picked_a))
    tb = threading.Thread(target=drain, args=(uuid.uuid4().hex, picked_b))
    ta.start(); tb.start(); ta.join(); tb.join()

    union = set(picked_a + picked_b)
    assert union == set(job_ids)
    assert not (set(picked_a) & set(picked_b)), "no overlap allowed"


def test_report_progress_writes_when_worker_matches(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    ok = repo.report_progress(job_id, worker, 42, "halfway")
    assert ok is True
    row = qone("SELECT progress, progress_message FROM jobs WHERE id=%s", (job_id,))
    assert row["progress"] == 42
    assert row["progress_message"] == "halfway"


def test_report_progress_noop_when_worker_mismatch(project_and_user):
    from jobs import repo
    worker_a = uuid.uuid4().hex
    worker_b = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker_a)
    ok = repo.report_progress(job_id, worker_b, 99, "stale")
    assert ok is False
    row = qone("SELECT progress FROM jobs WHERE id=%s", (job_id,))
    assert row["progress"] == 0  # untouched


def test_set_terminal_success_writes_result_ref(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    ok = repo.set_terminal_success(
        job_id, worker, {"kind": "simulation_run_v2", "id": str(uuid.uuid4())}
    )
    assert ok is True
    row = qone("SELECT status, result_ref, finished_at, worker_id FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "success"
    assert row["result_ref"]["kind"] == "simulation_run_v2"
    assert row["finished_at"] is not None
    assert row["worker_id"] is None  # cleared on terminal


def test_mark_failed_writes_traceback(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    ok = repo.mark_failed(job_id, worker, "boom\nstack\ntrace")
    assert ok is True
    row = qone("SELECT status, error, finished_at FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "failed"
    assert "boom" in row["error"]


def test_terminal_writes_after_reap_are_noop(project_and_user):
    """If a worker comes back after being reaped, its terminal write must not stomp."""
    from jobs import repo
    worker_a = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker_a)
    # Simulate reap: clear worker_id and mark failed
    execute(
        "UPDATE jobs SET status='failed', worker_id=NULL, error='reaped', finished_at=now() "
        "WHERE id=%s", (job_id,),
    )
    # Now worker_a tries to mark success — should not change anything
    ok = repo.set_terminal_success(job_id, worker_a, {"kind": "simulation_run_v2", "id": "x"})
    assert ok is False
    row = qone("SELECT status, error FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "failed"
    assert row["error"] == "reaped"


def test_request_cancel_sets_flag(project_and_user):
    from jobs import repo
    job_id = repo.insert_job(
        kind="simulate_optimize",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    assert repo.request_cancel(job_id) is True
    row = qone("SELECT cancel_requested FROM jobs WHERE id=%s", (job_id,))
    assert row["cancel_requested"] is True


def test_request_cancel_returns_false_when_terminal(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="simulate_optimize",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    repo.set_terminal_success(job_id, worker, {"kind": "simulation_run_v2", "id": "x"})
    assert repo.request_cancel(job_id) is False  # already terminal


def test_is_cancel_requested(project_and_user):
    from jobs import repo
    job_id = repo.insert_job(
        kind="simulate_optimize",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    assert repo.is_cancel_requested(job_id) is False
    repo.request_cancel(job_id)
    assert repo.is_cancel_requested(job_id) is True


def test_get_job_returns_row(project_and_user):
    from jobs import repo
    job_id = repo.insert_job(
        kind="ni43101_export",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={"fmt": "docx"},
    )
    row = repo.get_job(job_id)
    assert row["id"] == job_id
    assert row["payload"] == {"fmt": "docx"}


def test_get_job_returns_none_for_missing():
    from jobs import repo
    assert repo.get_job(str(uuid.uuid4())) is None


def test_get_payload_returns_only_payload(project_and_user):
    from jobs import repo
    job_id = repo.insert_job(
        kind="ni43101_export",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={"fmt": "pdf", "lang": "fr"},
    )
    payload = repo.get_payload(job_id)
    assert payload == {"fmt": "pdf", "lang": "fr"}


def test_list_jobs_filters_by_project_and_kind(project_and_user):
    from jobs import repo
    pid = project_and_user["project_id"]
    uid = project_and_user["user_id"]
    repo.insert_job(kind="sensitivity_spider", project_id=pid, created_by=uid, payload={})
    repo.insert_job(kind="sensitivity_tornado", project_id=pid, created_by=uid, payload={})
    repo.insert_job(kind="sensitivity_spider", project_id=pid, created_by=uid, payload={})
    items, total = repo.list_jobs(project_ids=[pid], kind="sensitivity_spider")
    assert total == 2
    assert all(j["kind"] == "sensitivity_spider" for j in items)


def test_list_jobs_filters_by_status(project_and_user):
    from jobs import repo
    pid = project_and_user["project_id"]
    uid = project_and_user["user_id"]
    worker = uuid.uuid4().hex
    j1 = repo.insert_job(kind="sensitivity_spider", project_id=pid, created_by=uid, payload={})
    repo.insert_job(kind="sensitivity_spider", project_id=pid, created_by=uid, payload={})
    repo.pickup_one(worker)  # picks the oldest, which is j1
    repo.set_terminal_success(j1, worker, {"kind": "simulation_run_v2", "id": "x"})
    items, total = repo.list_jobs(project_ids=[pid], status="success")
    assert total == 1
    assert items[0]["status"] == "success"


def test_list_jobs_pagination(project_and_user):
    from jobs import repo
    pid = project_and_user["project_id"]
    uid = project_and_user["user_id"]
    for _ in range(7):
        repo.insert_job(kind="ni43101_export", project_id=pid, created_by=uid, payload={})
    items_page1, total = repo.list_jobs(project_ids=[pid], limit=3, offset=0)
    items_page2, _ = repo.list_jobs(project_ids=[pid], limit=3, offset=3)
    assert total >= 7
    assert {i["id"] for i in items_page1}.isdisjoint({i["id"] for i in items_page2})
    assert len(items_page1) == 3 and len(items_page2) == 3


def test_list_jobs_sets_has_result(project_and_user):
    from jobs import repo
    pid = project_and_user["project_id"]
    uid = project_and_user["user_id"]
    worker = uuid.uuid4().hex
    j_done = repo.insert_job(kind="ni43101_export", project_id=pid, created_by=uid, payload={})
    repo.pickup_one(worker)
    repo.set_terminal_success(j_done, worker, {"kind": "job_artifact", "id": 1})
    j_queued = repo.insert_job(kind="ni43101_export", project_id=pid, created_by=uid, payload={})
    items, _ = repo.list_jobs(project_ids=[pid])
    by_id = {i["id"]: i for i in items}
    assert by_id[j_done]["has_result"] is True
    assert by_id[j_queued]["has_result"] is False
    # result_ref MUST be omitted from list items per spec §6
    assert "result_ref" not in by_id[j_done]


def test_heartbeat_updates_only_workers_running_jobs(project_and_user):
    from jobs import repo
    worker_a = uuid.uuid4().hex
    worker_b = uuid.uuid4().hex
    pid = project_and_user["project_id"]; uid = project_and_user["user_id"]
    job_a = repo.insert_job(kind="sensitivity_spider", project_id=pid, created_by=uid, payload={})
    repo.pickup_one(worker_a)
    job_b = repo.insert_job(kind="sensitivity_spider", project_id=pid, created_by=uid, payload={})
    repo.pickup_one(worker_b)
    # Backdate both heartbeats
    execute("UPDATE jobs SET last_heartbeat_at = now() - interval '5 minutes' WHERE id IN (%s,%s)",
            (job_a, job_b))
    n = repo.heartbeat(worker_a)
    assert n == 1
    row_a = qone("SELECT last_heartbeat_at FROM jobs WHERE id=%s", (job_a,))
    row_b = qone("SELECT last_heartbeat_at FROM jobs WHERE id=%s", (job_b,))
    # Worker A's row got refreshed, B's didn't
    import datetime
    assert (datetime.datetime.now(datetime.timezone.utc) - row_a["last_heartbeat_at"]).total_seconds() < 10
    assert (datetime.datetime.now(datetime.timezone.utc) - row_b["last_heartbeat_at"]).total_seconds() > 60


def test_reap_zombies_marks_stale_running_as_failed(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="simulate_optimize",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    # Backdate heartbeat past the timeout
    execute("UPDATE jobs SET last_heartbeat_at = now() - interval '5 minutes' WHERE id=%s",
            (job_id,))
    reaped = repo.reap_zombies(timeout_seconds=90)
    assert reaped == 1
    row = qone("SELECT status, error, worker_id FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "failed"
    assert "worker died" in row["error"].lower() or "heartbeat" in row["error"].lower()
    assert row["worker_id"] is None


def test_reap_zombies_leaves_fresh_running_alone(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="simulate_optimize",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    reaped = repo.reap_zombies(timeout_seconds=90)
    assert reaped == 0
    row = qone("SELECT status FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "running"


def test_insert_and_get_artifact(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="ni43101_export",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={"fmt": "docx"},
    )
    repo.pickup_one(worker)
    art_id = repo.insert_artifact(
        job_id=job_id,
        filename="report.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        data=b"PK\x03\x04hello world",
    )
    art = repo.get_artifact(art_id)
    assert art["job_id"] == job_id
    assert art["filename"] == "report.docx"
    assert art["data"] == b"PK\x03\x04hello world"
    assert art["byte_size"] == len(b"PK\x03\x04hello world")


def test_purge_old_removes_terminal_jobs_past_retention(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    pid = project_and_user["project_id"]; uid = project_and_user["user_id"]
    j_old = repo.insert_job(kind="ni43101_export", project_id=pid, created_by=uid, payload={})
    repo.pickup_one(worker)
    repo.insert_artifact(j_old, "old.docx", "application/octet-stream", b"x")
    repo.set_terminal_success(j_old, worker, {"kind": "job_artifact", "id": 1})
    # Backdate finished_at
    execute("UPDATE jobs SET finished_at = now() - interval '30 days' WHERE id=%s", (j_old,))
    # And a recent terminal job
    j_recent = repo.insert_job(kind="ni43101_export", project_id=pid, created_by=uid, payload={})
    repo.pickup_one(worker)
    repo.set_terminal_success(j_recent, worker, {"kind": "job_artifact", "id": 2})

    deleted_jobs, deleted_artifacts = repo.purge_old(retention_days=7)
    assert deleted_jobs >= 1
    assert deleted_artifacts >= 1
    assert qone("SELECT id FROM jobs WHERE id=%s", (j_old,)) is None
    # Recent job survives
    assert qone("SELECT id FROM jobs WHERE id=%s", (j_recent,)) is not None


def test_purge_old_does_not_touch_running_jobs(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(
        kind="simulate_optimize",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    repo.pickup_one(worker)
    # No finished_at; even ancient created_at must NOT cause purge
    execute("UPDATE jobs SET created_at = now() - interval '30 days' WHERE id=%s", (job_id,))
    repo.purge_old(retention_days=7)
    assert qone("SELECT status FROM jobs WHERE id=%s", (job_id,))["status"] == "running"


def test_insert_job_fires_notify(project_and_user):
    """Spec §4: NOTIFY must arrive after the inserting transaction commits."""
    import psycopg2
    import psycopg2.extensions
    from jobs import repo

    dsn = os.environ["TEST_DATABASE_URL"]
    listen_conn = psycopg2.connect(dsn)
    listen_conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        cur = listen_conn.cursor()
        cur.execute("LISTEN jobs_new;")
        listen_conn.poll()
        # Drain any prior notifies
        listen_conn.notifies.clear()

        job_id = repo.insert_job(
            kind="sensitivity_spider",
            project_id=project_and_user["project_id"],
            created_by=project_and_user["user_id"],
            payload={"x": 1},
        )

        # Wait up to 1 second for NOTIFY
        deadline = time.time() + 1.0
        received = []
        while time.time() < deadline:
            listen_conn.poll()
            while listen_conn.notifies:
                received.append(listen_conn.notifies.pop(0))
            if received:
                break
            time.sleep(0.02)

        assert received, "no NOTIFY received within 1 second"
        assert any(n.payload == job_id for n in received)
    finally:
        listen_conn.close()


def test_get_job_by_id_returns_row_when_project_matches(project_and_user):
    from jobs import repo
    job_id = repo.insert_job(
        kind="sensitivity_spider",
        project_id=project_and_user["project_id"],
        created_by=project_and_user["user_id"],
        payload={},
    )
    row = repo.get_job_by_id(job_id=job_id, project_id=project_and_user["project_id"])
    assert row is not None
    assert row["type"] == "sensitivity_spider"  # alias `kind AS type`
    assert row["status"] == "queued"


def test_get_job_by_id_returns_none_for_other_project(project_and_user):
    from jobs import repo
    other_pid = str(uuid.uuid4())
    execute(
        "INSERT INTO projects (id, project_name, project_code, user_id) "
        "VALUES (%s, 'other', 'OTH', %s)",
        (other_pid, project_and_user["user_id"]),
    )
    try:
        job_id = repo.insert_job(
            kind="sensitivity_spider",
            project_id=other_pid,
            created_by=project_and_user["user_id"],
            payload={},
        )
        row = repo.get_job_by_id(job_id=job_id, project_id=project_and_user["project_id"])
        assert row is None
    finally:
        # Avoid leaking OTH project + its job into subsequent tests' pickup_one.
        execute("DELETE FROM projects WHERE id = %s", (other_pid,))


def test_list_jobs_by_project_filters_status_translates_done_to_success(project_and_user):
    from jobs import repo
    pid = project_and_user["project_id"]
    j1 = repo.insert_job(kind="sensitivity_spider", project_id=pid,
                         created_by=project_and_user["user_id"], payload={})
    j2 = repo.insert_job(kind="sensitivity_spider", project_id=pid,
                         created_by=project_and_user["user_id"], payload={})
    execute("UPDATE jobs SET status='success', finished_at=now() WHERE id=%s", (j1,))
    items = repo.list_jobs_by_project(project_id=pid, limit=50, status="done")
    ids = {i["id"] for i in items}
    assert j1 in ids and j2 not in ids


def test_cancel_job_if_pending_marks_queued_as_cancelled(project_and_user):
    from jobs import repo
    job_id = repo.insert_job(kind="sensitivity_spider",
                             project_id=project_and_user["project_id"],
                             created_by=project_and_user["user_id"], payload={})
    out = repo.cancel_job_if_pending(job_id=job_id,
                                     project_id=project_and_user["project_id"])
    assert out == "cancelled"
    row = qone("SELECT status, finished_at FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "cancelled"
    assert row["finished_at"] is not None


def test_cancel_job_if_pending_running_sets_cancel_flag(project_and_user):
    from jobs import repo
    worker = uuid.uuid4().hex
    job_id = repo.insert_job(kind="sensitivity_spider",
                             project_id=project_and_user["project_id"],
                             created_by=project_and_user["user_id"], payload={})
    repo.pickup_one(worker)
    out = repo.cancel_job_if_pending(job_id=job_id,
                                     project_id=project_and_user["project_id"])
    assert out == "cancelling"
    row = qone("SELECT status, cancel_requested FROM jobs WHERE id=%s", (job_id,))
    assert row["status"] == "running"
    assert row["cancel_requested"] is True


def test_settings_has_worker_fields():
    from settings import get_settings
    s = get_settings()
    assert s.worker_enabled in (True, False)
    assert s.job_retention_days >= 1
    assert s.job_zombie_timeout_seconds >= 30
    assert s.job_heartbeat_interval_seconds >= 1
    assert s.job_progress_throttle_ms >= 0
    assert s.job_cancel_cache_ms >= 0
    assert s.job_artifact_max_bytes >= 1024 * 1024
    assert s.job_payload_max_bytes >= 1024
