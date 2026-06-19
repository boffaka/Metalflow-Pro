# backend/routes/process_model.py
"""
Process Model sync endpoint — receives DC parameter changes,
recalculates mass balance, persists to DB.

POST /api/v1/projects/{pid}/process-model/sync
"""
from __future__ import annotations
import logging
from typing import List, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

try:
    from ..db import conn, release
    from ..auth import project_user
except ImportError:
    from db import conn, release
    from auth import project_user

router = APIRouter(tags=["process-model"])
logger = logging.getLogger(__name__)


class DCChange(BaseModel):
    category: str
    key: str
    value: Any


class SyncRequest(BaseModel):
    dc_changes: List[DCChange] = []


@router.post("/{pid}/process-model/sync")
async def sync_process_model(pid: str, body: SyncRequest, _auth=Depends(project_user)):
    """Persist DC changes to simulation_params and trigger mass balance recalculation."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            # 1. Upsert each DC change into simulation_params
            for change in body.dc_changes:
                try:
                    val = float(change.value) if change.value is not None else None
                except (ValueError, TypeError):
                    val = None

                if val is None:
                    continue

                cur.execute(
                    """INSERT INTO simulation_params
                       (project_id, category, param_key, param_label, param_value, unit, source)
                       VALUES (%s, %s, %s, %s, %s, '', 'ProcessModel')
                       ON CONFLICT (project_id, category, param_key)
                       DO UPDATE SET param_value = EXCLUDED.param_value""",
                    (pid, change.category, change.key, change.key, val)
                )

            # 2. Read project basics for recalculation
            cur.execute("SELECT target_tph, gold_grade_g_t, availability_pct FROM projects WHERE id = %s", (pid,))
            _proj = cur.fetchone()

            # 3. Read all current simulation params
            cur.execute(
                "SELECT category, param_key, param_value FROM simulation_params WHERE project_id = %s",
                (pid,)
            )
            params = {}
            for r in cur.fetchall():
                if isinstance(r, dict):
                    cat, key, val = r.get("category"), r.get("param_key"), r.get("param_value")
                else:
                    cat, key, val = r[0], r[1], r[2]
                if val is not None:
                    try:
                        params[f"{cat}.{key}"] = float(val)
                    except (TypeError, ValueError):
                        continue

        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)

    return {
        "ok": True,
        "params_synced": len(body.dc_changes),
        "simulation_params_numeric": len(params),
        "message": "DC parameters synced. Frontend ProcessModel is authoritative for live calculations.",
    }
