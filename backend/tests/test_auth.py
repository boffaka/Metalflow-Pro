"""
Tests for authentication and authorization (RBAC).
Covers: login, JWT validation, role enforcement, project access isolation.
"""
import os
import pytest


# ─── Login ────────────────────────────────────────────────────────────────────

def test_login_success(client):
    resp = client.post("/api/v1/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"],
        "password": os.environ["ADMIN_PASSWORD"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "user" in data
    assert "role" in data["user"]


def test_login_wrong_password(client):
    resp = client.post("/api/v1/auth/login", json={
        "email": os.environ["ADMIN_EMAIL"],
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


def test_login_unknown_user(client):
    resp = client.post("/api/v1/auth/login", json={
        "email": "nobody@unknown.example",
        "password": "irrelevant",
    })
    assert resp.status_code == 401


def test_login_rate_limit(client):
    """After 5 failed attempts the endpoint must return 429."""
    for _ in range(5):
        client.post("/api/v1/auth/login", json={
            "email": "rate@test.example",
            "password": "bad",
        })
    resp = client.post("/api/v1/auth/login", json={
        "email": "rate@test.example",
        "password": "bad",
    })
    assert resp.status_code == 429


# ─── Token validation ─────────────────────────────────────────────────────────

def test_me_with_valid_token(client, admin_headers):
    resp = client.get("/api/v1/auth/me", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["email"] == os.environ["ADMIN_EMAIL"]
    assert data["role"] == "Project Manager"


def test_me_without_token(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_me_with_invalid_token(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer invalidtoken"})
    assert resp.status_code == 401


def test_me_with_malformed_bearer(client):
    resp = client.get("/api/v1/auth/me", headers={"Authorization": "NotBearer token"})
    assert resp.status_code in (401, 403)


# ─── RBAC — Admin endpoints ───────────────────────────────────────────────────

def test_admin_users_requires_project_manager(client, admin_headers):
    """Admin endpoint accessible by Project Manager."""
    resp = client.get("/api/v1/admin/users", headers=admin_headers)
    assert resp.status_code == 200


def test_admin_users_denied_without_auth(client):
    resp = client.get("/api/v1/admin/users")
    assert resp.status_code == 401


def test_admin_create_user(client, admin_headers):
    """Project Manager can create a user."""
    resp = client.post("/api/v1/admin/users", headers=admin_headers, json={
        "email": "test-rbac-user@example.com",
        "password": "TestPass123!",
        "role": "Metallurgist",
        "full_name": "Test RBAC User",
    })
    # 201 = created, 409 = already exists (idempotent test)
    assert resp.status_code in (201, 409)


# ─── RBAC — Project access isolation ─────────────────────────────────────────

def test_project_access_isolation(client, admin_headers):
    """
    A non-PM user cannot access a project they don't own.
    Creates a project as admin, then verifies a different (read-only) user gets 404.
    """
    # Create a project as admin
    create_resp = client.post("/api/v1/projects", headers=admin_headers, json={
        "project_name": "Isolation Test Project",
        "project_code": "ISO-TEST-001",
        "target_tph": 21,
        "gold_grade_g_t": 1.5,
        "status": "SCOPING",
    })
    assert create_resp.status_code == 201
    project_id = create_resp.json()["id"]

    # Create a read-only user
    client.post("/api/v1/admin/users", headers=admin_headers, json={
        "email": "readonly-isolation@example.com",
        "password": "ReadOnly123!",
        "role": "Read-only",
        "full_name": "Read Only User",
    })

    # Login as read-only user
    login_resp = client.post("/api/v1/auth/login", json={
        "email": "readonly-isolation@example.com",
        "password": "ReadOnly123!",
    })
    # If user was just created (201) or already existed
    if login_resp.status_code != 200:
        pytest.skip("Could not login as read-only user")

    readonly_headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

    # Read-only user should NOT see admin's project
    resp = client.get(f"/api/v1/projects/{project_id}", headers=readonly_headers)
    assert resp.status_code == 404, "Read-only user should not see another user's project"


# ─── File upload validation ───────────────────────────────────────────────────

def test_upload_blocked_extension(client, admin_headers):
    """Dangerous file extensions must be rejected."""
    # Create a project first
    create_resp = client.post("/api/v1/projects", headers=admin_headers, json={
        "project_name": "Upload Test Project",
        "project_code": "UPL-TEST-001",
        "target_tph": 8,
        "gold_grade_g_t": 1.0,
        "status": "SCOPING",
    })
    assert create_resp.status_code == 201
    pid = create_resp.json()["id"]

    # Try to upload an .exe file
    resp = client.post(
        f"/api/v1/projects/{pid}/reports",
        headers=admin_headers,
        files={"file": ("malware.exe", b"MZ\x90\x00binary", "application/octet-stream")},
        data={"phase": "SCOPING", "description": "Malicious file"},
    )
    assert resp.status_code == 400


def test_upload_allowed_extension(client, admin_headers):
    """PDF files must be accepted."""
    create_resp = client.post("/api/v1/projects", headers=admin_headers, json={
        "project_name": "PDF Upload Test",
        "project_code": "PDF-TEST-001",
        "target_tph": 8,
        "gold_grade_g_t": 1.0,
        "status": "SCOPING",
    })
    assert create_resp.status_code == 201
    pid = create_resp.json()["id"]

    resp = client.post(
        f"/api/v1/projects/{pid}/reports",
        headers=admin_headers,
        files={"file": ("report.pdf", b"%PDF-1.4 minimal", "application/pdf")},
        data={"phase": "SCOPING", "description": "Rapport de base"},
    )
    assert resp.status_code == 201
