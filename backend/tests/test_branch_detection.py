"""Tests for engines.branch_detection."""
from engines.branch_detection import detect_branches


def _ball_mill_only():
    """Linear flowsheet: Feed → BallMill → CIL → Product."""
    blocks = [
        {"id": "feed", "op_code": "FEED"},
        {"id": "bm", "op_code": "BALL_MILL"},
        {"id": "cil", "op_code": "CIL"},
        {"id": "prod", "op_code": "PRODUCT"},
    ]
    connections = [
        {"from": "feed", "to": "bm"},
        {"from": "bm", "to": "cil"},
        {"from": "cil", "to": "prod"},
    ]
    return blocks, connections


def _with_gravity_loop():
    """Flowsheet with a gravity side-branch reconverging before CIL.

    Feed → BallMill ──────────────────────→ CIL → Product
                  └→ Gravity → Regrind → ─┘
    """
    blocks = [
        {"id": "feed", "op_code": "FEED"},
        {"id": "bm", "op_code": "BALL_MILL"},
        {"id": "grav", "op_code": "GRAVITY_CONCENTRATOR", "branch_label": "gravity-primary"},
        {"id": "regrind", "op_code": "REGRIND_MILL", "branch_label": "gravity-primary"},
        {"id": "cil", "op_code": "CIL"},
        {"id": "prod", "op_code": "PRODUCT"},
    ]
    connections = [
        {"from": "feed", "to": "bm"},
        {"from": "bm", "to": "cil"},        # trunk
        {"from": "bm", "to": "grav"},       # branch start
        {"from": "grav", "to": "regrind"},
        {"from": "regrind", "to": "cil"},   # branch reconverges
        {"from": "cil", "to": "prod"},
    ]
    return blocks, connections


def test_detect_branches_linear_has_no_branches():
    blocks, conns = _ball_mill_only()
    result = detect_branches(blocks, conns)
    assert result["branches"] == []
    assert result["warning"] is None


def test_detect_branches_gravity_loop():
    blocks, conns = _with_gravity_loop()
    result = detect_branches(blocks, conns)
    assert len(result["branches"]) == 1
    branch = result["branches"][0]
    assert branch["name"] == "gravity-primary"  # uses branch_label from blocks
    assert set(branch["op_codes"]) == {"GRAVITY_CONCENTRATOR", "REGRIND_MILL"}
    assert branch["divergence_node"] == "bm"
    assert branch["reconvergence_node"] == "cil"


def test_detect_branches_autogenerates_name_when_label_absent():
    blocks, conns = _with_gravity_loop()
    # Strip the branch_label
    for b in blocks:
        b.pop("branch_label", None)
    result = detect_branches(blocks, conns)
    assert len(result["branches"]) == 1
    name = result["branches"][0]["name"]
    # Auto-name format: <category>-<index> where category = first op_code
    # split on '_' lowercased. First off-trunk block is GRAVITY_CONCENTRATOR
    # → category = "gravity", index 1 → "gravity-1".
    assert name == "gravity-1"


def test_detect_branches_ignores_dangling_side_stream():
    """A side stream that never reconverges (e.g., standalone tailings sink)
    is NOT considered a branch — it's just a secondary output of the trunk node.
    """
    blocks = [
        {"id": "feed", "op_code": "FEED"},
        {"id": "bm", "op_code": "BALL_MILL"},
        {"id": "cil", "op_code": "CIL"},
        {"id": "prod", "op_code": "PRODUCT"},
        {"id": "tails", "op_code": "TAILINGS_THICKENER"},  # dangling sink
    ]
    connections = [
        {"from": "feed", "to": "bm"},
        {"from": "bm", "to": "cil"},
        {"from": "cil", "to": "prod"},
        {"from": "cil", "to": "tails"},  # tails is a side sink, never reconverges
    ]
    result = detect_branches(blocks, connections)
    # tails is its own sink — not a branch
    assert result["branches"] == []
    assert result["warning"] is None


def test_detect_branches_with_cycle_returns_warning():
    # Blocks with a true cycle (not a reconvergence) should return warning
    blocks = [
        {"id": "a", "op_code": "BALL_MILL"},
        {"id": "b", "op_code": "CLASSIFIER"},
    ]
    connections = [
        {"from": "a", "to": "b"},
        {"from": "b", "to": "a"},  # cycle
    ]
    result = detect_branches(blocks, connections)
    assert result["warning"] is not None
    assert "cycle" in result["warning"].lower() or "indéterministe" in result["warning"].lower()


def test_detect_branches_empty_flowsheet():
    result = detect_branches([], [])
    assert result["branches"] == []
    assert result["warning"] is None
