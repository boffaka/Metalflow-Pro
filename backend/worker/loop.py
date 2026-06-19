"""Worker main loop and synchronous job processor.

The loop is split into two halves:
- _process_one_job(job, worker_id): the synchronous core. Tests call this
  directly without spawning a process.
- run(): the long-running loop that owns the LISTEN connection, heartbeat
  thread, signal handlers, and reaper schedule. Implemented in Task 2.5.
"""
from __future__ import annotations

import logging
import os
import select
import threading
import time
import traceback
import uuid as _uuid
from typing import Any

import psycopg2
import psycopg2.extensions

try:
    from db import get_conn
    from jobs import repo
    from jobs.errors import JobCancelledException
    from settings import get_settings
    from worker import registry
    from worker.context import JobContext
except ImportError:  # pragma: no cover
    from backend.db import get_conn
    from backend.jobs import repo
    from backend.jobs.errors import JobCancelledException
    from backend.settings import get_settings
    from backend.worker import registry
    from backend.worker.context import JobContext

logger = logging.getLogger("mpdpms.worker")


def _process_one_job(job: dict[str, Any], worker_id: str) -> None:
    """Run one job synchronously and write its terminal status.

    Public testable interface (spec §7). Handles:
    - Pre-handler cancel short-circuit (queued-then-cancelled race).
    - Handler dispatch via registry.
    - Terminal status write with worker_id guard.
    - Exception → failed (with traceback).
    - JobCancelledException → cancelled.
    - Unknown kind → failed ("no handler").
    """
    job_id = str(job["id"])
    kind = job["kind"]
    started = time.monotonic()
    settings = get_settings()

    # Pre-handler cancel check: avoids the race where DELETE arrived before pickup.
    if repo.is_cancel_requested(job_id):
        repo.mark_cancelled(job_id, worker_id)
        logger.info("[worker] job %s kind=%s status=cancelled (pre-handler)", job_id, kind)
        return

    handler = registry.get(kind)
    if handler is None:
        repo.mark_failed(job_id, worker_id, f"no handler registered for kind={kind!r}")
        logger.error("[worker] job %s kind=%s status=failed (no handler)", job_id, kind)
        return

    # Open a runner-managed connection for the handler. The runner commits on
    # success and rolls back on cancel/failure — guaranteeing handler writes
    # are atomic with the terminal status decision.
    with get_conn() as conn:
        ctx = JobContext(
            job_id=job_id,
            project_id=str(job["project_id"]),
            user_id=str(job["created_by"]),
            worker_id=worker_id,
            conn=conn,
            progress_throttle_ms=settings.job_progress_throttle_ms,
            cancel_cache_ms=settings.job_cancel_cache_ms,
        )

        try:
            result_ref = handler(job["payload"], ctx)
            conn.commit()
        except JobCancelledException:
            conn.rollback()
            repo.mark_cancelled(job_id, worker_id)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info("[worker] job %s kind=%s status=cancelled duration_ms=%s", job_id, kind, duration_ms)
            return
        except Exception:
            conn.rollback()
            tb = traceback.format_exc()
            repo.mark_failed(job_id, worker_id, tb)
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.exception("[worker] job %s kind=%s status=failed duration_ms=%s", job_id, kind, duration_ms)
            return

    if not isinstance(result_ref, dict) or "kind" not in result_ref:
        repo.mark_failed(job_id, worker_id, f"handler returned invalid result_ref: {result_ref!r}")
        logger.error("[worker] job %s kind=%s status=failed (bad result_ref)", job_id, kind)
        return

    ok = repo.set_terminal_success(job_id, worker_id, result_ref)
    duration_ms = int((time.monotonic() - started) * 1000)
    if not ok:
        # Worker_id mismatch — job was reaped by another actor while running.
        logger.warning(
            "[worker] job %s kind=%s status=success-DROPPED (worker_id mismatch — reaped) duration_ms=%s",
            job_id,
            kind,
            duration_ms,
        )
    else:
        logger.info("[worker] job %s kind=%s status=success duration_ms=%s", job_id, kind, duration_ms)


