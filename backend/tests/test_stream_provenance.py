"""Unit tests for stream provenance — Lot A traceability (no DB needed)."""

import pytest

pytestmark = pytest.mark.no_db


def get_fn():
    try:
        from backend.engines.stream_provenance import stream_source_basis
    except ImportError:
        from engines.stream_provenance import stream_source_basis
    return stream_source_basis


def test_basis_names_producing_node_and_operation():
    fn = get_fn()
    out = fn(
        source_node_label="Lixiviation CIL",
        source_node_op_code="CIL",
        input_stream_labels=["Alimentation broyée", "Solution NaCN"],
    )
    assert "Lixiviation CIL" in out["source_basis"]
    assert "CIL" in out["source_basis"]
    assert "Alimentation broyée" in out["source_basis"]
    # refs are machine-readable provenance tokens
    assert "op:CIL" in out["source_refs"]
    assert any(r.startswith("node:") for r in out["source_refs"])
    assert "stream:Alimentation broyée" in out["source_refs"]


def test_feed_stream_without_source_node_is_external():
    """A stream with no producing node is an external plant feed, not a crash."""
    fn = get_fn()
    out = fn(source_node_label=None, source_node_op_code=None, input_stream_labels=[])
    assert out["source_basis"]  # non-empty human string
    assert "aliment" in out["source_basis"].lower() or "externe" in out["source_basis"].lower()
    assert out["source_refs"] == []


def test_node_without_inputs_states_no_upstream():
    fn = get_fn()
    out = fn(source_node_label="Concassage primaire", source_node_op_code="CRUSH", input_stream_labels=[])
    assert "Concassage primaire" in out["source_basis"]
    assert "op:CRUSH" in out["source_refs"]
    # no input streams → no stream: refs
    assert not any(r.startswith("stream:") for r in out["source_refs"])


def test_result_is_json_safe():
    fn = get_fn()
    out = fn(source_node_label="X", source_node_op_code="Y", input_stream_labels=["a"])
    assert isinstance(out["source_basis"], str)
    assert isinstance(out["source_refs"], list)
    assert all(isinstance(r, str) for r in out["source_refs"])
