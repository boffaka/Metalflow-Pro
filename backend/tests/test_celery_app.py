# backend/tests/test_celery_app.py
"""Verify Celery app is correctly configured."""
import os, pytest

def test_celery_app_has_correct_broker():
    """Celery broker URL must come from REDIS_URL env var."""
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mpdpms")
    from celery_app import celery_app
    assert "redis" in celery_app.conf.broker_url

def test_celery_app_result_backend_is_redis():
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
    from celery_app import celery_app
    assert "redis" in celery_app.conf.result_backend

def test_celery_app_name():
    from celery_app import celery_app
    assert celery_app.main == "mpdpms"
