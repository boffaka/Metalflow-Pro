"""MPDPMS — Prometheus metrics definitions and middleware."""
from __future__ import annotations

import re
import time

from fastapi import Request
from fastapi.responses import Response

try:
    from prometheus_client import (
        REGISTRY,
        Counter,
        Histogram,
        Gauge,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
except ImportError:
    # Stub for environments without prometheus_client
    class _Stub:
        def __init__(self, *a, **kw): pass
        def labels(self, **kw): return self
        def inc(self, *a): pass
        def observe(self, *a): pass
        def set(self, *a): pass
        _labelnames = ()
    Counter = Histogram = Gauge = _Stub
    REGISTRY = None  # type: ignore[misc, assignment]

    def generate_latest(): return b""
    CONTENT_TYPE_LATEST = "text/plain"


def _reuse_counter(name: str, documentation: str, labelnames: list[str]):
    """Register Counter or return existing (duplicate ``import main`` / ``import backend.main``)."""
    try:
        return Counter(name, documentation, labelnames)
    except ValueError:
        stem = name[:-6] if name.endswith("_total") else name
        for key in (stem, name):
            existing = REGISTRY._names_to_collectors.get(key)  # type: ignore[union-attr]
            if existing is not None:
                return existing
        raise


def _reuse_histogram(
    name: str,
    documentation: str,
    labelnames: list[str],
    *,
    buckets: list[float],
):
    try:
        return Histogram(name, documentation, labelnames, buckets=buckets)
    except ValueError:
        existing = REGISTRY._names_to_collectors.get(name)  # type: ignore[union-attr]
        if existing is not None:
            return existing
        raise


def _reuse_gauge(name: str, documentation: str, labelnames: list[str] | None = None):
    try:
        if labelnames:
            return Gauge(name, documentation, labelnames)
        return Gauge(name, documentation)
    except ValueError:
        existing = REGISTRY._names_to_collectors.get(name)  # type: ignore[union-attr]
        if existing is not None:
            return existing
        raise


REQUESTS_TOTAL = _reuse_counter(
    "mpdpms_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"],
)

REQUEST_DURATION = _reuse_histogram(
    "mpdpms_request_duration_seconds",
    "Request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

DB_POOL_ACTIVE = _reuse_gauge(
    "mpdpms_db_pool_active_connections",
    "Active database connections in pool",
)

DB_POOL_WAITING = _reuse_gauge(
    "mpdpms_db_pool_waiting",
    "Threads waiting for a DB connection",
)

CELERY_TASKS = _reuse_counter(
    "mpdpms_celery_tasks_total",
    "Celery tasks by state",
    ["task_name", "state"],
)

SYNC_PENDING = _reuse_gauge(
    "mpdpms_sync_pending_mutations",
    "Pending offline mutations awaiting sync",
    ["project_id"],
)

VALIDATION_FLAGS = _reuse_counter(
    "mpdpms_validation_flags_total",
    "LIMS validation flags raised",
    ["severity", "rule_code"],
)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _normalize_path(path: str) -> str:
    """Collapse UUIDs in path to {id} for cardinality control."""
    return _UUID_RE.sub("{id}", path)


async def prometheus_middleware(request: Request, call_next):
    """FastAPI middleware to record request metrics."""
    if request.url.path == "/metrics":
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    endpoint = _normalize_path(request.url.path)
    REQUESTS_TOTAL.labels(
        method=request.method,
        endpoint=endpoint,
        status=response.status_code,
    ).inc()
    REQUEST_DURATION.labels(
        method=request.method,
        endpoint=endpoint,
    ).observe(duration)

    return response


def metrics_endpoint(_request: Request) -> Response:
    """Expose Prometheus metrics at /metrics."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
