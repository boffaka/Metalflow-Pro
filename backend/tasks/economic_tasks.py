# backend/tasks/economic_tasks.py
"""Celery tasks for Monte Carlo economic simulation."""
import json
import logging

logger = logging.getLogger(__name__)

try:
    from celery_app import celery_app
    from db import conn, release
except ImportError:
    from backend.celery_app import celery_app
    from backend.db import conn, release


@celery_app.task(bind=True, name="tasks.economic_tasks.run_monte_carlo_task",
                 time_limit=900)
def run_monte_carlo_task(self, project_id: str, mc_run_id: str,
                          n_iterations: int, base_params: dict):
    """Run Monte Carlo simulation and store results."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE monte_carlo_runs SET status='running', celery_task_id=%s WHERE id=%s",
                (self.request.id, mc_run_id)
            )
        db.commit()

        try:
            import asyncio
            from ws_manager import ws_manager
            asyncio.run(ws_manager.broadcast(project_id, {
                "type": "simulation_progress", "task_id": mc_run_id, "pct": 0,
                "label": "Monte Carlo starting..."
            }))
        except Exception:
            pass

        try:
            from engines.monte_carlo import run_monte_carlo
        except ImportError:
            from backend.engines.monte_carlo import run_monte_carlo

        results = run_monte_carlo(n_iterations=n_iterations, base_params=base_params)

        with db.cursor() as cur:
            cur.execute(
                "UPDATE monte_carlo_runs SET status='done', results=%s WHERE id=%s",
                (json.dumps(results), mc_run_id)
            )
        db.commit()

        try:
            import asyncio
            from ws_manager import ws_manager
            asyncio.run(ws_manager.broadcast(project_id, {
                "type": "simulation_done", "task_id": mc_run_id,
                "results_url": f"/api/v1/projects/{project_id}/economics/monte-carlo/{mc_run_id}"
            }))
        except Exception:
            pass

        return results
    except Exception as e:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE monte_carlo_runs SET status='failed', results=%s WHERE id=%s",
                (json.dumps({"error": str(e)}), mc_run_id)
            )
        db.commit()
        raise
    finally:
        if db is not None:
            release(db)
