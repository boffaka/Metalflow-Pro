"""Tests for /api/v1/projects/{pid}/simulation-v2/compare* (Plan 3)."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from db import execute, qone


def _seed_run(pid: str, template_id: str, *, label: str, results: dict, ops: list[str]) -> str:
    """Insert a simulation_runs_v2 row and return run_id."""
    run_id = str(uuid.uuid4())
    execute(
        "INSERT INTO simulation_runs_v2 "
        "(id, project_id, template_id, run_type, run_mode, ops_simulated, results, label) "
        "VALUES (%s, %s, %s, 'rigorous', 'global', %s, %s::jsonb, %s)",
        (run_id, pid, template_id, ops, json.dumps(results), label),
    )
    return run_id


def test_compare_creates_set(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    tpl_id = seeded_simple_project["template_id"]
    r1 = _seed_run(pid, tpl_id, label="A",
                   results={"overall": {"total_recovery_pct": 90.0, "energy_kwh_t": 15.0}},
                   ops=["HPGR", "BALL_MILL", "CIL"])
    r2 = _seed_run(pid, tpl_id, label="B",
                   results={"overall": {"total_recovery_pct": 91.5, "energy_kwh_t": 14.2}},
                   ops=["HPGR", "BALL_MILL", "CIL"])

    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/compare",
        json={"name": "A vs B", "run_ids": [r1, r2]},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    set_id = body["set_id"]

    row = qone(
        "SELECT id::text AS id, name FROM simulation_comparison_sets WHERE id::text = %s",
        (set_id,),
    )
    assert row is not None
    assert row["name"] == "A vs B"


def test_compare_matrix_returns_kpis_per_run(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    tpl_id = seeded_simple_project["template_id"]

    r1 = _seed_run(pid, tpl_id, label="Low",
                   results={"overall": {
                       "total_recovery_pct": 88.0, "energy_kwh_t": 18.0,
                       "capex_musd": 150.0, "opex_usd_t": 12.5,
                   }},
                   ops=["HPGR", "BALL_MILL", "CIL"])
    r2 = _seed_run(pid, tpl_id, label="High",
                   results={"overall": {
                       "total_recovery_pct": 93.0, "energy_kwh_t": 16.0,
                       "capex_musd": 180.0, "opex_usd_t": 11.0,
                   }},
                   ops=["HPGR", "BALL_MILL", "CIL", "GRAVITY"])

    created = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/compare",
        json={"name": "Matrix test", "run_ids": [r1, r2]},
        headers=auth_headers,
    )
    assert created.status_code == 201
    set_id = created.json()["set_id"]

    r = client.get(
        f"/api/v1/projects/{pid}/simulation-v2/compare/{set_id}",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["set_id"] == set_id
    assert len(body["runs"]) == 2

    kpis_by_id = {e["run_id"]: e["kpis"] for e in body["runs"]}
    assert kpis_by_id[r1]["recovery"] == 88.0
    assert kpis_by_id[r1]["energy"] == 18.0
    # capex normalized to USD (× 1e6)
    assert abs(kpis_by_id[r1]["capex"] - 150_000_000.0) < 1.0
    assert kpis_by_id[r2]["opex"] == 11.0


def test_compare_diff_returns_ops_delta(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    tpl_id = seeded_simple_project["template_id"]
    r1 = _seed_run(pid, tpl_id, label="A",
                   results={"overall": {"total_recovery_pct": 90.0}},
                   ops=["HPGR", "BALL_MILL", "CIL"])
    r2 = _seed_run(pid, tpl_id, label="B",
                   results={"overall": {"total_recovery_pct": 91.0}},
                   ops=["HPGR", "BALL_MILL", "GRAVITY", "CIL"])

    created = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/compare",
        json={"name": "Diff", "run_ids": [r1, r2]},
        headers=auth_headers,
    )
    set_id = created.json()["set_id"]

    r = client.get(
        f"/api/v1/projects/{pid}/simulation-v2/compare/{set_id}/diff",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["set_id"] == set_id

    added = body["ops_added_per_pair"]
    removed = body["ops_removed_per_pair"]
    assert len(added) == 1 and len(removed) == 1
    # Pair (r1 -> r2): r2 has GRAVITY as an extra op
    assert "GRAVITY" in added[0]["ops_added"]
    # r1 has no extra ops relative to r2
    assert removed[0]["ops_removed"] == []


def test_compare_rejects_runs_from_other_project(client: TestClient, auth_headers, seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    tpl_id = seeded_simple_project["template_id"]
    r1 = _seed_run(pid, tpl_id, label="inside",
                   results={"overall": {"total_recovery_pct": 90.0}},
                   ops=["HPGR"])
    # A foreign run_id (never inserted) → treated as not-in-project
    foreign = str(uuid.uuid4())

    r = client.post(
        f"/api/v1/projects/{pid}/simulation-v2/compare",
        json={"name": "bad", "run_ids": [r1, foreign]},
        headers=auth_headers,
    )
    # The endpoint raises 404 when one of the run_ids is not in the project
    assert r.status_code in (403, 404), r.text
