"""Layer 1 — circuit template loader (no DB)."""
from __future__ import annotations

import pytest


def test_load_template_returns_parsed_dict():
    from services.circuit_templates import load_template
    t = load_template("cil_conventional")
    assert t["key"] == "cil_conventional"
    assert isinstance(t["equipment"], list) and len(t["equipment"]) >= 5
    assert "default_factors" in t
    assert 0 < t["default_factors"]["indirect_pct"] < 1


def test_load_template_unknown_key_raises():
    from services.circuit_templates import load_template
    with pytest.raises(ValueError, match="Unknown circuit template"):
        load_template("definitely_not_a_real_circuit")


def test_list_templates_returns_all_yaml_files():
    from services.circuit_templates import list_templates
    items = list_templates()
    keys = {item["key"] for item in items}
    # All 11 spec-defined circuits must be present
    assert keys >= {
        "cil_conventional", "cip_conventional", "sabc", "sag_ball",
        "hpgr_ball", "three_stage_crush_ball", "heap_leach",
        "gravity_flotation_cil", "pox_refractory", "biox_refractory",
        "roaster_refractory",
    }
    for item in items:
        assert "label" in item and item["label"]
