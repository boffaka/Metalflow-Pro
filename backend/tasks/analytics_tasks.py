# backend/tasks/analytics_tasks.py
"""
Celery tasks for analytics:
  - train_isolation_forest: train anomaly detection model on tag history
  - detect_anomaly: score a new reading against trained model
  - compute_kpi_snapshot: aggregate daily KPIs
  - train_anomaly_models_for_project: Celery task — retrain for all active tags in a project
  - generate_scheduled_reports: weekly Celery Beat task
"""
from __future__ import annotations
import io, uuid, logging
import numpy as np
from typing import List, Tuple

logger = logging.getLogger(__name__)

try:
    from celery_app import celery_app
except ImportError:
    from backend.celery_app import celery_app


def train_isolation_forest(values: List[float], contamination: float = 0.05) -> bytes:
    """Train an Isolation Forest on a list of tag values. Returns serialized model bytes."""
    try:
        from sklearn.ensemble import IsolationForest
        import joblib

        X = np.array(values).reshape(-1, 1)
        model = IsolationForest(contamination=contamination, random_state=42, n_estimators=100)
        model.fit(X)

        buf = io.BytesIO()
        joblib.dump(model, buf)
        return buf.getvalue()
    except Exception as e:
        logger.error("Failed to train Isolation Forest model (n_values=%d, contamination=%.2f): %s", len(values), contamination, e)
        raise RuntimeError(f"Isolation Forest training failed: {e}") from e


def detect_anomaly(
    model_bytes: bytes,
    value: float,
    history: List[float],
) -> Tuple[bool, float]:
    """Score a new reading against the trained Isolation Forest. Returns (is_anomaly, sigma_deviation)."""
    try:
        import joblib

        model = joblib.load(io.BytesIO(model_bytes))
        prediction = model.predict([[value]])  # -1 = anomaly, 1 = normal
        is_anomaly = bool(prediction[0] == -1)

        arr = np.array(history)
        mean, std = arr.mean(), arr.std()
        sigma = abs(value - mean) / std if std > 0 else 0.0

        return is_anomaly, round(sigma, 2)
    except Exception as e:
        logger.error("Anomaly detection failed for value=%.4f (history_len=%d): %s", value, len(history), e)
        return False, 0.0


def compute_kpi_snapshot(
    annual_oz: float,
    avail_pct: float,
    recovery_pct: float,
    energy_kwh_t: float,
    nacn_kg_t: float,
    aisc_usd_oz: float,
) -> dict:
    """Compute daily KPI values from annual/design parameters."""
    return {
        "oz_produced_daily": round(annual_oz / 365.0, 1),
        "recovery_pct": recovery_pct,
        "energy_kwh_t": energy_kwh_t,
        "nacn_kg_t": nacn_kg_t,
        "aisc_usd_oz": aisc_usd_oz,
        "availability_pct": avail_pct,
    }


@celery_app.task(name="tasks.analytics_tasks.train_anomaly_models_for_project")
def train_anomaly_models_for_project(project_id: str):
    """
    Train Isolation Forest models for all active process tags in a project.
    Triggered when sufficient data accumulates (>= 100 readings per tag).
    """
    try:
        from db import conn, release
    except ImportError:
        from backend.db import conn, release

    db = None
    trained_count = 0
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, tag_name FROM process_tags WHERE project_id=%s",
                (project_id,)
            )
            tags = cur.fetchall()

        for tag_id, tag_name in tags:
            with db.cursor() as cur:
                cur.execute(
                    "SELECT value FROM tag_readings WHERE tag_id=%s ORDER BY time DESC LIMIT 2000",
                    (str(tag_id),)
                )
                values = [row[0] for row in cur.fetchall()]

            if len(values) < 100:
                continue

            model_bytes = train_isolation_forest(values)
            artifact_id = str(uuid.uuid4())
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE model_artifacts SET is_active=false WHERE project_id=%s AND model_type='isolation_forest'",
                    (project_id,)
                )
                cur.execute(
                    """INSERT INTO model_artifacts
                       (id, project_id, model_type, version, is_active, artifact,
                        training_samples_n, training_score, trained_at, trained_by)
                       VALUES (%s, %s, 'isolation_forest', 1, true, %s, %s, NULL, NOW(), 'celery')""",
                    (artifact_id, project_id, model_bytes, len(values))
                )
            db.commit()
            trained_count += 1
    except Exception:
        if db is not None:
            db.rollback()
        logger.exception("train_anomaly_models_for_project failed for project %s", project_id)
        raise
    finally:
        if db is not None:
            release(db)

    return {"trained_models": trained_count, "project_id": project_id}


@celery_app.task(name="tasks.analytics_tasks.generate_scheduled_reports")
def generate_scheduled_reports():
    """Weekly PDF report generation for all active projects (Celery Beat)."""
    try:
        from db import conn, release
    except ImportError:
        from backend.db import conn, release

    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, project_name FROM projects WHERE status NOT IN ('Closed','Archived')"
            )
            projects = cur.fetchall()
    except Exception:
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)

    generated = []
    for project_id, project_name in projects:
        logger.info("Generating weekly report for %s (%s)", project_name, project_id)
        # Placeholder: in full implementation, render python-docx template
        generated.append(str(project_id))

    return {"reports_generated": len(generated)}
