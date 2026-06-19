# backend/tests/test_layout3d_route.py
"""Integration tests for 3D layout endpoints."""
import pytest, os

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Requires TEST_DATABASE_URL"
)

def test_create_layout_zone(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/layout3d/zones",
        json={"zone_code": "400", "zone_name": "CIL Leaching", "color_hex": "#22c55e",
              "bbox": {"x_min": 0, "x_max": 50, "y_min": 0, "y_max": 30, "z_min": 0, "z_max": 10}},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert "zone_id" in r.json()

def test_get_layout_zones(client, auth_headers, test_project_id):
    r = client.get(f"/api/v1/projects/{test_project_id}/layout3d/zones", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_set_equipment_position(client, auth_headers, test_project_id):
    import uuid
    equip_id = str(uuid.uuid4())
    r = client.post(
        f"/api/v1/projects/{test_project_id}/layout3d/positions",
        json={"equipment_id": equip_id, "x": 10.0, "y": 5.0, "z": 0.0, "rotation_deg": 0.0, "zone": "400"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert "position_id" in r.json()

def test_get_all_positions(client, auth_headers, test_project_id):
    r = client.get(f"/api/v1/projects/{test_project_id}/layout3d/positions", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_auto_arrange_returns_positions(client, auth_headers, test_project_id):
    r = client.post(f"/api/v1/projects/{test_project_id}/layout3d/auto-arrange", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "arranged" in data
