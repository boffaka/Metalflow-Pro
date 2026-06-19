# backend/engines/topology_analyzer.py
"""
TopologyAnalyzer — détecte les boucles (DAG vs cycles), identifie les tear
streams optimaux et calcule l'ordre d'exécution séquentiel.

Utilise networkx (déjà dans les dépendances backend).
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field

import networkx as nx

logger = logging.getLogger("mpdpms.topology_analyzer")


@dataclass
class GraphNode:
    id: str
    op_code: str
    params: dict = field(default_factory=dict)
    position_x: float = 0
    position_y: float = 0


@dataclass
class GraphEdge:
    id: str
    source_node: str
    target_node: str
    port_source: str = "out"
    port_target: str = "in"
    is_tear_stream: bool = False
    stream_label: str = ""


@dataclass
class FlowsheetGraph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]


@dataclass
class TopologyResult:
    has_loops: bool
    tear_streams: list[GraphEdge]
    execution_order: list[GraphNode]
    loops_detected: list[str]  # description textuelle des cycles


class TopologyAnalyzer:

    def __init__(self, graph: FlowsheetGraph):
        self._graph = graph
        self._node_map = {n.id: n for n in graph.nodes}
        self._edge_map = {e.id: e for e in graph.edges}

    def analyze(self) -> TopologyResult:
        if not self._graph.nodes:
            return TopologyResult(has_loops=False, tear_streams=[],
                                  execution_order=[], loops_detected=[])

        G = self._build_nx()
        cycles = list(nx.simple_cycles(G))

        if not cycles:
            order = self._topological_order(G)
            return TopologyResult(has_loops=False, tear_streams=[],
                                  execution_order=order, loops_detected=[])

        tear_edges = self._select_tear_streams(G, cycles)
        G_dag = G.copy()
        for e in tear_edges:
            if G_dag.has_edge(e.source_node, e.target_node):
                G_dag.remove_edge(e.source_node, e.target_node)

        order = self._topological_order(G_dag)
        loop_descriptions = [
            "→".join(c + [c[0]]) for c in cycles[:5]
        ]
        return TopologyResult(has_loops=True, tear_streams=tear_edges,
                              execution_order=order,
                              loops_detected=loop_descriptions)

    def _build_nx(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_nodes_from(n.id for n in self._graph.nodes)
        for e in self._graph.edges:
            G.add_edge(e.source_node, e.target_node, edge_id=e.id)
        return G

    def _topological_order(self, G: nx.DiGraph) -> list[GraphNode]:
        try:
            order = list(nx.topological_sort(G))
        except nx.NetworkXUnfeasible:
            order = list(G.nodes)
        return [self._node_map[nid] for nid in order if nid in self._node_map]

    def _select_tear_streams(self, G: nx.DiGraph, cycles: list) -> list[GraphEdge]:
        """Sélectionne les tear streams qui brisent tous les cycles (greedy).

        Tiebreaker: préfère les back-edges (arêtes hors de l'arbre DFS),
        ce qui correspond aux flux de recirculation naturels.
        """
        edge_by_pair: dict[tuple, GraphEdge] = {}
        for e in self._graph.edges:
            edge_by_pair[(e.source_node, e.target_node)] = e

        # Identifier les back-edges via l'arbre DFS
        try:
            tree_edges = set(nx.dfs_tree(G).edges())
        except Exception:
            tree_edges = set()
        back_edges = set(G.edges()) - tree_edges

        remaining_cycles = [set(zip(c, c[1:] + [c[0]])) for c in cycles]
        selected: list[GraphEdge] = []

        while any(remaining_cycles):
            # compter combien de cycles chaque arête couvre
            counter: dict[tuple, int] = {}
            for cycle_pairs in remaining_cycles:
                for pair in cycle_pairs:
                    counter[pair] = counter.get(pair, 0) + 1
            if not counter:
                break
            max_count = max(counter.values())
            # parmi les arêtes avec le même score, préférer les back-edges
            candidates = [p for p, cnt in counter.items() if cnt == max_count]
            back_candidates = [p for p in candidates if p in back_edges]
            best_pair = back_candidates[0] if back_candidates else candidates[0]
            edge = edge_by_pair.get(best_pair)
            if edge:
                selected.append(edge)
            remaining_cycles = [c for c in remaining_cycles if best_pair not in c]

        return selected
