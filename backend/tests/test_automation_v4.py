# backend/tests/test_automation_v4.py
"""Integration tests for v4 SCADA/DCS automation endpoints."""
import pytest, os

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Requires TEST_DATABASE_URL"
)

def test_list_pid_loops(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/automation/pid-loops",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_tune_pid_loop(client, auth_headers, test_project_id):
    """POST /automation/pid-loops/{id}/tune returns tuned Kp/Ti/Td."""
    r = client.post(
        f"/api/v1/projects/{test_project_id}/automation/pid-loops",
        json={
            "loop_tag": "TEST-100-001",
            "tuning_method": "ziegler_nichols",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    loop_id = r.json()["loop_id"]

    r2 = client.post(
        f"/api/v1/projects/{test_project_id}/automation/pid-loops/{loop_id}/tune",
        json={
            "method": "ziegler_nichols",
            "ku": 2.0,
            "pu_s": 60.0,
            "controller_type": "PI",
        },
        headers=auth_headers,
    )
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert "kp" in data
    assert "ti_s" in data

def test_create_grafcet_sequence(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/automation/grafcet",
        json={
            "sequence_name": "SAG Mill Start",
            "area": "200",
            "steps": [
                {"id": 0, "label": "Initial", "actions": []},
                {"id": 1, "label": "Lube System Start", "actions": ["start_lube_pump"]},
                {"id": 2, "label": "Main Drive Start", "actions": ["start_main_drive"]},
            ],
            "transitions": [
                {"from_step": 0, "to_step": 1, "condition": "start_cmd"},
                {"from_step": 1, "to_step": 2, "condition": "lube_pressure_ok"},
            ],
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert "sequence_id" in r.json()

def test_generate_fat_checklists(client, auth_headers, test_project_id):
    """POST /automation/fat-sat-checklists/generate auto-generates from equipment."""
    r = client.post(
        f"/api/v1/projects/{test_project_id}/automation/fat-sat-checklists/generate",
        json={"checklist_type": "FAT"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "checklists_created" in data

def test_get_cause_effect_matrix(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/automation/cause-effect",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
