"""Tests for column whitelisting on PATCH endpoints."""
import pytest


def test_project_patch_rejects_disallowed_field(client, admin_headers, test_project_id):
    """PATCH /projects/{pid} should ignore fields not in ALLOWED_FIELDS_PROJECT."""
    resp = client.patch(
        f"/api/v1/projects/{test_project_id}",
        json={"id": "aaaaaaaa-0000-0000-0000-000000000000", "project_name": "Safe Name"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == test_project_id  # id was NOT overwritten
    assert resp.json()["project_name"] == "Safe Name"


def test_project_patch_empty_after_filter(client, admin_headers, test_project_id):
    """PATCH with only disallowed fields returns 400."""
    resp = client.patch(
        f"/api/v1/projects/{test_project_id}",
        json={"id": "aaaaaaaa-0000-0000-0000-000000000000"},
        headers=admin_headers,
    )
    assert resp.status_code == 400


def test_equipment_patch_rejects_disallowed_field(client, admin_headers, test_project_id, test_equipment_id):
    """PATCH /equipment/{eid} should ignore fields not in ALLOWED_FIELDS_EQUIPMENT."""
    resp = client.patch(
        f"/api/v1/projects/{test_project_id}/equipment/{test_equipment_id}",
        json={"project_id": "aaaaaaaa-0000-0000-0000-000000000000", "equipment_tag": "SAFE-001"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["equipment_tag"] == "SAFE-001"
