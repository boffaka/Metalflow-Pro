import pytest

from engines.flowsheet_validator import validate_flowsheet
from engines.topology_analyzer import FlowsheetGraph, GraphEdge, GraphNode

pytestmark = pytest.mark.no_db


def test_validator_rejects_graph_without_feed():
    graph = FlowsheetGraph(nodes=[GraphNode("n1", "CIL_TANK")], edges=[])
    result = validate_flowsheet(graph)
    assert not result.valid
    assert any(e.code == "MISSING_FEED" for e in result.errors)


def test_validator_rejects_invalid_port():
    graph = FlowsheetGraph(
        nodes=[GraphNode("f", "FEED"), GraphNode("c", "CYCLONE")],
        edges=[GraphEdge("e1", "f", "c", port_source="bad", port_target="in")],
    )
    result = validate_flowsheet(graph)
    assert not result.valid
    assert any(e.code == "INVALID_SOURCE_PORT" for e in result.errors)


def test_validator_rejects_out_of_range_parameter():
    graph = FlowsheetGraph(
        nodes=[GraphNode("f", "FEED", params={"feed_tph": -1}), GraphNode("t", "TSF")],
        edges=[GraphEdge("e1", "f", "t")],
    )
    result = validate_flowsheet(graph)
    assert not result.valid
    assert any(e.code == "PARAM_OUT_OF_RANGE" for e in result.errors)


def test_validator_accepts_linear_gold_flowsheet():
    graph = FlowsheetGraph(
        nodes=[
            GraphNode("f", "FEED", params={"feed_tph": 1000, "au_g_t": 1.4}),
            GraphNode("m", "BALL_MILL", params={"p80_um": 75}),
            GraphNode("c", "CIL_TANK", params={"srt_h": 24}),
            GraphNode("t", "TSF"),
        ],
        edges=[
            GraphEdge("e1", "f", "m"),
            GraphEdge("e2", "m", "c"),
            GraphEdge("e3", "c", "t"),
        ],
    )
    result = validate_flowsheet(graph)
    assert result.valid
    assert result.errors == []
    assert result.topology["execution_order"] == ["f", "m", "c", "t"]
