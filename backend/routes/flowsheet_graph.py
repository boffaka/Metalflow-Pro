# backend/routes/flowsheet_graph.py
"""
Flowsheet Simulator v4 — REST + WebSocket routes.

REST:
  GET    /api/v1/projects/{pid}/flowsheet-graphs          → liste
  POST   /api/v1/projects/{pid}/flowsheet-graphs          → créer
  GET    /api/v1/projects/{pid}/flowsheet-graphs/{gid}    → détail avec nœuds+arêtes
  PUT    /api/v1/projects/{pid}/flowsheet-graphs/{gid}    → mettre à jour name/canvas
  DELETE /api/v1/projects/{pid}/flowsheet-graphs/{gid}    → supprimer
  GET    /api/v1/flowsheet-starters                       → templates de départ
  POST   /api/v1/projects/{pid}/flowsheet-graphs/{gid}/run → simulation synchrone

WebSocket:
  WS     /api/v1/projects/{pid}/flowsheet/ws              → temps réel
"""
from __future__ import annotations
import json
import logging
import uuid
import asyncio

from fastapi import APIRouter, Depends, HTTPException, WebSocket, Query, Body
from starlette.websockets import WebSocketDisconnect

try:
    from ..auth import project_user
    from ..db import qone, qall, execute
    from ..websocket_handlers import ws_authenticate, ws_check_project
except ImportError:
    from auth import project_user
    from db import qone, qall, execute
    from websocket_handlers import ws_authenticate, ws_check_project

try:
    from ..engines.flowsheet_graph_engine import FlowsheetGraphEngine
    from ..engines.flowsheet_validator import validate_flowsheet
    from ..engines.optimization_problem_builder import build_optimization_problem
    from ..engines.unit_registry import unit_library_payload
    from ..engines.topology_analyzer import (
        FlowsheetGraph, GraphNode, GraphEdge, TopologyAnalyzer,
    )
    from ..engines.unit_op_dispatcher import ProjectContext
    from ..engines.ai_flowsheet_advisor import AIFlowsheetAdvisor
except ImportError:
    from engines.flowsheet_graph_engine import FlowsheetGraphEngine
    from engines.flowsheet_validator import validate_flowsheet
    from engines.optimization_problem_builder import build_optimization_problem
    from engines.unit_registry import unit_library_payload
    from engines.topology_analyzer import (
        FlowsheetGraph, GraphNode, GraphEdge, TopologyAnalyzer,
    )
    from engines.unit_op_dispatcher import ProjectContext
    from engines.ai_flowsheet_advisor import AIFlowsheetAdvisor

logger = logging.getLogger("mpdpms.flowsheet_graph")
router = APIRouter(tags=["flowsheet-graph"])

_engine = FlowsheetGraphEngine()
_advisor = AIFlowsheetAdvisor(cooldown_s=10)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_graph_from_db(gid: str) -> FlowsheetGraph:
    nodes_rows = qall(
        "SELECT id, op_code, label, params, position_x, position_y "
        "FROM fg_nodes WHERE graph_id=%s", (gid,)
    )
    edges_rows = qall(
        "SELECT id, source_node, target_node, stream_label, port_source, port_target, is_tear_stream "
        "FROM fg_edges WHERE graph_id=%s", (gid,)
    )
    nodes = [
        GraphNode(id=str(r["id"]), op_code=r["op_code"],
                  params=r.get("params") or {},
                  position_x=float(r.get("position_x") or 0),
                  position_y=float(r.get("position_y") or 0))
        for r in nodes_rows
    ]
    edges = [
        GraphEdge(id=str(r["id"]),
                  source_node=str(r["source_node"]),
                  target_node=str(r["target_node"]),
                  stream_label=r.get("stream_label") or "",
                  port_source=r.get("port_source") or "out",
                  port_target=r.get("port_target") or "in",
                  is_tear_stream=bool(r.get("is_tear_stream")))
        for r in edges_rows
    ]
    return FlowsheetGraph(nodes=nodes, edges=edges)


