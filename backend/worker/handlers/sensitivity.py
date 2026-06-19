"""Worker handlers for sensitivity jobs (spider + tornado)."""
from __future__ import annotations

import json
import uuid
from typing import Any

try:
    from compute.sensitivity import run_spider, run_tornado
    from worker.registry import register
except ImportError:  # pragma: no cover
    from backend.compute.sensitivity import run_spider, run_tornado
    from backend.worker.registry import register


def _persist_run(ctx, run_type: str, params: dict, results: dict) -> dict:
    run_id = str(uuid.uuid4())
    # Stamp the originating job_id so cancellation tests can verify atomicity.
    params_with_job = dict(params)
    params_with_job["__job_id"] = str(ctx.job_id)
    with ctx.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO simulation_runs_v2 "
            "(id, project_id, run_type, params, results, created_by) "
            "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s)",
            (run_id, str(ctx.project_id), run_type,
             json.dumps(params_with_job), json.dumps(results), str(ctx.user_id)),
        )
    return {"kind": "simulation_run_v2", "id": run_id}


def handle_spider(payload: dict[str, Any], ctx) -> dict:
    results = run_spider(payload, ctx)
    return _persist_run(ctx, "sensitivity_spider", payload, results)


def handle_tornado(payload: dict[str, Any], ctx) -> dict:
    results = run_tornado(payload, ctx)
    return _persist_run(ctx, "sensitivity_tornado", payload, results)


register("sensitivity_spider", handle_spider)
register("sensitivity_tornado", handle_tornado)
