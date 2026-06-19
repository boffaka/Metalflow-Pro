"""
Generic closed-circuit solver from compiled flowsheet connections.

Walks simple cycles in the block graph, maps them to template operation indices,
and converges recirculation by iterating around the cycle until mass flow stabilizes.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

try:
    from .op_model_registry import resolve_op_model, is_expected_passthrough
except ImportError:
    from engines.op_model_registry import resolve_op_model, is_expected_passthrough


def build_block_graph(
    blocks: list[dict],
    connections: list[dict],
) -> tuple[dict[str, dict], dict[str, list[str]], dict[str, list[str]]]:
    """Return block_by_id, out_adj, in_adj (block id strings)."""
    block_by_id: dict[str, dict] = {}
    for b in blocks or []:
        bid = b.get("id")
        if bid is not None:
            block_by_id[str(bid)] = b

    nodes = list(block_by_id.keys())
    out_adj: dict[str, list[str]] = {n: [] for n in nodes}
    in_adj: dict[str, list[str]] = {n: [] for n in nodes}

    for c in connections or []:
        src = str(c.get("from", ""))
        dst = str(c.get("to", ""))
        if src in out_adj and dst in out_adj:
            out_adj[src].append(dst)
            in_adj[dst].append(src)

    return block_by_id, out_adj, in_adj


def find_simple_cycles(
    block_by_id: dict[str, dict],
    out_adj: dict[str, list[str]],
    max_cycles: int = 12,
    max_length: int = 8,
) -> list[list[str]]:
    """Enumerate simple directed cycles (block id lists, canonical rotation)."""
    nodes = list(block_by_id.keys())
    cycles: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    def normalize_cycle(path: list[str]) -> tuple[str, ...]:
        if not path:
            return tuple()
        m = min(range(len(path)), key=lambda i: path[i])
        rotated = path[m:] + path[:m]
        return tuple(rotated)

    def dfs(start: str, u: str, path: list[str], on_stack: set[str]):
        if len(cycles) >= max_cycles:
            return
        on_stack.add(u)
        path.append(u)
        for v in out_adj.get(u, []):
            if v == start and len(path) >= 2:
                key = normalize_cycle(path)
                if key not in seen and len(key) <= max_length:
                    seen.add(key)
                    cycles.append(list(key))
            elif v not in on_stack and v not in path:
                if len(path) < max_length:
                    dfs(start, v, path, on_stack)
        path.pop()
        on_stack.discard(u)

    for n in sorted(nodes):
        dfs(n, n, [], set())

    return cycles


def _pick_recirc_edge(
    cycle_ids: list[str],
    connections: list[dict],
    block_by_id: dict[str, dict],
) -> Optional[tuple[str, str]]:
    """Choose the back-edge (from → merge) for convergence."""
    id_set = set(cycle_ids)
    candidates: list[tuple[str, str]] = []
    for c in connections or []:
        f, t = str(c.get("from", "")), str(c.get("to", ""))
        if f in id_set and t in id_set:
            candidates.append((f, t))

    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    def score(edge: tuple[str, str]) -> tuple[int, str]:
        f, _t = edge
        op = (block_by_id.get(f) or {}).get("op_code", "")
        model = resolve_op_model(str(op)) or ""
        priority = {
            "classification": 0,
            "flotation": 1,
            "ball_milling": 2,
            "sag_milling": 3,
        }.get(model, 9)
        return (priority, f)

    return min(candidates, key=score)


def order_cycle_blocks(
    cycle_ids: list[str],
    recirc_edge: tuple[str, str],
    out_adj: dict[str, list[str]],
) -> list[str]:
    """Traverse cycle starting at merge node (recirc edge target)."""
    merge = recirc_edge[1]
    id_set = set(cycle_ids)
    order = [merge]
    visited = {merge}
    current = merge

    while len(order) < len(cycle_ids):
        next_nodes = [n for n in out_adj.get(current, []) if n in id_set and n not in visited]
        if not next_nodes:
            break
        current = next_nodes[0]
        order.append(current)
        visited.add(current)

    for bid in cycle_ids:
        if bid not in visited:
            order.append(bid)

    return order


def _op_indices_for_blocks(
    operations: list[dict],
    ordered_block_ids: list[str],
    block_by_id: dict[str, dict],
) -> list[int]:
    """Map ordered blocks to indices in template operation list."""
    op_code_to_indices: dict[str, list[int]] = defaultdict(list)
    for idx, op in enumerate(operations):
        code = str(op.get("op_code", ""))
        if code:
            op_code_to_indices[code].append(idx)

    used: set[int] = set()
    indices: list[int] = []

    for bid in ordered_block_ids:
        b = block_by_id.get(bid) or {}
        code = str(b.get("op_code", ""))
        if not code or code in ("FEED", "PRODUCT"):
            continue
        pool = [i for i in op_code_to_indices.get(code, []) if i not in used]
        if pool:
            indices.append(pool[0])
            used.add(pool[0])
        elif op_code_to_indices.get(code):
            indices.append(op_code_to_indices[code][0])

    return indices


def _classify_graph_loop(op_codes: list[str], models: list[Optional[str]]) -> str:
    """Specialized type if pattern matches, else graph_cycle."""
    if len(models) >= 3 and tuple(models[:3]) == ("sag_milling", "ball_milling", "classification"):
        return "sag_ball_cyclone"
    if len(models) >= 2 and models[0] in ("ball_milling",) and models[1] == "classification":
        return "mill_classifier"
    if (
        len(models) >= 2
        and models[0] == "flotation"
        and models[1] == "flotation"
        and any("SCAV" in (c or "").upper() for c in op_codes)
    ):
        return "flotation_bank"
    return "graph_cycle"


def detect_graph_recirculation_loops(
    blocks: list[dict],
    connections: list[dict],
    operations: list[dict],
) -> list[dict[str, Any]]:
    """
    Build execution plans for recirculation loops from compiled flowsheet graph.

    Each plan:
      type, op_indices (ordered), entry_index, block_ids, recirc_edge, op_codes, models
    """
    if not blocks or not connections or not operations:
        return []

    block_by_id, out_adj, _in_adj = build_block_graph(blocks, connections)
    raw_cycles = find_simple_cycles(block_by_id, out_adj)

    plans: list[dict[str, Any]] = []
    covered_indices: set[int] = set()

    for cycle_ids in raw_cycles:
        kinetic_ids = [
            bid for bid in cycle_ids
            if not is_expected_passthrough((block_by_id.get(bid) or {}).get("op_code", ""))
            and resolve_op_model((block_by_id.get(bid) or {}).get("op_code", ""))
        ]
        if len(kinetic_ids) < 2:
            continue

        recirc_edge = _pick_recirc_edge(cycle_ids, connections, block_by_id)
        if not recirc_edge:
            continue

        ordered_blocks = order_cycle_blocks(cycle_ids, recirc_edge, out_adj)
        op_indices = _op_indices_for_blocks(operations, ordered_blocks, block_by_id)
        if len(op_indices) < 2:
            continue

        op_codes = [operations[i]["op_code"] for i in op_indices]
        models = [resolve_op_model(c) for c in op_codes]
        loop_type = _classify_graph_loop(op_codes, models)

        entry_index = min(op_indices)
        plan = {
            "type": loop_type,
            "source": "flowsheet_graph",
            "start": entry_index,
            "end": max(op_indices) + 1,
            "entry_index": entry_index,
            "op_indices": op_indices,
            "op_codes": op_codes,
            "models": models,
            "block_ids": ordered_blocks,
            "recirc_edge": {"from": recirc_edge[0], "to": recirc_edge[1]},
            "merge_block_id": recirc_edge[1],
        }
        plans.append(plan)
        covered_indices.update(op_indices)

    plans.sort(key=lambda p: p["entry_index"])
    return _dedupe_overlapping_plans(plans)


def _dedupe_overlapping_plans(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep non-overlapping loops; prefer larger kinetic coverage."""
    if not plans:
        return []
    plans = sorted(plans, key=lambda p: (-len(p.get("op_indices") or []), p["entry_index"]))
    kept: list[dict[str, Any]] = []
    used: set[int] = set()
    for p in plans:
        idxs = set(p.get("op_indices") or [])
        if idxs & used:
            continue
        kept.append(p)
        used |= idxs
    return sorted(kept, key=lambda p: p["entry_index"])


def merge_recirculation_plans(
    sequence_segments: list[dict[str, Any]],
    graph_loops: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """
    Merge sequence-based and graph-based plans.

    Returns (linear_segments_for_legacy, loop_by_entry_index).
    Graph loops take priority on overlapping operation indices.
    """
    graph_covered: set[int] = set()
    for g in graph_loops:
        graph_covered.update(g.get("op_indices") or [])

    loop_by_entry: dict[int, dict[str, Any]] = {
        g["entry_index"]: g for g in graph_loops
    }

    linear: list[dict[str, Any]] = []
    for seg in sequence_segments:
        seg_indices = set(range(seg["start"], seg["end"]))
        if seg_indices & graph_covered:
            continue
        linear.append(seg)

    return linear, loop_by_entry
