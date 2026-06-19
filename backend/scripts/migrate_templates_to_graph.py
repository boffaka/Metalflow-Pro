#!/usr/bin/env python3
"""
migrate_templates_to_graph.py — Convertit les circuit_templates existants
en entrées flowsheet_starters avec positions Sugiyama (networkx).

Usage : python backend/scripts/migrate_templates_to_graph.py
"""
from __future__ import annotations
import json
import sys
import os
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import networkx as nx


def _sugiyama_layout(operations: list[dict]) -> dict[str, tuple[float, float]]:
    """Retourne {orig_id: (x, y)} via layout hiérarchique."""
    G = nx.DiGraph()
    id_map = {str(op["id"]): op for op in operations}
    G.add_nodes_from(id_map.keys())
    for op in operations:
        if op.get("parent_id"):
            src = str(op["parent_id"])
            tgt = str(op["id"])
            if src in id_map:
                G.add_edge(src, tgt)

    pos = {}
    try:
        layers = list(nx.topological_generations(G)) if nx.is_directed_acyclic_graph(G) else [list(G.nodes)]
    except Exception:
        layers = [list(G.nodes)]

    for layer_idx, layer in enumerate(layers):
        for node_idx, nid in enumerate(layer):
            pos[nid] = (node_idx * 200.0, layer_idx * 150.0)
    return pos


def migrate_templates(db_conn=None):
    """
    Migre les circuit_templates vers flowsheet_starters.

    Si db_conn est None, importe depuis db.py (production).
    Accepte un conn injecté pour les tests.
    """
    if db_conn is None:
        try:
            from db import qall, execute
        except ImportError:
            print("ERROR: Cannot import db module. Run from project root.")
            sys.exit(1)
    else:
        # Injection pour tests — les fonctions sont mockées
        qall = db_conn["qall"]
        execute = db_conn["execute"]

    templates = qall(
        "SELECT id, name, family FROM circuit_templates ORDER BY created_at",
    )
    print(f"Templates trouvés : {len(templates)}")

    converted = 0
    for tmpl in templates:
        tid = str(tmpl["id"])
        operations = qall(
            "SELECT id, op_code, label, sort_order, parent_id "
            "FROM circuit_operations WHERE template_id=%s ORDER BY sort_order",
            (tid,),
        )
        if not operations:
            continue

        pos = _sugiyama_layout(operations)

        nodes = []
        for op in operations:
            nid = str(uuid.uuid4())
            x, y = pos.get(str(op["id"]), (0.0, 0.0))
            nodes.append({
                "id": nid,
                "op_code": op["op_code"],
                "label": op.get("label") or op["op_code"],
                "params": {},
                "position_x": x,
                "position_y": y,
                "_orig_id": str(op["id"]),
            })

        orig_to_new = {n["_orig_id"]: n["id"] for n in nodes}
        edges = []
        for op in operations:
            if op.get("parent_id"):
                src_id = orig_to_new.get(str(op["parent_id"]))
                tgt_id = orig_to_new.get(str(op["id"]))
                if src_id and tgt_id:
                    edges.append({
                        "id": str(uuid.uuid4()),
                        "source_node": src_id,
                        "target_node": tgt_id,
                        "port_source": "out",
                        "port_target": "in",
                    })

        # Remove internal _orig_id before storing
        clean_nodes = [{k: v for k, v in n.items() if k != "_orig_id"} for n in nodes]
        graph_json = {"nodes": clean_nodes, "edges": edges}
        code = f"LEGACY_{tid[:8].upper()}"
        family = tmpl.get("family") or "LEGACY"
        name = tmpl.get("name") or code

        execute(
            "INSERT INTO flowsheet_starters (id, code, family, name, graph_json) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (code) DO NOTHING",
            (str(uuid.uuid4()), code, family, name, json.dumps(graph_json)),
        )
        print(f"  ✓ {name} → {code}")
        converted += 1

    print(f"Migration terminée. {converted}/{len(templates)} templates convertis.")
    return converted


if __name__ == "__main__":
    migrate_templates()
