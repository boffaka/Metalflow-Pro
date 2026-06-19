# backend/tests/test_flowsheet_v4_integration.py
"""Tests d'intégration bout-en-bout sans DB — pile complète FlowsheetGraphEngine v4."""
import pytest
from engines.stream_state import StreamState
from engines.topology_analyzer import FlowsheetGraph, GraphNode, GraphEdge
from engines.unit_op_dispatcher import ProjectContext
from engines.flowsheet_graph_engine import FlowsheetGraphEngine

pytestmark = pytest.mark.no_db

CTX = ProjectContext(target_tph=1517, gold_price_usd=2000)


def _gold_cil_oxide_graph():
    """Circuit CIL oxyde : FEED→SAG→BALL→CIL→ELUTION→ELECTROLYSE→FUSION→TSF"""
    nodes = [
        GraphNode("n1", "FEED", params={"feed_tph": 1517, "au_g_t": 1.5, "p80_um": 150_000}),
        GraphNode("n2", "SAG_MILL", params={"wi": 14, "p80_um": 2000}),
        GraphNode("n3", "BALL_MILL", params={"wi": 12, "p80_um": 75}),
        GraphNode("n4", "CIL_TANK", params={"srt_h": 24, "r_inf": 0.94}),
        GraphNode("n5", "ELUTION_AARL", params={"recovery_pct": 99}),
        GraphNode("n6", "ELECTROLYSE", params={}),
        GraphNode("n7", "FUSION_DORE", params={}),
        GraphNode("n8", "TSF"),
    ]
    edges = [
        GraphEdge("e1", "n1", "n2"), GraphEdge("e2", "n2", "n3"),
        GraphEdge("e3", "n3", "n4"), GraphEdge("e4", "n4", "n5"),
        GraphEdge("e5", "n5", "n6", port_source="eluate"),
        GraphEdge("e6", "n6", "n7", port_source="sludge"),
        GraphEdge("e7", "n7", "n8", port_source="bullion"),
    ]
    return FlowsheetGraph(nodes=nodes, edges=edges)


def _gravity_flotation_graph():
    """Circuit gravité + flottation : FEED→SAG→KNELSON+FLOT→CIL→TSF"""
    nodes = [
        GraphNode("n1", "FEED", params={"feed_tph": 500, "au_g_t": 3.0, "p80_um": 150_000}),
        GraphNode("n2", "SAG_MILL", params={"wi": 13, "p80_um": 3000}),
        GraphNode("n3", "GRAVITE_KNELSON", params={"recovery_pct": 38, "mass_pull_pct": 2.5}),
        GraphNode("n4", "FLOTATION_ROUGHER", params={"r_max": 0.88, "k": 0.45, "tau_min": 10}),
        GraphNode("n5", "CIL_TANK", params={"srt_h": 20, "r_inf": 0.91}),
        GraphNode("n6", "TSF"),
    ]
    edges = [
        GraphEdge("e1", "n1", "n2"), GraphEdge("e2", "n2", "n3"),
        GraphEdge("e3", "n3", "n4", port_source="tails"),
        GraphEdge("e4", "n4", "n5", port_source="conc"),
        GraphEdge("e5", "n5", "n6"),
    ]
    return FlowsheetGraph(nodes=nodes, edges=edges)


def test_gold_cil_oxide_circuit_runs():
    eng = FlowsheetGraphEngine()
    result = eng.run(_gold_cil_oxide_graph(), CTX)
    assert result.converged
    assert result.kpis.get("total_recovery_pct", 0) > 0
    assert result.kpis.get("annual_oz", 0) > 0


def test_all_edges_have_stream_results():
    eng = FlowsheetGraphEngine()
    graph = _gold_cil_oxide_graph()
    result = eng.run(graph, CTX)
    # All non-TSF edges should have results
    non_tsf_edges = [e for e in graph.edges if e.target_node != "n8" or True]
    for edge in non_tsf_edges:
        if edge.id not in result.stream_results:
            pytest.fail(f"Edge {edge.id} ({edge.source_node}→{edge.target_node}) manquant")


def test_gravity_flotation_circuit_runs():
    eng = FlowsheetGraphEngine()
    result = eng.run(_gravity_flotation_graph(), CTX)
    assert result.converged


def test_engine_reusable():
    eng = FlowsheetGraphEngine()
    r1 = eng.run(_gold_cil_oxide_graph(), CTX)
    r2 = eng.run(_gold_cil_oxide_graph(), CTX)
    assert r1.kpis == r2.kpis


def test_params_override():
    eng = FlowsheetGraphEngine()
    # Override CIL recovery
    override = {"n4": {"srt_h": 36, "r_inf": 0.97}}
    result = eng.run(_gold_cil_oxide_graph(), CTX, params_override=override)
    assert result.converged
