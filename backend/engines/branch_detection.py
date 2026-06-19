"""Detect named branches in a compiled flowsheet.

A branch is a subgraph that diverges from the main trunk at a node and
reconverges at another node downstream. Examples: gravity side-loop, flotation
cleaner circuit, regrind.

Pure stdlib implementation (no NetworkX dependency).
"""
from __future__ import annotations

from collections import defaultdict


def _build_adjacency(connections: list[dict]) -> tuple[dict, dict]:
    """Return (out_edges, in_edges) as dicts of node → set of neighbors."""
    out_edges: dict[str, set[str]] = defaultdict(set)
    in_edges: dict[str, set[str]] = defaultdict(set)
    for c in connections:
        src, dst = c["from"], c["to"]
        out_edges[src].add(dst)
        in_edges[dst].add(src)
    return dict(out_edges), dict(in_edges)


def _has_cycle(nodes: list[str], out_edges: dict) -> bool:
    """Detect whether the directed graph contains a cycle (DFS-based)."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}

    def visit(u: str) -> bool:
        color[u] = GRAY
        for v in out_edges.get(u, set()):
            if color.get(v, WHITE) == GRAY:
                return True
            if color.get(v, WHITE) == WHITE and visit(v):
                return True
        color[u] = BLACK
        return False

    for n in nodes:
        if color[n] == WHITE and visit(n):
            return True
    return False


def _find_sources_sinks(nodes: list[str], in_edges: dict, out_edges: dict) -> tuple[list[str], list[str]]:
    sources = [n for n in nodes if not in_edges.get(n)]
    sinks = [n for n in nodes if not out_edges.get(n)]
    return sources, sinks


def _find_main_trunk(sources: list[str], sinks: list[str], out_edges: dict, block_by_id: dict) -> list[str]:
    """Find the main trunk path from any source to any sink.

    Heuristic (in priority order):
      1. Prefer paths that avoid blocks with a `branch_label` (explicit user signal).
      2. Tie-break: prefer the shortest simple path (side-loops typically add
         extra stages like regrind/gravity concentrators, so the shortest path
         between source and sink is the trunk).

    Ideal criterion per spec (mass flow) isn't available at compile time —
    topology-only heuristics are the best we can do pre-simulation.

    Returns the node id list of the trunk (from source to sink).
    """
    if not sources or not sinks:
        return []
    sink_set = set(sinks)
    best: list[str] = []
    best_key: tuple[int, int] = (10**9, 10**9)  # (num_branch_labels, path_length)

    def dfs(u: str, path: list[str]):
        nonlocal best, best_key
        if u in sink_set:
            n_labeled = sum(1 for n in path if block_by_id.get(n, {}).get("branch_label"))
            key = (n_labeled, len(path))
            if key < best_key:
                best_key = key
                best = list(path)
            return
        # Sort neighbors for deterministic iteration (set order is not stable)
        for v in sorted(out_edges.get(u, set())):
            if v not in path:  # simple path, no cycles
                path.append(v)
                dfs(v, path)
                path.pop()

    for src in sorted(sources):
        dfs(src, [src])
    return best


def detect_branches(blocks: list[dict], connections: list[dict]) -> dict:
    """Detect branches (divergent-then-reconvergent subgraphs) in a flowsheet.

    Returns:
        {
            "branches": [
                {"name": str, "op_codes": [str], "divergence_node": str,
                 "reconvergence_node": str, "node_ids": [str]}
            ],
            "warning": str | None
        }
    """
    if not blocks:
        return {"branches": [], "warning": None}

    node_ids = [b["id"] for b in blocks]
    block_by_id = {b["id"]: b for b in blocks}
    out_edges, in_edges = _build_adjacency(connections)

    if _has_cycle(node_ids, out_edges):
        return {
            "branches": [],
            "warning": "Cycle détecté — branches non déterministes. Nommage manuel requis."
        }

    sources, sinks = _find_sources_sinks(node_ids, in_edges, out_edges)
    trunk = _find_main_trunk(sources, sinks, out_edges, block_by_id)
    trunk_set = set(trunk)

    # A branch node = any node NOT in trunk that lies on a path between two trunk nodes
    # Find them by traversing from each trunk node that has >1 outgoing edge
    branches: list[dict] = []
    visited: set[str] = set()

    for idx, trunk_node in enumerate(trunk):
        successors = out_edges.get(trunk_node, set())
        off_trunk = [s for s in successors if s not in trunk_set]
        if not off_trunk:
            continue
        # Each off-trunk successor starts a potential branch
        for start in off_trunk:
            if start in visited:
                continue
            branch_nodes = _collect_branch(start, out_edges, trunk_set, visited)
            if not branch_nodes:
                continue
            # Find reconvergence: the first trunk node reached from any branch node
            reconv = _find_reconvergence(branch_nodes, out_edges, trunk_set)
            if reconv is None:
                continue  # dangling: skip (tailings branch or similar)

            name = _branch_name(branch_nodes, block_by_id, len(branches) + 1)
            op_codes = [block_by_id[n].get("op_code", "UNKNOWN") for n in branch_nodes]
            branches.append({
                "name": name,
                "op_codes": op_codes,
                "node_ids": list(branch_nodes),
                "divergence_node": trunk_node,
                "reconvergence_node": reconv,
            })

    return {"branches": branches, "warning": None}


def _collect_branch(start: str, out_edges: dict, trunk_set: set, visited: set) -> list[str]:
    """BFS from `start`, collecting nodes until we hit a trunk node."""
    collected: list[str] = []
    queue = [start]
    while queue:
        n = queue.pop(0)
        if n in visited or n in trunk_set:
            continue
        visited.add(n)
        collected.append(n)
        for v in out_edges.get(n, set()):
            if v not in trunk_set and v not in visited:
                queue.append(v)
    return collected


def _find_reconvergence(branch_nodes: list[str], out_edges: dict, trunk_set: set) -> str | None:
    """Return the first trunk node reached from any of the branch_nodes."""
    for n in branch_nodes:
        for v in out_edges.get(n, set()):
            if v in trunk_set:
                return v
    return None


def _branch_name(branch_nodes: list[str], block_by_id: dict, index: int) -> str:
    """Prefer user-set branch_label; else auto-name by first op category."""
    for n in branch_nodes:
        label = block_by_id.get(n, {}).get("branch_label")
        if label:
            return label
    # Auto-name from first op code
    first_op = block_by_id.get(branch_nodes[0], {}).get("op_code", "branch")
    category = first_op.split("_")[0].lower() if first_op else "branch"
    return f"{category}-{index}"
