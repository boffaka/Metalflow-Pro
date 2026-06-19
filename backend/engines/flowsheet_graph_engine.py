# backend/engines/flowsheet_graph_engine.py
"""
FlowsheetGraphEngine — orchestrateur de simulation sur graphe orienté.

Délègue à:
  - TopologyAnalyzer : ordre d'exécution + détection boucles
  - UnitOpDispatcher : calcul par nœud
  - TearStreamSolver : convergence des boucles (si has_loops)
"""
from __future__ import annotations
import hashlib
import json
import logging
from dataclasses import asdict, dataclass

from .stream_state import StreamState
from .topology_analyzer import FlowsheetGraph, TopologyAnalyzer
from .unit_op_dispatcher import UnitOpDispatcher, ProjectContext
from .tear_stream_solver import TearStreamSolver
from .flowsheet_validator import validate_flowsheet
from .unit_registry import REGISTRY_VERSION

logger = logging.getLogger("mpdpms.flowsheet_graph_engine")


@dataclass
class SimResult:
    converged: bool
    iterations: int
    stream_results: dict[str, StreamState]  # edge_id → StreamState
    kpis: dict
    final_residual: float = 0.0


class FlowsheetGraphEngine:

    def __init__(self):
        self._dispatcher = UnitOpDispatcher()
        self._solver = TearStreamSolver(max_iterations=50, tolerance=1e-4)

    def run(
        self,
        graph: FlowsheetGraph,
        ctx: ProjectContext,
        params_override: dict | None = None,
    ) -> SimResult:
        validation = validate_flowsheet(graph)
        audit = self._build_audit(graph)
        if not validation.valid:
            return SimResult(
                converged=False,
                iterations=0,
                stream_results={},
                kpis={
                    "diagnostics": {
                        "errors": [e.to_dict() for e in validation.errors],
                        "warnings": [w.to_dict() for w in validation.warnings],
                        "suggestions": validation.suggestions,
                    },
                    "audit": audit,
                    "node_results": [],
                },
            )

        if not graph.nodes:
            return SimResult(converged=True, iterations=0,
                             stream_results={}, kpis={"audit": audit, "node_results": []})

        topology = TopologyAnalyzer(graph).analyze()

        if not topology.has_loops:
            stream_results = self._sequential_pass(graph, topology.execution_order, ctx,
                                                   params_override)
            return SimResult(converged=True, iterations=1,
                             stream_results=stream_results,
                             kpis=self._compute_kpis(stream_results, graph, ctx, validation, audit))

        tear_ids = {e.id for e in topology.tear_streams}
        {e.id: e for e in graph.edges}

        feed_node = next((n for n in topology.execution_order if n.op_code == "FEED"), None)
        feed_tph = float((feed_node.params if feed_node else {}).get("feed_tph") or ctx.target_tph)
        init_tear = {
            eid: StreamState(solids_tph=feed_tph, water_tph=feed_tph * 1.5,
                             au_g_t=1.5, au_recovery_pct=100, p80_um=2000, energy_kwh_t=0)
            for eid in tear_ids
        }

        def update_fn(tear_streams):
            results = self._sequential_pass(
                graph, topology.execution_order, ctx,
                params_override, tear_overrides=tear_streams,
            )
            return {eid: results[eid] for eid in tear_ids if eid in results}

        conv = self._solver.solve(init_tear, update_fn)
        final_results = self._sequential_pass(
            graph, topology.execution_order, ctx,
            params_override, tear_overrides=conv.final_streams,
        )
        return SimResult(
            converged=conv.converged, iterations=conv.iterations,
            stream_results=final_results,
            kpis=self._compute_kpis(final_results, graph, ctx, validation, audit),
            final_residual=conv.final_residual,
        )

    def _sequential_pass(
        self,
        graph: FlowsheetGraph,
        execution_order,
        ctx: ProjectContext,
        params_override: dict | None,
        tear_overrides: dict[str, StreamState] | None = None,
    ) -> dict[str, StreamState]:
        node_outputs: dict[str, dict[str, StreamState]] = {}
        stream_results: dict[str, StreamState] = {}

        edge_by_target: dict[str, list] = {}
        for e in graph.edges:
            edge_by_target.setdefault(e.target_node, []).append(e)

        for node in execution_order:
            inlets: dict[str, StreamState] = {}
            for edge in edge_by_target.get(node.id, []):
                if tear_overrides and edge.id in tear_overrides:
                    inlets[edge.port_target] = tear_overrides[edge.id]
                elif edge.source_node in node_outputs:
                    src_port = edge.port_source
                    src_outs = node_outputs[edge.source_node]
                    inlets[edge.port_target] = src_outs.get(src_port, next(iter(src_outs.values())))

            params = dict(node.params)
            if params_override and node.id in params_override:
                params.update(params_override[node.id])

            if not inlets and node.op_code != "FEED":
                logger.debug("Nœud %s (%s) sans inlet — skipped", node.id, node.op_code)
                continue

            if not inlets:
                dummy = StreamState(solids_tph=0, water_tph=0, au_g_t=0,
                                    au_recovery_pct=0, p80_um=0, energy_kwh_t=0)
                inlets = {"in": dummy}

            try:
                outputs = self._dispatcher.dispatch(node.op_code, inlets, params, ctx)
            except Exception as exc:
                logger.warning("dispatch %s (%s) failed: %s", node.id, node.op_code, exc)
                outputs = {"out": next(iter(inlets.values()))}

            node_outputs[node.id] = outputs

            for edge in graph.edges:
                if edge.source_node == node.id:
                    port = edge.port_source
                    stream_results[edge.id] = outputs.get(port, next(iter(outputs.values()), None))

        return {k: v for k, v in stream_results.items() if v is not None}

    def _compute_kpis(
        self, stream_results: dict[str, StreamState],
        graph: FlowsheetGraph, ctx: ProjectContext,
        validation=None,
        audit: dict | None = None,
    ) -> dict:
        if not stream_results:
            return {
                "diagnostics": self._diagnostics_payload(validation),
                "audit": audit or self._build_audit(graph),
                "node_results": [],
            }

        # Récupération : prendre la valeur au_recovery_pct du dernier stream vers TSF
        # ou la valeur max parmi les streams de lixiviation
        tsf_node = next((n for n in graph.nodes if n.op_code == "TSF"), None)
        if tsf_node:
            edge_to_tsf = next(
                (e for e in graph.edges if e.target_node == tsf_node.id), None
            )
            if edge_to_tsf and edge_to_tsf.id in stream_results:
                total_rec = stream_results[edge_to_tsf.id].au_recovery_pct or 0
            else:
                # Fallback: max récupération parmi tous les streams sauf le feed
                recoveries = [s.au_recovery_pct for s in stream_results.values()
                              if s.au_recovery_pct is not None and s.p80_um < 10_000]
                total_rec = max(recoveries) if recoveries else 0
        else:
            recoveries = [s.au_recovery_pct for s in stream_results.values()
                          if s.au_recovery_pct is not None and s.p80_um < 10_000]
            total_rec = max(recoveries) if recoveries else 0

        total_energy = sum(
            s.energy_kwh_t for s in stream_results.values()
            if s.energy_kwh_t is not None
        )

        annual_hours = 8760 * ctx.availability
        tph = ctx.target_tph
        au_rec = total_rec / 100
        feed_streams = [s for s in stream_results.values() if s.p80_um and s.p80_um > 10_000]
        feed_grade = feed_streams[0].au_g_t if feed_streams else 1.5
        oz_per_year = tph * annual_hours * feed_grade * au_rec / 31.1035

        return {
            "total_recovery_pct": round(total_rec, 2),
            "energy_kwh_t": round(total_energy, 2),
            "annual_oz": round(oz_per_year),
            "diagnostics": self._diagnostics_payload(validation),
            "audit": audit or self._build_audit(graph),
            "node_results": self._node_results(stream_results, graph),
        }

    def _node_results(
        self,
        stream_results: dict[str, StreamState],
        graph: FlowsheetGraph,
    ) -> list[dict]:
        rows: list[dict] = []
        for node in graph.nodes:
            out_edges = [e for e in graph.edges if e.source_node == node.id and e.id in stream_results]
            in_edges = [e for e in graph.edges if e.target_node == node.id and e.id in stream_results]
            out_streams = [stream_results[e.id] for e in out_edges]
            in_streams = [stream_results[e.id] for e in in_edges]
            primary_out = out_streams[0] if out_streams else None
            primary_in = in_streams[0] if in_streams else None
            rows.append({
                "node_id": node.id,
                "op_code": node.op_code,
                "feed_rate_tph": round(primary_in.solids_tph, 4) if primary_in else 0.0,
                "product_rate_tph": round(sum(s.solids_tph for s in out_streams), 4),
                "recovery_pct": round(primary_out.au_recovery_pct, 4) if primary_out else 0.0,
                "energy_kwh_t": round(sum(s.energy_kwh_t for s in out_streams), 4),
                "utilization_rate": 0.0,
                "is_bottleneck": False,
                "kpis": {},
            })
        return rows

    def _diagnostics_payload(self, validation) -> dict:
        if validation is None:
            return {"errors": [], "warnings": [], "suggestions": []}
        return {
            "errors": [e.to_dict() for e in validation.errors],
            "warnings": [w.to_dict() for w in validation.warnings],
            "suggestions": validation.suggestions,
        }

    def _build_audit(self, graph: FlowsheetGraph) -> dict:
        graph_payload = {
            "nodes": [asdict(n) for n in graph.nodes],
            "edges": [asdict(e) for e in graph.edges],
        }
        canonical = json.dumps(graph_payload, sort_keys=True, separators=(",", ":"))
        return {
            "registry_version": REGISTRY_VERSION,
            "graph_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
        }
