"""Canonical hash of a flowsheet (blocks + connections).

Used for compilation deduplication: two flowsheets semantically identical
(same topology, same op params) produce the same hash regardless of list
ordering, key ordering, or whitespace in the source JSON.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical_sort(obj: Any) -> Any:
    """Recursively sort dict keys and sort lists of dicts by stable identity keys.

    - dict → sorted by key
    - list of dicts with 'id' → sorted by id (used for blocks)
    - list of dicts with 'from' AND 'to' → sorted by (from, to) (used for connections)
    - list of primitives or mixed → left in original order
    - scalars → unchanged
    """
    if isinstance(obj, dict):
        return {k: _canonical_sort(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, list):
        if obj and all(isinstance(item, dict) and "id" in item for item in obj):
            return [_canonical_sort(item) for item in sorted(obj, key=lambda x: str(x["id"]))]
        if obj and all(isinstance(item, dict) and "from" in item and "to" in item for item in obj):
            return [_canonical_sort(item) for item in sorted(obj, key=lambda x: (str(x["from"]), str(x["to"])))]
        return [_canonical_sort(item) for item in obj]
    return obj


def compute_blocks_hash(blocks: list[dict], connections: list[dict]) -> str:
    """Compute SHA-256 hex of canonical JSON of (blocks, connections).

    Returns a 64-char hex string.
    """
    canonical = {
        "blocks": _canonical_sort(blocks),
        "connections": _canonical_sort(connections),
    }
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
