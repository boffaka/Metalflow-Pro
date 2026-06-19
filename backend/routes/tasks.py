"""
Failed async tasks — compatibility layer for React FailedTasks page.

Maps ``jobs`` rows (status failed) to the legacy task shape.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

try:
    from ..auth import project_user
    from ..db import qall
    from ..jobs.repo import get_job_by_id, get_payload
    from .jobs import submit_job
except ImportError:
    from auth import project_user
    from db import qall
    from jobs.repo import get_job_by_id, get_payload
    from routes.jobs import submit_job

logger = logging.getLogger("mpdpms.tasks")

router = APIRouter(prefix="/api/v1/projects", tags=["tasks"])


def _job_to_task(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "task_name": row.get("type") or row.get("kind") or "job",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "retries": int(row.get("attempt") or row.get("retry_count") or 0),
        "exception": row.get("error") or "",
        "resolved": False,
        "status": "failed",
    }


@router.get("/{pid}/tasks/failed")
def list_failed_tasks(
    pid: str,
    resolved: bool | None = None,
    limit: int = 50,
    user=Depends(project_user),
):
    """
    List failed background jobs for the project.

    ``resolved`` is accepted for API compatibility; only unresolved failures
    are returned today (failed jobs in ``jobs`` table).
    """
    lim = min(max(limit, 1), 200)
    if resolved is True:
        return []
    rows = qall(
        """
        SELECT id, type, status, error, created_at, finished_at
        FROM jobs
        WHERE project_id = %s AND status = 'failed'
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (pid, lim),
    ) or []
    return [_job_to_task(dict(r)) for r in rows]


@router.post("/{pid}/tasks/failed/{task_id}/retry")
def retry_failed_task(pid: str, task_id: str, user=Depends(project_user)):
    """Re-queue a failed job using its stored payload."""
    row = get_job_by_id(job_id=task_id, project_id=pid)
    if not row:
        raise HTTPException(404, "Task not found")
    if row.get("status") != "failed":
        raise HTTPException(400, "Only failed tasks can be retried")
    payload = get_payload(task_id) or {}
    job_type = row.get("type") or "retry"
    user_id = str(user.get("id") or user.get("sub") or "system")
    resp = submit_job(
        project_id=pid,
        user_id=user_id,
        job_type=job_type,
        payload={**payload, "retry_of": task_id},
    )
    return {"ok": True, "new_job_id": resp.job_id, "status": resp.status}
