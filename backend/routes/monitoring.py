"""
Monitoring API — health check with dependency probing.

Endpoints:
  GET /api/v1/health — Enriched health check (DB, Redis, Celery, disk)
"""
from __future__ import annotations

import logging
import psycopg2
import shutil

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger("mpdpms.monitoring")

try:
    from ..db import conn, release
    from ..settings import get_settings
except ImportError:  # pragma: no cover - supports direct script imports
    from db import conn, release
    from settings import get_settings

router = APIRouter(tags=["monitoring"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _check_db() -> dict:
    """Probe database with SELECT 1."""
    try:
        db = conn()
        try:
            with db.cursor() as cur:
                cur.execute("SELECT 1")
            return {"status": "ok"}
        finally:
            release(db)
    except Exception as exc:  # intentional: graceful fallback on optional operation
        logger.warning("Health check: DB probe failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _check_redis() -> dict:
    """Ping Redis if configured."""
    settings = get_settings()
    if not settings.redis_url:
        return {"status": "skipped", "detail": "Redis not configured"}
    try:
        import redis
        r = redis.from_url(settings.redis_url, socket_connect_timeout=2)
        if r.ping():
            return {"status": "ok"}
        return {"status": "error", "detail": "ping returned False"}
    except ImportError:
        return {"status": "skipped", "detail": "redis package not installed"}
    except Exception as exc:  # intentional: graceful fallback on optional operation
        logger.warning("Health check: Redis probe failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _check_celery() -> dict:
    """Inspect active Celery workers."""
    try:
        from celery import Celery
        settings = get_settings()
        broker_url = settings.redis_url if settings.redis_url else None
        if not broker_url:
            return {"status": "skipped", "detail": "No broker configured"}
        app = Celery(broker=broker_url)
        inspector = app.control.inspect(timeout=2.0)
        active = inspector.active()
        if active is None:
            return {"status": "error", "detail": "No workers responded"}
        return {"status": "ok", "workers": len(active)}
    except ImportError:
        return {"status": "skipped", "detail": "celery package not installed"}
    except Exception as exc:  # intentional: graceful fallback on optional operation
        logger.warning("Health check: Celery probe failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


def _check_disk() -> dict:
    """Check disk usage on the data partition."""
    try:
        usage = shutil.disk_usage("/")
        free_pct = (usage.free / usage.total) * 100
        return {
            "status": "ok" if free_pct > 5.0 else "warning",
            "total_gb": round(usage.total / (1024 ** 3), 2),
            "free_gb": round(usage.free / (1024 ** 3), 2),
            "free_pct": round(free_pct, 2),
        }
    except Exception as exc:  # intentional: graceful fallback on optional operation
        logger.warning("Health check: Disk probe failed: %s", exc)
        return {"status": "error", "detail": str(exc)}


# ─── Health endpoint ──────────────────────────────────────────────────────────

@router.get("/api/v1/health/extended")
def health_check():
    """Extended health check — probes DB, Redis, Celery, and disk (renamed from /health to avoid duplicate with main.py)."""
    try:
        db_status = _check_db()
        redis_status = _check_redis()
        celery_status = _check_celery()
        disk_status = _check_disk()

        checks = {
            "db": db_status,
            "redis": redis_status,
            "celery": celery_status,
            "disk": disk_status,
        }

        # Determine overall status
        statuses = [v["status"] for v in checks.values()]
        if db_status["status"] == "error":
            overall = "unhealthy"
        elif any(s == "error" for s in statuses):
            overall = "degraded"
        elif any(s == "warning" for s in statuses):
            overall = "degraded"
        else:
            overall = "healthy"

        payload = {"status": overall, **checks}
        status_code = 503 if overall == "unhealthy" else 200

        return JSONResponse(content=payload, status_code=status_code)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
