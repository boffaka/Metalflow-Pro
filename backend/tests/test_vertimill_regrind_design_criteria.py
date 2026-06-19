from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines.circuit_catalog import CATALOG
    from backend.engines.dc_cascade import compute_formula, load_dag
except ImportError:  # pragma: no cover
    from engines.circuit_catalog import CATALOG  # type: ignore[no-redef]
    from engines.dc_cascade import compute_formula, load_dag  # type: ignore[no-redef]


def _criteria(op_code: str) -> list[dict]:
    for entry in CATALOG:
        if entry["op_code"] == op_code:
            return entry["default_criteria"]
    raise AssertionError(f"{op_code} missing from catalog")


def test_vertimill_regrind_catalog_is_rich_and_process_complete():
    rows = _criteria("VERTIMILL_REGRIND")
    items = " | ".join((r.get("item") or "") for r in rows).lower()

    assert len(rows) >= 18
    for expected in [
        "regrind circuit feed",
        "feed p80",
        "product p80",
        "signature plot",
        "specific energy",
        "installed power",
        "feed density",
        "media size",
        "media consumption",
        "circulating load",
        "cyclone overflow",
        "water addition",
        "gland seal water",
    ]:
        assert expected in items


def test_vertimill_regrind_rows_have_dag_keys_for_cascade_inputs_and_outputs():
    keyed = {
        (r.get("item") or ""): r.get("dag_key")
        for r in _criteria("VERTIMILL_REGRIND")
        if r.get("dag_key")
    }

    assert "regrind_feed_tph" in keyed.values()
    assert "regrind_feed_p80_um" in keyed.values()
    assert "regrind_product_p80_um" in keyed.values()
    assert "regrind_sig_kwh_t" in keyed.values()
    assert "regrind_specific_energy_kwh_t" in keyed.values()
    assert "regrind_installed_power_kw" in keyed.values()


def test_vertimill_regrind_cascade_uses_morrell_signature_logic():
    energy = compute_formula(
        "regrind_specific_energy",
        {
            "regrind_sig_kwh_t": 7.5,
            "regrind_feed_p80_um": 106,
            "regrind_product_p80_um": 25,
        },
    )
    assert 10.5 < energy < 11.5

    power = compute_formula(
        "regrind_power",
        {
            "regrind_feed_tph": 95,
            "regrind_specific_energy_kwh_t": energy,
            "regrind_mech_efficiency": 94,
            "regrind_install_margin_pct": 15,
        },
    )
    assert 1200 < power < 1400


def test_vertimill_regrind_nodes_are_in_dag():
    dag = load_dag()
    nodes = dag["nodes"]
    assert nodes["regrind_feed_tph"]["formula_ref"] == "regrind_feed"
    assert nodes["regrind_specific_energy_kwh_t"]["formula_ref"] == "regrind_specific_energy"
    assert nodes["regrind_installed_power_kw"]["formula_ref"] == "regrind_power"
