"""Layer 2 — services.capex integration with the real DB.
Requires TEST_DATABASE_URL with the 20260427_000036 migration applied.

This test file defines its OWN function-scoped `test_project_id` fixture
that shadows the session-scoped one in conftest.py — each test must start
from a fresh project so seed/override scenarios stay isolated.
"""
from __future__ import annotations

import os
import uuid
import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")


@pytest.fixture
def test_project_id():
    """Create a throwaway project, yield its UUID, drop it after the test.
    Function-scoped — overrides the session-scoped conftest fixture for
    this file so each test starts clean."""
    try:
        from backend.db import qone, execute  # type: ignore
    except ImportError:
        from db import qone, execute  # type: ignore
    pid = str(uuid.uuid4())
    short = pid[:8]
    execute(
        "INSERT INTO projects (id, project_name, project_code, target_tph, circuit_type) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, f"test_capex_{short}", f"TC-{short}", 36000, "cil_conventional"),
    )
    yield pid
    # equipment_v2 / capex_factors cascade-delete via FK ON DELETE CASCADE
    execute("DELETE FROM projects WHERE id=%s", (pid,))


def test_seed_from_template_inserts_rows_and_factors(test_project_id):
    from services import capex
    from db import qall, qone

    inserted = capex.seed_from_template(test_project_id, "cil_conventional", force=True)
    assert inserted >= 5

    rows = qall(
        "SELECT template_key, parametric_alpha, parametric_beta, is_overridden, "
        "       seeded_from_template, price_cad "
        "FROM equipment_v2 WHERE project_id=%s AND seeded_from_template=true",
        (test_project_id,),
    )
    assert len(rows) >= 5
    for r in rows:
        assert r["template_key"] is not None
        assert r["parametric_alpha"] is not None
        assert r["parametric_beta"] is not None
        assert r["is_overridden"] is False
        assert float(r["price_cad"]) > 0  # parametric pricing applied

    factors = qone("SELECT * FROM capex_factors WHERE project_id=%s", (test_project_id,))
    assert factors is not None
    assert float(factors["indirect_pct"]) == 0.30
    assert float(factors["epcm_pct"]) == 0.15
    assert float(factors["contingency_pct"]) == 0.15


def test_seed_force_false_preserves_overridden_rows(test_project_id):
    from services import capex
    from db import qone, execute

    capex.seed_from_template(test_project_id, "cil_conventional", force=True)
    # User overrides one row's price
    execute(
        "UPDATE equipment_v2 SET price_cad=%s, is_overridden=true "
        "WHERE project_id=%s AND template_key=%s",
        (99999999, test_project_id, "primary_crusher"),
    )
    # Re-seed without force
    capex.seed_from_template(test_project_id, "cil_conventional", force=False)
    row = qone(
        "SELECT price_cad, is_overridden FROM equipment_v2 "
        "WHERE project_id=%s AND template_key=%s",
        (test_project_id, "primary_crusher"),
    )
    assert float(row["price_cad"]) == 99999999
    assert row["is_overridden"] is True


def test_seed_force_true_replaces_seeded_rows_only(test_project_id):
    """Manual additions (seeded_from_template=false) must survive force re-seed."""
    from services import capex
    from db import qone, execute

    capex.seed_from_template(test_project_id, "cil_conventional", force=True)
    # Insert a manual row (real schema has many NOT NULL cols — populate all).
    manual_id = str(uuid.uuid4())
    execute(
        "INSERT INTO equipment_v2 "
        "(id, project_id, wbs_code, wbs_description, eq_type, seq_no, "
        " equipment_tag, equipment_name, price_cad, enabled, "
        " seeded_from_template, is_overridden) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, false, true)",
        (manual_id, test_project_id, "999", "Utilities Manual",
         "Compressor", "001", "MANUAL-CO-001", "Compresseur d'usine",
         250000),
    )
    capex.seed_from_template(test_project_id, "cil_conventional", force=True)
    survivor = qone("SELECT id FROM equipment_v2 WHERE id=%s", (manual_id,))
    assert survivor is not None, "Manual row was wiped by force re-seed"


def test_compute_total_returns_aggregate_dict(test_project_id):
    from services import capex
    capex.seed_from_template(test_project_id, "cil_conventional", force=True)
    out = capex.compute_total(test_project_id)
    assert "direct_cad" in out and "total_cad" in out
    assert out["total_cad"] > out["direct_cad"]  # factors add up


def test_recompute_for_project_skips_overridden(test_project_id):
    """Overridden rows keep their manual price; un-overridden recompute from tph."""
    from services import capex
    from db import qone, execute

    capex.seed_from_template(test_project_id, "cil_conventional", force=True)
    execute(
        "UPDATE equipment_v2 SET price_cad=12345678, is_overridden=true "
        "WHERE project_id=%s AND template_key=%s",
        (test_project_id, "ball_mill_main"),
    )
    capex.recompute_for_project(test_project_id)
    locked = qone(
        "SELECT price_cad FROM equipment_v2 WHERE project_id=%s AND template_key=%s",
        (test_project_id, "ball_mill_main"),
    )
    assert float(locked["price_cad"]) == 12345678  # untouched


def test_seed_unknown_circuit_raises(test_project_id):
    from services import capex
    with pytest.raises(ValueError, match="Unknown circuit template"):
        capex.seed_from_template(test_project_id, "not_a_circuit", force=True)
