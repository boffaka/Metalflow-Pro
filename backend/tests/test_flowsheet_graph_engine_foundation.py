import pytest

from engines.flowsheet_graph_engine import FlowsheetGraphEngine
from engines.topology_analyzer import FlowsheetGraph, GraphEdge, GraphNode
from engines.unit_op_dispatcher import ProjectContext

pytestmark = pytest.mark.no_db


def test_engine_returns_validation_diagnostics_for_invalid_graph():
    graph = FlowsheetGraph(nodes=[GraphNode("n1", "UNKNOWN")], edges=[])
    result = FlowsheetGraphEngine().run(graph, ProjectContext(1000, 2000))
    assert not result.converged
    codes = {e["code"] for e in result.kpis["diagnostics"]["errors"]}
    assert "UNKNOWN_OP" in codes
    assert "MISSING_FEED" in codes


def test_engine_audit_includes_registry_version():
    graph = FlowsheetGraph(
        nodes=[
            GraphNode("f", "FEED", params={"feed_tph": 100, "au_g_t": 1.2}),
            GraphNode("t", "TSF"),
        ],
        edges=[GraphEdge("e1", "f", "t")],
    )
    result = FlowsheetGraphEngine().run(graph, ProjectContext(100, 2000))
    assert result.converged
    assert result.kpis["audit"]["registry_version"]
    assert result.kpis["audit"]["graph_hash"]


def test_engine_reports_node_results_for_valid_graph():
    graph = FlowsheetGraph(
        nodes=[
            GraphNode("f", "FEED", params={"feed_tph": 100, "au_g_t": 1.2}),
            GraphNode("m", "BALL_MILL", params={"p80_um": 75}),
            GraphNode("t", "TSF"),
        ],
        edges=[GraphEdge("e1", "f", "m"), GraphEdge("e2", "m", "t")],
    )
    result = FlowsheetGraphEngine().run(graph, ProjectContext(100, 2000))
    assert "node_results" in result.kpis
    assert any(n["node_id"] == "m" for n in result.kpis["node_results"])
