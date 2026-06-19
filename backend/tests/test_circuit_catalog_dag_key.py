"""Tests for catalog→`dag_key` propagation (Chunk 1.5.B).

The `_c(...)` helper in `backend/engines/circuit_catalog.py` learns an
optional `dag_key=` argument. A small set of catalog entries — those that
correspond to a node or input in `dc_dag_registry.yaml` — carry an explicit
`dag_key` so that `_generate_default_criteria` and the cascade engine can map
rows directly without deriving keys from `ref_number`.

Catalog entries with no DAG correspondence (descriptive parameters such as
equipment count, dimensions, motor type, etc.) leave `dag_key=None` — these
tests assert that some such entries remain unmapped, on purpose.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines.circuit_catalog import CATALOG, _c
except ImportError:  # pragma: no cover - direct script imports
    from engines.circuit_catalog import CATALOG, _c


def _entries(op_code: str) -> list[dict]:
    """Return the default_criteria list for an op_code, or [] if missing."""
    for entry in CATALOG:
        if entry["op_code"] == op_code:
            return entry["default_criteria"]
    return []


def _by_item(op_code: str, substring: str) -> dict | None:
    """Find the first criterion whose item contains `substring` (case-insensitive)."""
    s = substring.lower()
    for crit in _entries(op_code):
        if s in (crit.get("item") or "").lower():
            return crit
    return None


# ---------------------------------------------------------------------------
# Helper signature
# ---------------------------------------------------------------------------

def test_c_helper_accepts_dag_key():
    """`_c(...)` accepts an optional `dag_key` keyword argument."""
    crit = _c(
        "99", "Test section", "Test item", "-",
        1, 1, 1, 1, [], source="X", dag_key="test_dag_node",
    )
    assert crit["dag_key"] == "test_dag_node"


def test_c_helper_dag_key_default_none():
    """When `dag_key` is omitted the field is present but None."""
    crit = _c(
        "99", "Test section", "Test item", "-",
        1, 1, 1, 1, [], source="X",
    )
    assert "dag_key" in crit
    assert crit["dag_key"] is None


# ---------------------------------------------------------------------------
# Specific mappings called out in the implementation plan
# ---------------------------------------------------------------------------

def test_sag_mill_power_has_dag_key():
    """SAG Mill installed power → sag_power_kw."""
    crit = _by_item("SAG_MILL", "PUISSANCE INSTALLÉE SAG")
    assert crit is not None, "Catalog must have a SAG_MILL installed power entry"
    assert crit.get("dag_key") == "sag_power_kw"


def test_sag_mill_f80_has_dag_key():
    """SAG F80 alimentation → sag_f80_mm."""
    crit = _by_item("SAG_MILL", "F80 alim SAG")
    assert crit is not None
    assert crit.get("dag_key") == "sag_f80_mm"


def test_ball_mill_power_has_dag_key():
    """Ball Mill installed power → bm_power_kw."""
    crit = _by_item("BALL_MILL", "PUISSANCE INSTALLÉE total moteur")
    assert crit is not None
    assert crit.get("dag_key") == "bm_power_kw"


def test_hydrocyclone_circ_load_has_dag_key():
    """Hydrocyclone circulating load → bm_circ_load_pct (input)."""
    crit = _by_item("HYDROCYCLONE", "Charge circulante CL")
    assert crit is not None
    assert crit.get("dag_key") == "bm_circ_load_pct"


def test_thickener_unit_area_has_dag_key():
    """Thickener unit area → thickener_area_m2."""
    crit = _by_item("EPAISSISSEUR", "Surface unitaire requise")
    assert crit is not None
    assert crit.get("dag_key") == "thickener_area_m2"


# ---------------------------------------------------------------------------
# Negative coverage: descriptive params stay unmapped
# ---------------------------------------------------------------------------

def test_descriptive_entries_have_no_dag_key():
    """Equipment count / motor type / model entries don't map to the DAG."""
    # Pick a few entries that are clearly descriptive
    crit_motor_config = _by_item("BALL_MILL", "Configuration moteur")
    crit_kc_model = _by_item("GRAVITE_KNELSON", "Modèle")
    crit_bm_type = _by_item("BALL_MILL", "Type broyeur")
    for crit in (crit_motor_config, crit_kc_model, crit_bm_type):
        if crit is not None:
            assert crit.get("dag_key") is None, (
                f"Descriptive criterion {crit['item']!r} should not be mapped "
                "to a DAG key"
            )


# ---------------------------------------------------------------------------
# Sanity: the catalog as a whole sets dag_key on a non-trivial number of rows
# ---------------------------------------------------------------------------

def test_catalog_has_at_least_a_dozen_dag_key_mappings():
    """At least 12 catalog entries map to DAG keys (sanity check)."""
    mapped = []
    for entry in CATALOG:
        for crit in entry.get("default_criteria", []):
            if crit.get("dag_key"):
                mapped.append((entry["op_code"], crit["item"], crit["dag_key"]))
    assert len(mapped) >= 12, (
        f"Expected at least 12 dag_key mappings, found {len(mapped)}: {mapped[:5]}…"
    )


def test_no_duplicate_dag_keys_within_op():
    """A given dag_key shouldn't appear twice on the same op_code's criteria.

    Cross-op duplication is allowed (e.g. `target_tph` may appear on multiple
    op_codes' "Débit alimentation" rows) — those are descriptive copies of
    the same upstream value.
    """
    for entry in CATALOG:
        seen: dict[str, str] = {}
        for crit in entry.get("default_criteria", []):
            k = crit.get("dag_key")
            if k is None:
                continue
            assert k not in seen, (
                f"{entry['op_code']}: duplicate dag_key {k!r} "
                f"(items: {seen[k]!r} and {crit['item']!r})"
            )
            seen[k] = crit["item"]
