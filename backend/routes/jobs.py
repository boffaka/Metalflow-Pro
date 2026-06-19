"""Generic /jobs API: status, list, cancel, artifact download.

Job submission lives next to each resource (sensitivity, optimize, ni43101) but
all submissions go through `submit_job(...)` exported here.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response

try:
    from ..auth import project_user
    from ..db import qone
    from ..jobs.repo import (
        insert_job, get_job_by_id, list_jobs_by_project, cancel_job_if_pending,
    )
    from .jobs_schemas import (
        JobSubmitResponse, JobStatusResponse, JobListItem, JobListResponse,
        ProgressFragment,
    )
except ImportError:  # pragma: no cover
    from auth import project_user
    from db import qone
    from jobs.repo import (
        insert_job, get_job_by_id, list_jobs_by_project, cancel_job_if_pending,
    )
    from routes.jobs_schemas import (
        JobSubmitResponse, JobStatusResponse, JobListItem, JobListResponse,
        ProgressFragment,
    )

router = APIRouter(prefix="/api/v1/projects", tags=["jobs"])
logger = logging.getLogger("mpdpms")


# Header-injection-safe Content-Disposition value. Strips control chars
# (CR/LF/TAB/etc.), path separators, quotes/backslashes, and parameter
# delimiters (; , =) that would let a crafted filename break out of the
# value and inject extra parameters or headers. Followed by RFC 6266:
# `filename=` carries an ASCII-only fallback, `filename*=UTF-8''…` the
# percent-encoded original.
_FILENAME_STRIP = re.compile(r'[\x00-\x1f\x7f/\\";,=]')
_WHITESPACE_RUN = re.compile(r"\s+")


def _content_disposition_attachment(raw_name: str | None) -> str:
    cleaned = _FILENAME_STRIP.sub("", raw_name or "")
    cleaned = _WHITESPACE_RUN.sub(" ", cleaned).strip(". ")
    if not cleaned:
        cleaned = "download"
    ascii_name = cleaned.encode("ascii", "ignore").decode("ascii").strip(". ") or "download"
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(cleaned, safe='')}"
    )


# ─── Submission helper (used by the */async endpoints in other routers) ─────

def submit_job(*, project_id: str, user_id: str, job_type: str,
               payload: dict[str, Any]) -> JobSubmitResponse:
    """Insert a queued job. Caller has already done auth.

    NOTIFY is emitted by the AFTER INSERT trigger created in migration
    20260426_000035 — there is no application-side notify call.
    `insert_job` commits internally.
    """
    job_id = insert_job(
        kind=job_type,
        project_id=project_id,
        created_by=user_id,
        payload=payload,
    )
    return JobSubmitResponse(job_id=str(job_id), status="queued")


# ─── Status / list / cancel / artifact ────────────────────────────────────────

# DB → API status translation (DB stores 'success', API exposes 'done').
_DB_STATUS_TO_API = {
    "queued": "queued", "running": "running",
    "success": "done", "failed": "failed", "cancelled": "cancelled",
}


def _to_status_response(row: dict) -> JobStatusResponse:
    # The jobs table stores `progress` as INT 0..100 and `progress_message`
    # as a separate TEXT column (see migration 20260426_000035). We map both
    # to the API's ProgressFragment(current/total/message) shape.
    pct = int(row.get("progress") or 0)
    return JobStatusResponse(
        job_id=str(row["id"]),
        type=row["type"],
        status=_DB_STATUS_TO_API.get(row["status"], row["status"]),
        progress=ProgressFragment(
            current=pct,
            total=100,
            message=row.get("progress_message"),
        ),
        result_ref=row.get("result_ref"),
        error=row.get("error"),
        created_at=row["created_at"].isoformat() if row.get("created_at") else None,
        started_at=row["started_at"].isoformat() if row.get("started_at") else None,
        finished_at=row["finished_at"].isoformat() if row.get("finished_at") else None,
    )


@router.get("/{pid}/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(pid: str, job_id: str, user=Depends(project_user)):
    row = get_job_by_id(job_id=job_id, project_id=pid)
    if not row:
        raise HTTPException(404, "Job not found")
    return _to_status_response(row)


_VALID_STATUSES = {"queued", "running", "done", "failed", "cancelled"}


@router.get("/{pid}/jobs", response_model=JobListResponse)
def list_project_jobs(pid: str, limit: int = 50, status: str | None = None,
                      user=Depends(project_user)):
    if status and status not in _VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(_VALID_STATUSES)}")
    rows = list_jobs_by_project(
        project_id=pid, limit=min(max(limit, 1), 200), status=status,
    )
    return JobListResponse(jobs=[
        JobListItem(
            job_id=str(r["id"]), type=r["type"],
            status=_DB_STATUS_TO_API.get(r["status"], r["status"]),
            created_at=r["created_at"].isoformat(),
            finished_at=r["finished_at"].isoformat() if r.get("finished_at") else None,
        ) for r in rows
    ])


@router.post("/{pid}/jobs/{job_id}/cancel")
def cancel_job(pid: str, job_id: str, user=Depends(project_user)):
    row = get_job_by_id(job_id=job_id, project_id=pid)
    if not row:
        raise HTTPException(404, "Job not found")
    # row["status"] holds the DB value (success/failed/cancelled for terminal).
    if row["status"] in ("success", "failed", "cancelled"):
        return {"job_id": job_id,
                "status": _DB_STATUS_TO_API.get(row["status"], row["status"])}
    new_status = cancel_job_if_pending(job_id=job_id, project_id=pid)
    # cancel_job_if_pending already returns API-flavored values
    # ('cancelled' | 'cancelling' | 'done' | 'failed' | 'not_found').
    return {"job_id": job_id, "status": new_status}


@router.get("/{pid}/jobs/{job_id}/artifact")
def download_artifact(pid: str, job_id: str, user=Depends(project_user)):
    row = get_job_by_id(job_id=job_id, project_id=pid)
    if not row:
        raise HTTPException(404, "Job not found")
    ref = row.get("result_ref") or {}
    if ref.get("kind") != "job_artifact":
        raise HTTPException(404, "No artifact for this job")
    artifact = qone(
        "SELECT filename, content_type, data FROM job_artifacts "
        "WHERE id = %s AND job_id = %s",
        (ref["id"], job_id),
    )
    if not artifact:
        raise HTTPException(404, "Artifact missing")
    return Response(
        content=bytes(artifact["data"]),
        media_type=artifact["content_type"],
        headers={"Content-Disposition": _content_disposition_attachment(artifact.get("filename"))},
    )
