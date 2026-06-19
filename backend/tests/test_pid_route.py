# backend/tests/test_pid_route.py
"""Integration tests for P&ID endpoints."""
import pytest, os

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Requires TEST_DATABASE_URL"
)

CONTROL_INSTRUMENT_TYPES = ["FIC", "TIC", "LIC", "AIC", "PIC"]

def test_create_pid_diagram(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/pid",
        json={
            "sheet_number": 1, "title": "CIL Circuit P&ID", "area_code": "400",
            "elements": [], "connections": [], "revision": "A",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert "diagram_id" in data
    return data["diagram_id"]

def test_get_pid_diagram(client, auth_headers, test_project_id):
    r = client.get(f"/api/v1/projects/{test_project_id}/pid", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_save_control_instrument_creates_pid_loop(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/pid",
        json={"sheet_number": 2, "title": "Test Sheet", "area_code": "400",
              "elements": [], "connections": []},
        headers=auth_headers,
    )
    diagram_id = r.json()["diagram_id"]
    r2 = client.post(
        f"/api/v1/projects/{test_project_id}/pid/{diagram_id}/instruments",
        json={"tag": "FIC-400-001", "service": "CIL Feed Flow", "instrument_type": "FIC",
              "loop_number": "400-001", "area": "400"},
        headers=auth_headers,
    )
    assert r2.status_code == 201, r2.text
    r3 = client.get(f"/api/v1/projects/{test_project_id}/automation/pid-loops", headers=auth_headers)
    assert r3.status_code == 200
    loops = r3.json()
    loop_tags = [l.get("loop_tag") for l in loops]
    assert "FIC-400-001" in loop_tags

def test_save_indicator_instrument_does_not_create_pid_loop(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/pid",
        json={"sheet_number": 3, "title": "Test", "area_code": "400",
              "elements": [], "connections": []},
        headers=auth_headers,
    )
    diagram_id = r.json()["diagram_id"]
    r2 = client.post(
        f"/api/v1/projects/{test_project_id}/pid/{diagram_id}/instruments",
        json={"tag": "FT-400-099", "service": "Flow Transmitter", "instrument_type": "FT", "area": "400"},
        headers=auth_headers,
    )
    assert r2.status_code == 201
    r3 = client.get(f"/api/v1/projects/{test_project_id}/automation/pid-loops", headers=auth_headers)
    loop_tags = [l.get("loop_tag") for l in r3.json()]
    assert "FT-400-099" not in loop_tags

def test_auto_generate_pid_from_flowsheet(client, auth_headers, test_project_id):
    r = client.post(f"/api/v1/projects/{test_project_id}/pid/auto-generate", json={}, headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "diagrams_created" in data
