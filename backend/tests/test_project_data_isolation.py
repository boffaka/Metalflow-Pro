"""Ensure Golden Mine / Gosselin (and any pair of projects) never share LIMS or geomet cache."""
from __future__ import annotations

import pytest

from routes.geomet_intelligence import _domain_cache, invalidate_domain_cache


def _create_project(client, headers, code: str, name: str) -> str:
    resp = client.post(
        "/api/v1/projects",
        headers=headers,
        json={
            "project_name": name,
            "project_code": code,
            "target_tph": 100,
            "gold_grade_g_t": 1.0,
            "status": "SCOPING",
        },
    )
    assert resp.status_code in (201, 409), resp.text
    if resp.status_code == 201:
        return resp.json()["id"]
    listed = client.get("/api/v1/projects", headers=headers).json()
    items = listed.get("items", listed) if isinstance(listed, dict) else listed
    match = next((p["id"] for p in items if p.get("project_code") == code), None)
    assert match, f"project {code} not found after 409"
    return match


def test_lims_samples_isolated_between_projects(client, admin_headers):
    pid_a = _create_project(client, admin_headers, "ISO-A-001", "Isolation Project A")
    pid_b = _create_project(client, admin_headers, "ISO-B-001", "Isolation Project B")

    sample_resp = client.post(
        f"/api/v1/projects/{pid_a}/lims/samples",
        headers=admin_headers,
        json={
            "sample_id_display": "ISO-A-SAMPLE-1",
            "phase": "SCOPING",
            "sample_type": "CORE",
            "lithology": "Granite",
        },
    )
    assert sample_resp.status_code == 201, sample_resp.text
    sample_id = sample_resp.json()["id"]

    list_a = client.get(f"/api/v1/projects/{pid_a}/lims/samples", headers=admin_headers)
    list_b = client.get(f"/api/v1/projects/{pid_b}/lims/samples", headers=admin_headers)
    assert list_a.status_code == 200
    assert list_b.status_code == 200

    ids_a = {s["id"] for s in (list_a.json().get("items") or list_a.json())}
    ids_b = {s["id"] for s in (list_b.json().get("items") or list_b.json())}

    assert sample_id in ids_a
    assert sample_id not in ids_b


@pytest.mark.no_db
def test_geomet_domain_cache_isolated_per_project():
    invalidate_domain_cache("proj-a")
    invalidate_domain_cache("proj-b")
    _domain_cache["proj-a"] = {"status": "ok", "domains": [{"id": "A"}]}
    _domain_cache["proj-b"] = {"status": "ok", "domains": [{"id": "B"}]}

    assert _domain_cache["proj-a"]["domains"][0]["id"] == "A"
    assert _domain_cache["proj-b"]["domains"][0]["id"] == "B"

    invalidate_domain_cache("proj-a")
    assert "proj-a" not in _domain_cache
    assert _domain_cache["proj-b"]["domains"][0]["id"] == "B"

    invalidate_domain_cache("proj-b")
    assert not _domain_cache.get("proj-b")
