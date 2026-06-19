"""Generic loop solver from compiled flowsheet connections."""
import pytest

pytestmark = pytest.mark.no_db


def _ops(*codes):
    return [{"op_code": c, "label": c} for c in codes]


def _ball_cyclone_graph():
    blocks = [
        {"id": "mill", "op_code": "BALL_MILL"},
        {"id": "cy", "op_code": "HYDROCYCLONE"},
        {"id": "flot", "op_code": "FLOTATION_ROUGHER"},
    ]
    connections = [
        {"from": "mill", "to": "cy"},
        {"from": "cy", "to": "flot"},
        {"from": "cy", "to": "mill"},
    ]
    return blocks, connections


def test_find_simple_cycle_ball_cyclone():
    from engines.generic_loop_solver import (
        build_block_graph,
        find_simple_cycles,
    )

    blocks, connections = _ball_cyclone_graph()
    block_by_id, out_adj, _ = build_block_graph(blocks, connections)
    cycles = find_simple_cycles(block_by_id, out_adj)
    assert len(cycles) >= 1
    for cyc in cycles:
        assert "mill" in cyc and "cy" in cyc


def test_detect_graph_loop_mill_classifier():
    from engines.generic_loop_solver import detect_graph_recirculation_loops

    blocks, connections = _ball_cyclone_graph()
    operations = _ops("GIRATOIRE", "BALL_MILL", "HYDROCYCLONE", "FLOTATION_ROUGHER")
    plans = detect_graph_recirculation_loops(blocks, connections, operations)
    assert len(plans) >= 1
    plan = plans[0]
    assert plan["type"] in ("mill_classifier", "graph_cycle")
    assert "BALL_MILL" in plan["op_codes"]
    assert "HYDROCYCLONE" in plan["op_codes"]
    assert plan.get("recirc_edge")
    assert len(plan["op_indices"]) >= 2


def test_merge_graph_over_sequence_overlap():
    from engines.generic_loop_solver import merge_recirculation_plans
    from engines.recirculation_solver import detect_recirculation_segments

    operations = _ops("BALL_MILL", "HYDROCYCLONE", "FLOTATION_ROUGHER")
    seq = detect_recirculation_segments(operations)
    graph = [
        {
            "type": "mill_classifier",
            "source": "flowsheet_graph",
            "start": 0,
            "end": 2,
            "entry_index": 0,
            "op_indices": [0, 1],
            "op_codes": ["BALL_MILL", "HYDROCYCLONE"],
            "models": ["ball_milling", "classification"],
        }
    ]
    linear, loop_by_entry = merge_recirculation_plans(seq, graph)
    assert 0 in loop_by_entry
    assert loop_by_entry[0]["source"] == "flowsheet_graph"
    linear_starts = [s["start"] for s in linear]
    assert 0 not in linear_starts or all(
        set(range(s["start"], s["end"])) & {0, 1} == set()
        for s in linear
        if s["start"] == 0
    )


def test_sag_ball_cyclone_reclassified():
    from engines.generic_loop_solver import detect_graph_recirculation_loops

    blocks = [
        {"id": "sag", "op_code": "SAG_MILL"},
        {"id": "bm", "op_code": "BALL_MILL"},
        {"id": "cy", "op_code": "HYDROCYCLONE"},
    ]
    connections = [
        {"from": "sag", "to": "bm"},
        {"from": "bm", "to": "cy"},
        {"from": "cy", "to": "sag"},
    ]
    operations = _ops("SAG_MILL", "BALL_MILL", "HYDROCYCLONE", "CIL")
    plans = detect_graph_recirculation_loops(blocks, connections, operations)
    assert plans
    assert plans[0]["type"] == "sag_ball_cyclone"
