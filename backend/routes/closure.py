# backend/routes/closure.py
"""
Mine closure planning endpoints:
  POST /api/v1/projects/{pid}/closure/plan          — add closure activity
  GET  /api/v1/projects/{pid}/closure/cost-estimate — aggregate cost provision
"""
from __future__ import annotations
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from ..db import conn, release
    from ..auth import project_user
except ImportError:
    from db import conn, release
    from auth import project_user

router = APIRouter(tags=["closure"])

VALID_PHASES = {"progressive", "final", "post_closure"}


class ClosureItemIn(BaseModel):
    phase: str
    component: Optional[str] = None
    activity: str
    year_target: Optional[int] = None
    unit_cost_usd: float = Field(ge=0)
    quantity: float = Field(default=1.0, ge=0)
    unit: Optional[str] = None
    success_criteria: Optional[str] = None
    responsible: Optional[str] = None


@router.post("/{pid}/closure/plan", status_code=201)
async def add_closure_activity(pid: str, body: ClosureItemIn, _auth=Depends(project_user)):
    if body.phase not in VALID_PHASES:
        raise HTTPException(
            status_code=422,
            detail=f"phase must be one of {sorted(VALID_PHASES)}"
        )
    total_cost = round(body.unit_cost_usd * body.quantity, 2)
    record_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO closure_plan_items
                   (id, project_id, phase, component, activity,
                    year_target, unit_cost_usd, quantity, unit,
                    total_cost_usd, success_criteria, responsible)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (record_id, pid, body.phase, body.component, body.activity,
                 body.year_target, body.unit_cost_usd, body.quantity, body.unit,
                 total_cost, body.success_criteria, body.responsible)
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    return {
        "id": record_id,
        "phase": body.phase,
        "activity": body.activity,
        "total_cost_usd": total_cost,
    }


@router.get("/{pid}/closure/cost-estimate")
async def get_cost_estimate(pid: str, _auth=Depends(project_user)):
    """Aggregate total closure provision and break down by phase."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT phase, SUM(total_cost_usd) as phase_total
                   FROM closure_plan_items
                   WHERE project_id = %s
                   GROUP BY phase
                   ORDER BY phase""",
                (pid,)
            )
            rows = cur.fetchall()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    by_phase = {r[0]: float(round(r[1], 2)) for r in rows}
    total = float(round(sum(by_phase.values()), 2))
    return {
        "total_provision_usd": total,
        "by_phase": by_phase,
        "note": "Regulatory financial guarantee requirement per ANCOLD/MAC",
    }
