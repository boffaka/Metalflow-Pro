"""Integration tests for the per-project flowsheet tree endpoints.

Skips automatically when TEST_DATABASE_URL is not set.
"""
from __future__ import annotations

import os
import uuid

import pytest

if not os.getenv("TEST_DATABASE_URL"):
    pytest.skip(
        "TEST_DATABASE_URL not set — skipping flowsheet integration tests",
        allow_module_level=True,
    )

from fastapi.testclient import TestClient  # noqa: E402

from db import execute, qone  # noqa: E402


# ── Fixture: minimal project + ensured flowsheet template ────────────────────

@pytest.fixture
def fs_project(client: TestClient, auth_headers):
    pid = str(uuid.uuid4())
    execute(
        "INSERT INTO projects (id, project_name, project_code) VALUES (%s, %s, %s)",
        (pid, f"FsTst-{pid[:6]}", f"FST-{pid[:6]}"),
    )
    yield pid
    execute("DELETE FROM projects WHERE id = %s", (pid,))


def _create_node(client, headers, pid, **body) -> str:
    r = client.post(
        f"/api/v1/projects/{pid}/flowsheet/operations",
        json=body,
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ── Tests ────────────────────────────────────────────────────────────────────

def test_flowsheet_empty_returns_404(client, auth_headers, fs_project):
    r = client.get(f"/api/v1/projects/{fs_project}/flowsheet", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["reason"] == "no_flowsheet"


def test_ensure_creates_template(client, auth_headers, fs_project):
    r = client.post(f"/api/v1/projects/{fs_project}/flowsheet", headers=auth_headers)
    assert r.status_code == 201
    tid = r.json()["template_id"]
    row = qone("SELECT id FROM circuit_templates WHERE id=%s", (tid,))
    assert row is not None


def test_flowsheet_single_root_linear(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid,
                        op_code="FEED", node_label="Feed",
                        throughput_tph=500, grade_au_gt=2.0)
    mid = _create_node(client, auth_headers, pid,
                       op_code="SAG_MILL", parent_op_id=root,
                       recovery_pct=99, throughput_tph=500)
    leaf = _create_node(client, auth_headers, pid,
                        op_code="LEACH_CIL", parent_op_id=mid,
                        recovery_pct=92, product_kind="bullion")
    r = client.get(f"/api/v1/projects/{pid}/flowsheet", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["tree"]["id"] == root
    assert body["tree"]["children"][0]["id"] == mid
    assert body["tree"]["children"][0]["children"][0]["id"] == leaf


def test_flowsheet_branching(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid, op_code="FEED")
    branch_a = _create_node(client, auth_headers, pid, op_code="A", parent_op_id=root)
    branch_b = _create_node(client, auth_headers, pid, op_code="B", parent_op_id=root)
    r = client.get(f"/api/v1/projects/{pid}/flowsheet", headers=auth_headers)
    assert r.status_code == 200
    children = r.json()["tree"]["children"]
    ids = {c["id"] for c in children}
    assert ids == {branch_a, branch_b}


def test_flowsheet_kpis_recovery_product(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid,
                        op_code="FEED", throughput_tph=500, grade_au_gt=2.0)
    sag = _create_node(client, auth_headers, pid,
                       op_code="SAG_MILL", parent_op_id=root, recovery_pct=99)
    cil = _create_node(client, auth_headers, pid,
                       op_code="LEACH_CIL", parent_op_id=sag, recovery_pct=92)
    _create_node(client, auth_headers, pid,
                 op_code="ELUTION", parent_op_id=cil,
                 recovery_pct=98, product_kind="bullion")
    r = client.get(f"/api/v1/projects/{pid}/flowsheet", headers=auth_headers)
    assert r.status_code == 200
    kpis = r.json()["kpis"]
    # 0.99 * 0.92 * 0.98 = 0.892584 → 89.258%
    assert abs(kpis["global_recovery_pct"] - 89.258) < 0.01
    # production = 500 * 2.0 * 0.892584 / 31.1035
    assert abs(kpis["production_oz_h"] - (500 * 2.0 * 0.892584 / 31.1035)) < 0.05


def test_flowsheet_kpis_null_when_no_bullion_leaf(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid,
                        op_code="FEED", throughput_tph=500, grade_au_gt=2.0)
    _create_node(client, auth_headers, pid,
                 op_code="SAG_MILL", parent_op_id=root, recovery_pct=99)
    r = client.get(f"/api/v1/projects/{pid}/flowsheet", headers=auth_headers)
    assert r.status_code == 200
    kpis = r.json()["kpis"]
    assert kpis["feed_tph"] == 500
    assert kpis["global_recovery_pct"] is None
    assert kpis["production_oz_h"] is None


def test_post_operation_rejects_second_root(client, auth_headers, fs_project):
    pid = fs_project
    _create_node(client, auth_headers, pid, op_code="FEED")
    r = client.post(
        f"/api/v1/projects/{pid}/flowsheet/operations",
        json={"op_code": "FEED2"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "root" in r.json()["detail"].lower()


def test_post_operation_rejects_foreign_parent(client, auth_headers, fs_project):
    pid = fs_project
    _create_node(client, auth_headers, pid, op_code="FEED")
    r = client.post(
        f"/api/v1/projects/{pid}/flowsheet/operations",
        json={
            "op_code": "X",
            "parent_op_id": "00000000-0000-0000-0000-000000000000",
        },
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_patch_value_sets_source_manual(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid, op_code="FEED")
    r = client.patch(
        f"/api/v1/projects/{pid}/flowsheet/operations/{root}",
        json={"recovery_pct": 88},
        headers=auth_headers,
    )
    assert r.status_code == 200
    row = qone(
        "SELECT values_source, recovery_pct FROM circuit_template_operations WHERE id=%s",
        (root,),
    )
    assert row["values_source"] == "manual"
    assert float(row["recovery_pct"]) == 88


def test_patch_null_resets_to_lims_auto(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid,
                        op_code="FEED", recovery_pct=88)
    # Now wipe all values → should switch back to lims_auto
    r = client.patch(
        f"/api/v1/projects/{pid}/flowsheet/operations/{root}",
        json={
            "recovery_pct": None,
            "throughput_tph": None,
            "water_m3h": None,
            "grade_au_gt": None,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    row = qone(
        "SELECT values_source FROM circuit_template_operations WHERE id=%s",
        (root,),
    )
    assert row["values_source"] == "lims_auto"


def test_patch_rejects_cycle(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid, op_code="FEED")
    child = _create_node(client, auth_headers, pid, op_code="A", parent_op_id=root)
    # Try to make root child of `child` → cycle
    r = client.patch(
        f"/api/v1/projects/{pid}/flowsheet/operations/{root}",
        json={"parent_op_id": child},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "cycle" in r.json()["detail"].lower()


def test_delete_node_cascades(client, auth_headers, fs_project):
    pid = fs_project
    root = _create_node(client, auth_headers, pid, op_code="FEED")
    child = _create_node(client, auth_headers, pid, op_code="A", parent_op_id=root)
    grandchild = _create_node(client, auth_headers, pid, op_code="B", parent_op_id=child)

    r = client.delete(
        f"/api/v1/projects/{pid}/flowsheet/operations/{child}",
        headers=auth_headers,
    )
    assert r.status_code == 204

    # Both child and grandchild gone
    assert qone(
        "SELECT id FROM circuit_template_operations WHERE id=%s", (child,)
    ) is None
    assert qone(
        "SELECT id FROM circuit_template_operations WHERE id=%s", (grandchild,)
    ) is None
    # Root still there
    assert qone(
        "SELECT id FROM circuit_template_operations WHERE id=%s", (root,)
    ) is not None


# ── Chunk 2 / Task 2.1 — last_run_id field on GET /flowsheet ────────────────


def test_get_flowsheet_returns_last_run_id_when_run_exists(client, seeded_project, seeded_node, seeded_run):
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/flowsheet",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["last_run_id"] == str(seeded_run["id"])


def test_get_flowsheet_last_run_id_is_null_when_no_run(client, auth_headers, fs_project):
    """Use function-scoped `fs_project` (auto-cleaned) so the FEED root inserted
    here doesn't leak into the session-scoped `seeded_project` used elsewhere."""
    pid = fs_project
    # Ensure a flowsheet template exists, then add a single root.
    client.post(f"/api/v1/projects/{pid}/flowsheet", headers=auth_headers)
    _create_node(
        client, auth_headers, pid,
        op_code="FEED", node_label="Test feed", throughput_tph=100.0,
    )
    res = client.get(
        f"/api/v1/projects/{pid}/flowsheet",
        headers=auth_headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["last_run_id"] is None


# ── Chunk 4 / Task 4.1 — equipment_id FK validation on PATCH ───────────────
# Uses function-scoped fs_project (auto-cleaned) instead of session-scoped
# seeded_project to avoid the seeded_node "Template already has a root node"
# leak when multiple tests in this module run together (same pattern as the
# chunk-2 fix-up).


def test_patch_node_rejects_unknown_equipment_id(client, auth_headers, fs_project):
    pid = fs_project
    client.post(f"/api/v1/projects/{pid}/flowsheet", headers=auth_headers)
    nid = _create_node(
        client, auth_headers, pid,
        op_code="FEED", node_label="Test feed", throughput_tph=100.0,
    )
    bogus = "00000000-0000-0000-0000-deadbeefdead"
    res = client.patch(
        f"/api/v1/projects/{pid}/flowsheet/operations/{nid}",
        json={"equipment_id": bogus},
        headers=auth_headers,
    )
    assert res.status_code == 400, res.text
    assert "equipment" in res.text.lower()


def test_patch_node_accepts_valid_equipment_id(client, auth_headers, fs_project):
    pid = fs_project
    client.post(f"/api/v1/projects/{pid}/flowsheet", headers=auth_headers)
    nid = _create_node(
        client, auth_headers, pid,
        op_code="FEED", node_label="Test feed", throughput_tph=100.0,
    )
    # First create an equipment row in this project. The `equipment` table uses
    # `equipment_tag` (not `name`) — see backend/schema.sql.
    from db import execute  # flat import — pytest runs from backend/
    eq = execute(
        "INSERT INTO equipment (project_id, equipment_tag, equipment_type) "
        "VALUES (%s, 'Test SAG', 'SAG_MILL') RETURNING id",
        (pid,),
    )
    res = client.patch(
        f"/api/v1/projects/{pid}/flowsheet/operations/{nid}",
        json={"equipment_id": str(eq["id"])},
        headers=auth_headers,
    )
    assert res.status_code == 200, res.text
    assert str(res.json().get("equipment_id")) == str(eq["id"])
