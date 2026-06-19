"""Decisions log — CRUD for project decisions."""
from __future__ import annotations
import logging
import psycopg2
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user
    from ..db import qone, execute, build_update_sets, paginated_qall
except ImportError:
    from auth import project_user
    from db import qone, execute, build_update_sets, paginated_qall

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["decisions"])

_WRITE_ROLES = ("Project Manager", "Process Engineer", "Cost Engineer", "Metallurgist")
_VALID_STATUSES = {"open", "accepted", "rejected", "deferred"}


class DecisionIn(BaseModel):
    title: str
    description: Optional[str] = None
    gate_id: Optional[str] = None
    status: str = "open"


class DecisionPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    gate_id: Optional[str] = None


def _serialize(row: dict) -> dict:
    out = dict(row)
    for k in ("id", "project_id", "gate_id", "decided_by"):
        if out.get(k):
            out[k] = str(out[k])
    for k in ("created_at", "updated_at", "decided_at"):
        if out.get(k):
            out[k] = str(out[k])
    return out


@router.get("/decisions")
def list_decisions(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM decisions WHERE project_id=%s ORDER BY created_at DESC", (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/decisions", status_code=201)
def create_decision(pid: str, body: DecisionIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant pour créer une décision")
        if body.status not in _VALID_STATUSES:
            raise HTTPException(422, f"Statut invalide. Valeurs acceptées: {_VALID_STATUSES}")
        row = execute(
            "INSERT INTO decisions (project_id, gate_id, title, description, status, decided_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
            (pid, body.gate_id or None, body.title, body.description, body.status, user["id"])
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/decisions/{did}")
def patch_decision(pid: str, did: str, body: DecisionPatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant pour modifier une décision")
        existing = qone("SELECT * FROM decisions WHERE id=%s AND project_id=%s", (did, pid))
        if not existing:
            raise HTTPException(404, "Décision introuvable")
        if body.status and body.status not in _VALID_STATUSES:
            raise HTTPException(422, f"Statut invalide. Valeurs acceptées: {_VALID_STATUSES}")
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            return _serialize(existing)
        fields.append("updated_at = NOW()")
        vals += [did, pid]
        row = execute(
            f"UPDATE decisions SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *",
            vals
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/decisions/{did}")
def delete_decision(pid: str, did: str, user=Depends(project_user)):
    try:
        if user["role"] != "Project Manager":
            raise HTTPException(403, "Seul un Project Manager peut supprimer une décision")
        existing = qone("SELECT id FROM decisions WHERE id=%s AND project_id=%s", (did, pid))
        if not existing:
            raise HTTPException(404, "Décision introuvable")
        execute("DELETE FROM decisions WHERE id=%s", (did,))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
