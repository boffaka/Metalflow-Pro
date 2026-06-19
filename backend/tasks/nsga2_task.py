# backend/tasks/nsga2_task.py
"""Celery task wrapper for NSGA-II optimization."""
import json, uuid, logging
logger = logging.getLogger(__name__)

try:
    from celery_app import celery_app
    from db import conn, release
except ImportError:
    from backend.celery_app import celery_app
    from backend.db import conn, release


@celery_app.task(bind=True, name="tasks.nsga2_task.run_nsga2_optimization",
                 time_limit=600)
def run_nsga2_optimization(self, project_id: str, run_id: str, base_params: dict,
                            n_pop: int = 50, n_gen: int = 100):
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='running', celery_task_id=%s WHERE id=%s",
                (self.request.id, run_id)
            )
        db.commit()

        try:
            import asyncio
            from ws_manager import ws_manager
            asyncio.run(ws_manager.broadcast(project_id, {
                "type": "simulation_progress", "task_id": run_id, "pct": 0
            }))
        except Exception:
            pass

        try:
            from engines.optimization import run_nsga2
        except ImportError:
            from backend.engines.optimization import run_nsga2

        results = run_nsga2(base_params, n_pop=n_pop, n_gen=n_gen)

        pareto_id = str(uuid.uuid4())
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='done', results=%s WHERE id=%s",
                (json.dumps(results), run_id)
            )
            cur.execute(
                """INSERT INTO pareto_fronts (id, run_id, solutions, n_solutions, generated_at)
                   VALUES (%s, %s, %s, %s, NOW())""",
                (pareto_id, run_id, json.dumps(results["solutions"]), results["n_solutions"])
            )
        db.commit()

        try:
            import asyncio
            from ws_manager import ws_manager
            asyncio.run(ws_manager.broadcast(project_id, {
                "type": "simulation_done", "task_id": run_id,
                "results_url": f"/api/v1/projects/{project_id}/simulation/pareto/{run_id}"
            }))
        except Exception:
            pass

        return results
    except Exception as e:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='failed', results=%s WHERE id=%s",
                (json.dumps({"error": str(e)}), run_id)
            )
        db.commit()
        raise
    finally:
        release(db)
