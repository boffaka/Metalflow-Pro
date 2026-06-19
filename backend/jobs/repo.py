"""SQL repository for the jobs subsystem.

All functions return plain dicts (or primitives). No FastAPI imports;
this module is shared by both the API and the worker process.
"""
from __future__ import annotations

import json
from typing import Any

import psycopg2

try:
    from db import execute, qone, qall, get_cursor
except ImportError:  # pragma: no cover
    from backend.db import execute, qone, qall, get_cursor


def psycopg2_binary(data: bytes):
    """Wrap bytes in psycopg2.Binary for proper bytea encoding."""
    return psycopg2.Binary(data)


def insert_job(
    *, kind: str, project_id: str, created_by: str, payload: dict[str, Any]
) -> str:
    """INSERT a queued job. Commits immediately (required for NOTIFY delivery).

    Returns the new job_id (UUID string).
    """
    row = execute(
        "INSERT INTO jobs (kind, project_id, created_by, payload) "
        "VALUES (%s, %s, %s, %s::jsonb) RETURNING id",
        (kind, project_id, created_by, json.dumps(payload)),
    )
    return str(row["id"])


def pickup_one(worker_id: str) -> dict[str, Any] | None:
    """Atomically pick the oldest queued job and mark it running.

    Uses FOR UPDATE SKIP LOCKED so concurrent workers never collide.
    Sets worker_id, started_at, last_heartbeat_at on the picked row.

    Returns the full job row (dict), or None if the queue is empty.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE jobs
               SET status = 'running',
                   started_at = now(),
                   last_heartbeat_at = now(),
                   worker_id = %s
             WHERE id = (
                 SELECT id FROM jobs
                  WHERE status = 'queued'
                  ORDER BY created_at
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
             )
             RETURNING *
            """,
            (worker_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def report_progress(job_id: str, worker_id: str, pct: int, message: str | None) -> bool:
    """Update progress only if this worker still owns the job.

    Returns True if a row was updated (worker_id matched and status='running'),
    False if the row was reaped or no longer running.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE jobs SET progress=%s, progress_message=%s "
            "WHERE id=%s AND worker_id=%s AND status='running'",
            (pct, message, job_id, worker_id),
        )
        return cur.rowcount > 0


def set_terminal_success(job_id: str, worker_id: str, result_ref: dict[str, Any]) -> bool:
    """Mark job success with a result_ref pointer. Clears worker_id.

    Returns True only if this worker still owned the row (worker_id match guard).
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE jobs SET status='success', result_ref=%s::jsonb, "
            "       progress=100, progress_message='done', "
            "       finished_at=now(), worker_id=NULL "
            "WHERE id=%s AND worker_id=%s AND status='running'",
            (json.dumps(result_ref), job_id, worker_id),
        )
        return cur.rowcount > 0


def mark_failed(job_id: str, worker_id: str, error: str) -> bool:
    """Mark job failed with an error message. Clears worker_id."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE jobs SET status='failed', error=%s, finished_at=now(), worker_id=NULL "
            "WHERE id=%s AND worker_id=%s AND status='running'",
            (error, job_id, worker_id),
        )
        return cur.rowcount > 0


def mark_cancelled(job_id: str, worker_id: str) -> bool:
    """Mark job cancelled (handler observed cancel_requested). Clears worker_id."""
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE jobs SET status='cancelled', finished_at=now(), worker_id=NULL "
            "WHERE id=%s AND worker_id=%s AND status='running'",
            (job_id, worker_id),
        )
        return cur.rowcount > 0


def request_cancel(job_id: str) -> bool:
    """Set cancel_requested=true if job is queued or running.

    Returns True if the flag was set, False if job already terminal.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE jobs SET cancel_requested=true "
            "WHERE id=%s AND status IN ('queued','running')",
            (job_id,),
        )
        return cur.rowcount > 0


def is_cancel_requested(job_id: str) -> bool:
    """Read the cancel flag. Used by JobContext.check_cancelled with caching."""
    row = qone("SELECT cancel_requested FROM jobs WHERE id=%s", (job_id,))
    return bool(row and row["cancel_requested"])


def heartbeat(worker_id: str) -> int:
    """Refresh last_heartbeat_at for all jobs this worker currently owns.

    Called every JOB_HEARTBEAT_INTERVAL_SECONDS by the worker's heartbeat
    thread. Returns the number of rows touched (0 if worker is idle).
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            "UPDATE jobs SET last_heartbeat_at=now() "
            "WHERE worker_id=%s AND status='running'",
            (worker_id,),
        )
        return cur.rowcount


def reap_zombies(timeout_seconds: int) -> int:
    """Mark running jobs whose heartbeat is older than timeout_seconds as failed.

    Clears worker_id so the original worker (if it returns) cannot stomp.
    Returns the number of jobs reaped.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            """
            UPDATE jobs
               SET status='failed',
                   error='worker died — last heartbeat ' ||
                         COALESCE(age(now(), last_heartbeat_at)::text, 'never') || ' ago',
                   finished_at=now(),
                   worker_id=NULL
             WHERE status='running'
               AND (last_heartbeat_at IS NULL
                    OR last_heartbeat_at < now() - make_interval(secs => %s))
            """,
            (timeout_seconds,),
        )
        return cur.rowcount


_LIST_FIELDS = (
    "id, kind, status, progress, progress_message, "
    "(result_ref IS NOT NULL) AS has_result, error, "
    "created_at, started_at, finished_at"
)


