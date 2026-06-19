"""Tests for engines.compile — end-to-end DB flow."""
import json
import uuid
import pytest

from db import qone, qall, execute
from engines.compile import compile_flowsheet, load_compilation_graph


def test_compile_creates_compilation_and_snapshot_template(seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=seeded_simple_project["flowsheet_id"])

    assert result["cached"] is False
    assert result["compilation_id"]
    assert result["template_id"]
    assert result["template_id"] != seeded_simple_project["template_id"]  # new snapshot

    # Check compilation row exists
    row = qone("SELECT blocks_hash, sections_resolved, topo_order FROM circuit_compilations WHERE id = %s",
               (result["compilation_id"],))
    assert row is not None
    assert len(row["blocks_hash"]) == 64  # SHA-256 hex

    # Check snapshot template exists and is inactive
    tpl = qone("SELECT name, is_active FROM circuit_templates WHERE id = %s", (result["template_id"],))
    assert tpl["name"].startswith("__snap_")
    assert tpl["is_active"] is False


def test_compile_is_idempotent(seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    r1 = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=seeded_simple_project["flowsheet_id"])
    r2 = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=seeded_simple_project["flowsheet_id"])

    assert r1["compilation_id"] == r2["compilation_id"]
    assert r1["template_id"] == r2["template_id"]
    assert r2["cached"] is True


def test_compile_copies_design_criteria_from_source_template(seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=seeded_simple_project["flowsheet_id"])

    dcs = qall(
        "SELECT op_code, ref_number, design_value FROM design_criteria_v2 WHERE template_id = %s",
        (result["template_id"],)
    )
    # At least the HPGR DC-001 row must have been copied
    hpgr_dcs = [d for d in dcs if d["op_code"] == "HPGR" and d["ref_number"] == "DC-001"]
    assert len(hpgr_dcs) == 1
    assert float(hpgr_dcs[0]["design_value"]) == 2.5


def test_compile_detects_topological_order(seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=seeded_simple_project["flowsheet_id"])
    # HPGR must come before BALL_MILL which must come before CIL
    order = result["topo_order"]
    assert order.index("HPGR") < order.index("BALL_MILL") < order.index("CIL")


def test_compile_defaults_to_active_flowsheet_when_source_id_null(seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=None)
    assert result["compilation_id"]


def test_compile_unknown_op_raises(seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    bad_blocks = [{"id": "b1", "op_code": "UNKNOWN_OP_XYZ", "enabled": True}]
    fs_id = str(uuid.uuid4())
    execute(
        "INSERT INTO flowsheets (id, project_id, blocks, connections) VALUES (%s, %s, %s::jsonb, '[]'::jsonb)",
        (fs_id, pid, json.dumps(bad_blocks))
    )
    with pytest.raises(ValueError, match="UNKNOWN_OP_XYZ"):
        compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=fs_id)


def test_compile_records_warnings_for_missing_feed(seeded_simple_project):
    pid = seeded_simple_project["project_id"]
    # Flowsheet without FEED block
    blocks = [{"id": "b1", "op_code": "BALL_MILL", "enabled": True}]
    fs_id = str(uuid.uuid4())
    execute(
        "INSERT INTO flowsheets (id, project_id, blocks, connections) VALUES (%s, %s, %s::jsonb, '[]'::jsonb)",
        (fs_id, pid, json.dumps(blocks))
    )
    result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=fs_id)
    codes = [w["code"] for w in result["warnings"]]
    assert "NO_FEED" in codes


def test_compile_emits_warning_on_cycle(seeded_simple_project):
    """A cyclic flowsheet should emit CYCLE_DETECTED warning."""
    pid = seeded_simple_project["project_id"]
    blocks = [
        {"id": "b1", "op_code": "HPGR", "enabled": True},
        {"id": "b2", "op_code": "BALL_MILL", "enabled": True},
    ]
    # Cycle b1 → b2 → b1
    connections = [{"from": "b1", "to": "b2"}, {"from": "b2", "to": "b1"}]
    fs_id = str(uuid.uuid4())
    execute(
        "INSERT INTO flowsheets (id, project_id, blocks, connections) VALUES (%s, %s, %s::jsonb, %s::jsonb)",
        (fs_id, pid, json.dumps(blocks), json.dumps(connections))
    )
    result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=fs_id)
    codes = [w["code"] for w in result["warnings"]]
    assert "CYCLE_DETECTED" in codes


def test_compile_reads_op_code_from_block_type_field(seeded_simple_project):
    """Flowsheet designer stores op in `type`; compile must accept it."""
    pid = seeded_simple_project["project_id"]
    blocks = [
        {"id": "b1", "type": "BALL_MILL", "enabled": True},
        {"id": "b2", "type": "CIL", "enabled": True},
    ]
    fs_id = str(uuid.uuid4())
    execute(
        "INSERT INTO flowsheets (id, project_id, blocks, connections) VALUES (%s, %s, %s::jsonb, %s::jsonb)",
        (fs_id, pid, json.dumps(blocks), json.dumps([{"from": "b1", "to": "b2"}])),
    )
    result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=fs_id)
    assert "BALL_MILL" in result["topo_order"]
    assert "CIL" in result["topo_order"]


def test_load_compilation_graph_reads_flowsheet_not_missing_columns(seeded_simple_project):
    """Regression: graph loader must not query blocks/compiled_at on circuit_compilations."""
    pid = seeded_simple_project["project_id"]
    result = compile_flowsheet(
        project_id=pid,
        source_type="flowsheet",
        source_id=seeded_simple_project["flowsheet_id"],
    )
    blocks, connections = load_compilation_graph(pid, result["template_id"])
    assert len(blocks) >= 1
    assert any(b.get("op_code") == "HPGR" for b in blocks)
