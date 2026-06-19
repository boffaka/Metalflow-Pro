"""Tests for engines.circuit_hash."""
from engines.circuit_hash import compute_blocks_hash


def test_hash_is_deterministic():
    blocks = [{"id": "b1", "op_code": "HPGR", "params": {"power_kw": 800}}]
    connections = [{"from": "b1", "to": "b2"}]
    h1 = compute_blocks_hash(blocks, connections)
    h2 = compute_blocks_hash(blocks, connections)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex length


def test_hash_ignores_key_order():
    blocks_a = [{"id": "b1", "op_code": "HPGR", "params": {"power_kw": 800}}]
    blocks_b = [{"params": {"power_kw": 800}, "op_code": "HPGR", "id": "b1"}]
    connections = [{"from": "b1", "to": "b2"}]
    assert compute_blocks_hash(blocks_a, connections) == compute_blocks_hash(blocks_b, connections)


def test_hash_stable_across_connection_order():
    blocks = [{"id": "b1", "op_code": "HPGR"}, {"id": "b2", "op_code": "BALL_MILL"}]
    conn_a = [{"from": "b1", "to": "b2"}, {"from": "b2", "to": "b3"}]
    conn_b = [{"from": "b2", "to": "b3"}, {"from": "b1", "to": "b2"}]
    assert compute_blocks_hash(blocks, conn_a) == compute_blocks_hash(blocks, conn_b)


def test_hash_changes_with_blocks():
    blocks_a = [{"id": "b1", "op_code": "HPGR"}]
    blocks_b = [{"id": "b1", "op_code": "SAG_MILL"}]
    assert compute_blocks_hash(blocks_a, []) != compute_blocks_hash(blocks_b, [])


def test_hash_changes_with_connections():
    blocks = [{"id": "b1", "op_code": "HPGR"}]
    conn_a = [{"from": "b1", "to": "b2"}]
    conn_b = [{"from": "b1", "to": "b3"}]
    assert compute_blocks_hash(blocks, conn_a) != compute_blocks_hash(blocks, conn_b)


def test_hash_stable_across_list_order_when_ids_differ():
    # If blocks have stable IDs, reordering the list should not change the hash.
    blocks_a = [{"id": "b1", "op_code": "HPGR"}, {"id": "b2", "op_code": "BALL_MILL"}]
    blocks_b = [{"id": "b2", "op_code": "BALL_MILL"}, {"id": "b1", "op_code": "HPGR"}]
    assert compute_blocks_hash(blocks_a, []) == compute_blocks_hash(blocks_b, [])


def test_hash_handles_nested_params():
    blocks = [{"id": "b1", "op_code": "BALL_MILL", "params": {"diameter_m": 6.0, "length_m": 9.0, "nested": {"a": 1, "b": [2, 3]}}}]
    h = compute_blocks_hash(blocks, [])
    assert len(h) == 64


def test_hash_empty_inputs():
    h = compute_blocks_hash([], [])
    assert len(h) == 64
    assert h == compute_blocks_hash([], [])
