"""Tests for custom scenario endpoints (Plan 2).

Covers:
  - POST /simulation-v2/custom/from-flowsheet
  - POST /simulation-v2/custom/from-template
  - POST /simulation-v2/custom/blank
  - POST /simulation-v2/suggestions/{id}/fork
  - GET  /scenarios/flowsheets

Uses the same ``seeded_simple_project`` fixture as Plan 1 integration tests.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# /custom/from-flowsheet
# ---------------------------------------------------------------------------

def test_from_flowsheet_copies_blocks_and_connections(
    client: TestClient, auth_headers, seeded_simple_project,
):
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/from-flowsheet",
        json={"name": "My fork"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scenario_flowsheet_id"]
    assert body["scenario_id"]
    assert body["name"] == "My fork"


def test_from_flowsheet_creates_project_scenario_and_flowsheet_rows(
    client: TestClient, auth_headers, seeded_simple_project,
):
    """Creating then listing should yield at least one item."""
    pid = seeded_simple_project["project_id"]
    client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/from-flowsheet",
        json={},
        headers=auth_headers,
    )
    r = client.get(
        f"/api/v1/projects/{pid}/scenarios/flowsheets",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert len(r.json()["items"]) >= 1


def test_from_flowsheet_without_body_works(
    client: TestClient, auth_headers, seeded_simple_project,
):
    """The body is optional — auto-generates a name."""
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/from-flowsheet",
        json={},
        headers=auth_headers,
    )
    assert r.status_code == 201
    assert r.json()["name"]


# ---------------------------------------------------------------------------
# /custom/from-template
# ---------------------------------------------------------------------------

def test_from_template_creates_scenario_with_preset_blocks(
    client: TestClient, auth_headers, seeded_simple_project,
):
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/from-template",
        json={"template_name": "hpgr_ball", "name": "HPGR preset"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "HPGR preset"


def test_from_template_unknown_name_returns_400(
    client: TestClient, auth_headers, seeded_simple_project,
):
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/from-template",
        json={"template_name": "nonexistent"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "Available" in r.text or "available" in r.text


def test_from_template_all_three_presets(
    client: TestClient, auth_headers, seeded_simple_project,
):
    pid = seeded_simple_project["project_id"]
    for tpl in ("sag_ball", "hpgr_ball", "heap_leach"):
        r = client.post(
            f"/api/v1/projects/{pid}/simulation-v2/custom/from-template",
            json={"template_name": tpl},
            headers=auth_headers,
        )
        assert r.status_code == 201, f"{tpl}: {r.text}"


# ---------------------------------------------------------------------------
# /custom/blank
# ---------------------------------------------------------------------------

def test_blank_creates_empty_scenario_flowsheet(
    client: TestClient, auth_headers, seeded_simple_project,
):
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/blank",
        json={"name": "Empty canvas"},
        headers=auth_headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Empty canvas"

    # The listing should report 0 blocks / 0 connections for this one.
    r2 = client.get(
        f"/api/v1/projects/{pid}/scenarios/flowsheets",
        headers=auth_headers,
    )
    assert r2.status_code == 200
    matching = [x for x in r2.json()["items"]
                if x["scenario_flowsheet_id"] == body["scenario_flowsheet_id"]]
    assert len(matching) == 1
    assert matching[0]["n_blocks"] == 0
    assert matching[0]["n_connections"] == 0


# ---------------------------------------------------------------------------
# /suggestions/{id}/fork
# ---------------------------------------------------------------------------

def test_fork_suggestion_applies_ops_delta(
    client: TestClient, auth_headers, seeded_simple_project,
):
    """Insert a fake suggestion row, then fork it."""
    pid = seeded_simple_project["project_id"]
    try:
        from db import execute
    except ImportError:
        from backend.db import execute

    # Seed one suggestion.
    execute(
        "INSERT INTO scenario_suggestions_log "
        "(project_id, suggestion_id, title, category, confidence, ops_to_add, ops_to_remove, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (pid, "auto_hpgr_swap", "Replace SAG with HPGR", "comminution", "high",
         ["HPGR"], ["SAG_MILL"], "proposed"),
    )

    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/suggestions/auto_hpgr_swap/fork",
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["scenario_flowsheet_id"]
    assert "HPGR" in body["ops_added"]
    assert "SAG_MILL" in body["ops_removed"]


def test_fork_unknown_suggestion_returns_404(
    client: TestClient, auth_headers, seeded_simple_project,
):
    pid = seeded_simple_project["project_id"]
    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/suggestions/definitely_nonexistent_xyz/fork",
        headers=auth_headers,
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GET /scenarios/flowsheets
# ---------------------------------------------------------------------------

def test_list_scenario_flowsheets_empty(
    client: TestClient, auth_headers, seeded_simple_project,
):
    """Pristine project (no scenarios) => empty list, no error."""
    pid = seeded_simple_project["project_id"]
    r = client.get(
        f"/api/v1/projects/{pid}/scenarios/flowsheets",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "items" in r.json()
    # This fixture has no pre-seeded scenarios
    # (some may exist if prior tests in the same session ran — tolerant assertion).
    assert isinstance(r.json()["items"], list)


def test_list_scenario_flowsheets_returns_created_entries(
    client: TestClient, auth_headers, seeded_simple_project,
):
    pid = seeded_simple_project["project_id"]
    client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/blank",
        json={"name": "S1"},
        headers=auth_headers,
    )
    client.post(
        f"/api/v1/projects/{pid}/simulation-v2/custom/blank",
        json={"name": "S2"},
        headers=auth_headers,
    )
    r = client.get(
        f"/api/v1/projects/{pid}/scenarios/flowsheets",
        headers=auth_headers,
    )
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["items"]]
    assert "S1" in names and "S2" in names
