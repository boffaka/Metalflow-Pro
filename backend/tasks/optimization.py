"""Celery tasks + synchronous fallbacks for optimisation jobs.

For dev mode (no Celery worker running) we use the synchronous helpers
directly from the route layer. In production (Celery worker up), the
route dispatches the task via `task.delay(job_id)`.

Both paths read/update the same `optimization_jobs` row.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("mpdpms.optimization_tasks")


def _get_celery():
    try:
        from celery_app import celery_app
    except ImportError:
        from backend.celery_app import celery_app
    return celery_app


def _get_db():
    try:
        from db import conn, release
    except ImportError:
        from backend.db import conn, release
    return conn, release


def _load_job(cur, job_id: str) -> dict | None:
    cur.execute(
        "SELECT id, project_id, compilation_id, mode, objective, objectives, "
        "variables, constraints, status FROM optimization_jobs WHERE id = %s",
        (job_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    # RealDictCursor returns dict; default returns tuple
    if isinstance(row, dict):
        return row
    cols = [desc[0] for desc in cur.description]
    return dict(zip(cols, row))


def _coerce_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _get_template_id(cur, job: dict) -> str | None:
    """Resolve template_id from the job's compilation_id (if any)."""
    compilation_id = job.get("compilation_id")
    if not compilation_id:
        return None
    cur.execute(
        "SELECT template_id FROM circuit_compilations WHERE id = %s",
        (compilation_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, dict):
        return str(row["template_id"])
    return str(row[0])


def _extract_objective_value(sim_result: dict, objective: str) -> float | None:
    """Pick the right objective scalar from a simulate_circuit result."""
    overall = (sim_result or {}).get("overall") or {}
    if objective == "recovery":
        return overall.get("total_recovery_pct") or overall.get("recovery_pct")
    if objective == "energy":
        return overall.get("energy_kwh_t") or overall.get("total_energy_kwh_t")
    if objective == "aisc":
        return overall.get("aisc_usd_oz") or overall.get("aisc")
    return None


def _run_sweep_sync(job_id: str) -> dict:
    """Synchronous sweep execution. Updates optimization_jobs row."""
    conn_fn, release_fn = _get_db()
    import psycopg2.extras as _pge
    db = conn_fn()
    try:
        cur = db.cursor(cursor_factory=_pge.RealDictCursor)
        job = _load_job(cur, job_id)
        if job is None:
            cur.close()
            return {"error": "Job not found"}

        # Mark running
        cur.execute(
            "UPDATE optimization_jobs SET status='running' WHERE id = %s",
            (job_id,),
        )
        db.commit()

        try:
            variables = _coerce_json(job.get("variables")) or []
            objective = job.get("objective") or "recovery"
            template_id = _get_template_id(cur, job)

            if not variables or not template_id:
                raise ValueError("Missing variables or template_id for sweep")

            # First variable only (MVP).
            var = variables[0]
            param = var["param"]
            lo = float(var["min"])
            hi = float(var["max"])
            steps = int(var.get("steps") or 10)
            if steps < 2:
                steps = 2

            try:
                from engines.process_simulator import simulate_circuit
            except ImportError:
                from backend.engines.process_simulator import simulate_circuit

            curve: list[dict[str, float]] = []
            for i in range(steps):
                frac = i / (steps - 1)
                x = lo + frac * (hi - lo)
                try:
                    sim = simulate_circuit(
                        job["project_id"], template_id,
                        params_override={param: x}, cursor=cur,
                    )
                    y = _extract_objective_value(sim, objective)
                    curve.append({"x": round(x, 6), "y": round(float(y), 6) if y is not None else None})
                except Exception as inner:
                    logger.warning("sweep point %s=%s failed: %s", param, x, inner)
                    curve.append({"x": round(x, 6), "y": None, "error": str(inner)[:200]})

            result = {"curve": curve, "objective": objective, "param": param}
            cur.execute(
                "UPDATE optimization_jobs SET status='done', result=%s::jsonb, "
                "completed_at=NOW() WHERE id = %s",
                (json.dumps(result), job_id),
            )
            db.commit()
            cur.close()
            return result
        except Exception as e:
            logger.exception("sweep_sync failed for job %s", job_id)
            cur.execute(
                "UPDATE optimization_jobs SET status='failed', "
                "result=%s::jsonb, completed_at=NOW() WHERE id = %s",
                (json.dumps({"error": str(e)}), job_id),
            )
            db.commit()
            cur.close()
            return {"error": str(e)}
    finally:
        release_fn(db)


def _run_nsga2_sync(job_id: str) -> dict:
    """Synchronous NSGA-2 execution. Updates optimization_jobs row."""
    conn_fn, release_fn = _get_db()
    import psycopg2.extras as _pge
    db = conn_fn()
    try:
        cur = db.cursor(cursor_factory=_pge.RealDictCursor)
        job = _load_job(cur, job_id)
        if job is None:
            cur.close()
            return {"error": "Job not found"}

        cur.execute(
            "UPDATE optimization_jobs SET status='running' WHERE id = %s",
            (job_id,),
        )
        db.commit()

        try:
            template_id = _get_template_id(cur, job)
            if not template_id:
                raise ValueError("Missing compilation_id / template_id")

            objectives = _coerce_json(job.get("objectives")) or []
            constraints_raw = _coerce_json(job.get("constraints"))
            # nsga2_optimize expects constraints as a dict (optional)
            constraints_dict = None
            if isinstance(constraints_raw, dict):
                constraints_dict = constraints_raw
            elif isinstance(constraints_raw, list) and constraints_raw:
                # Convert list-of-dicts into a simple dict
                constraints_dict = {}
                for c in constraints_raw:
                    if isinstance(c, dict) and "name" in c and "value" in c:
                        constraints_dict[c["name"]] = c["value"]

            try:
                from engines.nsga2_optimizer import nsga2_optimize
            except ImportError:
                from backend.engines.nsga2_optimizer import nsga2_optimize

            # Decision-space bounds (param/min/max) — same schema as sweep jobs
            vars_list = _coerce_json(job.get("variables")) or []
            # population_size / n_generations from env shape, fallback to 20/10
            pop_size = 10
            n_gen = 3
            # If caller put explicit settings into constraints dict, pick them up
            if constraints_dict:
                pop_size = int(constraints_dict.get("population_size", pop_size))
                n_gen = int(constraints_dict.get("generations", n_gen))

            engine_result = nsga2_optimize(
                job["project_id"], template_id, cur,
                population_size=pop_size, n_generations=n_gen,
                objectives=objectives or None,
                constraints=constraints_dict,
                job_variables=vars_list if isinstance(vars_list, list) else None,
            )

            pareto_full = engine_result.get("pareto_front") or []
            # Normalized pareto array (list of objective vectors) for /pareto endpoint
            pareto_vectors: list[list[float]] = []
            for sol in pareto_full:
                objs = sol.get("objectives") or {}
                if isinstance(objs, dict):
                    pareto_vectors.append([float(v) for v in objs.values()])
                elif isinstance(objs, list):
                    pareto_vectors.append([float(v) for v in objs])

            result_payload = {
                "pareto": pareto_vectors,
                "pareto_full": pareto_full,
                "best_balanced": engine_result.get("best_balanced"),
                "best_npv": engine_result.get("best_npv"),
                "n_pareto_solutions": engine_result.get("n_pareto_solutions", len(pareto_vectors)),
            }
            cur.execute(
                "UPDATE optimization_jobs SET status='done', result=%s::jsonb, "
                "completed_at=NOW() WHERE id = %s",
                (json.dumps(result_payload, default=str), job_id),
            )
            db.commit()
            cur.close()
            return result_payload
        except Exception as e:
            logger.exception("nsga2_sync failed for job %s", job_id)
            cur.execute(
                "UPDATE optimization_jobs SET status='failed', "
                "result=%s::jsonb, completed_at=NOW() WHERE id = %s",
                (json.dumps({"error": str(e)}), job_id),
            )
            db.commit()
            cur.close()
            return {"error": str(e)}
    finally:
        release_fn(db)


# Celery task wrappers (kept for prod; memory broker in dev means these are no-ops)
celery_app = _get_celery()


@celery_app.task(bind=True, name="tasks.optimization.run_sweep_task")
def run_sweep_task(self, job_id: str):
    return _run_sweep_sync(job_id)


@celery_app.task(bind=True, name="tasks.optimization.run_nsga2_task")
def run_nsga2_task(self, job_id: str):
    return _run_nsga2_sync(job_id)
