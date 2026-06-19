"""
Stream provenance — Lot A traceability.

Single source of truth for the human-readable "source basis" of a simulated
process stream in the v2 flowsheet graph engine. Each stream is produced by a
graph node (unit operation) from that node's input streams; this module turns
that graph context into an auditable provenance string + machine-readable refs,
surfaced in the UI (StreamTable tooltip) and reusable by exports / NI 43-101.

Pure functions only — no DB, no app imports — so it is unit-testable in isolation.
"""

from __future__ import annotations


def stream_source_basis(
    *,
    source_node_label: str | None,
    source_node_op_code: str | None,
    input_stream_labels: list[str] | None = None,
) -> dict:
    """Describe how a process stream was produced.

    Args:
        source_node_label: Label of the node that emitted the stream (None for an
            external plant feed with no upstream node).
        source_node_op_code: Operation code of that node (e.g. "CIL", "CRUSH").
        input_stream_labels: Labels of the streams feeding the producing node.

    Returns:
        {"source_basis": str, "source_refs": list[str]} — a human string and
        machine-readable provenance tokens (node:/op:/stream:).
    """
    inputs = [str(s) for s in (input_stream_labels or []) if s]

    if not source_node_label:
        return {
            "source_basis": "Flux d'alimentation externe — entrée du procédé, sans nœud amont.",
            "source_refs": [],
        }

    op = (source_node_op_code or "").strip()
    op_suffix = f" (opération {op})" if op else ""
    if inputs:
        basis = f"Calculé par {source_node_label}{op_suffix} à partir de : {', '.join(inputs)}."
    else:
        basis = f"Calculé par {source_node_label}{op_suffix} — sans flux amont."

    refs: list[str] = [f"node:{source_node_label}"]
    if op:
        refs.append(f"op:{op}")
    refs.extend(f"stream:{s}" for s in inputs)

    return {"source_basis": basis, "source_refs": refs}
