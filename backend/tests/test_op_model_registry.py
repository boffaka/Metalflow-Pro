"""Compile ↔ process_simulator op_code alignment (no DB)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

from engines.circuit_catalog import get_all_op_codes
from engines.op_model_registry import (
    resolve_op_model,
    is_expected_passthrough,
    unmapped_catalog_ops,
)


@pytest.mark.parametrize(
    "op_code,model",
    [
        ("GRAVITE_KNELSON", "gravity"),
        ("GRAVITE_FALCON", "gravity"),
        ("GRAVITY_CONC", "gravity"),
        ("BALL_MILL", "ball_milling"),
        ("FLOTATION_ROUGHER", "flotation"),
        ("CIL", "cil"),
    ],
)
def test_production_op_codes_resolve(op_code, model):
    assert resolve_op_model(op_code) == model


def test_legacy_gravity_concentrator_alias():
    assert resolve_op_model("GRAVITY_CONCENTRATOR") == "gravity"


def test_catalog_gravity_ops_mapped():
    catalog = get_all_op_codes()
    for op in ("GRAVITE_KNELSON", "GRAVITE_FALCON"):
        assert op in catalog
        assert resolve_op_model(op) == "gravity"


def test_unmapped_excludes_reagents_and_utilities():
    catalog = get_all_op_codes()
    missing = unmapped_catalog_ops(catalog)
    assert "REACTIF_PAX" not in missing
    assert "GRAVITE_KNELSON" not in missing
    assert is_expected_passthrough("BIOX")
    assert resolve_op_model("BIOX") == "refractory_pretreatment"
    assert "BIOX" not in missing
