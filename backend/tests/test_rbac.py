"""RBAC enforcement tests — Kokoya Gold Mine project."""
import pytest, os

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

def test_readonly_cannot_create_project(client, readonly_headers):
    r = client.post("/api/v1/projects", json={"project_name": "X", "project_code": "X", "target_tph": 1}, headers=readonly_headers)
    assert r.status_code == 403

def test_readonly_can_list_projects(client, readonly_headers):
    assert client.get("/api/v1/projects", headers=readonly_headers).status_code == 200

def test_readonly_cannot_delete_project(client, test_project_id, readonly_headers):
    assert client.delete(f"/api/v1/projects/{test_project_id}", headers=readonly_headers).status_code == 403

def test_pm_can_access_admin(client, auth_headers):
    assert client.get("/api/v1/admin/users", headers=auth_headers).status_code == 200

def test_metallurgist_cannot_access_admin(client, metallurgist_headers):
    assert client.get("/api/v1/admin/users", headers=metallurgist_headers).status_code == 403

def test_readonly_cannot_create_decision(client, test_project_id, readonly_headers):
    r = client.post(f"/api/v1/projects/{test_project_id}/decisions", json={"title": "X"}, headers=readonly_headers)
    assert r.status_code == 403

def test_pm_can_create_decision(client, test_project_id, auth_headers):
    r = client.post(f"/api/v1/projects/{test_project_id}/decisions",
        json={"title": "Choisir procédé flottation", "description": "Cellule standard vs Jameson — Kokoya"},
        headers=auth_headers)
    assert r.status_code == 201

def test_readonly_cannot_create_campaign(client, test_project_id, readonly_headers):
    assert client.post(f"/api/v1/projects/{test_project_id}/campaigns", json={"name": "X"}, headers=readonly_headers).status_code == 403

def test_metallurgist_can_create_campaign(client, test_project_id, metallurgist_headers):
    r = client.post(f"/api/v1/projects/{test_project_id}/campaigns",
        json={"name": "Kokoya Phase 1 Testwork", "description": "Bench-scale flotation — refractory ore"},
        headers=metallurgist_headers)
    assert r.status_code == 201

def test_unauthenticated_rejected(client, test_project_id):
    assert client.get(f"/api/v1/projects/{test_project_id}/dashboard").status_code == 401
