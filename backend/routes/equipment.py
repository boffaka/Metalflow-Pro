"""
MPDPMS — Equipment CRUD routes.
"""
from __future__ import annotations

import logging
import psycopg2
from fastapi import APIRouter, HTTPException, Depends, Query

logger = logging.getLogger("mpdpms.equipment")

try:
    from ..auth import project_user
    from ..db import execute, build_update_sets, paginated_qall
    from ..models import EquipIn, EquipPatch
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import execute, build_update_sets, paginated_qall
    from models import EquipIn, EquipPatch

router = APIRouter(prefix="/api/v1/projects", tags=["equipment"])


@router.get("/{pid}/equipment")
def list_equipment(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        return paginated_qall("SELECT * FROM equipment WHERE project_id=%s ORDER BY created_at", (pid,), limit=limit, offset=offset)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/{pid}/equipment", status_code=201)
def add_equipment(pid: str, body: EquipIn, user=Depends(project_user)):
    try:
        return execute(
            "INSERT INTO equipment (project_id, equipment_tag, equipment_type, power_installed_kw, design_capacity_t_h, is_long_lead) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, body.equipment_tag, body.equipment_type, body.power_installed_kw, body.design_capacity_t_h, body.is_long_lead)
        )
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/{pid}/equipment/{eid}")
# SQL SAFETY: field names come from EquipPatch Pydantic model — user cannot inject arbitrary columns.
def patch_equipment(pid: str, eid: str, body: EquipPatch, user=Depends(project_user)):
    try:
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields: raise HTTPException(400, "Rien a mettre a jour")
        vals += [eid, pid]
        return execute(f"UPDATE equipment SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/{pid}/equipment/{eid}")
def delete_equipment(pid: str, eid: str, user=Depends(project_user)):
    try:
        execute("DELETE FROM equipment WHERE id=%s AND project_id=%s", (eid, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
