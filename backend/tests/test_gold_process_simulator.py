"""Tests for dynamic gold process simulator."""
import pytest


@pytest.mark.no_db
def test_classify_operations_cil_route():
    from engines.gold_process_simulator import classify_operations

    ops = ["GIRATOIRE", "SAG_MILL", "BALL_MILL", "FLOTATION_ROUGHER", "CIL", "FEED", "PRODUCT"]
    out = classify_operations(ops)
    assert out["can_run_rigorous"] is True
    assert out["coverage_pct"] >= 50
    assert "CIL" in [m["op_code"] for m in out["modeled"]]
    assert "FLOTATION_ROUGHER" in [m["op_code"] for m in out["modeled"]]


@pytest.mark.no_db
def test_classify_heap_leach_route():
    from engines.gold_process_simulator import classify_operations

    ops = ["GIRATOIRE", "HEAP_LEACH", "CIP", "FEED", "PRODUCT"]
    out = classify_operations(ops)
    assert out["can_run_rigorous"] is True
    modeled_codes = {m["op_code"] for m in out["modeled"]}
    assert "HEAP_LEACH" in modeled_codes


@pytest.mark.no_db
def test_route_family_heap():
    from engines.metallurgical_levers import _detect_flowsheet_family

    assert _detect_flowsheet_family({"HEAP_LEACH", "GIRATOIRE"}) == "heap_leach"


@pytest.mark.no_db
def test_list_gold_presets_has_families():
    from engines.gold_process_simulator import list_gold_presets

    grouped = list_gold_presets()
    assert len(grouped) >= 6
    total = sum(len(v) for v in grouped.values())
    assert total >= 40


@pytest.mark.no_db
def test_resolve_prefers_active_source(monkeypatch):
    from engines import gold_process_simulator as gps

    compiled = {
        "template_id": "tpl-compiled",
        "compilation_id": "comp-1",
        "topo_order": ["FEED", "HPGR", "BALL_MILL", "CIL", "PRODUCT"],
        "branches_detected": [],
        "warnings": [],
        "blocks_hash": "abc",
        "cached": True,
    }

    monkeypatch.setattr(gps, "_active_source", lambda pid: {
        "source_type": "flowsheet",
        "source_id": "fs-1",
    })
    import engines.compile as compile_mod
    monkeypatch.setattr(compile_mod, "compile_flowsheet", lambda **kw: compiled)

    out = gps.resolve_simulation_ops("pid", compile_if_needed=True)
    assert out["template_id"] == "tpl-compiled"
    assert "CIL" in out["op_codes"]
    assert out["source"]["kind"] == "compiled_flowsheet"


@pytest.mark.no_db
def test_graph_loops_for_template_without_db_does_not_query_invalid_columns(monkeypatch):
    """_graph_loops_for_template must not SELECT blocks/compiled_at from circuit_compilations."""
    from engines import gold_process_simulator as gps

    def _boom(*_a, **_kw):
        raise AssertionError("load_compilation_graph should not run when template_id is None")

    monkeypatch.setattr(
        "engines.gold_process_simulator.load_compilation_graph",
        _boom,
        raising=False,
    )
    loops, linear, entries = gps._graph_loops_for_template(None, "pid", ["BALL_MILL", "CIL"])
    assert loops == []
    assert linear == []
    assert entries == []
