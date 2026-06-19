"""Recirculation segment detection for process simulator."""
import pytest

pytestmark = pytest.mark.no_db


def _ops(*codes):
    return [{"op_code": c, "label": c} for c in codes]


def test_detect_ball_cyclone_segment():
    from engines.recirculation_solver import detect_recirculation_segments

    ops = _ops("GIRATOIRE", "BALL_MILL", "HYDROCYCLONE", "FLOTATION_ROUGHER")
    segs = detect_recirculation_segments(ops)
    assert len(segs) == 1
    assert segs[0]["type"] == "mill_classifier"
    assert segs[0]["op_codes"] == ["BALL_MILL", "HYDROCYCLONE"]


def test_detect_sag_ball_cyclone_segment():
    from engines.recirculation_solver import detect_recirculation_segments

    ops = _ops("SAG_MILL", "BALL_MILL", "HYDROCYCLONE", "CIL")
    segs = detect_recirculation_segments(ops)
    assert len(segs) == 1
    assert segs[0]["type"] == "sag_ball_cyclone"


def test_detect_flotation_bank():
    from engines.recirculation_solver import detect_recirculation_segments

    ops = _ops("BALL_MILL", "FLOTATION_ROUGHER", "FLOTATION_SCAVENGER", "CIL")
    segs = detect_recirculation_segments(ops)
    types = [s["type"] for s in segs]
    assert "flotation_bank" in types


def test_find_graph_cycle_simple():
    from engines.recirculation_solver import find_graph_cycles

    blocks = [
        {"id": "a", "op_code": "BALL_MILL"},
        {"id": "b", "op_code": "HYDROCYCLONE"},
    ]
    connections = [
        {"from": "a", "to": "b"},
        {"from": "b", "to": "a"},
    ]
    cycles = find_graph_cycles(blocks, connections)
    assert len(cycles) >= 1
