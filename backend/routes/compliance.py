"""
Compliance API — NI 43-101 review workflows and data snapshots.

Endpoints:
  POST /api/v1/projects/{pid}/compliance/workflows       — Create workflow
  GET  /api/v1/projects/{pid}/compliance/workflows       — List workflows
  GET  /api/v1/projects/{pid}/compliance/workflows/{wid} — Get workflow detail
  POST /api/v1/projects/{pid}/compliance/workflows/{wid}/transition — State transition
  GET  /api/v1/projects/{pid}/compliance/snapshots       — List snapshots
"""
from __future__ import annotations

import hashlib
import json
import logging
import psycopg2
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("mpdpms.compliance")

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, paginated_qall
    from ..audit import record_event
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, paginated_qall
    from audit import record_event

router = APIRouter(tags=["compliance"])


# ─── Pydantic models ─────────────────────────────────────────────────────────

class WorkflowCreate(BaseModel):
    title: str
    report_type: str = "ni43101"


class ReviewAction(BaseModel):
    action: str = Field(..., pattern=r"^(submit|start_review|approve|reject)$")
    comment: Optional[str] = None


class CommentCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


# ─── Helpers ──────────────────────────────────────────────────────────────────

_VALID_TRANSITIONS = {
    ("draft", "submitted"),
    ("submitted", "under_review"),
    ("under_review", "approved"),
    ("under_review", "rejected"),
    ("rejected", "draft"),
}


def _validate_transition(current: str, target: str) -> bool:
    """Return True if the state transition is allowed."""
    return (current, target) in _VALID_TRANSITIONS


def _serialize(row: dict) -> dict:
    out = dict(row)
    if "updated_at" not in out:
        out["updated_at"] = out.get("reviewed_at") or out.get("submitted_at") or out.get("created_at")
    for k in ("id", "project_id", "user_id", "snapshot_id", "created_by", "submitted_by", "reviewed_by"):
        if out.get(k):
            out[k] = str(out[k])
    for k in ("created_at", "updated_at", "submitted_at", "reviewed_at"):
        if out.get(k):
            out[k] = str(out[k])
    return out


_ACTION_TO_STATE = {
    "submit": "submitted",
    "start_review": "under_review",
    "approve": "approved",
    "reject": "rejected",
}


def _serialize_comment(row: dict) -> dict:
    return {
        "id": str(row.get("id")),
        "user_id": str(row.get("user_id")),
        "user": row.get("user") or row.get("email") or str(row.get("user_id")),
        "text": row.get("text") or row.get("comment") or "",
        "created_at": str(row.get("created_at")) if row.get("created_at") else None,
    }


def _workflow_comments(workflow_id: str) -> list[dict]:
    rows = qall(
        "SELECT ac.id, ac.user_id, "
        "       COALESCE(u.full_name, u.email, ac.user_id::text) AS user, "
        "       ac.comment AS text, ac.created_at "
        "FROM approval_comments ac "
        "LEFT JOIN users u ON u.id = ac.user_id "
        "WHERE ac.workflow_id = %s "
        "ORDER BY ac.created_at ASC",
        (workflow_id,),
    )
    return [_serialize_comment(r) for r in rows]


def _workflow_snapshot(snapshot_id: str | None) -> dict | None:
    if not snapshot_id:
        return None
    row = qone(
        "SELECT id, snapshot_data, checksum, created_at "
        "FROM data_snapshots WHERE id = %s",
        (snapshot_id,),
    )
    if not row:
        return None
    return {
        "id": str(row.get("id")),
        "data": row.get("snapshot_data") or {},
        "checksum": row.get("checksum"),
        "created_at": str(row.get("created_at")) if row.get("created_at") else None,
    }


def _hydrate_workflow(row: dict) -> dict:
    out = _serialize(row)
    workflow_id = str(row["id"])
    out["comments"] = _workflow_comments(workflow_id)
    out["snapshot"] = _workflow_snapshot(str(row["snapshot_id"])) if row.get("snapshot_id") else None
    return out

# Tables captured in compliance snapshots
_SNAPSHOT_TABLES = [
    "design_criteria",
    "operating_envelopes",
    "lims_b1",
    "lims_d1",
    "lims_c2",
    "lims_e1",
    "lims_a1",
]


def _create_snapshot(pid: str, workflow_id: str, user_id: str) -> dict:
    """Gather project data into a JSONB snapshot with SHA-256 hash."""
    data: dict = {}
    for table in _SNAPSHOT_TABLES:
        try:
            rows = qall(f"SELECT * FROM {table} WHERE project_id=%s", (pid,))
            data[table] = rows
        except Exception:  # intentional: fallback to empty/default on optional data
            data[table] = []

    payload = json.dumps(data, sort_keys=True, default=str)
    data_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    snapshot_id = str(uuid.uuid4())

    row = execute(
        "INSERT INTO data_snapshots "
        "(id, project_id, workflow_id, snapshot_data, checksum, created_by) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s) RETURNING *",
        (snapshot_id, pid, workflow_id, payload, data_hash, user_id),
    )
    return row


# ─── Create workflow ──────────────────────────────────────────────────────────

