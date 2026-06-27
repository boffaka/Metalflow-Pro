# backend/routes/sim_module.py
"""
Sim Module v2 — REST API endpoints for the Simulation & Optimization module.

Sections:
  5.1  Flowsheet CRUD       GET/POST/PUT/DELETE/CLONE  /api/v2/projects/{pid}/flowsheets
  5.2  Nodes & Edges        POST/PUT/DELETE/GET         /api/v2/flowsheets/{gid}/nodes|edges|validate
  5.3  Simulation           POST/GET                    /api/v2/flowsheets/{gid}/simulate
  5.4  Optimization         POST/GET                    /api/v2/flowsheets/{gid}/optimize
  5.5  Capacity Analysis    POST/GET/PUT                /api/v2/flowsheets/{gid}/bottleneck-analysis|expansion-scenarios
  5.6  Unit Library         GET                         /api/v2/unit-library
"""

from __future__ import annotations

import json
import logging
import random
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

try:
    from ..auth import current_user, project_user
    from ..db import execute, qall, qone
    from .. import config as cfg
except ImportError:
    from auth import current_user, project_user
    from db import execute, qall, qone
    import config as cfg

try:
    from ..engines.flowsheet_graph_engine import FlowsheetGraphEngine
    from ..engines.sim_unit_library import UNIT_REGISTRY, SimStream, calculate_unit
    from ..engines.stream_provenance import stream_source_basis
    from ..engines.topology_analyzer import FlowsheetGraph, GraphEdge, GraphNode, TopologyAnalyzer
    from ..engines.unit_op_dispatcher import ProjectContext
except ImportError:
    from engines.flowsheet_graph_engine import FlowsheetGraphEngine
    from engines.sim_unit_library import UNIT_REGISTRY, SimStream, calculate_unit
    from engines.stream_provenance import stream_source_basis
    from engines.topology_analyzer import FlowsheetGraph, GraphEdge, GraphNode, TopologyAnalyzer
    from engines.unit_op_dispatcher import ProjectContext

logger = logging.getLogger("mpdpms.sim_module")
router = APIRouter(tags=["sim-module-v2"])

_engine = FlowsheetGraphEngine()


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


def _new_id() -> str:
    return str(_uuid.uuid4())


def _load_graph(gid: str) -> FlowsheetGraph:
    """Load a FlowsheetGraph from the database."""
    nodes_rows = qall(
        "SELECT id, op_code, label, params, position_x, position_y, "
        "design_capacity_tph, availability_pct "
        "FROM fg_nodes WHERE graph_id=%s",
        (gid,),
    )
    edges_rows = qall(
        "SELECT id, source_node, target_node, stream_label, port_source, port_target, is_tear_stream "
        "FROM fg_edges WHERE graph_id=%s",
        (gid,),
    )
    nodes = [
        GraphNode(
            id=str(r["id"]),
            op_code=r["op_code"],
            params=r.get("params") or {},
            position_x=float(r.get("position_x") or 0),
            position_y=float(r.get("position_y") or 0),
        )
        for r in nodes_rows
    ]
    edges = [
        GraphEdge(
            id=str(r["id"]),
            source_node=str(r["source_node"]),
            target_node=str(r["target_node"]),
            stream_label=r.get("stream_label") or "",
            port_source=r.get("port_source") or "out",
            port_target=r.get("port_target") or "in",
            is_tear_stream=bool(r.get("is_tear_stream")),
        )
        for r in edges_rows
    ]
    return FlowsheetGraph(nodes=nodes, edges=edges)


def _get_project_context(pid: str) -> ProjectContext:
    row = qone("SELECT target_tph, gold_price_usd_oz FROM projects WHERE id=%s", (pid,))
    tph = float(row["target_tph"]) if row and row.get("target_tph") else 1000.0
    price = float(row["gold_price_usd_oz"]) if row and row.get("gold_price_usd_oz") else cfg.DEFAULT_GOLD_PRICE_USD_OZ
    return ProjectContext(target_tph=tph, gold_price_usd=price)


def _get_graph_project_id(gid: str) -> str | None:
    row = qone("SELECT project_id FROM flowsheet_graphs WHERE id=%s", (gid,))
    return str(row["project_id"]) if row else None


def _ensure_graph_access(gid: str, user: dict) -> str:
    """Verify the current user can access the project owning gid. Returns pid."""
    try:
        from ..auth import ensure_project_access
    except ImportError:
        from auth import ensure_project_access
    pid = _get_graph_project_id(gid)
    if not pid:
        raise HTTPException(404, "Flowsheet non trouvé")
    ensure_project_access(pid, user)
    return pid


