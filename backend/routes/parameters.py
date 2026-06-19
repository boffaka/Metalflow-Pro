"""
MPDPMS — Project parameter management routes.
CRUD for project_params with audit trail integration.
"""
from __future__ import annotations

import logging
import psycopg2

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger("mpdpms.parameters")

try:
    from ..auth import project_user
    from ..db import qone, qall, execute
    from ..audit import record_event
except ImportError:
    from auth import project_user
    from db import qone, qall, execute
    from audit import record_event

router = APIRouter(prefix="/api/v1/projects", tags=["parameters"])


class ParamSetIn(BaseModel):
    param_key: str
    value: Optional[float] = None
    value_text: Optional[str] = None
    source: str = "M"
    source_detail: Optional[str] = None


@router.get("/{pid}/params")
def list_project_params(pid: str, user=Depends(project_user)):
    try:
        rows = qall(
            "SELECT DISTINCT ON (param_key) id, param_key, value, value_text, "
            "source, source_detail, set_by, set_at, version "
            "FROM project_params WHERE project_id = %s "
            "ORDER BY param_key, version DESC",
            (pid,),
        )
        return rows or []
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/params/{key}")
def get_project_param(pid: str, key: str, user=Depends(project_user)):
    try:
        rows = qall(
            "SELECT id, param_key, value, value_text, source, source_detail, "
            "set_by, set_at, version "
            "FROM project_params WHERE project_id = %s AND param_key = %s "
            "ORDER BY version DESC",
            (pid, key),
        )
        if not rows:
            raise HTTPException(404, f"Parametre '{key}' non trouve pour ce projet")
        return {"current": rows[0], "history": rows}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.put("/{pid}/params")
def set_project_param(pid: str, body: ParamSetIn, user=Depends(project_user)):
    try:
        return _set_project_param_impl(pid, body, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _set_project_param_impl(pid: str, body: ParamSetIn, user):
    reg = qone("SELECT * FROM parameter_registry WHERE key = %s", (body.param_key,))
    if not reg:
        raise HTTPException(400, f"Cle '{body.param_key}' non enregistree dans parameter_registry")

    if body.value is not None and reg.get("min_value") is not None:
        if body.value < float(reg["min_value"]) or body.value > float(reg["max_value"]):
            raise HTTPException(422, (
                f"{body.param_key}: {body.value} hors limites "
                f"[{reg['min_value']}, {reg['max_value']}]"
            ))

    current = qone(
        "SELECT value, value_text, version FROM project_params "
        "WHERE project_id = %s AND param_key = %s ORDER BY version DESC LIMIT 1",
        (pid, body.param_key),
    )
    new_version = (current["version"] + 1) if current else 1
    old_value = current["value"] if current else None

    row = execute(
        "INSERT INTO project_params "
        "(project_id, param_key, value, value_text, source, source_detail, set_by, version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (pid, body.param_key, body.value, body.value_text,
         body.source, body.source_detail, user["id"], new_version),
    )

    record_event(
        user_id=user["id"],
        project_id=pid,
        entity_type="project_params",
        entity_id=str(row["id"]),
        action="update" if current else "create",
        field_name=body.param_key,
        old_value=old_value,
        new_value=body.value or body.value_text,
        source="web",
    )
    return row


@router.get("/{pid}/params-registry")
def list_registry(pid: str, user=Depends(project_user)):
    try:
        return qall("SELECT * FROM parameter_registry ORDER BY category, key") or []
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
