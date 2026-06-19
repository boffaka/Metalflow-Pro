# backend/tests/test_flowsheet_graph_engine.py
import pytest
from engines.stream_state import StreamState
from engines.topology_analyzer import FlowsheetGraph, GraphNode, GraphEdge
from engines.unit_op_dispatcher import ProjectContext
from engines.flowsheet_graph_engine import FlowsheetGraphEngine, SimResult

pytestmark = pytest.mark.no_db

CTX = ProjectContext(target_tph=1517, gold_price_usd=2000)


def _linear_graph():
    nodes = [
        GraphNode("n1", "FEED", params={"feed_tph": 1517, "au_g_t": 1.5, "p80_um": 150_000}),
        GraphNode("n2", "SAG_MILL", params={"wi": 14, "p80_um": 2000}),
        GraphNode("n3", "CIL_TANK", params={"srt_h": 24, "r_inf": 0.93}),
        GraphNode("n4", "TSF"),
    ]
    edges = [
        GraphEdge("e1", "n1", "n2"),
        GraphEdge("e2", "n2", "n3"),
        GraphEdge("e3", "n3", "n4"),
    ]
    return FlowsheetGraph(nodes=nodes, edges=edges)


def test_linear_run_returns_sim_result():
    eng = FlowsheetGraphEngine()
    result = eng.run(_linear_graph(), CTX)
    assert isinstance(result, SimResult)
    assert result.converged


def test_linear_run_has_stream_results():
    eng = FlowsheetGraphEngine()
    result = eng.run(_linear_graph(), CTX)
    assert "e1" in result.stream_results
    assert "e2" in result.stream_results


def test_linear_run_kpis():
    eng = FlowsheetGraphEngine()
    result = eng.run(_linear_graph(), CTX)
    assert "total_recovery_pct" in result.kpis
    assert result.kpis["total_recovery_pct"] > 0


def test_empty_graph_returns_empty_result():
    eng = FlowsheetGraphEngine()
    result = eng.run(FlowsheetGraph(nodes=[], edges=[]), CTX)
    assert result.stream_results == {}