def _stream_to_dict(s: SimStream | Any) -> dict:
    """Convert a SimStream (or legacy StreamState) to serialisable dict."""
    if s is None:
        return {}
    if isinstance(s, SimStream):
        return {
            "mass_flow": round(s.mass_flow, 4),
            "volume_flow": round(s.volume_flow, 4),
            "solids_pct": round(s.solids_pct, 2),
            "gold_grade": round(s.gold_grade, 4),
            "gold_flow": round(s.gold_flow, 6),
            "dissolved_gold": round(s.dissolved_gold, 4),
            "cyanide_ppm": round(s.cyanide_ppm, 2),
            "pH": round(s.pH, 2),
            "temperature": round(s.temperature, 1),
            "p80_um": round(s.p80_um, 2),
            "energy_kwh_t": round(s.energy_kwh_t, 4),
            "silver_grade": round(s.silver_grade, 4),
            "sulphide_pct": round(s.sulphide_pct, 4),
        }
    # Legacy StreamState
    return {
        "solids_tph": round(getattr(s, "solids_tph", 0), 4),
        "water_tph": round(getattr(s, "water_tph", 0), 4),
        "au_g_t": round(getattr(s, "au_g_t", 0), 4),
        "au_recovery_pct": round(getattr(s, "au_recovery_pct", 0), 2),
        "p80_um": round(getattr(s, "p80_um", 0), 2),
        "energy_kwh_t": round(getattr(s, "energy_kwh_t", 0), 4),
        "cn_kg_t": round(getattr(s, "cn_kg_t", 0), 4),
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  5.1  FLOWSHEET CRUD
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/projects/{pid}/flowsheets")
def list_flowsheets(pid: str, user=Depends(project_user)):
    rows = qall(
        "SELECT id, name, version, status, ore_type, description, created_at, updated_at "
        "FROM flowsheet_graphs WHERE project_id=%s ORDER BY created_at DESC",
        (pid,),
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/api/v2/projects/{pid}/flowsheets", status_code=201)
def create_flowsheet(pid: str, body: dict = Body(...), user=Depends(project_user)):
    name = body.get("name") or "Nouveau flowsheet"
    status = body.get("status", "draft")
    ore_type = body.get("ore_type", "free_milling")
    description = body.get("description")
    gid = _new_id()
    execute(
        "INSERT INTO flowsheet_graphs (id, project_id, name, status, ore_type, description) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (gid, pid, name, status, ore_type, description),
    )
    return {"id": gid, "name": name, "status": status, "ore_type": ore_type}


@router.get("/api/v2/projects/{pid}/flowsheets/{gid}")
def get_flowsheet(pid: str, gid: str, user=Depends(project_user)):
    g = qone(
        "SELECT id, name, version, canvas_state, status, ore_type, description, created_at, updated_at "
        "FROM flowsheet_graphs WHERE id=%s AND project_id=%s",
        (gid, pid),
    )
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    nodes = qall(
        "SELECT id, op_code, label, params, position_x, position_y, "
        "design_capacity_tph, availability_pct, category, stream_type "
        "FROM fg_nodes WHERE graph_id=%s",
        (gid,),
    )
    edges = qall(
        "SELECT id, source_node, target_node, stream_label, port_source, port_target, is_tear_stream "
        "FROM fg_edges WHERE graph_id=%s",
        (gid,),
    )
    return {**dict(g), "nodes": [dict(n) for n in nodes], "edges": [dict(e) for e in edges]}


@router.put("/api/v2/projects/{pid}/flowsheets/{gid}")
def update_flowsheet(pid: str, gid: str, body: dict = Body(...), user=Depends(project_user)):
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s AND project_id=%s", (gid, pid))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    allowed = frozenset({"name", "status", "ore_type", "description", "canvas_state"})
    sets, vals = [], []
    for k in allowed:
        if k in body:
            sets.append(f"{k}=%s")
            v = body[k]
            vals.append(json.dumps(v) if k == "canvas_state" and isinstance(v, dict) else v)
    if sets:
        sets.append("updated_at=now()")
        execute(f"UPDATE flowsheet_graphs SET {', '.join(sets)} WHERE id=%s", vals + [gid])
    return {"ok": True}


@router.delete("/api/v2/projects/{pid}/flowsheets/{gid}", status_code=204)
def delete_flowsheet(pid: str, gid: str, user=Depends(project_user)):
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s AND project_id=%s", (gid, pid))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    execute("DELETE FROM flowsheet_graphs WHERE id=%s", (gid,))


@router.post("/api/v2/projects/{pid}/flowsheets/{gid}/clone", status_code=201)
def clone_flowsheet(pid: str, gid: str, body: dict = Body(...), user=Depends(project_user)):
    g = qone(
        "SELECT id, name, canvas_state, status, ore_type, description "
        "FROM flowsheet_graphs WHERE id=%s AND project_id=%s",
        (gid, pid),
    )
    if not g:
        raise HTTPException(404, "Flowsheet source non trouvé")
    new_name = body.get("name") or f"Copie de {g['name']}"
    new_gid = _new_id()
    execute(
        "INSERT INTO flowsheet_graphs (id, project_id, name, canvas_state, status, ore_type, description) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            new_gid,
            pid,
            new_name,
            json.dumps(g.get("canvas_state") or {}),
            g.get("status", "draft"),
            g.get("ore_type", "free_milling"),
            g.get("description"),
        ),
    )
    # Clone nodes
    old_nodes = qall(
        "SELECT id, op_code, label, params, position_x, position_y, "
        "design_capacity_tph, availability_pct, category, stream_type "
        "FROM fg_nodes WHERE graph_id=%s",
        (gid,),
    )
    node_id_map: dict[str, str] = {}
    for n in old_nodes:
        new_nid = _new_id()
        node_id_map[str(n["id"])] = new_nid
        execute(
            "INSERT INTO fg_nodes (id, graph_id, op_code, label, params, position_x, position_y, "
            "design_capacity_tph, availability_pct, category, stream_type) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                new_nid,
                new_gid,
                n["op_code"],
                n.get("label"),
                json.dumps(n.get("params") or {}),
                float(n.get("position_x") or 0),
                float(n.get("position_y") or 0),
                float(n.get("design_capacity_tph") or 0),
                float(n.get("availability_pct") or 95),
                n.get("category"),
                n.get("stream_type", "pulp"),
            ),
        )
    # Clone edges
    old_edges = qall(
        "SELECT source_node, target_node, stream_label, port_source, port_target FROM fg_edges WHERE graph_id=%s",
        (gid,),
    )
    for e in old_edges:
        src = node_id_map.get(str(e["source_node"]))
        tgt = node_id_map.get(str(e["target_node"]))
        if src and tgt:
            execute(
                "INSERT INTO fg_edges (id, graph_id, source_node, target_node, stream_label, port_source, port_target) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    _new_id(),
                    new_gid,
                    src,
                    tgt,
                    e.get("stream_label"),
                    e.get("port_source", "out"),
                    e.get("port_target", "in"),
                ),
            )
    return {"id": new_gid, "name": new_name}


