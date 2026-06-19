"""Integration test for PATCH /dc-pipeline/rows/{rid} (Chunk 1.5.D).

The Living Circuit edit flow needs PATCH writes to land in
`design_criteria_v2`. The legacy `PATCH /design-criteria/rows/{rid}` writes
to legacy `design_criteria`, which has no `ref_number` column and is never
read by the cascade engine — so user edits never become observable to the
DAG. This test pins the new behaviour.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping integration test")
def test_patch_v2_writes_to_design_criteria_v2(client, auth_headers):
    """PATCH /dc-pipeline/rows/{rid} updates design_criteria_v2 in-place."""
    try:
        from db import execute, qone
    except ImportError:  # pragma: no cover
        from backend.db import execute, qone  # type: ignore[no-redef]

    pid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    try:
        execute(
            "INSERT INTO projects (id, project_name, project_code, target_tph, gold_grade_g_t) "
            "VALUES (%s, %s, %s, %s, %s)",
            (pid, f"PATCH-V2-{pid[:6]}", f"PV2-{pid[:6]}", 1500, 1.5),
        )
        execute(
            "INSERT INTO unit_operations_catalog (op_code, category, label) "
            "VALUES ('SAG_MILL', 'broyage', 'SAG Mill') ON CONFLICT (op_code) DO NOTHING"
        )
        execute(
            "INSERT INTO circuit_templates (id, project_id, name, is_active) "
            "VALUES (%s, %s, 'patch-v2-tpl', TRUE)",
            (tid, pid),
        )
        row = execute(
            "INSERT INTO design_criteria_v2 "
            "(project_id, template_id, op_code, ref_number, dag_key, item, design_value, source_code, enabled) "
            "VALUES (%s, %s, 'SAG_MILL', '3.1.01', 'target_tph', 'Débit', 1500.0, 'D', TRUE) "
            "RETURNING id",
            (pid, tid),
        )
        rid = row["id"]

        r = client.patch(
            f"/api/v1/projects/{pid}/dc-pipeline/rows/{rid}",
            json={"design_value": 1800.0, "comments": "patched via dc-pipeline"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        # Verify the write landed in design_criteria_v2
        check = qone("SELECT design_value, comments FROM design_criteria_v2 WHERE id = %s", (rid,))
        assert check is not None
        assert float(check["design_value"]) == pytest.approx(1800.0)
        assert check["comments"] == "patched via dc-pipeline"
    finally:
        execute("DELETE FROM projects WHERE id = %s", (pid,))


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping integration test")
def test_patch_v2_increments_version_and_sets_updated_by(client, auth_headers):
    """PATCH bumps `version` and stamps `updated_by` on every write.

    The schema declares `version INTEGER DEFAULT 1` and
    `updated_by UUID REFERENCES users(id)`. Optimistic-locking-aware clients
    rely on `version` advancing on each write; audit / provenance dashboards
    rely on `updated_by` always reflecting the most recent author.
    """
    try:
        from db import execute, qone
    except ImportError:  # pragma: no cover
        from backend.db import execute, qone  # type: ignore[no-redef]

    pid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    try:
        execute(
            "INSERT INTO projects (id, project_name, project_code, target_tph, gold_grade_g_t) "
            "VALUES (%s, %s, %s, %s, %s)",
            (pid, f"VER-{pid[:6]}", f"VER-{pid[:6]}", 1500, 1.5),
        )
        execute(
            "INSERT INTO unit_operations_catalog (op_code, category, label) "
            "VALUES ('SAG_MILL', 'broyage', 'SAG Mill') ON CONFLICT (op_code) DO NOTHING"
        )
        execute(
            "INSERT INTO circuit_templates (id, project_id, name, is_active) "
            "VALUES (%s, %s, 'ver-tpl', TRUE)",
            (tid, pid),
        )
        row = execute(
            "INSERT INTO design_criteria_v2 "
            "(project_id, template_id, op_code, ref_number, dag_key, item, design_value, source_code, enabled) "
            "VALUES (%s, %s, 'SAG_MILL', '3.1.01', 'target_tph', 'Débit', 1500.0, 'D', TRUE) "
            "RETURNING id, version",
            (pid, tid),
        )
        rid = row["id"]
        v0 = int(row["version"] or 1)

        # First PATCH — version must advance by 1, updated_by must be set.
        r1 = client.patch(
            f"/api/v1/projects/{pid}/dc-pipeline/rows/{rid}",
            json={"design_value": 1700.0},
            headers=auth_headers,
        )
        assert r1.status_code == 200, r1.text
        body1 = r1.json()
        assert int(body1["version"]) == v0 + 1
        assert body1["updated_by"] is not None

        # Second PATCH — version advances again from the previous write.
        r2 = client.patch(
            f"/api/v1/projects/{pid}/dc-pipeline/rows/{rid}",
            json={"design_value": 1800.0},
            headers=auth_headers,
        )
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert int(body2["version"]) == v0 + 2

        # Final DB state matches what the API returned.
        check = qone(
            "SELECT version, updated_by, design_value FROM design_criteria_v2 WHERE id = %s",
            (rid,),
        )
        assert int(check["version"]) == v0 + 2
        assert check["updated_by"] is not None
        assert float(check["design_value"]) == pytest.approx(1800.0)
    finally:
        execute("DELETE FROM projects WHERE id = %s", (pid,))


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping integration test")
def test_patch_v2_404_for_row_outside_project(client, auth_headers):
    """PATCH refuses to touch a row whose template belongs to a different project."""
    try:
        from db import execute
    except ImportError:  # pragma: no cover
        from backend.db import execute  # type: ignore[no-redef]

    pid_a = str(uuid.uuid4())
    pid_b = str(uuid.uuid4())
    tid_b = str(uuid.uuid4())
    try:
        execute(
            "INSERT INTO projects (id, project_name, project_code) VALUES (%s, %s, %s), (%s, %s, %s)",
            (pid_a, f"A-{pid_a[:6]}", f"A-{pid_a[:6]}",
             pid_b, f"B-{pid_b[:6]}", f"B-{pid_b[:6]}"),
        )
        execute(
            "INSERT INTO unit_operations_catalog (op_code, category, label) "
            "VALUES ('SAG_MILL', 'broyage', 'SAG Mill') ON CONFLICT (op_code) DO NOTHING"
        )
        execute(
            "INSERT INTO circuit_templates (id, project_id, name, is_active) "
            "VALUES (%s, %s, 'project-b-tpl', TRUE)",
            (tid_b, pid_b),
        )
        row_b = execute(
            "INSERT INTO design_criteria_v2 "
            "(project_id, template_id, op_code, ref_number, item, design_value, source_code, enabled) "
            "VALUES (%s, %s, 'SAG_MILL', '3.1.01', 'Débit', 1500.0, 'D', TRUE) RETURNING id",
            (pid_b, tid_b),
        )
        rid_b = row_b["id"]

        # Try to patch project B's row via project A's URL — must 404
        r = client.patch(
            f"/api/v1/projects/{pid_a}/dc-pipeline/rows/{rid_b}",
            json={"design_value": 999.0},
            headers=auth_headers,
        )
        assert r.status_code == 404
    finally:
        execute("DELETE FROM projects WHERE id IN (%s, %s)", (pid_a, pid_b))


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping integration test")
def test_patch_v2_returns_409_on_version_conflict(client, auth_headers):
    """When `expected_version` doesn't match the row's current version,
    the backend returns 409 (audit final review §3 — optimistic locking).

    Two writers race: writer A and writer B both load the row at v=1.
    A patches with expected_version=1 (success → v=2). B patches with
    expected_version=1 too — must 409, not silently overwrite.
    """
    try:
        from db import execute, qone
    except ImportError:  # pragma: no cover
        from backend.db import execute, qone  # type: ignore[no-redef]

    pid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    try:
        execute(
            "INSERT INTO projects (id, project_name, project_code, target_tph, gold_grade_g_t) "
            "VALUES (%s, %s, %s, %s, %s)",
            (pid, f"OPT-{pid[:6]}", f"OPT-{pid[:6]}", 1500, 1.5),
        )
        execute(
            "INSERT INTO unit_operations_catalog (op_code, category, label) "
            "VALUES ('SAG_MILL', 'broyage', 'SAG Mill') ON CONFLICT (op_code) DO NOTHING"
        )
        execute(
            "INSERT INTO circuit_templates (id, project_id, name, is_active) "
            "VALUES (%s, %s, 'opt-tpl', TRUE)",
            (tid, pid),
        )
        row = execute(
            "INSERT INTO design_criteria_v2 "
            "(project_id, template_id, op_code, ref_number, dag_key, item, design_value, source_code, enabled) "
            "VALUES (%s, %s, 'SAG_MILL', '3.1.01', 'target_tph', 'Débit', 1500.0, 'D', TRUE) "
            "RETURNING id, version",
            (pid, tid),
        )
        rid = row["id"]
        v0 = int(row["version"] or 1)

        # Writer A: PATCH with the correct expected_version → 200, version bumps.
        r1 = client.patch(
            f"/api/v1/projects/{pid}/dc-pipeline/rows/{rid}",
            json={"design_value": 1700.0, "expected_version": v0},
            headers=auth_headers,
        )
        assert r1.status_code == 200, r1.text
        assert int(r1.json()["version"]) == v0 + 1

        # Writer B: PATCH with a stale expected_version → 409.
        r2 = client.patch(
            f"/api/v1/projects/{pid}/dc-pipeline/rows/{rid}",
            json={"design_value": 1800.0, "expected_version": v0},
            headers=auth_headers,
        )
        assert r2.status_code == 409, r2.text
        # Body confirms it's a version conflict, not a missing row.
        assert "Version conflict" in r2.json().get("detail", "")

        # Database state reflects writer A's value (1700), not writer B's (1800).
        check = qone("SELECT design_value, version FROM design_criteria_v2 WHERE id = %s", (rid,))
        assert float(check["design_value"]) == pytest.approx(1700.0)
        assert int(check["version"]) == v0 + 1

        # Sanity: omitting expected_version remains backwards-compatible —
        # last-write-wins applies and the row updates without 409.
        r3 = client.patch(
            f"/api/v1/projects/{pid}/dc-pipeline/rows/{rid}",
            json={"design_value": 1900.0},
            headers=auth_headers,
        )
        assert r3.status_code == 200, r3.text
    finally:
        execute("DELETE FROM projects WHERE id = %s", (pid,))


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping integration test")
def test_patch_then_cascade_observes_change(client, auth_headers):
    """End-to-end U1 + Option A — patched value participates in next cascade."""
    try:
        from db import execute
    except ImportError:  # pragma: no cover
        from backend.db import execute  # type: ignore[no-redef]

    pid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    try:
        execute(
            "INSERT INTO projects (id, project_name, project_code, target_tph, gold_grade_g_t) "
            "VALUES (%s, %s, %s, %s, %s)",
            (pid, f"PtC-{pid[:6]}", f"PtC-{pid[:6]}", 1500, 1.5),
        )
        execute(
            "INSERT INTO unit_operations_catalog (op_code, category, label) "
            "VALUES ('SAG_MILL', 'broyage', 'SAG Mill') ON CONFLICT (op_code) DO NOTHING"
        )
        execute(
            "INSERT INTO circuit_templates (id, project_id, name, is_active) "
            "VALUES (%s, %s, 'ptc-tpl', TRUE)",
            (tid, pid),
        )
        for ref, dag_key, val, item in [
            ("3.1.02", "avg_bwi",          14.0, "Bond BWi"),
            ("3.1.03", "sag_f80_mm",      135.0, "F80 alim SAG"),
            ("3.1.04", "sag_p80_mm",        2.5, "T80 sortie SAG"),
            ("3.1.05", "mech_efficiency",  95.0, "Rendement moteur"),
        ]:
            execute(
                "INSERT INTO design_criteria_v2 "
                "(project_id, template_id, op_code, ref_number, dag_key, item, design_value, source_code, enabled) "
                "VALUES (%s, %s, 'SAG_MILL', %s, %s, %s, %s, 'D', TRUE)",
                (pid, tid, ref, dag_key, item, val),
            )
        # Make a v2 row for target_tph that the PATCH will mutate
        target_row = execute(
            "INSERT INTO design_criteria_v2 "
            "(project_id, template_id, op_code, ref_number, dag_key, item, design_value, source_code, enabled) "
            "VALUES (%s, %s, 'SAG_MILL', '3.1.01', 'target_tph', 'Débit', 1500.0, 'D', TRUE) RETURNING id",
            (pid, tid),
        )
        rid = target_row["id"]

        # Patch the v2 row, then cascade
        r = client.patch(
            f"/api/v1/projects/{pid}/dc-pipeline/rows/{rid}",
            json={"design_value": 1800.0},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

        c = client.post(
            f"/api/v1/projects/{pid}/dc-pipeline/cascade",
            json={"changes": [{"key": "target_tph", "value": 1800.0}]},
            headers=auth_headers,
        )
        assert c.status_code == 200, c.text
        keys_updated = [u.get("key") for u in c.json().get("updates", [])]
        assert "sag_power_kw" in keys_updated
    finally:
        execute("DELETE FROM projects WHERE id = %s", (pid,))
