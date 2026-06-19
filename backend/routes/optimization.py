"""Routes for optimisation jobs (sweep + NSGA-2).

Uses `optimization_jobs` table (created by migration 000033). For dev
(no Celery worker) the sweep/NSGA-2 run synchronously inside the route —
the job row is marked 'done' before the response returns.
"""
from __future__ import annotations

import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Depends

try:
    from ..auth import project_user
    from ..db import qone, execute
    from ..models import (
        SweepRequest, Nsga2Request,
        OptimizationJobResponse, ParetoFrontResponse,
    )
except ImportError:
    from auth import project_user
    from db import qone, execute
    from models import (
        SweepRequest, Nsga2Request,
        OptimizationJobResponse, ParetoFrontResponse,
    )

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["optimization"])
logger = logging.getLogger("mpdpms.optimization")


def _coerce_json(value):
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # intentional: graceful fallback
            return value
    return value


def _validate_compilation(pid: str, compilation_id: str):
    row = qone(
        "SELECT id FROM circuit_compilations WHERE id = %s AND project_id = %s",
        (compilation_id, pid),
    )
    if not row:
        raise HTTPException(404, "Compilation not found for this project")


@router.post("/optimization/sweep", status_code=201)
def create_sweep(pid: str, body: SweepRequest, user=Depends(project_user)):
    """Create a sweep job and run it synchronously in dev mode.

    Returns {job_id, status} — status will be 'done' or 'failed' in sync mode.
    In prod with a Celery worker, status may still be 'running'/'queued' when
    the response is returned.
    """
    _validate_compilation(pid, body.compilation_id)

    job_id = str(uuid.uuid4())
    variables_json = json.dumps([v.model_dump() for v in body.variables])
    constraints_json = json.dumps(body.constraints or [])

    execute(
        "INSERT INTO optimization_jobs "
        "(id, project_id, compilation_id, mode, objective, variables, constraints, status) "
        "VALUES (%s, %s, %s, 'sweep', %s, %s::jsonb, %s::jsonb, 'queued')",
        (job_id, pid, body.compilation_id, body.objective, variables_json, constraints_json),
    )

    # DEV MODE SYNC — execute the sweep synchronously. In prod with a Celery
    # worker attached, replace this with `run_sweep_task.delay(job_id)` and
    # let the worker handle the computation. For now we guarantee the job
    # reaches a terminal state before the response returns.
    try:
        try:
            from ..tasks.optimization import _run_sweep_sync
        except ImportError:
            from tasks.optimization import _run_sweep_sync
        _run_sweep_sync(job_id)
    except Exception:  # intentional: log and continue on optional operation
        logger.exception("sync sweep execution failed for job %s", job_id)
        # Make sure the row reflects failure; _run_sweep_sync also does this
        execute(
            "UPDATE optimization_jobs SET status='failed', completed_at=NOW() "
            "WHERE id = %s AND status NOT IN ('done','failed')",
            (job_id,),
        )

    # Re-fetch status
    row = qone("SELECT status FROM optimization_jobs WHERE id = %s", (job_id,))
    return {"job_id": job_id, "status": (row or {}).get("status", "unknown")}


@router.post("/optimization/nsga2", status_code=201)
def create_nsga2(pid: str, body: Nsga2Request, user=Depends(project_user)):
    """Create an NSGA-2 job and run synchronously in dev mode.

    See `/optimization/sweep` for sync/async semantics.
    """
    _validate_compilation(pid, body.compilation_id)

    job_id = str(uuid.uuid4())
    variables_json = json.dumps([v.model_dump() for v in body.variables])
    # Pack generations/population into constraints for the sync runner
    constraints_payload = body.constraints or []
    if isinstance(constraints_payload, list):
        # Augment with a dict carrying gen/pop
        meta = {"generations": body.generations}
        if body.population_size is not None:
            meta["population_size"] = body.population_size
        constraints_payload = constraints_payload + [meta]
    constraints_json = json.dumps(constraints_payload)
    objectives_json = json.dumps(body.objectives)

    execute(
        "INSERT INTO optimization_jobs "
        "(id, project_id, compilation_id, mode, objectives, variables, constraints, status) "
        "VALUES (%s, %s, %s, 'nsga2', %s::jsonb, %s::jsonb, %s::jsonb, 'queued')",
        (job_id, pid, body.compilation_id, objectives_json, variables_json, constraints_json),
    )

    # DEV MODE SYNC — see comment in create_sweep
    try:
        try:
            from ..tasks.optimization import _run_nsga2_sync
        except ImportError:
            from tasks.optimization import _run_nsga2_sync
        _run_nsga2_sync(job_id)
    except Exception:  # intentional: log and continue on optional operation
        logger.exception("sync nsga2 execution failed for job %s", job_id)
        execute(
            "UPDATE optimization_jobs SET status='failed', completed_at=NOW() "
            "WHERE id = %s AND status NOT IN ('done','failed')",
            (job_id,),
        )

    row = qone("SELECT status FROM optimization_jobs WHERE id = %s", (job_id,))
    return {"job_id": job_id, "status": (row or {}).get("status", "unknown")}


@router.get("/optimization/{job_id}", response_model=OptimizationJobResponse)
def get_optimization_job(pid: str, job_id: str, user=Depends(project_user)):
    """Return the full job row + result JSONB."""
    row = qone(
        "SELECT id::text, project_id::text, compilation_id::text, mode, status, objective, "
        "objectives, variables, constraints, result, "
        "created_at::text AS created_at, completed_at::text AS completed_at "
        "FROM optimization_jobs WHERE id = %s AND project_id = %s",
        (job_id, pid),
    )
    if not row:
        raise HTTPException(404, "Optimization job not found")

    for k in ("objectives", "variables", "constraints", "result"):
        row[k] = _coerce_json(row.get(k))

    return row


@router.get("/optimization/{job_id}/pareto", response_model=ParetoFrontResponse)
def get_pareto(pid: str, job_id: str, user=Depends(project_user)):
    """NSGA-2 only: return the Pareto front stored in result JSONB."""
    row = qone(
        "SELECT mode, status, result FROM optimization_jobs "
        "WHERE id = %s AND project_id = %s",
        (job_id, pid),
    )
    if not row:
        raise HTTPException(404, "Optimization job not found")
    if row["mode"] != "nsga2":
        raise HTTPException(400, f"Pareto endpoint is NSGA-2 only (mode={row['mode']})")
    if row["status"] != "done":
        raise HTTPException(409, f"Job not done (status={row['status']})")

    result = _coerce_json(row.get("result")) or {}
    return {
        "job_id": job_id,
        "pareto": result.get("pareto") or [],
        "pareto_full": result.get("pareto_full"),
        "best_balanced": result.get("best_balanced"),
    }
