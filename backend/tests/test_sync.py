"""Integration tests for offline sync API (routes/sync.py)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set",
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def test_sync_push_accepts_mutation(client, auth_headers, test_project_id):
    entity_id = str(uuid.uuid4())
    r = client.post(
        f"/api/v1/projects/{test_project_id}/sync/push",
        headers=auth_headers,
        json={
            "mutations": [
                {
                    "entity_type": "lims_sample",
                    "entity_id": entity_id,
                    "action": "update",
                    "field_changes": {"au_g_t": 2.5},
                    "client_timestamp": _iso(datetime.now(timezone.utc)),
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["accepted"]) == 1
    assert body["conflicts"] == []


def test_sync_pull_invalid_since_returns_400(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/sync/pull",
        headers=auth_headers,
        params={"since": "not-a-valid-timestamp"},
    )
    assert r.status_code == 400


def test_sync_pull_returns_audit_events(client, auth_headers, test_project_id):
    entity_id = str(uuid.uuid4())
    since = _iso(datetime.now(timezone.utc) - timedelta(days=1))
    push = client.post(
        f"/api/v1/projects/{test_project_id}/sync/push",
        headers=auth_headers,
        json={
            "mutations": [
                {
                    "entity_type": "lims_sample",
                    "entity_id": entity_id,
                    "action": "update",
                    "field_changes": {"au_g_t": 3.1},
                    "client_timestamp": _iso(datetime.now(timezone.utc)),
                }
            ]
        },
    )
    assert push.status_code == 200, push.text

    r = client.get(
        f"/api/v1/projects/{test_project_id}/sync/pull",
        headers=auth_headers,
        params={"since": since},
    )
    assert r.status_code == 200, r.text
    events = r.json()
    assert isinstance(events, list)
    assert any(e.get("entity_id") == entity_id for e in events)


def test_sync_detects_field_conflict(client, auth_headers, test_project_id):
    from audit import record_event
    from db import qone

    entity_id = str(uuid.uuid4())
    admin = qone(
        "SELECT id FROM users WHERE email = %s",
        (os.environ.get("ADMIN_EMAIL", "admin@test.dev"),),
    )
    assert admin, "admin user required for conflict test"
    user_id = str(admin["id"])

    client_ts = datetime.now(timezone.utc) - timedelta(hours=2)

    record_event(
        user_id=user_id,
        project_id=test_project_id,
        entity_type="lims_sample",
        entity_id=entity_id,
        action="update",
        field_name="au_g_t",
        new_value={"au_g_t": 4.2},
        source="web",
    )

    r = client.post(
        f"/api/v1/projects/{test_project_id}/sync/push",
        headers=auth_headers,
        json={
            "mutations": [
                {
                    "entity_type": "lims_sample",
                    "entity_id": entity_id,
                    "action": "update",
                    "field_changes": {"au_g_t": 2.0},
                    "client_timestamp": _iso(client_ts),
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["conflicts"]) >= 1
    assert body["conflicts"][0]["field_name"] == "au_g_t"

    listed = client.get(
        f"/api/v1/projects/{test_project_id}/sync/conflicts",
        headers=auth_headers,
    )
    assert listed.status_code == 200, listed.text
    open_conflicts = listed.json()
    assert len(open_conflicts) >= 1

    conflict_id = open_conflicts[0]["id"]
    resolved = client.post(
        f"/api/v1/projects/{test_project_id}/sync/conflicts/{conflict_id}/resolve",
        headers=auth_headers,
        json={"resolution": "local"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json().get("resolved_at") is not None


def test_sync_unauthenticated_rejected(client, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/sync/push",
        json={"mutations": []},
    )
    assert r.status_code == 401
