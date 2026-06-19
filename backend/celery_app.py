# backend/celery_app.py
"""
Celery application for MPDPMS v4 async workers.
Handles: simulation runs, Monte Carlo, NSGA-II, PDF exports, ML training.
"""
import os
import sys
from pathlib import Path
from celery import Celery
from celery.signals import worker_process_init

CELERY_BROKER_URL = (
    os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL")
    or "memory://"
)
CELERY_RESULT_BACKEND = (
    os.getenv("CELERY_RESULT_BACKEND")
    or os.getenv("REDIS_URL")
    or "cache+memory://"
)

celery_app = Celery(
    "mpdpms",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "tasks.simulation_tasks",
        "tasks.economic_tasks",
        "tasks.export_tasks",
        "tasks.analytics_tasks",
        "tasks.ml_training_tasks",
        "tasks.geotech_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,  # 24h
    # Retry config
    task_default_retry_delay=10,
    task_max_retries=3,
    # Time limits (soft=30min, hard=31min)
    task_soft_time_limit=1800,
    task_time_limit=1860,
)

@worker_process_init.connect
def _init_telemetry_in_worker(**_kwargs):
    """Each Celery worker process gets its own Sentry/OTel init."""
    try:
        from telemetry import init_all, instrument_celery
    except ImportError:
        # Celery can import this module as top-level (celery_app) where
        # relative imports are unavailable; ensure local backend path is loadable.
        backend_dir = Path(__file__).resolve().parent
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        try:
            from telemetry import init_all, instrument_celery
        except ImportError:
            return
    status = init_all(service="mpdpms-worker")
    if status.get("otel"):
        instrument_celery()


# Celery Beat schedule (periodic tasks)
celery_app.conf.beat_schedule = {
    "retrain-ml-models-weekly": {
        "task": "tasks.ml_training_tasks.retrain_all_models",
        "schedule": 604800.0,  # 7 days in seconds
    },
    "generate-weekly-reports": {
        "task": "tasks.analytics_tasks.generate_scheduled_reports",
        "schedule": 604800.0,
    },
}
