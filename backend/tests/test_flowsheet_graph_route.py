# backend/tests/test_flowsheet_graph_route.py
import pytest
from unittest.mock import patch

pytestmark = pytest.mark.no_db


def _make_client():
    from fastapi import FastAPI
    from routes.flowsheet_graph import router
    app = FastAPI()
    app.include_router(router)
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


def test_list_starters_route_exists():
    client = _make_client()
    with patch("routes.flowsheet_graph.qall", return_value=[]):
        resp = client.get("/api/v1/flowsheet-starters")
    assert resp.status_code == 200
    assert "items" in resp.json()


def test_unit_library_route_returns_registry_payload():
    client = _make_client()
    resp = client.get("/api/v1/flowsheet-unit-library")
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"]
    assert any(u["op_code"] == "CIL_TANK" for u in data["items"])


def test_list_graphs_requires_auth():
    client = _make_client()
    resp = client.get("/api/v1/projects/test-pid/flowsheet-graphs")
    assert resp.status_code in (401, 403, 422)


def test_create_graph_requires_auth():
    client = _make_client()
    resp = client.post("/api/v1/projects/test-pid/flowsheet-graphs",
                       json={"name": "Test"})
    assert resp.status_code in (401, 403, 422)


def test_router_has_expected_routes():
    from routes.flowsheet_graph import router
    paths = [r.path for r in router.routes]
    assert any("flowsheet-graphs" in p for p in paths)
    assert any("flowsheet-starters" in p for p in paths)
    assert any("flowsheet-unit-library" in p for p in paths)
    assert any(p.endswith("/validate") for p in paths)
    assert any(p.endswith("/optimization-problem") for p in paths)
    assert any("flowsheet/ws" in p for p in paths)
