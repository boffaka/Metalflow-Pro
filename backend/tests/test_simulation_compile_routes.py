"""Tests for backend/routes/simulation_compile.py."""
import uuid

from fastapi.testclient import TestClient


def test_compile_endpoint_returns_compilation(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/compile",
        json={"source_type": "flowsheet"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["compilation_id"]
    assert body["template_id"]
    assert len(body["blocks_hash"]) == 64
    assert body["cached"] is False


def test_compile_endpoint_idempotent(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    r1 = client.post(f"/api/v1/projects/{pid}/simulation-v2/compile",
                     json={"source_type": "flowsheet"}, headers=auth_headers)
    r2 = client.post(f"/api/v1/projects/{pid}/simulation-v2/compile",
                     json={"source_type": "flowsheet"}, headers=auth_headers)
    assert r1.json()["compilation_id"] == r2.json()["compilation_id"]
    assert r2.json()["cached"] is True


def test_active_source_get_returns_default_flowsheet(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    r = client.get(f"/api/v1/projects/{pid}/simulation-v2/active-source", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["source_type"] == "flowsheet"
    assert body["source_id"] == seeded_simple_project["flowsheet_id"]


def test_active_source_post_persists_selection(client: TestClient, auth_headers, seeded_simple_project):
    """POST active-source; subsequent GET returns that ID."""
    pid = seeded_simple_project["project_id"]
    fs_id = seeded_simple_project["flowsheet_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/active-source",
        json={"source_type": "flowsheet", "source_id": fs_id},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["source_id"] == fs_id

    r2 = client.get(f"/api/v1/projects/{pid}/simulation-v2/active-source", headers=auth_headers)
    assert r2.json()["source_id"] == fs_id


def test_active_source_post_rejects_foreign_flowsheet(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/active-source",
        json={"source_type": "flowsheet", "source_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert r.status_code == 404
