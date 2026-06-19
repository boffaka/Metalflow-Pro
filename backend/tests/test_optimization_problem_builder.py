import pytest

from engines.optimization_problem_builder import build_optimization_problem
from engines.topology_analyzer import FlowsheetGraph, GraphEdge, GraphNode

pytestmark = pytest.mark.no_db


def test_builder_derives_node_scoped_variables():
    graph = FlowsheetGraph(
        nodes=[GraphNode("f", "FEED"), GraphNode("c", "CIL_TANK"), GraphNode("t", "TSF")],
        edges=[GraphEdge("e1", "f", "c"), GraphEdge("e2", "c", "t")],
    )
    problem = build_optimization_problem(graph)
    assert any(v["node_id"] == "c" and v["parameter"] == "srt_h" for v in problem["variables"])


def test_builder_includes_default_objectives_and_constraints():
    problem = build_optimization_problem(FlowsheetGraph(nodes=[GraphNode("f", "FEED")], edges=[]))
    assert {"metric": "global_results.overall_recovery", "direction": "max"} in problem["objectives"]
    assert any(c["metric"] == "global_results.cn_in_tailings_ppm" for c in problem["constraints"])


def test_builder_reports_validation_errors():
    problem = build_optimization_problem(FlowsheetGraph(nodes=[GraphNode("x", "UNKNOWN")], edges=[]))
    assert not problem["valid"]
    assert any(e["code"] == "UNKNOWN_OP" for e in problem["diagnostics"]["errors"])
