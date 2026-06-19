"""Worker handler for the simulate-optimize job."""
from __future__ import annotations

import json
import uuid
from typing import Any

try:
    import psycopg2.extras as psycopg2_extras
except ImportError:  # pragma: no cover
    psycopg2_extras = None

try:
    from compute.simulate_optimize import run_optimize
    from worker.registry import register
except ImportError:  # pragma: no cover
    from backend.compute.simulate_optimize import run_optimize
    from backend.worker.registry import register


def handle_optimize(payload: dict[str, Any], ctx) -> dict:
    payload = dict(payload)
    payload["_project_id"] = str(ctx.project_id)

    scenario_id = payload.get("scenario_id")
    if scenario_id:
        with ctx.conn.cursor() as cur:
            cur.execute(
                "SELECT params_override FROM simulation_scenarios WHERE id=%s AND project_id=%s",
                (str(scenario_id), str(ctx.project_id)),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(f"scenario_id not found for this project: {scenario_id}")
            po = row[0]
            if isinstance(po, str):
                po = json.loads(po) if po else {}
            elif po is None:
                po = {}
            merged = dict(po)
            merged.update(payload.get("base_params") or {})
            payload["base_params"] = merged

    circuit_cursor = None
    ce = payload.get("circuit_evaluation") or {}
    mode = str(ce.get("mode") or "surrogate").lower().strip()
    if mode == "rigorous":
        if psycopg2_extras is None:
            raise RuntimeError("psycopg2.extras required for rigorous circuit evaluation")
        circuit_cursor = ctx.conn.cursor(cursor_factory=psycopg2_extras.RealDictCursor)

    try:
        results = run_optimize(payload, ctx, circuit_cursor=circuit_cursor)
    finally:
        if circuit_cursor is not None:
            try:
                circuit_cursor.close()
            except Exception:
                pass

    run_id = str(uuid.uuid4())
    params_with_job = dict(payload)
    params_with_job["__job_id"] = str(ctx.job_id)
    with ctx.conn.cursor() as cur:
        cur.execute(
            "INSERT INTO simulation_runs_v2 "
            "(id, project_id, run_type, params, results, created_by) "
            "VALUES (%s, %s, 'simulate_optimize', %s::jsonb, %s::jsonb, %s)",
            (run_id, str(ctx.project_id),
             json.dumps(params_with_job), json.dumps(results), str(ctx.user_id)),
        )
    return {"kind": "simulation_run_v2", "id": run_id}


register("simulate_optimize", handle_optimize)
