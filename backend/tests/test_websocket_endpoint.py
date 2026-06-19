# backend/tests/test_websocket_endpoint.py
"""Verify WebSocket endpoint is registered and connection works."""
from fastapi.testclient import TestClient
import pytest

def get_client():
    try:
        from backend.main import app
    except ImportError:
        from main import app
    return TestClient(app)

def test_websocket_endpoint_exists():
    """WebSocket endpoint must be reachable (returns 400 without upgrade header in TestClient)."""
    client = get_client()
    r = client.get("/ws/projects/test-project-id")
    assert r.status_code != 404, "WebSocket endpoint is not registered"

def test_websocket_connection_succeeds():
    """WebSocket connection should upgrade successfully."""
    client = get_client()
    with client.websocket_connect("/ws/projects/test-project-id") as ws:
        pass  # Connection opened and closed cleanly

def test_app_route_exists():
    """GET /app should not return 404.
    Returns 503 when frontend/dist is absent (fresh checkout), 200 when built."""
    client = get_client()
    r = client.get("/app", follow_redirects=False)
    assert r.status_code in (200, 301, 302, 307, 308, 503), \
        f"/app returned unexpected {r.status_code}"
