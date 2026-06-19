"""Ramp-up factors — monthly production factor management."""
from __future__ import annotations
import logging
import psycopg2
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user
    from ..db import qall, execute
except ImportError:
    from auth import project_user
    from db import qall, execute

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["rampup"])

_WRITE_ROLES = ("Project Manager", "Cost Engineer")


class RampupIn(BaseModel):
    month: int = Field(..., ge=1, le=60)
    factor_pct: float = Field(..., ge=0.0, le=100.0)
    notes: Optional[str] = None


def _serialize(row: dict) -> dict:
    out = dict(row)
    if out.get("id"):
        out["id"] = str(out["id"])
    if out.get("project_id"):
        out["project_id"] = str(out["project_id"])
    out["factor_pct"] = float(out.get("factor_pct") or 0)
    return out


@router.get("/rampup")
def list_rampup(pid: str, user=Depends(project_user)):
    try:
        rows = qall("SELECT * FROM rampup_factors WHERE project_id=%s ORDER BY month", (pid,)) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/rampup", status_code=201)
def upsert_rampup(pid: str, body: RampupIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant pour définir les facteurs de ramp-up")
        row = execute(
            "INSERT INTO rampup_factors (project_id, month, factor_pct, notes) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (project_id, month) DO UPDATE SET factor_pct=EXCLUDED.factor_pct, notes=EXCLUDED.notes "
            "RETURNING *",
            (pid, body.month, body.factor_pct, body.notes)
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/rampup/{month}")
def delete_rampup(pid: str, month: int, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        execute("DELETE FROM rampup_factors WHERE project_id=%s AND month=%s", (pid, month))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/rampup/cumulative")
def get_rampup_cumulative(pid: str, user=Depends(project_user)):
    """Return all 60 months. Undefined months default to 100.0%."""
    try:
        defined = qall("SELECT month, factor_pct FROM rampup_factors WHERE project_id=%s", (pid,)) or []
        defined_map = {int(r["month"]): float(r["factor_pct"]) for r in defined}
        return [{"month": m, "factor_pct": defined_map.get(m, 100.0)} for m in range(1, 61)]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
