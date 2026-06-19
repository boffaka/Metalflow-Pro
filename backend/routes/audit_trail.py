"""
Audit Trail API — query and verify immutable audit events.

Endpoints:
  GET /api/v1/projects/{pid}/audit/events  — List audit events with filters
  GET /api/v1/projects/{pid}/audit/verify   — Verify checksum chain integrity
"""
from __future__ import annotations

import hashlib
import json
import logging
import psycopg2
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

logger = logging.getLogger("mpdpms.audit_trail")

try:
    from ..auth import project_user, require_role
    from ..db import qall
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user, require_role
    from db import qall

router = APIRouter(tags=["audit"])

_pm_only = require_role("Project Manager")


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _serialize(row: dict) -> dict:
    out = dict(row)
    for k in ("id", "project_id", "user_id", "entity_id"):
        if out.get(k):
            out[k] = str(out[k])
    if out.get("timestamp"):
        out["timestamp"] = str(out["timestamp"])
    return out


def _compute_checksum(event: dict, previous_checksum: str) -> str:
    """Recompute SHA-256 using the same algorithm as audit.py."""
    canonical = json.dumps(
        {k: v for k, v in sorted(event.items()) if k != "checksum"},
        sort_keys=True,
        default=str,
    )
    raw = f"{previous_checksum}:{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ─── List audit events ───────────────────────────────────────────────────────

@router.get("/{pid}/audit/events")
def list_audit_events(
    pid: str,
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user=Depends(project_user),
):
    """List audit events for a project with optional filters."""
    try:
        clauses = ["project_id = %s"]
        params: list = [pid]

        if entity_type:
            clauses.append("entity_type = %s")
            params.append(entity_type)
        if entity_id:
            clauses.append("entity_id = %s")
            params.append(entity_id)
        if action:
            clauses.append("action = %s")
            params.append(action)

        where = " AND ".join(clauses)
        params.extend([limit, offset])

        rows = qall(
            f"SELECT * FROM audit_events WHERE {where} "
            f"ORDER BY timestamp DESC LIMIT %s OFFSET %s",
            params,
        )
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ─── Verify checksum chain ───────────────────────────────────────────────────

@router.get("/{pid}/audit/verify")
def verify_checksum_chain(pid: str, user=Depends(_pm_only)):
    """Verify the integrity of the audit event checksum chain for a project.

    Iterates all events in chronological order, recomputes each checksum,
    and compares against the stored value.
    """
    try:
        rows = qall(
            "SELECT * FROM audit_events WHERE project_id = %s ORDER BY timestamp ASC",
            (pid,),
        )

        previous_checksum = "0" * 64
        for idx, row in enumerate(rows):
            event = {
                "user_id": row.get("user_id"),
                "project_id": row.get("project_id"),
                "entity_type": row.get("entity_type"),
                "entity_id": row.get("entity_id"),
                "action": row.get("action"),
                "field_name": row.get("field_name"),
                "old_value": row.get("old_value"),
                "new_value": row.get("new_value"),
                "source": row.get("source"),
                "ip_address": row.get("ip_address"),
            }
            expected = _compute_checksum(event, previous_checksum)
            stored = row.get("checksum", "")

            if expected != stored:
                return {
                    "verified": False,
                    "count": len(rows),
                    "broken_at_index": idx,
                }
            previous_checksum = stored

        return {
            "verified": True,
            "count": len(rows),
            "broken_at_index": None,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
