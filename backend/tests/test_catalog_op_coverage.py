"""All 60 catalog unit operations must map to kinetic or passthrough models."""
import pytest

pytestmark = pytest.mark.no_db


def test_catalog_has_60_operations():
    from engines.circuit_catalog import get_all_op_codes
    assert len(get_all_op_codes()) == 60


def test_all_catalog_ops_classified():
    from engines.circuit_catalog import get_all_op_codes
    from engines.op_model_registry import (
        resolve_op_model,
        is_expected_passthrough,
        unmapped_catalog_ops,
        CATALOG_OP_MODEL_MAP,
    )

    codes = get_all_op_codes()
    assert len(CATALOG_OP_MODEL_MAP) >= 60
    assert unmapped_catalog_ops(codes) == []
    kinetic = sum(1 for c in codes if resolve_op_model(c))
    passthrough = sum(1 for c in codes if is_expected_passthrough(c) and not resolve_op_model(c))
    assert kinetic + passthrough == len(codes)


@pytest.mark.parametrize(
    "alias,model",
    [
        ("CRUSH_GYRATORY", "crushing"),
        ("CYCLONE", "classification"),
        ("LEACH_CIL", "cil"),
        ("LEACH_CIP", "cip"),
        ("FLOT_ROUGHER", "flotation"),
    ],
)
def test_flowsheet_aliases_resolve(alias, model):
    from engines.op_model_registry import resolve_op_model
    assert resolve_op_model(alias) == model


def test_catalog_coverage_report_zero_gaps():
    from engines.op_model_registry import catalog_coverage_report
    r = catalog_coverage_report()
    assert r["catalog_count"] == 60
    assert r["gap_count"] == 0
    assert r["kinetic_count"] >= 35
