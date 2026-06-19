"""
Detect and classify closed-circuit recirculation segments for process_simulator.

Supports grinding loops (SAG/Ball/Rod + hydrocyclone) and flotation banks
(rougher + scavenger). Pure detection — simulation stays in process_simulator.
"""
from __future__ import annotations

from typing import Any, Optional

try:
    from .op_model_registry import resolve_op_model
except ImportError:
    from engines.op_model_registry import resolve_op_model

# Model-name sequences → loop type
_SEQUENCE_PATTERNS: list[dict[str, Any]] = [
    {
        "type": "sag_ball_cyclone",
        "models": ("sag_milling", "ball_milling", "classification"),
        "length": 3,
    },
    {
        "type": "mill_classifier",
        "models": ("ball_milling", "classification"),
        "length": 2,
        "mill_models": ("ball_milling", "ball_milling"),
    },
    {
        "type": "mill_classifier",
        "models": ("ball_milling", "classification"),
        "length": 2,
        "mill_models": ("rod_milling", "ball_milling"),
        "rod_mill_op": "ROD_MILL",
    },
    {
        "type": "flotation_bank",
        "models": ("flotation", "flotation"),
        "length": 2,
        "requires_scavenger": True,
    },
]


def _op_models(operations: list[dict]) -> list[Optional[str]]:
    return [resolve_op_model(o.get("op_code", "")) for o in operations]


def _is_flotation_scavenger_pair(op_a: str, op_b: str) -> bool:
    a, b = (op_a or "").upper(), (op_b or "").upper()
    if "FLOT" not in a or "FLOT" not in b:
        return False
    return ("ROUGH" in a or "PRIMARY" in a) and "SCAV" in b


def detect_recirculation_segments(operations: list[dict]) -> list[dict[str, Any]]:
    """
    Return non-overlapping recirculation segments in template sort order.

    Each segment: {type, start, end, op_codes, models}
    """
    if not operations:
        return []
    models = _op_models(operations)
    segments: list[dict[str, Any]] = []
    i = 0
    n = len(operations)

    while i < n:
        matched = False

        # SAG + ball + cyclone
        if i + 2 < n and tuple(models[i : i + 3]) == ("sag_milling", "ball_milling", "classification"):
            segments.append(_segment_dict(operations, i, i + 3, "sag_ball_cyclone", models[i : i + 3]))
            i += 3
            matched = True
            continue

        # Ball/Rod + cyclone
        if (
            i + 1 < n
            and models[i] in ("ball_milling",)
            and models[i + 1] == "classification"
        ):
            segments.append(_segment_dict(
                operations, i, i + 2, "mill_classifier", models[i : i + 2],
                mill_model=models[i],
            ))
            i += 2
            matched = True
            continue

        if (
            i + 1 < n
            and operations[i].get("op_code") == "ROD_MILL"
            and models[i + 1] == "classification"
        ):
            segments.append(_segment_dict(
                operations, i, i + 2, "mill_classifier", models[i : i + 2],
                mill_model="ball_milling",
            ))
            i += 2
            matched = True
            continue

        # Flotation rougher + scavenger
        if i + 1 < n and _is_flotation_scavenger_pair(
            operations[i].get("op_code", ""),
            operations[i + 1].get("op_code", ""),
        ):
            segments.append(_segment_dict(
                operations, i, i + 2, "flotation_bank", models[i : i + 2],
            ))
            i += 2
            matched = True
            continue

        if not matched:
            i += 1

    return segments


def _segment_dict(
    operations: list[dict],
    start: int,
    end: int,
    seg_type: str,
    model_slice: tuple,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "type": seg_type,
        "start": start,
        "end": end,
        "source": "sequence",
        "op_codes": [operations[j]["op_code"] for j in range(start, end)],
        "models": list(model_slice),
        **extra,
    }


def segment_covering_index(segments: list[dict], index: int) -> Optional[dict]:
    for seg in segments:
        if seg["start"] <= index < seg["end"]:
            return seg
    return None


def find_graph_cycles(
    blocks: list[dict],
    connections: list[dict],
) -> list[list[str]]:
    """Return simple cycles as lists of block ids (Johnson-lite via DFS)."""
    if not blocks or not connections:
        return []
    nodes = [str(b["id"]) for b in blocks if b.get("id")]
    out_adj: dict[str, list[str]] = {n: [] for n in nodes}
    for c in connections:
        src, dst = str(c.get("from", "")), str(c.get("to", ""))
        if src in out_adj and dst in out_adj:
            out_adj[src].append(dst)

    cycles: list[list[str]] = []
    path: list[str] = []
    on_path: set[str] = set()

    def dfs(u: str):
        on_path.add(u)
        path.append(u)
        for v in out_adj.get(u, []):
            if v in on_path:
                idx = path.index(v)
                cycle = path[idx:]
                if 2 <= len(cycle) <= 8:
                    cycles.append(list(cycle))
            elif v not in on_path:
                dfs(v)
        path.pop()
        on_path.discard(u)

    for n in nodes:
        if n not in on_path:
            dfs(n)

    # Deduplicate by frozenset
    seen: set[frozenset] = set()
    unique: list[list[str]] = []
    for c in cycles:
        key = frozenset(c)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique[:10]


def cycles_to_op_codes(cycles: list[list[str]], block_by_id: dict[str, dict]) -> list[list[str]]:
    out: list[list[str]] = []
    for cycle in cycles:
        ops = []
        for bid in cycle:
            b = block_by_id.get(bid) or {}
            op = b.get("op_code")
            if op and op not in ("FEED", "PRODUCT"):
                ops.append(str(op))
        if len(ops) >= 2:
            out.append(ops)
    return out
