# backend/tasks/ml_training_tasks.py
"""
Celery tasks for ML model training and retraining.
Triggered: weekly via Celery Beat, or on LIMS data update.
"""
import json
import uuid
import logging
import numpy as np

logger = logging.getLogger(__name__)

try:
    from celery_app import celery_app
    from db import conn, release
except ImportError:
    from backend.celery_app import celery_app
    from backend.db import conn, release


@celery_app.task(name="tasks.ml_training_tasks.retrain_surrogate")
def retrain_surrogate(project_id: str):
    """
    Retrain the surrogate model using completed rigorous simulation runs.
    Stores the resulting model in model_artifacts table.
    """
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                """SELECT params, results FROM simulation_runs
                   WHERE project_id=%s AND type='rigorous' AND status='done'
                   ORDER BY created_at DESC LIMIT 1000""",
                (project_id,)
            )
            rows = cur.fetchall()

        if len(rows) < 10:
            logger.info("Not enough data to train surrogate (need >=10, have %d)", len(rows))
            return {"status": "skipped", "reason": "insufficient_data", "n_samples": len(rows)}

        X_list, y_list = [], []
        for params_raw, results_raw in rows:
            p = params_raw if isinstance(params_raw, dict) else json.loads(params_raw)
            r = results_raw if isinstance(results_raw, dict) else json.loads(results_raw)
            features = [
                p.get("p80_um", 75.0), p.get("nacn_mg_l", 350.0), p.get("do_mg_l", 8.0),
                p.get("ph", 10.5), p.get("srt_h", 24.0), p.get("tph", 500.0),
                p.get("bwi_kwh_t", p.get("wi", 14.0)), p.get("f80_um", 3000.0),
            ]
            target = r.get("recovery_pct", None)
            if target is not None:
                X_list.append(features)
                y_list.append(target)

        if len(X_list) < 10:
            return {"status": "skipped", "reason": "no_valid_targets"}

        try:
            from ml.surrogate import SurrogateModel
        except ImportError:
            from backend.ml.surrogate import SurrogateModel

        model = SurrogateModel()
        score = model.train(np.array(X_list), np.array(y_list))
        blob = model.serialize()

        artifact_id = str(uuid.uuid4())
        with db.cursor() as cur:
            cur.execute(
                "UPDATE model_artifacts SET is_active=false WHERE project_id=%s AND model_type='surrogate_rf'",
                (project_id,)
            )
            cur.execute(
                """INSERT INTO model_artifacts
                   (id, project_id, model_type, version, is_active, artifact,
                    training_samples_n, training_score, trained_at, trained_by)
                   VALUES (%s, %s, 'surrogate_rf', 1, true, %s, %s, %s, NOW(), 'celery_task')""",
                (artifact_id, project_id, blob, len(X_list), score)
            )
        db.commit()

        return {"status": "trained", "n_samples": len(X_list), "r2_score": round(score, 4)}
    finally:
        release(db)


@celery_app.task(name="tasks.ml_training_tasks.retrain_all_models")
def retrain_all_models():
    """Weekly retraining of all project models — triggered by Celery Beat."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute("SELECT id FROM projects WHERE status NOT IN ('Closed','Archived')")
            project_ids = [str(r[0]) for r in cur.fetchall()]
    except Exception as e:
        logger.error("retrain_all_models: failed to fetch active projects: %s", e)
        return {"error": str(e), "trained": 0}
    finally:
        if db is not None:
            release(db)

    results = {}
    for pid in project_ids:
        try:
            result = retrain_surrogate.delay(pid)
            results[pid] = result
        except Exception as e:
            logger.error("retrain_all_models: failed to dispatch retraining for project %s: %s", pid, e)
            results[pid] = {"error": str(e)}
    return results