def _heartbeat_loop(stop_event: threading.Event, worker_id: str, interval_s: int) -> None:
    """Run on a dedicated thread + dedicated DB connection. Refreshes
    last_heartbeat_at on this worker's running jobs every interval_s seconds.
    """
    dsn = os.environ["DATABASE_URL"]
    conn = None
    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        while not stop_event.wait(interval_s):
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE jobs SET last_heartbeat_at=now() "
                        "WHERE worker_id=%s AND status='running'",
                        (worker_id,),
                    )
            except Exception:
                logger.exception("[worker] heartbeat update failed")
                # Reconnect on next tick if the connection broke
                try:
                    if conn is not None:
                        conn.close()
                except Exception:
                    pass
                conn = None
                try:
                    conn = psycopg2.connect(dsn)
                    conn.autocommit = True
                except Exception:
                    logger.exception("[worker] heartbeat reconnect failed")
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def run(
    *,
    stop_event: threading.Event | None = None,
    heartbeat_interval_seconds: int | None = None,
    reaper_interval_seconds: int = 30,
    poll_timeout_seconds: float = 2.0,
) -> None:
    """Main worker loop.

    Steps (per spec §7):
    1. Generate worker_id and run a startup reaper sweep.
    2. Open a dedicated LISTEN connection.
    3. Spawn the heartbeat thread (its own connection).
    4. Loop:
       a. Try pickup_one(worker_id). If a job is returned, run _process_one_job.
       b. Else wait on NOTIFY (with poll_timeout_seconds fallback).
       c. Every reaper_interval_seconds, run reap_zombies.
    5. On stop_event.set(): join the heartbeat thread and close the LISTEN connection.
    """
    settings = get_settings()
    if stop_event is None:
        stop_event = threading.Event()
    if heartbeat_interval_seconds is None:
        heartbeat_interval_seconds = settings.job_heartbeat_interval_seconds

    worker_id = _uuid.uuid4().hex
    logger.info("[worker] booting worker_id=%s", worker_id)

    # Startup reap of zombies left over from a previous (crashed) worker.
    try:
        reaped = repo.reap_zombies(settings.job_zombie_timeout_seconds)
        if reaped:
            logger.warning("[worker] startup reaped %s zombie jobs", reaped)
    except Exception:
        logger.exception("[worker] startup reaper failed")

    # Heartbeat thread on a dedicated connection.
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(stop_event, worker_id, heartbeat_interval_seconds),
        name="job-heartbeat",
        daemon=True,
    )
    hb_thread.start()

    # LISTEN connection (dedicated, autocommit — required by LISTEN).
    dsn = os.environ["DATABASE_URL"]
    listen_conn = psycopg2.connect(dsn)
    listen_conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with listen_conn.cursor() as cur:
            cur.execute("LISTEN jobs_new;")
        logger.info("[worker] ready worker_id=%s listening on jobs_new", worker_id)

        last_reap = time.monotonic()
        while not stop_event.is_set():
            picked = repo.pickup_one(worker_id)
            if picked is not None:
                _process_one_job(picked, worker_id)
                continue

            # No job — wait for NOTIFY or timeout.
            r, _, _ = select.select([listen_conn], [], [], poll_timeout_seconds)
            if r:
                listen_conn.poll()
                listen_conn.notifies.clear()  # we don't actually need the payload

            if time.monotonic() - last_reap >= reaper_interval_seconds:
                try:
                    n = repo.reap_zombies(settings.job_zombie_timeout_seconds)
                    if n:
                        logger.warning("[worker] reaped %s zombie jobs", n)
                except Exception:
                    logger.exception("[worker] reaper failed")
                last_reap = time.monotonic()
    finally:
        logger.info("[worker] stopping worker_id=%s", worker_id)
        try:
            listen_conn.close()
        except Exception:
            pass
        hb_thread.join(timeout=10)
        if hb_thread.is_alive():
            logger.warning("[worker] heartbeat thread did not exit cleanly")
        logger.info("[worker] stopped worker_id=%s", worker_id)