def get_job(job_id: str) -> dict[str, Any] | None:
    """Fetch full job row by id, or None if not found."""
    return qone("SELECT * FROM jobs WHERE id = %s", (job_id,))


def get_payload(job_id: str) -> dict[str, Any] | None:
    """Fetch only the payload column. Returns None if job not found."""
    row = qone("SELECT payload FROM jobs WHERE id = %s", (job_id,))
    return row["payload"] if row else None


def list_jobs(
    *, project_ids: list[str], kind: str | None = None, status: str | None = None,
    limit: int = 50, offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """List jobs across the given project_ids with optional filters.

    Returns (items, total). Each item omits payload and result_ref but
    includes has_result: bool. Items ordered created_at DESC.
    """
    if not project_ids:
        return [], 0
    where = ["project_id = ANY(%s::uuid[])"]
    params: list[Any] = [project_ids]
    if kind is not None:
        where.append("kind = %s")
        params.append(kind)
    if status is not None:
        where.append("status = %s")
        params.append(status)
    where_sql = " AND ".join(where)

    total_row = qone(f"SELECT COUNT(*) AS n FROM jobs WHERE {where_sql}", tuple(params))
    total = int(total_row["n"]) if total_row else 0

    items = qall(
        f"SELECT {_LIST_FIELDS} FROM jobs WHERE {where_sql} "
        f"ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset),
    )
    return items, total


def insert_artifact(
    job_id: str, filename: str, content_type: str, data: bytes
) -> int:
    """Insert a binary artifact bound to a job. Returns artifact id."""
    row = execute(
        "INSERT INTO job_artifacts (job_id, filename, content_type, data, byte_size) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (job_id, filename, content_type, psycopg2_binary(data), len(data)),
    )
    return int(row["id"])


def get_artifact(artifact_id: int) -> dict[str, Any] | None:
    """Fetch artifact row by id (data is bytes)."""
    row = qone("SELECT * FROM job_artifacts WHERE id=%s", (artifact_id,))
    if row and isinstance(row["data"], memoryview):
        row["data"] = bytes(row["data"])
    return row


def get_job_by_id(*, job_id: str, project_id: str) -> dict[str, Any] | None:
    """Project-scoped get. Aliases `kind AS type` for the API surface."""
    return qone(
        "SELECT id, kind AS type, status, progress, progress_message, "
        "       result_ref, error, created_at, started_at, finished_at, "
        "       worker_id "
        "FROM jobs WHERE id = %s AND project_id = %s",
        (job_id, project_id),
    )


_API_STATUS_TO_DB = {
    "queued": "queued", "running": "running",
    "done": "success", "failed": "failed", "cancelled": "cancelled",
}


def list_jobs_by_project(*, project_id: str, limit: int = 50,
                         status: str | None = None) -> list[dict[str, Any]]:
    """API-facing list: aliases `kind AS type`, translates `status='done'` to DB 'success'."""
    if status is not None:
        db_status = _API_STATUS_TO_DB.get(status, status)
        return qall(
            "SELECT id, kind AS type, status, created_at, finished_at "
            "FROM jobs WHERE project_id = %s AND status = %s "
            "ORDER BY created_at DESC LIMIT %s",
            (project_id, db_status, limit),
        )
    return qall(
        "SELECT id, kind AS type, status, created_at, finished_at "
        "FROM jobs WHERE project_id = %s "
        "ORDER BY created_at DESC LIMIT %s",
        (project_id, limit),
    )


def cancel_job_if_pending(*, job_id: str, project_id: str) -> str:
    """Atomic cancel:
      - status='queued' → status='cancelled', finished_at=now() — returns 'cancelled'
      - status='running' → cancel_requested=true                — returns 'cancelling'
      - status terminal  → no-op                                — returns the existing status
    """
    with get_cursor(commit=True) as cur:
        cur.execute(
            "SELECT status FROM jobs WHERE id=%s AND project_id=%s FOR UPDATE",
            (job_id, project_id),
        )
        row = cur.fetchone()
        if row is None:
            return "not_found"
        cur_status = row["status"]
        if cur_status == "queued":
            cur.execute(
                "UPDATE jobs SET status='cancelled', finished_at=now() WHERE id=%s",
                (job_id,),
            )
            return "cancelled"
        if cur_status == "running":
            cur.execute(
                "UPDATE jobs SET cancel_requested=true WHERE id=%s",
                (job_id,),
            )
            return "cancelling"
        # Terminal — already success/failed/cancelled.
        return "done" if cur_status == "success" else cur_status


def purge_old(retention_days: int) -> tuple[int, int]:
    """Delete terminal jobs whose finished_at is older than retention_days.

    job_artifacts cascade via FK ON DELETE CASCADE.
    Returns (deleted_jobs, deleted_artifacts).
    """
    with get_cursor(commit=True) as cur:
        # First count artifacts that will cascade so we can return the number.
        cur.execute(
            "SELECT COUNT(*) AS n FROM job_artifacts a "
            "JOIN jobs j ON j.id = a.job_id "
            "WHERE j.status IN ('success','failed','cancelled') "
            "  AND j.finished_at < now() - make_interval(days => %s)",
            (retention_days,),
        )
        deleted_artifacts = int(cur.fetchone()["n"])
        cur.execute(
            "DELETE FROM jobs WHERE status IN ('success','failed','cancelled') "
            "AND finished_at < now() - make_interval(days => %s)",
            (retention_days,),
        )
        deleted_jobs = cur.rowcount
        return deleted_jobs, deleted_artifacts