@router.post("/{pid}/compliance/workflows", status_code=201)
def create_workflow(pid: str, body: WorkflowCreate, user=Depends(project_user)):
    """Create a new compliance review workflow in draft state."""
    try:
        workflow_id = str(uuid.uuid4())
        row = execute(
            "INSERT INTO approval_workflows "
            "(id, project_id, title, report_type, status, submitted_by) "
            "VALUES (%s, %s, %s, %s, 'draft', %s) RETURNING *",
            (workflow_id, pid, body.title, body.report_type, user["id"]),
        )

        record_event(
            user_id=user["id"],
            project_id=pid,
            entity_type="compliance_workflow",
            entity_id=workflow_id,
            action="create",
            new_value={"title": body.title, "report_type": body.report_type, "status": "draft"},
        )
        return _hydrate_workflow(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


# ─── List workflows ──────────────────────────────────────────────────────────

@router.get("/{pid}/compliance/workflows")
def list_workflows(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    """List all compliance workflows for a project."""
    try:
        rows = paginated_qall(
            "SELECT * FROM approval_workflows WHERE project_id = %s ORDER BY created_at DESC",
            (pid,), limit=limit, offset=offset)
        return [_hydrate_workflow(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ─── Get workflow detail ──────────────────────────────────────────────────────

@router.get("/{pid}/compliance/workflows/{wid}")
def get_workflow(pid: str, wid: str, user=Depends(project_user)):
    """Get a single compliance workflow by ID."""
    try:
        row = qone(
            "SELECT * FROM approval_workflows WHERE id = %s AND project_id = %s",
            (wid, pid),
        )
        if not row:
            raise HTTPException(404, "Workflow not found")
        return _hydrate_workflow(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ─── Transition workflow state ────────────────────────────────────────────────

@router.post("/{pid}/compliance/workflows/{wid}/transition")
def transition_workflow(pid: str, wid: str, body: ReviewAction, user=Depends(project_user)):
    """Advance workflow state via approve/reject action."""
    try:
        workflow = qone(
            "SELECT * FROM approval_workflows WHERE id = %s AND project_id = %s",
            (wid, pid),
        )
        if not workflow:
            raise HTTPException(404, "Workflow not found")

        current_status = workflow["status"]
        target_status = _ACTION_TO_STATE.get(body.action)

        if not target_status:
            raise HTTPException(400, f"Unknown action: {body.action}")

        if not _validate_transition(current_status, target_status):
            raise HTTPException(
                409,
                f"Invalid transition: {current_status} -> {target_status}",
            )

        # Approve: user must have is_qp flag; create snapshot
        snapshot_id = None
        if body.action == "approve":
            if not user.get("is_qp"):
                raise HTTPException(403, "Only a Qualified Person (QP) can approve")
            snapshot = _create_snapshot(pid, wid, user["id"])
            snapshot_id = snapshot.get("id")

        # Reject: comment is required
        if body.action == "reject":
            if not body.comment:
                raise HTTPException(400, "A comment is required when rejecting")

        # Perform the update
        update_fields = "status = %s"
        update_params: list = [target_status]
        if body.action == "submit":
            update_fields += ", submitted_at = NOW()"
        if body.action in ("start_review", "approve", "reject"):
            update_fields += ", reviewed_by = %s, reviewed_at = NOW()"
            update_params.append(user["id"])
        if snapshot_id:
            update_fields += ", snapshot_id = %s"
            update_params.append(snapshot_id)
        update_params.extend([wid, pid])

        row = execute(
            f"UPDATE approval_workflows SET {update_fields} "
            f"WHERE id = %s AND project_id = %s RETURNING *",
            update_params,
        )

        record_event(
            user_id=user["id"],
            project_id=pid,
            entity_type="compliance_workflow",
            entity_id=wid,
            action=body.action,
            old_value={"status": current_status},
            new_value={"status": target_status, "comment": body.comment},
        )
        return _hydrate_workflow(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


# ─── List snapshots ──────────────────────────────────────────────────────────

@router.get("/{pid}/compliance/snapshots")
def list_snapshots(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    """List all compliance data snapshots for a project."""
    try:
        rows = paginated_qall(
            "SELECT id, project_id, workflow_id, checksum, created_by, created_at "
            "FROM data_snapshots WHERE project_id = %s ORDER BY created_at DESC",
            (pid,), limit=limit, offset=offset)
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/{pid}/compliance/workflows/{wid}/comments", status_code=201)
def add_workflow_comment(pid: str, wid: str, body: CommentCreate, user=Depends(project_user)):
    """Attach a reviewer comment to a compliance workflow."""
    try:
        workflow = qone(
            "SELECT id FROM approval_workflows WHERE id = %s AND project_id = %s",
            (wid, pid),
        )
        if not workflow:
            raise HTTPException(404, "Workflow not found")

        comment_id = str(uuid.uuid4())
        row = execute(
            "INSERT INTO approval_comments (id, workflow_id, user_id, comment) "
            "VALUES (%s, %s, %s, %s) RETURNING *",
            (comment_id, wid, user["id"], body.text.strip()),
        )

        record_event(
            user_id=user["id"],
            project_id=pid,
            entity_type="compliance_workflow_comment",
            entity_id=comment_id,
            action="create",
            new_value={"workflow_id": wid},
        )
        return _serialize_comment(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
