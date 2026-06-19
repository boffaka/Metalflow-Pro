"""
Sync API — offline-first mutation sync with conflict detection.

Endpoints:
  POST /api/v1/projects/{pid}/sync/push                       — Push mutations
  GET  /api/v1/projects/{pid}/sync/conflicts                  — List unresolved conflicts
  POST /api/v1/projects/{pid}/sync/conflicts/{cid}/resolve    — Resolve a conflict
  GET  /api/v1/projects/{pid}/sync/pull                       — Pull changes since timestamp
"""
from __future__ import annotations

import json
import logging
import psycopg2
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

logger = logging.getLogger("mpdpms.sync")

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, paginated_qall
    from ..audit import record_event
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, paginated_qall
    from audit import record_event

router = APIRouter(tags=["sync"])


# ─── Pydantic models ─────────────────────────────────────────────────────────

class MutationItem(BaseModel):
    entity_type: str
    entity_id: str
    action: str
    field_changes: dict = Field(default_factory=dict)
    client_timestamp: str


class SyncRequest(BaseModel):
    mutations: List[MutationItem]


class ConflictResolution(BaseModel):
    resolution: str = Field(..., pattern=r"^(local|remote|manual)$")
    manual_values: Optional[dict] = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _serialize(row: dict) -> dict:
    out = dict(row)
    for k in ("id", "project_id", "user_id", "entity_id", "conflict_id"):
        if out.get(k):
            out[k] = str(out[k])
    for k in ("created_at", "resolved_at", "timestamp", "client_timestamp"):
        if out.get(k):
            out[k] = str(out[k])
    return out


def _detect_field_conflict(
    pid: str, entity_type: str, entity_id: str, field_name: str, client_ts: str
) -> Optional[dict]:
    """Check if a server-side change exists for the same field after client_ts."""
    row = qone(
        "SELECT * FROM audit_events "
        "WHERE project_id = %s AND entity_type = %s AND entity_id = %s "
        "AND field_name = %s AND timestamp > %s "
        "ORDER BY timestamp DESC LIMIT 1",
        (pid, entity_type, entity_id, field_name, client_ts),
    )
    return row


# ─── Push mutations ──────────────────────────────────────────────────────────

@router.post("/{pid}/sync/push")
def push_mutations(pid: str, body: SyncRequest, user=Depends(project_user)):
    """Receive offline mutations. Detect field-level conflicts or enqueue."""
    try:
        accepted = []
        conflicts = []

        for mut in body.mutations:
            has_conflict = False

            for field_name, new_value in mut.field_changes.items():
                server_change = _detect_field_conflict(
                    pid, mut.entity_type, mut.entity_id, field_name, mut.client_timestamp,
                )
                if server_change:
                    has_conflict = True
                    conflict_id = str(uuid.uuid4())
                    execute(
                        "INSERT INTO sync_conflicts "
                        "(id, project_id, user_id, entity_type, entity_id, field_name, "
                        "client_value, server_value, client_timestamp, server_timestamp) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s) RETURNING id",
                        (
                            conflict_id, pid, user["id"],
                            mut.entity_type, mut.entity_id, field_name,
                            json.dumps(new_value), json.dumps(server_change.get("new_value")),
                            mut.client_timestamp, str(server_change.get("timestamp")),
                        ),
                    )
                    conflicts.append({
                        "conflict_id": conflict_id,
                        "entity_type": mut.entity_type,
                        "entity_id": mut.entity_id,
                        "field_name": field_name,
                    })

            if not has_conflict:
                queue_id = str(uuid.uuid4())
                execute(
                    "INSERT INTO sync_queue "
                    "(id, project_id, user_id, entity_type, entity_id, action, "
                    "field_changes, client_timestamp) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) RETURNING id",
                    (
                        queue_id, pid, user["id"],
                        mut.entity_type, mut.entity_id, mut.action,
                        json.dumps(mut.field_changes), mut.client_timestamp,
                    ),
                )
                accepted.append({
                    "queue_id": queue_id,
                    "entity_type": mut.entity_type,
                    "entity_id": mut.entity_id,
                })

                record_event(
                    user_id=user["id"],
                    project_id=pid,
                    entity_type=mut.entity_type,
                    entity_id=mut.entity_id,
                    action=mut.action,
                    new_value=mut.field_changes,
                    source="offline_sync",
                )

        return {"accepted": accepted, "conflicts": conflicts}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


# ─── List conflicts ──────────────────────────────────────────────────────────

@router.get("/{pid}/sync/conflicts")
def list_conflicts(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    """List unresolved sync conflicts for a project."""
    try:
        rows = paginated_qall(
            "SELECT * FROM sync_conflicts "
            "WHERE project_id = %s AND resolved_at IS NULL "
            "ORDER BY created_at DESC",
            (pid,), limit=limit, offset=offset)
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ─── Resolve conflict ────────────────────────────────────────────────────────

@router.post("/{pid}/sync/conflicts/{cid}/resolve")
def resolve_conflict(pid: str, cid: str, body: ConflictResolution, user=Depends(project_user)):
    """Resolve a sync conflict with local, remote, or manual resolution."""
    try:
        conflict = qone(
            "SELECT * FROM sync_conflicts WHERE id = %s AND project_id = %s AND resolved_at IS NULL",
            (cid, pid),
        )
        if not conflict:
            raise HTTPException(404, "Conflict not found or already resolved")

        if body.resolution == "manual" and not body.manual_values:
            raise HTTPException(400, "manual_values required for manual resolution")

        resolved_value = None
        if body.resolution == "local":
            resolved_value = conflict.get("client_value")
        elif body.resolution == "remote":
            resolved_value = conflict.get("server_value")
        elif body.resolution == "manual":
            resolved_value = body.manual_values

        row = execute(
            "UPDATE sync_conflicts SET resolved_at = NOW(), resolution = %s, "
            "resolved_value = %s::jsonb, resolved_by = %s "
            "WHERE id = %s AND project_id = %s RETURNING *",
            (body.resolution, json.dumps(resolved_value), user["id"], cid, pid),
        )

        record_event(
            user_id=user["id"],
            project_id=pid,
            entity_type=conflict.get("entity_type", "unknown"),
            entity_id=conflict.get("entity_id"),
            action="sync_conflict_resolved",
            old_value={"resolution": body.resolution},
            new_value=resolved_value,
            source="offline_sync",
        )

        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


# ─── Pull changes ────────────────────────────────────────────────────────────

@router.get("/{pid}/sync/pull")
def pull_changes(
    pid: str,
    since: str = Query(..., description="ISO 8601 timestamp"),
    limit: int = Query(500, ge=1, le=5000),
    user=Depends(project_user),
):
    """Pull audit events (changes) since a given timestamp."""
    try:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Invalid ISO 8601 timestamp for 'since'")

        rows = qall(
            "SELECT * FROM audit_events "
            "WHERE project_id = %s AND timestamp > %s "
            "ORDER BY timestamp ASC LIMIT %s",
            (pid, since_dt, limit),
        )
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
