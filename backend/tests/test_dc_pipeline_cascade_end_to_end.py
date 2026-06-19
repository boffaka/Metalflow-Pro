"""Integration test for the cascade endpoint after the dag_key wiring.

Chunk 1.5.C — `run_cascade` now reads `dag_key` directly from
`design_criteria_v2` instead of deriving keys by normalising `ref_number`.
This test verifies that a project with seeded v2 rows carrying canonical
dag_keys produces non-empty `updates` when the engine recomputes downstream.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.integration

_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping integration test")
def test_cascade_observes_v2_row_changes_via_dag_key(client, auth_headers):
    """A change on `target_tph` should produce at least one downstream update.

    Today (pre-fix): the cascade reads no DAG-shaped keys from v2, so
    `updates` is empty.

    After the fix: rows seeded with explicit `dag_key` participate in the
    `current_values` snapshot, and `cascade_recalculate` walks downstream
    nodes (sag_power_kw, bm_power_kw, leach_feed_tph, …).
    """
    # Create a minimal project + circuit_template + a row with dag_key='target_tph'
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
            (pid, f"DAG-Key-Test-{pid[:6]}", f"DKT-{pid[:6]}", 1500, 1.5),
        )
        execute(
            "INSERT INTO unit_operations_catalog (op_code, category, label) "
            "VALUES ('SAG_MILL', 'broyage', 'SAG Mill') ON CONFLICT (op_code) DO NOTHING"
        )
        execute(
            "INSERT INTO circuit_templates (id, project_id, name, is_active) "
            "VALUES (%s, %s, 'cascade-test-tpl', TRUE)",
            (tid, pid),
        )
        execute(
            "INSERT INTO circuit_operations (template_id, op_code, enabled) "
            "VALUES (%s, 'SAG_MILL', TRUE) ON CONFLICT (template_id, op_code) DO NOTHING",
            (tid,),
        )

        # Seed the canonical inputs the SAG power formula depends on
        for ref, dag_key, val, item in [
            ("3.1.01", "target_tph",     1500.0, "Débit alimentation"),
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

        r = client.post(
            f"/api/v1/projects/{pid}/dc-pipeline/cascade",
            json={"changes": [{"key": "target_tph", "value": 2000.0}]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # The DAG node `sag_power_kw` depends on target_tph, so a change there
        # should produce an update on it.
        keys_updated = [u.get("key") for u in body.get("updates", [])]
        assert "sag_power_kw" in keys_updated, (
            f"cascade did not propagate target_tph → sag_power_kw; updates={keys_updated}"
        )
    finally:
        execute("DELETE FROM projects WHERE id = %s", (pid,))
