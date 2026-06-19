import pytest

from engines.unit_registry import get_unit, list_units, resolve_op_code, unit_library_payload

pytestmark = pytest.mark.no_db


def test_registry_exposes_cil_schema_and_optimization_vars():
    unit = get_unit("CIL_TANK")
    assert unit.op_code == "CIL_TANK"
    assert unit.ports_in == ["in"]
    assert unit.ports_out == ["out"]
    assert "srt_h" in {p.name for p in unit.params}
    assert any(v.parameter == "srt_h" for v in unit.optimizable)


def test_registry_resolves_legacy_aliases():
    assert resolve_op_code("LEACH_CIL") == "CIL_TANK"
    assert resolve_op_code("concentrate") == "conc"
    assert resolve_op_code("tailings") == "tails"


def test_registry_has_core_gold_flowsheet_units():
    codes = {u.op_code for u in list_units()}
    for code in {
        "FEED",
        "SAG_MILL",
        "BALL_MILL",
        "GRAVITE_KNELSON",
        "FLOTATION_ROUGHER",
        "CIL_TANK",
        "CIP",
        "DETOX_INCO",
        "ELUTION_AARL",
        "FUSION_DORE",
        "TSF",
    }:
        assert code in codes


def test_registry_payload_is_frontend_safe():
    payload = unit_library_payload()
    cil = next(u for u in payload["items"] if u["op_code"] == "CIL_TANK")
    assert cil["unit_type"] == "CIL_TANK"
    assert cil["inlet_ports"] == ["in"]
    assert cil["outlet_ports"] == ["out"]
    assert any(p["name"] == "srt_h" and p["min"] == 4 for p in cil["param_schema"])
