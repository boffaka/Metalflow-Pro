# backend/tests/test_topology_analyzer.py
import pytest
from engines.topology_analyzer import TopologyAnalyzer, FlowsheetGraph, GraphNode, GraphEdge

pytestmark = pytest.mark.no_db


def _make_linear_graph():
    """FEED → SAG → BALL → CIL → TSF (DAG, pas de boucle)"""
    nodes = [
        GraphNode("n1", "FEED"), GraphNode("n2", "SAG_MILL"),
        GraphNode("n3", "BALL_MILL"), GraphNode("n4", "CIL_TANK"),
        GraphNode("n5", "TSF"),
    ]
    edges = [
        GraphEdge("e1", "n1", "n2"), GraphEdge("e2", "n2", "n3"),
        GraphEdge("e3", "n3", "n4"), GraphEdge("e4", "n4", "n5"),
    ]
    return FlowsheetGraph(nodes=nodes, edges=edges)


def _make_loop_graph():
    """FEED → SAG → CYCLONE → (overflow→CIL, underflow→SAG) cycle sur SAG"""
    nodes = [
        GraphNode("n1", "FEED"), GraphNode("n2", "SAG_MILL"),
        GraphNode("n3", "CYCLONE"), GraphNode("n4", "CIL_TANK"),
    ]
    edges = [
        GraphEdge("e1", "n1", "n2"),
        GraphEdge("e2", "n2", "n3"),
        GraphEdge("e3", "n3", "n4", port_source="overflow"),
        GraphEdge("e4", "n3", "n2", port_source="underflow"),  # boucle
    ]
    return FlowsheetGraph(nodes=nodes, edges=edges)


def test_linear_graph_no_loops():
    g = _make_linear_graph()
    result = TopologyAnalyzer(g).analyze()
    assert not result.has_loops
    assert result.tear_streams == []


def test_linear_graph_execution_order():
    g = _make_linear_graph()
    result = TopologyAnalyzer(g).analyze()
    ids = [n.id for n in result.execution_order]
    assert ids.index("n1") < ids.index("n2") < ids.index("n3")


def test_loop_graph_has_loops():
    g = _make_loop_graph()
    result = TopologyAnalyzer(g).analyze()
    assert result.has_loops


def test_loop_graph_tear_stream_identified():
    g = _make_loop_graph()
    result = TopologyAnalyzer(g).analyze()
    assert len(result.tear_streams) >= 1
    tear_ids = [e.id for e in result.tear_streams]
    assert "e4" in tear_ids


def test_empty_graph():
    g = FlowsheetGraph(nodes=[], edges=[])
    result = TopologyAnalyzer(g).analyze()
    assert not result.has_loops
    assert result.execution_order == []
