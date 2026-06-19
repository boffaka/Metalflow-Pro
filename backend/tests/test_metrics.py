"""Tests for Prometheus metrics module."""
from metrics import REQUESTS_TOTAL, REQUEST_DURATION, DB_POOL_ACTIVE, CELERY_TASKS, _normalize_path


def test_metrics_exist():
    """Core metrics are defined."""
    assert REQUESTS_TOTAL is not None
    assert REQUEST_DURATION is not None
    assert DB_POOL_ACTIVE is not None
    assert CELERY_TASKS is not None


def test_normalize_path_replaces_uuids():
    """UUIDs in paths are collapsed to {id}."""
    path = "/api/v1/projects/550e8400-e29b-41d4-a716-446655440000/lims/a1"
    assert _normalize_path(path) == "/api/v1/projects/{id}/lims/a1"


def test_normalize_path_no_uuid():
    """Paths without UUIDs are unchanged."""
    path = "/api/v1/health"
    assert _normalize_path(path) == "/api/v1/health"


def test_normalize_path_multiple_uuids():
    """Multiple UUIDs are all replaced."""
    path = "/api/v1/projects/550e8400-e29b-41d4-a716-446655440000/lims/123e4567-e89b-12d3-a456-426614174000"
    assert "{id}" in _normalize_path(path)
    assert "550e8400" not in _normalize_path(path)