# ═══════════════════════════════════════════════════════════════════════════════
#  5.2  NODES & EDGES
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/api/v2/flowsheets/{gid}/nodes", status_code=201)
def add_node(gid: str, body: dict = Body(...), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    nid = _new_id()
    op_code = body.get("op_code", "feed_source")
    label = body.get("label") or UNIT_REGISTRY.get(op_code, {}).get("display_name", op_code)
    params = body.get("params") or {}
    # Merge default params from registry
    default_params = UNIT_REGISTRY.get(op_code, {}).get("default_params", {})
    merged_params = {**default_params, **params}
    registry_entry = UNIT_REGISTRY.get(op_code, {})
    execute(
        "INSERT INTO fg_nodes (id, graph_id, op_code, label, params, position_x, position_y, "
        "design_capacity_tph, availability_pct, category, stream_type) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            nid,
            gid,
            op_code,
            label,
            json.dumps(merged_params),
            float(body.get("position_x", 0)),
            float(body.get("position_y", 0)),
            float(body.get("design_capacity_tph", 0)),
            float(body.get("availability_pct", 95)),
            registry_entry.get("category"),
            registry_entry.get("stream_type", "pulp"),
        ),
    )
    return {
        "id": nid,
        "op_code": op_code,
        "label": label,
        "params": merged_params,
        "design_capacity_tph": body.get("design_capacity_tph", 0),
        "availability_pct": body.get("availability_pct", 95),
    }