def _get_project_context(pid: str) -> ProjectContext:
    # FIX: colonne correcte = gold_price_usd_oz (pas gold_price)
    row = qone("SELECT target_tph, gold_price_usd_oz FROM projects WHERE id=%s", (pid,))
    tph = float(row["target_tph"]) if row and row.get("target_tph") else 1000
    price = float(row["gold_price_usd_oz"]) if row and row.get("gold_price_usd_oz") else 2000
    return ProjectContext(target_tph=tph, gold_price_usd=price)


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/api/v1/projects/{pid}/flowsheet-graphs")
def list_graphs(pid: str, user=Depends(project_user)):
    rows = qall(
        "SELECT id, name, version, created_at, updated_at "
        "FROM flowsheet_graphs WHERE project_id=%s ORDER BY created_at DESC",
        (pid,),
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/api/v1/projects/{pid}/flowsheet-graphs", status_code=201)
def create_graph(pid: str, body: dict = Body(...), user=Depends(project_user)):
    name = body.get("name") or "Nouveau flowsheet"
    gid = str(uuid.uuid4())
    execute(
        "INSERT INTO flowsheet_graphs (id, project_id, name) VALUES (%s, %s, %s)",
        (gid, pid, name),
    )
    return {"id": gid, "name": name}


@router.get("/api/v1/projects/{pid}/flowsheet-graphs/{gid}")
def get_graph(pid: str, gid: str, user=Depends(project_user)):
    g = qone("SELECT id, name, version, canvas_state FROM flowsheet_graphs "
             "WHERE id=%s AND project_id=%s", (gid, pid))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    nodes = qall("SELECT id, op_code, label, params, position_x, position_y "
                 "FROM fg_nodes WHERE graph_id=%s", (gid,))
    edges = qall("SELECT id, source_node, target_node, stream_label, port_source, port_target "
                 "FROM fg_edges WHERE graph_id=%s", (gid,))
    return {**dict(g), "nodes": [dict(n) for n in nodes], "edges": [dict(e) for e in edges]}


@router.put("/api/v1/projects/{pid}/flowsheet-graphs/{gid}")
def update_graph(pid: str, gid: str, body: dict = Body(...), user=Depends(project_user)):
    name = body.get("name")
    canvas = body.get("canvas_state")
    if name:
        execute("UPDATE flowsheet_graphs SET name=%s, updated_at=now() WHERE id=%s AND project_id=%s",
                (name, gid, pid))
    if canvas:
        execute("UPDATE flowsheet_graphs SET canvas_state=%s, updated_at=now() WHERE id=%s AND project_id=%s",
                (json.dumps(canvas), gid, pid))
    return {"ok": True}


@router.delete("/api/v1/projects/{pid}/flowsheet-graphs/{gid}", status_code=204)
def delete_graph(pid: str, gid: str, user=Depends(project_user)):
    execute("DELETE FROM flowsheet_graphs WHERE id=%s AND project_id=%s", (gid, pid))


@router.get("/api/v1/flowsheet-starters")
def list_starters(user=Depends(project_user)):
    rows = qall("SELECT id, code, family, name, description FROM flowsheet_starters ORDER BY family, name")
    return {"items": [dict(r) for r in rows]}


@router.get("/api/v1/flowsheet-unit-library")
def get_unit_library(user=Depends(project_user)):
    return unit_library_payload()


@router.post("/api/v1/projects/{pid}/flowsheet-graphs/{gid}/validate")
def validate_graph(pid: str, gid: str, user=Depends(project_user)):
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s AND project_id=%s", (gid, pid))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    graph = _load_graph_from_db(gid)
    return validate_flowsheet(graph).to_dict()


@router.get("/api/v1/projects/{pid}/flowsheet-graphs/{gid}/optimization-problem")
def get_optimization_problem(pid: str, gid: str, user=Depends(project_user)):
    g = qone("SELECT id FROM flowsheet_graphs WHERE id=%s AND project_id=%s", (gid, pid))
    if not g:
        raise HTTPException(404, "Flowsheet non trouvé")
    graph = _load_graph_from_db(gid)
    return build_optimization_problem(graph)


@router.post("/api/v1/projects/{pid}/flowsheet-graphs/{gid}/run")
def run_simulation_rest(pid: str, gid: str, user=Depends(project_user)):
    graph = _load_graph_from_db(gid)
    ctx = _get_project_context(pid)
    result = _engine.run(graph, ctx)
    run_id = str(uuid.uuid4())
    execute(
        "INSERT INTO flowsheet_runs (id, graph_id, status, iterations, kpis) "
        "VALUES (%s, %s, %s, %s, %s)",
        (run_id, gid,
         "converged" if result.converged else "failed",
         result.iterations, json.dumps(result.kpis)),
    )
    return {
        "run_id": run_id,
        "converged": result.converged,
        "iterations": result.iterations,
        "kpis": result.kpis,
    }


# ── WebSocket ─────────────────────────────────────────────────────────────────

@router.websocket("/api/v1/projects/{pid}/flowsheet/ws")
async def flowsheet_ws(ws: WebSocket, pid: str, token: str = Query(...)):
    user = await ws_authenticate(ws, token)
    if not user:
        return
    ok = await ws_check_project(ws, pid)
    if not ok:
        return

    await ws.accept()
    graph_id: str | None = None
    graph_state: FlowsheetGraph | None = None
    _ai_task: asyncio.Task | None = None

    async def _send(msg: dict):
        await ws.send_json(msg)

    async def _run_ai_analysis_after_cooldown():
        await asyncio.sleep(_advisor.cooldown_s)
        if graph_state and not _advisor.is_in_cooldown():
            ctx = _get_project_context(pid)
            result = _engine.run(graph_state, ctx)
            summary = {"nodes": len(graph_state.nodes), "edges": len(graph_state.edges)}
            context = _advisor.build_context(summary, result.kpis, {})
            observations = await _advisor.analyze(context)
            for obs in observations:
                await _send({"type": "ai_observation", "severity": obs.severity,
                             "message": obs.message, "action": obs.action})

    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_json(), timeout=60)
            except asyncio.TimeoutError:
                await _send({"type": "ping"})
                continue

            msg_type = data.get("type")

            if msg_type == "load_graph" and data.get("graph_id"):
                graph_id = data["graph_id"]
                graph_state = _load_graph_from_db(graph_id)
                topo = TopologyAnalyzer(graph_state).analyze()
                await _send({
                    "type": "topology_update",
                    "loops_detected": topo.loops_detected,
                    "tear_streams": [e.id for e in topo.tear_streams],
                    "execution_order": [n.id for n in topo.execution_order],
                })

            elif msg_type == "add_node" and graph_id:
                node_id = str(uuid.uuid4())
                op_code = data.get("op_code", "FEED")
                pos = data.get("position", {})
                execute(
                    "INSERT INTO fg_nodes (id, graph_id, op_code, position_x, position_y) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (node_id, graph_id, op_code,
                     float(pos.get("x", 0)), float(pos.get("y", 0))),
                )
                graph_state = _load_graph_from_db(graph_id)
                await _send({"type": "node_added", "node_id": node_id, "op_code": op_code})

            elif msg_type == "remove_node" and graph_id:
                execute("DELETE FROM fg_nodes WHERE id=%s AND graph_id=%s",
                        (data.get("node_id"), graph_id))
                graph_state = _load_graph_from_db(graph_id)
                await _send({"type": "node_removed", "node_id": data.get("node_id")})

            elif msg_type == "patch_node" and graph_id:
                params = data.get("params", {})
                execute("UPDATE fg_nodes SET params=%s WHERE id=%s AND graph_id=%s",
                        (json.dumps(params), data.get("node_id"), graph_id))
                graph_state = _load_graph_from_db(graph_id)
                # Auto-simulate après patch (frontend a déjà attendu le debounce 400ms)
                ctx = _get_project_context(pid)
                result = _engine.run(graph_state, ctx)
                for edge_id, stream in result.stream_results.items():
                    await _send({
                        "type": "stream_update", "edge_id": edge_id,
                        "iteration": result.iterations,
                        "solids_tph": round(stream.solids_tph, 2),
                        "au_g_t": round(stream.au_g_t, 4),
                        "au_recovery_pct": round(stream.au_recovery_pct, 2),
                        "p80_um": round(stream.p80_um, 1),
                        "energy_kwh_t": round(stream.energy_kwh_t, 3),
                    })
                await _send({"type": "sim_complete", "kpis": result.kpis})

            elif msg_type == "add_edge" and graph_id:
                edge_id = str(uuid.uuid4())
                execute(
                    "INSERT INTO fg_edges (id, graph_id, source_node, target_node, "
                    "port_source, port_target) VALUES (%s, %s, %s, %s, %s, %s)",
                    (edge_id, graph_id, data.get("source_node"),
                     data.get("target_node"),
                     data.get("port_source", "out"), data.get("port_target", "in")),
                )
                graph_state = _load_graph_from_db(graph_id)
                topo = TopologyAnalyzer(graph_state).analyze()
                await _send({"type": "topology_update",
                             "loops_detected": topo.loops_detected,
                             "tear_streams": [e.id for e in topo.tear_streams],
                             "execution_order": [n.id for n in topo.execution_order]})

            elif msg_type == "remove_edge" and graph_id:
                execute("DELETE FROM fg_edges WHERE id=%s AND graph_id=%s",
                        (data.get("edge_id"), graph_id))
                graph_state = _load_graph_from_db(graph_id)

            elif msg_type == "run_simulation" and graph_state:
                ctx = _get_project_context(pid)
                result = _engine.run(graph_state, ctx)
                for edge_id, stream in result.stream_results.items():
                    await _send({
                        "type": "stream_update",
                        "edge_id": edge_id,
                        "iteration": result.iterations,
                        "solids_tph": round(stream.solids_tph, 2),
                        "au_g_t": round(stream.au_g_t, 4),
                        "au_recovery_pct": round(stream.au_recovery_pct, 2),
                        "p80_um": round(stream.p80_um, 1),
                        "energy_kwh_t": round(stream.energy_kwh_t, 3),
                    })
                await _send({"type": "sim_complete", "kpis": result.kpis})
                if _ai_task and not _ai_task.done():
                    _ai_task.cancel()
                _ai_task = asyncio.create_task(_run_ai_analysis_after_cooldown())

            elif msg_type == "load_starter" and graph_id:
                starter = qone("SELECT graph_json FROM flowsheet_starters WHERE code=%s",
                               (data.get("starter_code"),))
                if starter:
                    await _send({"type": "starter_loaded",
                                 "graph_json": starter["graph_json"]})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected pid=%s", pid)
    except Exception as exc:
        logger.error("WebSocket error pid=%s: %s", pid, exc)
        try:
            await _send({"type": "error", "code": "SERVER_ERROR", "message": str(exc)})
        except Exception:
            pass
    finally:
        if _ai_task and not _ai_task.done():
            _ai_task.cancel()
