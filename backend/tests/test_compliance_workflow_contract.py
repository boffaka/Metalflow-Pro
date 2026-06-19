"""Contract tests for the Compliance page workflow API.

These are pure route tests: DB boundaries are mocked so the frontend/backend
workflow contract can be verified without a live PostgreSQL instance.
"""
from __future__ import annotations

import os
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

try:
    from backend.auth import project_user
    from backend.routes.compliance import router
    import backend.routes.compliance as compliance_mod
except ImportError:  # pragma: no cover
    from auth import project_user
    from routes.compliance import router
    import routes.compliance as compliance_mod


pytestmark = pytest.mark.no_db


def _client(fake_user: dict[str, Any] | None = None) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[project_user] = lambda: fake_user or {
        "id": "user-1",
        "email": "met@example.test",
        "is_qp": True,
    }
    app.include_router(router, prefix="/api/v1/projects")
    return TestClient(app, raise_server_exceptions=False)


def test_create_workflow_defaults_report_type(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_execute(sql: str, params: tuple[Any, ...]):
        captured["params"] = params
        return {
            "id": params[0],
            "project_id": params[1],
            "title": params[2],
            "report_type": params[3],
            "status": "draft",
            "submitted_by": params[4],
            "created_at": "2026-05-09T00:00:00Z",
            "updated_at": "2026-05-09T00:00:00Z",
        }

    monkeypatch.setattr(compliance_mod, "execute", fake_execute)
    monkeypatch.setattr(compliance_mod, "qall", lambda sql, params: [])
    monkeypatch.setattr(compliance_mod, "record_event", lambda **kwargs: None)

    response = _client().post("/api/v1/projects/proj-1/compliance/workflows", json={"title": "Revue QP"})

    assert response.status_code == 201, response.text
    assert response.json()["report_type"] == "ni43101"
    assert captured["params"][3] == "ni43101"


def test_submit_and_start_review_transitions(monkeypatch):
    workflow = {
        "id": "wf-1",
        "project_id": "proj-1",
        "status": "draft",
        "title": "Revue QP",
        "report_type": "ni43101",
    }

    def fake_qone(sql: str, params: tuple[Any, ...]):
        return dict(workflow)

    def fake_execute(sql: str, params: list[Any]):
        workflow["status"] = params[0]
        if "submitted_at" in sql:
            workflow["submitted_at"] = "2026-05-09T00:00:00Z"
        if "reviewed_by" in sql:
            workflow["reviewed_by"] = "user-1"
        return dict(workflow)

    monkeypatch.setattr(compliance_mod, "qone", fake_qone)
    monkeypatch.setattr(compliance_mod, "qall", lambda sql, params: [])
    monkeypatch.setattr(compliance_mod, "execute", fake_execute)
    monkeypatch.setattr(compliance_mod, "record_event", lambda **kwargs: None)

    client = _client()
    submitted = client.post(
        "/api/v1/projects/proj-1/compliance/workflows/wf-1/transition",
        json={"action": "submit"},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"

    under_review = client.post(
        "/api/v1/projects/proj-1/compliance/workflows/wf-1/transition",
        json={"action": "start_review"},
    )
    assert under_review.status_code == 200, under_review.text
    assert under_review.json()["status"] == "under_review"


def test_workflow_detail_includes_comments_and_snapshot(monkeypatch):
    monkeypatch.setattr(
        compliance_mod,
        "qone",
        lambda sql, params: {
            "id": "wf-1",
            "project_id": "proj-1",
            "status": "approved",
            "snapshot_id": "snap-1",
            "title": "Revue QP",
            "report_type": "ni43101",
        }
        if "approval_workflows" in sql
        else {"snapshot_data": {"design_criteria": [{"item": "Throughput"}]}, "checksum": "abc123"},
    )
    monkeypatch.setattr(
        compliance_mod,
        "qall",
        lambda sql, params: [
            {
                "id": "comment-1",
                "user_id": "user-1",
                "user": "met@example.test",
                "text": "Ready for signature",
                "created_at": "2026-05-09T00:00:00Z",
            }
        ],
    )

    response = _client().get("/api/v1/projects/proj-1/compliance/workflows/wf-1")

    assert response.status_code == 200, response.text
    assert response.json()["comments"][0]["text"] == "Ready for signature"
    assert response.json()["snapshot"]["data"]["design_criteria"][0]["item"] == "Throughput"
    assert response.json()["snapshot"]["checksum"] == "abc123"


def test_add_comment_persists_to_workflow(monkeypatch):
    monkeypatch.setattr(
        compliance_mod,
        "qone",
        lambda sql, params: {"id": "wf-1", "project_id": "proj-1"} if "approval_workflows" in sql else None,
    )

    def fake_execute(sql: str, params: tuple[Any, ...]):
        return {
            "id": params[0],
            "workflow_id": params[1],
            "user_id": params[2],
            "comment": params[3],
            "created_at": "2026-05-09T00:00:00Z",
        }

    monkeypatch.setattr(compliance_mod, "execute", fake_execute)
    monkeypatch.setattr(compliance_mod, "record_event", lambda **kwargs: None)

    response = _client().post(
        "/api/v1/projects/proj-1/compliance/workflows/wf-1/comments",
        json={"text": "Please attach QP certificate."},
    )

    assert response.status_code == 201, response.text
    assert response.json()["text"] == "Please attach QP certificate."
