"""Tests for the CSRF protection middleware (backend/csrf.py)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from csrf import build_csrf_middleware


@pytest.fixture
def app():
    a = FastAPI()
    middleware = build_csrf_middleware(["https://app.mpdpms.io", "http://localhost:5173"])

    @a.middleware("http")
    async def _csrf(request, call_next):
        return await middleware(request, call_next)

    @a.get("/safe")
    def safe():
        return {"ok": True}

    @a.post("/mutate")
    def mutate():
        return {"ok": True}

    @a.post("/api/v1/auth/login")
    def login():
        return {"ok": True}

    return a


@pytest.fixture
def client(app):
    return TestClient(app)


def test_safe_method_passes_without_origin(client):
    r = client.get("/safe")
    assert r.status_code == 200


def test_mutation_with_allowed_origin_passes(client):
    r = client.post("/mutate", headers={"Origin": "https://app.mpdpms.io"})
    assert r.status_code == 200


def test_mutation_with_disallowed_origin_rejected(client):
    r = client.post("/mutate", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert "CSRF" in r.json()["detail"]


def test_mutation_with_allowed_referer_passes(client):
    r = client.post("/mutate", headers={"Referer": "http://localhost:5173/projects"})
    assert r.status_code == 200


def test_mutation_with_disallowed_referer_rejected(client):
    r = client.post("/mutate", headers={"Referer": "https://evil.example.com/x"})
    assert r.status_code == 403


def test_login_endpoint_is_exempt(client):
    r = client.post("/api/v1/auth/login")
    assert r.status_code == 200


def test_no_origin_no_referer_no_cookie_passes(client):
    """Server-to-server / CLI clients (no browser headers, no cookie) should be allowed."""
    r = client.post("/mutate")
    assert r.status_code == 200


def test_no_origin_with_session_cookie_rejected(client):
    """Cookie-only auth without browser headers indicates a forged request."""
    r = client.post("/mutate", cookies={"access_token": "abc"})
    assert r.status_code == 403


def test_no_origin_with_session_cookie_but_bearer_passes(client):
    """Bearer token override is acceptable (e.g. server-to-server with stale cookie)."""
    r = client.post(
        "/mutate",
        cookies={"access_token": "abc"},
        headers={"Authorization": "Bearer xyz"},
    )
    assert r.status_code == 200


def test_websocket_path_prefix_is_exempt(client):
    """WebSocket upgrade probes use POST/PUT semantics; the prefix is exempt."""
    middleware = build_csrf_middleware(["https://app.mpdpms.io"])
    a = FastAPI()

    @a.middleware("http")
    async def _csrf(request, call_next):
        return await middleware(request, call_next)

    @a.post("/ws/projects/1")
    def ws_post():
        return {"ok": True}

    r = TestClient(a).post("/ws/projects/1")
    assert r.status_code == 200


def test_patch_csrf_script_is_removed():
    """Guard-rail: the CSRF-disabling script must not return."""
    repo_root = Path(__file__).resolve().parents[2]
    assert not (repo_root / "frontend" / "patch_csrf.py").exists(), (
        "frontend/patch_csrf.py disables CSRF protection — must not be reintroduced"
    )
