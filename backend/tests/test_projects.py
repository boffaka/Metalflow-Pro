"""Tests for /api/v1/projects endpoints (simulation v3 feature flag exposure)."""
from __future__ import annotations


def test_get_project_exposes_feature_flags(client, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    # Set a flag
    from db import execute
    execute(
        "UPDATE projects SET feature_flags = jsonb_set(COALESCE(feature_flags,'{}'::jsonb), "
        "'{SIM_V3_UI}', 'true') WHERE id = %s",
        (pid,),
    )
    r = client.get(f"/api/v1/projects/{pid}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "feature_flags" in body
    assert body["feature_flags"].get("SIM_V3_UI") is True