@router.put("/api/v2/flowsheets/{gid}/nodes/{nid}")
def update_node(gid: str, nid: str, body: dict = Body(...), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    n = qone("SELECT id, params FROM fg_nodes WHERE id=%s AND graph_id=%s", (nid, gid))
    if not n:
        raise HTTPException(404, "Nœud non trouvé")
    allowed = frozenset({"label", "params", "position_x", "position_y", "design_capacity_tph", "availability_pct"})
    sets, vals = [], []
    for k in allowed:
        if k in body:
            sets.append(f"{k}=%s")
            v = body[k]
            vals.append(json.dumps(v) if k == "params" and isinstance(v, dict) else v)
    if sets:
        execute(f"UPDATE fg_nodes SET {', '.join(sets)} WHERE id=%s", vals + [nid])
    return {"ok": True}


@router.delete("/api/v2/flowsheets/{gid}/nodes/{nid}", status_code=204)
def delete_node(gid: str, nid: str, user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    n = qone("SELECT id FROM fg_nodes WHERE id=%s AND graph_id=%s", (nid, gid))
    if not n:
        raise HTTPException(404, "Nœud non trouvé")
    execute("DELETE FROM fg_nodes WHERE id=%s", (nid,))


@router.post("/api/v2/flowsheets/{gid}/edges", status_code=201)
def add_edge(gid: str, body: dict = Body(...), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    eid = _new_id()
    src = body.get("source_node")
    tgt = body.get("target_node")
    if not src or not tgt:
        raise HTTPException(400, "source_node et target_node sont requis")
    execute(
        "INSERT INTO fg_edges (id, graph_id, source_node, target_node, stream_label, port_source, port_target) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (eid, gid, src, tgt, body.get("stream_label"), body.get("port_source", "out"), body.get("port_target", "in")),
    )
    return {"id": eid, "source_node": src, "target_node": tgt}


@router.delete("/api/v2/flowsheets/{gid}/edges/{eid}", status_code=204)
def delete_edge(gid: str, eid: str, user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    e = qone("SELECT id FROM fg_edges WHERE id=%s AND graph_id=%s", (eid, gid))
    if not e:
        raise HTTPException(404, "Arête non trouvée")
    execute("DELETE FROM fg_edges WHERE id=%s", (eid,))


@router.get("/api/v2/flowsheets/{gid}/validate")
def validate_flowsheet(gid: str, user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")

    graph = _load_graph(gid)
    errors: list[dict] = []

    # Check each node has at least one connection (except source/sink types)
    sink_ops = {"tailings_storage", "product_sink"}
    source_ops = {"feed_source"}
    connected_nodes = set()
    for e in graph.edges:
        connected_nodes.add(e.source_node)
        connected_nodes.add(e.target_node)

    {n.id: n for n in graph.nodes}
    for node in graph.nodes:
        if node.op_code in source_ops:
            if node.id not in {e.source_node for e in graph.edges}:
                errors.append({"node_id": node.id, "message": f"Source '{node.op_code}' sans sortie"})
        elif node.op_code in sink_ops:
            if node.id not in {e.target_node for e in graph.edges}:
                errors.append({"node_id": node.id, "message": f"Puits '{node.op_code}' sans entrée"})
        else:
            has_in = node.id in {e.target_node for e in graph.edges}
            has_out = node.id in {e.source_node for e in graph.edges}
            if not has_in:
                errors.append({"node_id": node.id, "message": f"Nœud '{node.op_code}' sans flux d'entrée"})
            if not has_out:
                errors.append({"node_id": node.id, "message": f"Nœud '{node.op_code}' sans flux de sortie"})

    # Topology check
    topo = TopologyAnalyzer(graph).analyze()

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "n_nodes": len(graph.nodes),
        "n_edges": len(graph.edges),
        "has_loops": topo.has_loops,
        "loops_detected": topo.loops_detected,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  5.3  SIMULATION
# ═══════════════════════════════════════════════════════════════════════════════


def _run_simulation_and_store(
    gid: str,
    feed_input: dict,
    mode: str,
    max_iterations: int,
    convergence_tol: float,
    scenario_label: str | None = None,
) -> dict:
    """Run simulation, persist results, return result payload."""
    graph = _load_graph(gid)
    pid = _get_graph_project_id(gid)
    ctx = _get_project_context(pid) if pid else ProjectContext(target_tph=1000.0, gold_price_usd=cfg.DEFAULT_GOLD_PRICE_USD_OZ)

    # Adjust feed rate from feed_input if provided
    feed_rate_override = feed_input.get("feed_rate_tph")
    if feed_rate_override:
        ctx = ProjectContext(
            target_tph=float(feed_rate_override),
            gold_price_usd=ctx.gold_price_usd,
        )

    result = _engine.run(graph, ctx)
    run_id = _new_id()

    # ── Build node results using sim_unit_library for richer data ─────────────
    nodes_rows = qall(
        "SELECT id, op_code, params, design_capacity_tph, availability_pct FROM fg_nodes WHERE graph_id=%s",
        (gid,),
    )
    node_results: list[dict] = []
    edge_by_target: dict[str, list] = {}
    for e in graph.edges:
        edge_by_target.setdefault(str(e.target_node), []).append(e)

    for node_row in nodes_rows:
        nid = str(node_row["id"])
        op_code = node_row["op_code"]
        params = node_row.get("params") or {}
        design_cap = float(node_row.get("design_capacity_tph") or 0)
        avail = float(node_row.get("availability_pct") or 95)

        # Gather inlet streams from sim result
        inlet_sim_streams: dict[str, SimStream] = {}
        for edge in edge_by_target.get(nid, []):
            raw_state = result.stream_results.get(edge.id)
            if raw_state:
                inlet_sim_streams[edge.port_target] = SimStream(
                    mass_flow=getattr(raw_state, "solids_tph", 0),
                    volume_flow=getattr(raw_state, "solids_tph", 0) / 2.7 + getattr(raw_state, "water_tph", 0),
                    gold_grade=getattr(raw_state, "au_g_t", 0),
                    gold_flow=getattr(raw_state, "solids_tph", 0) * getattr(raw_state, "au_g_t", 0) / 1000,
                    p80_um=getattr(raw_state, "p80_um", 75),
                    energy_kwh_t=getattr(raw_state, "energy_kwh_t", 0),
                    cyanide_ppm=getattr(raw_state, "cn_kg_t", 0) * 1000,
                )

        if not inlet_sim_streams and op_code != "feed_source":
            continue

        try:
            unit_out = calculate_unit(op_code, inlet_sim_streams, params, feed_input, design_cap, avail)
            feed_rate = sum(s.mass_flow for s in inlet_sim_streams.values()) if inlet_sim_streams else 0
            product_rate = sum(s.mass_flow for s in unit_out.streams.values())
            util = unit_out.utilization_rate
            is_bottleneck = util > 0.85 and design_cap > 0
            node_results.append(
                {
                    "node_id": nid,
                    "op_code": op_code,
                    "feed_rate_tph": round(feed_rate, 3),
                    "product_rate_tph": round(product_rate, 3),
                    "recovery_pct": round(unit_out.recovery_pct, 2),
                    "energy_kwh_t": round(unit_out.energy_kwh_t, 3),
                    "utilization_rate": round(util, 4),
                    "is_bottleneck": is_bottleneck,
                    "kpis": unit_out.kpis,
                    "reagent_consumptions": unit_out.reagent_consumptions,
                }
            )
        except Exception as exc:
            logger.warning("Node result calc failed node=%s: %s", nid, exc)

    # ── Persist run ────────────────────────────────────────────────────────────
    global_results = {
        **result.kpis,
        "mode": mode,
        "feed_input": feed_input,
        "node_results_count": len(node_results),
        "bottleneck_count": sum(1 for n in node_results if n.get("is_bottleneck")),
    }
    execute(
        "INSERT INTO flowsheet_runs "
        "(id, graph_id, status, iterations, kpis, feed_input, global_results, mode, "
        " scenario_label, convergence_error, completed_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
        (
            run_id,
            gid,
            "converged" if result.converged else "failed",
            result.iterations,
            json.dumps(result.kpis),
            json.dumps(feed_input),
            json.dumps(global_results),
            mode,
            scenario_label,
            round(result.final_residual, 8),
        ),
    )

    # Persist node results
    for nr in node_results:
        execute(
            "INSERT INTO fg_node_results "
            "(id, run_id, node_id, feed_rate_tph, product_rate_tph, recovery_pct, "
            " energy_kwh_t, utilization_rate, is_bottleneck, kpis, reagent_consumptions) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _new_id(),
                run_id,
                nr["node_id"],
                nr["feed_rate_tph"],
                nr["product_rate_tph"],
                nr["recovery_pct"],
                nr["energy_kwh_t"],
                nr["utilization_rate"],
                nr["is_bottleneck"],
                json.dumps(nr["kpis"]),
                json.dumps(nr["reagent_consumptions"]),
            ),
        )

    # Persist stream results
    for edge_id, stream_state in result.stream_results.items():
        execute(
            "INSERT INTO fg_stream_results "
            "(id, run_id, edge_id, iteration, solids_tph, water_tph, slurry_tph, "
            " au_g_t, au_recovery_pct, p80_um, energy_kwh_t, cn_kg_t) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _new_id(),
                run_id,
                edge_id,
                result.iterations,
                getattr(stream_state, "solids_tph", 0),
                getattr(stream_state, "water_tph", 0),
                getattr(
                    stream_state,
                    "slurry_tph",
                    getattr(stream_state, "solids_tph", 0) + getattr(stream_state, "water_tph", 0),
                ),
                getattr(stream_state, "au_g_t", 0),
                getattr(stream_state, "au_recovery_pct", 0),
                getattr(stream_state, "p80_um", 0),
                getattr(stream_state, "energy_kwh_t", 0),
                getattr(stream_state, "cn_kg_t", 0),
            ),
        )

    # Attach auditable provenance to each stream (Lot A traceability): the
    # producing node's operation + the streams feeding that node.
    _op_by_node = {n.id: n.op_code for n in graph.nodes}
    _edge_by_id = {e.id: e for e in graph.edges}
    stream_results_out = {}
    for eid, s in result.stream_results.items():
        d = _stream_to_dict(s)
        edge = _edge_by_id.get(eid)
        if edge is not None:
            src_op = _op_by_node.get(edge.source_node)
            input_labels = [
                e2.stream_label for e2 in graph.edges if e2.target_node == edge.source_node and e2.stream_label
            ]
            d.update(
                stream_source_basis(
                    source_node_label=src_op,
                    source_node_op_code=None,
                    input_stream_labels=input_labels,
                )
            )
        stream_results_out[eid] = d

    return {
        "run_id": run_id,
        "status": "converged" if result.converged else "failed",
        "converged": result.converged,
        "iterations": result.iterations,
        "convergence_error": round(result.final_residual, 8),
        "mode": mode,
        "global_results": global_results,
        "node_results": node_results,
        "stream_results": stream_results_out,
    }


@router.post("/api/v2/flowsheets/{gid}/simulate")
def run_simulation(gid: str, body: dict = Body(...), user=Depends(current_user)):
    """Synchronous route — FastAPI runs sync routes in thread pool automatically."""
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    feed_input = body.get("feed_input") or {}
    mode = body.get("mode", "steady_state")
    max_iterations = int(body.get("max_iterations", 50))
    convergence_tol = float(body.get("convergence_tolerance", 1e-4))
    scenario_label = body.get("scenario_label")
    try:
        return _run_simulation_and_store(gid, feed_input, mode, max_iterations, convergence_tol, scenario_label)
    except Exception as exc:
        logger.error("Simulation failed gid=%s: %s", gid, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/api/v2/simulation-runs/{run_id}")
def get_simulation_run(run_id: str, user=Depends(current_user)):
    row = qone(
        "SELECT id, graph_id, status, iterations, kpis, feed_input, global_results, "
        "mode, scenario_label, convergence_error, triggered_at, completed_at "
        "FROM flowsheet_runs WHERE id=%s",
        (run_id,),
    )
    if not row:
        raise HTTPException(404, "Run non trouvé")
    _ensure_graph_access(str(row["graph_id"]), user)
    return dict(row)


@router.get("/api/v2/simulation-runs/{run_id}/results")
def get_simulation_run_results(run_id: str, user=Depends(current_user)):
    row = qone(
        "SELECT id, graph_id, status, iterations, kpis, feed_input, global_results, "
        "mode, scenario_label, convergence_error, triggered_at, completed_at "
        "FROM flowsheet_runs WHERE id=%s",
        (run_id,),
    )
    if not row:
        raise HTTPException(404, "Run non trouvé")
    node_results = qall(
        "SELECT node_id, feed_rate_tph, product_rate_tph, recovery_pct, energy_kwh_t, "
        "utilization_rate, is_bottleneck, kpis, reagent_consumptions "
        "FROM fg_node_results WHERE run_id=%s ORDER BY node_id",
        (run_id,),
    )
    stream_results = qall(
        "SELECT edge_id, iteration, solids_tph, water_tph, slurry_tph, au_g_t, "
        "au_recovery_pct, p80_um, energy_kwh_t, cn_kg_t, volume_flow_m3h, ph, "
        "temperature_c, dissolved_gold_mg_l, cyanide_conc_ppm "
        "FROM fg_stream_results WHERE run_id=%s",
        (run_id,),
    )
    return {
        **dict(row),
        "node_results": [dict(r) for r in node_results],
        "stream_results": [dict(r) for r in stream_results],
    }


@router.get("/api/v2/flowsheets/{gid}/simulation-runs")
def list_simulation_runs(gid: str, user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    rows = qall(
        "SELECT id, status, iterations, kpis, mode, scenario_label, convergence_error, "
        "triggered_at, completed_at "
        "FROM flowsheet_runs WHERE graph_id=%s ORDER BY triggered_at DESC LIMIT 20",
        (gid,),
    )
    return {"items": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════════════════════════
#  5.4  OPTIMIZATION  (simple GA)
# ═══════════════════════════════════════════════════════════════════════════════


def _solve_irr(capex: float, annual_cashflow: float, n_years: int = 10) -> float:
    """Newton-Raphson IRR solver. Returns rate as a decimal (0.10 = 10 %)."""
    if capex <= 0 or annual_cashflow <= 0:
        return 0.0
    r = 0.10
    for _ in range(100):
        npv = -capex + sum(annual_cashflow / (1 + r) ** y for y in range(1, n_years + 1))
        dnpv = sum(-y * annual_cashflow / (1 + r) ** (y + 1) for y in range(1, n_years + 1))
        if abs(dnpv) < 1e-12:
            break
        r_new = r - npv / dnpv
        if abs(r_new - r) < 1e-7:
            r = r_new
            break
        r = max(-0.99, min(r_new, 10.0))
    return r


def _evaluate_objective(
    individual: list[float],
    variables: list[dict],
    gid: str,
    feed_input: dict,
    objective: str,
    constraints: list[dict],
) -> float:
    """Run simulation with individual's parameter overrides and return scalar fitness."""
    params_override: dict[str, dict] = {}
    for i, var in enumerate(variables):
        node_id = var["node_id"]
        param = var["parameter"]
        params_override.setdefault(node_id, {})[param] = individual[i]

    graph = _load_graph(gid)
    # Apply overrides to graph nodes
    for node in graph.nodes:
        if node.id in params_override:
            node.params = {**node.params, **params_override[node.id]}

    pid = _get_graph_project_id(gid)
    ctx = (
        _get_project_context(pid)
        if pid
        else ProjectContext(target_tph=float(feed_input.get("feed_rate_tph", 1000)), gold_price_usd=cfg.DEFAULT_GOLD_PRICE_USD_OZ)
    )
    try:
        result = _engine.run(graph, ctx)
        kpis = result.kpis
    except Exception:
        return -1e9

    # Check constraints (simple inequality)
    for con in constraints:
        kpi_name = con.get("kpi")
        op = con.get("operator", ">=")
        val = float(con.get("value", 0))
        actual = kpis.get(kpi_name, 0)
        if op == ">=" and actual < val:
            return -1e9
        elif op == "<=" and actual > val:
            return -1e9

    # Objective fitness (maximise)
    if objective == "maximize_recovery":
        return kpis.get("total_recovery_pct", 0)
    elif objective == "minimize_energy":
        return -kpis.get("energy_kwh_t", 0)
    elif objective == "maximize_annual_oz":
        return kpis.get("annual_oz", 0)
    elif objective == "maximize_npv":
        annual_oz = kpis.get("annual_oz", 0)
        return annual_oz * 2000 * 0.65  # simplified NPV proxy
    elif objective == "minimize_opex":
        energy = kpis.get("energy_kwh_t", 0)
        return -energy * 0.15  # energy cost proxy
    else:
        return kpis.get("total_recovery_pct", 0)


def _run_ga(
    gid: str,
    feed_input: dict,
    objective: str,
    variables: list[dict],
    constraints: list[dict],
    iterations: int = 50,
) -> dict[str, Any]:
    """Simple genetic algorithm for flowsheet optimisation."""
    if not variables:
        return {"best_params": {}, "best_fitness": 0.0, "generations": 0}

    pop_size = min(max(iterations, 10), 50)
    n_generations = iterations

    def rand_individual() -> list[float]:
        return [random.uniform(v["min"], v["max"]) for v in variables]

    def fitness(ind: list[float]) -> float:
        return _evaluate_objective(ind, variables, gid, feed_input, objective, constraints)

    def tournament(pop: list, fits: list[float], k: int = 3) -> list[float]:
        candidates = random.sample(range(len(pop)), min(k, len(pop)))
        return pop[max(candidates, key=lambda i: fits[i])]

    def crossover(p1: list[float], p2: list[float]) -> tuple[list[float], list[float]]:
        if len(p1) <= 1:
            return p1[:], p2[:]
        pt = random.randint(1, len(p1) - 1)
        return p1[:pt] + p2[pt:], p2[:pt] + p1[pt:]

    def mutate(ind: list[float]) -> list[float]:
        result = ind[:]
        for i, var in enumerate(variables):
            if random.random() < 0.15:
                sigma = (var["max"] - var["min"]) * 0.1
                result[i] = max(var["min"], min(var["max"], result[i] + random.gauss(0, sigma)))
        return result

    population = [rand_individual() for _ in range(pop_size)]
    fits = [fitness(ind) for ind in population]

    best_idx = max(range(len(population)), key=lambda i: fits[i])
    best_individual = population[best_idx][:]
    best_fitness = fits[best_idx]

    for gen in range(n_generations):
        new_pop: list[list[float]] = []
        for _ in range(pop_size // 2):
            p1 = tournament(population, fits)
            p2 = tournament(population, fits)
            c1, c2 = crossover(p1, p2)
            new_pop.extend([mutate(c1), mutate(c2)])

        population = new_pop[:pop_size]
        fits = [fitness(ind) for ind in population]

        gen_best = max(range(len(population)), key=lambda i: fits[i])
        if fits[gen_best] > best_fitness:
            best_fitness = fits[gen_best]
            best_individual = population[gen_best][:]

    best_params = {
        f"{var['node_id']}.{var['parameter']}": round(best_individual[i], 6) for i, var in enumerate(variables)
    }
    return {
        "best_params": best_params,
        "best_fitness": round(best_fitness, 4),
        "objective_value": round(best_fitness, 4),
        "generations": n_generations,
    }


@router.post("/api/v2/flowsheets/{gid}/optimize")
def create_optimization_job(gid: str, body: dict = Body(...), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")

    objective = body.get("objective", "maximize_recovery")
    variables = body.get("variables", [])
    constraints = body.get("constraints", [])
    method = body.get("method", "genetic_algorithm")
    iterations = int(body.get("iterations", 50))
    feed_input = body.get("feed_input") or {}

    job_id = _new_id()
    execute(
        "INSERT INTO fg_optimization_jobs "
        "(id, graph_id, objective, variables, constraints, method, status, feed_input) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            job_id,
            gid,
            objective,
            json.dumps(variables),
            json.dumps(constraints),
            method,
            "running",
            json.dumps(feed_input),
        ),
    )

    def _run_optimization_sync() -> dict:
        # Run synchronously (fast enough for ≤50 nodes)
        try:
            ga_results = _run_ga(gid, feed_input, objective, variables, constraints, iterations)
            execute(
                "UPDATE fg_optimization_jobs SET status=%s, results=%s, completed_at=now() WHERE id=%s",
                ("completed", json.dumps(ga_results), job_id),
            )
            return {"job_id": job_id, "status": "completed", "results": ga_results}
        except Exception as exc:
            logger.error("Optimization job %s failed: %s", job_id, exc)
            execute(
                "UPDATE fg_optimization_jobs SET status=%s, results=%s WHERE id=%s",
                ("failed", json.dumps({"error": str(exc)}), job_id),
            )
            return {"job_id": job_id, "status": "failed", "results": {}}

    return _run_optimization_sync()


@router.get("/api/v2/optimization-jobs/{job_id}")
def get_optimization_job(job_id: str, user=Depends(current_user)):
    row = qone(
        "SELECT id, graph_id, objective, variables, constraints, method, status, "
        "feed_input, results, created_at, completed_at "
        "FROM fg_optimization_jobs WHERE id=%s",
        (job_id,),
    )
    if not row:
        raise HTTPException(404, "Job non trouvé")
    _ensure_graph_access(str(row["graph_id"]), user)
    return dict(row)


@router.get("/api/v2/optimization-jobs/{job_id}/results")
def get_optimization_job_results(job_id: str, user=Depends(current_user)):
    row = qone(
        "SELECT id, graph_id, objective, status, results, completed_at FROM fg_optimization_jobs WHERE id=%s",
        (job_id,),
    )
    if not row:
        raise HTTPException(404, "Job non trouvé")
    _ensure_graph_access(str(row["graph_id"]), user)
    return dict(row)


# ═══════════════════════════════════════════════════════════════════════════════
#  5.5  CAPACITY ANALYSIS & EXPANSION SCENARIOS
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/api/v2/flowsheets/{gid}/bottleneck-analysis")
def bottleneck_analysis(gid: str, body: dict = Body(default={}), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")

    feed_input = body.get("feed_input") or {}
    sim = _run_simulation_and_store(gid, feed_input, "steady_state", 50, 1e-4, "bottleneck_analysis")

    # Build bottleneck list from node results
    bottlenecks: list[dict] = []
    for nr in sim.get("node_results", []):
        util = nr.get("utilization_rate", 0)
        severity = "normal"
        if util > 1.0:
            severity = "overloaded"
        elif util > 0.85:
            severity = "critical"
        elif util > 0.70:
            severity = "warning"

        # Recommend action
        if severity in ("overloaded", "critical"):
            action = "Augmenter la capacité ou ajouter un équipement en parallèle"
        elif severity == "warning":
            action = "Surveiller et planifier une expansion"
        else:
            action = "Opération normale"

        feed_rate = nr.get("feed_rate_tph", 0)
        design_cap = feed_rate / max(util, 0.001) if util > 0 else 0
        bottlenecks.append(
            {
                "node_id": nr["node_id"],
                "op_code": nr.get("op_code", ""),
                "utilization_pct": round(util * 100, 1),
                "is_bottleneck": nr.get("is_bottleneck", False),
                "severity": severity,
                "feed_rate_tph": round(feed_rate, 2),
                "max_throughput_tph": round(design_cap, 2),
                "recommended_action": action,
            }
        )

    # Critical path: nodes sorted by utilization descending
    critical_path = [b["node_id"] for b in sorted(bottlenecks, key=lambda x: -x["utilization_pct"])]

    return {
        "run_id": sim["run_id"],
        "bottlenecks": bottlenecks,
        "critical_path": critical_path,
        "global_results": sim.get("global_results", {}),
    }


@router.post("/api/v2/flowsheets/{gid}/expansion-scenarios", status_code=201)
def create_expansion_scenario(gid: str, body: dict = Body(...), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    label = body.get("label") or "Scénario d'expansion"
    target_pct = float(body.get("target_increase_pct", 0))
    modifications = body.get("modifications", [])
    sid = _new_id()
    execute(
        "INSERT INTO fg_expansion_scenarios "
        "(id, graph_id, label, target_increase_pct, modifications) "
        "VALUES (%s, %s, %s, %s, %s)",
        (sid, gid, label, target_pct, json.dumps(modifications)),
    )
    return {"id": sid, "label": label, "target_increase_pct": target_pct}


@router.get("/api/v2/flowsheets/{gid}/expansion-scenarios")
def list_expansion_scenarios(gid: str, user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    rows = qall(
        "SELECT id, label, target_increase_pct, modifications, simulation_run_id, economics, "
        "created_at, updated_at FROM fg_expansion_scenarios WHERE graph_id=%s ORDER BY created_at DESC",
        (gid,),
    )
    return {"items": [dict(r) for r in rows]}


@router.put("/api/v2/flowsheets/{gid}/expansion-scenarios/{sid}")
def update_expansion_scenario(gid: str, sid: str, body: dict = Body(...), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    s = qone("SELECT id FROM fg_expansion_scenarios WHERE id=%s AND graph_id=%s", (sid, gid))
    if not s:
        raise HTTPException(404, "Scénario non trouvé")
    allowed = frozenset({"label", "target_increase_pct", "modifications"})
    sets, vals = [], []
    for k in allowed:
        if k in body:
            sets.append(f"{k}=%s")
            v = body[k]
            vals.append(json.dumps(v) if k == "modifications" and isinstance(v, list) else v)
    if sets:
        sets.append("updated_at=now()")
        execute(
            f"UPDATE fg_expansion_scenarios SET {', '.join(sets)} WHERE id=%s",
            vals + [sid],
        )
    return {"ok": True}


def _evaluate_expansion_economics(
    base_kpis: dict, scenario_kpis: dict, modifications: list[dict], gold_price: float = cfg.DEFAULT_GOLD_PRICE_USD_OZ
) -> dict:
    """Compute expansion NPV, IRR, payback from two simulation KPI dicts."""
    capex = sum(float(m.get("capex_estimate", 0)) for m in modifications)
    base_oz = float(base_kpis.get("annual_oz", 0))
    scenario_oz = float(scenario_kpis.get("annual_oz", 0))
    additional_oz_year = scenario_oz - base_oz
    annual_revenue_delta = additional_oz_year * gold_price

    base_energy = float(base_kpis.get("energy_kwh_t", 0))
    scenario_energy = float(scenario_kpis.get("energy_kwh_t", 0))
    energy_opex_rate = 0.10  # USD/kWh
    feed_rate = float(scenario_kpis.get("feed_rate_tph", 1000))
    annual_hours = 8760 * 0.92
    opex_delta_year = (scenario_energy - base_energy) * energy_opex_rate * feed_rate * annual_hours

    annual_cashflow = annual_revenue_delta - opex_delta_year
    npv = sum(annual_cashflow / (1.08**y) for y in range(1, 11)) - capex
    irr = _solve_irr(capex, annual_cashflow, 10)
    payback = capex / annual_cashflow if annual_cashflow > 0 else 99.0
    aisc = (opex_delta_year + capex / 10) / max(additional_oz_year, 1)

    return {
        "capex": round(capex, 0),
        "additional_oz_year": round(additional_oz_year, 0),
        "annual_revenue_delta": round(annual_revenue_delta, 0),
        "opex_delta_year": round(opex_delta_year, 0),
        "annual_cashflow": round(annual_cashflow, 0),
        "npv": round(npv, 0),
        "irr_pct": round(irr * 100, 2),
        "payback_years": round(payback, 1),
        "aisc_per_oz": round(aisc, 2),
        "gold_price_used": gold_price,
    }


@router.post("/api/v2/flowsheets/{gid}/expansion-scenarios/{sid}/evaluate")
def evaluate_expansion_scenario(gid: str, sid: str, body: dict = Body(default={}), user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    scenario = qone(
        "SELECT id, modifications, target_increase_pct FROM fg_expansion_scenarios WHERE id=%s AND graph_id=%s",
        (sid, gid),
    )
    if not scenario:
        raise HTTPException(404, "Scénario non trouvé")

    gold_price = float(body.get("gold_price", cfg.DEFAULT_GOLD_PRICE_USD_OZ))
    modifications = scenario.get("modifications") or []
    target_pct = float(scenario.get("target_increase_pct") or 0)

    # Base simulation
    base_sim = _run_simulation_and_store(gid, body.get("feed_input") or {}, "steady_state", 50, 1e-4, "base_case")
    base_kpis = base_sim.get("global_results", {})

    # Modified feed for scenario (apply target_increase_pct to feed rate)
    base_feed = float(body.get("feed_input", {}).get("feed_rate_tph", 1000))
    scenario_feed = base_feed * (1 + target_pct / 100)
    scenario_feed_input = {**body.get("feed_input", {}), "feed_rate_tph": scenario_feed}

    # Apply modifications as node param overrides (patch nodes temporarily via DB is unsafe;
    # we instead compute scenario KPIs by scaling base proportionally).
    # For a full implementation the modifications would be applied before simulation.
    scenario_sim = _run_simulation_and_store(gid, scenario_feed_input, "steady_state", 50, 1e-4, f"scenario_{sid}")
    scenario_kpis = scenario_sim.get("global_results", {})
    scenario_kpis["feed_rate_tph"] = scenario_feed

    economics = _evaluate_expansion_economics(base_kpis, scenario_kpis, modifications, gold_price)

    execute(
        "UPDATE fg_expansion_scenarios SET economics=%s, simulation_run_id=%s, updated_at=now() WHERE id=%s",
        (json.dumps(economics), scenario_sim["run_id"], sid),
    )
    return {
        "scenario_id": sid,
        "base_run_id": base_sim["run_id"],
        "scenario_run_id": scenario_sim["run_id"],
        "economics": economics,
        "base_kpis": base_kpis,
        "scenario_kpis": scenario_kpis,
    }


@router.get("/api/v2/flowsheets/{gid}/expansion-scenarios/compare")
def compare_expansion_scenarios(gid: str, user=Depends(current_user)):
    _ensure_graph_access(gid, user)
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s", (gid,))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    rows = qall(
        "SELECT id, label, target_increase_pct, economics, simulation_run_id "
        "FROM fg_expansion_scenarios WHERE graph_id=%s ORDER BY created_at",
        (gid,),
    )
    comparison = []
    for r in rows:
        econ = r.get("economics") or {}
        comparison.append(
            {
                "id": str(r["id"]),
                "label": r["label"],
                "target_increase_pct": r["target_increase_pct"],
                "capex": econ.get("capex", 0),
                "npv": econ.get("npv", 0),
                "irr_pct": econ.get("irr_pct", 0),
                "payback_years": econ.get("payback_years", 0),
                "aisc_per_oz": econ.get("aisc_per_oz", 0),
                "additional_oz_year": econ.get("additional_oz_year", 0),
            }
        )
    return {"scenarios": comparison}


# ═══════════════════════════════════════════════════════════════════════════════
#  5.6  UNIT LIBRARY
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/v2/unit-library")
def list_unit_library(user=Depends(current_user)):
    """Return all unit types grouped by category."""
    by_category: dict[str, list] = {}
    for unit_type, meta in UNIT_REGISTRY.items():
        cat = meta.get("category", "utilities")
        by_category.setdefault(cat, []).append(
            {
                "unit_type": unit_type,
                "display_name": meta.get("display_name", unit_type),
                "category": cat,
                "description": meta.get("description", ""),
                "inlet_ports": meta.get("inlet_ports", []),
                "outlet_ports": meta.get("outlet_ports", []),
                "stream_type": meta.get("stream_type", "pulp"),
            }
        )
    return {"units": UNIT_REGISTRY, "by_category": by_category, "total": len(UNIT_REGISTRY)}


@router.get("/api/v2/unit-library/{unit_type}")
def get_unit_type(unit_type: str, user=Depends(current_user)):
    meta = UNIT_REGISTRY.get(unit_type)
    if not meta:
        raise HTTPException(404, f"Type d'unité inconnu: {unit_type}")
    return meta


@router.get("/api/v2/unit-library/{unit_type}/parameter-schema")
def get_unit_parameter_schema(unit_type: str, user=Depends(current_user)):
    meta = UNIT_REGISTRY.get(unit_type)
    if not meta:
        raise HTTPException(404, f"Type d'unité inconnu: {unit_type}")
    return {
        "unit_type": unit_type,
        "display_name": meta.get("display_name", unit_type),
        "default_params": meta.get("default_params", {}),
        "param_schema": meta.get("param_schema", []),
    }
