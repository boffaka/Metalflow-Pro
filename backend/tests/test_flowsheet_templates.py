"""Structural tests for the flowsheet templates catalogue.

These tests guard against authoring errors in `backend/flowsheet_templates.py`:
  - unique codes
  - exactly one root per template
  - terminal `bullion` or `concentrate` leaf
  - every op_code resolves to the equipment catalog
  - no orphan parent references
  - groupings reachable via `get_templates_grouped`
"""
from __future__ import annotations

import pytest

# Flat imports — backend is on sys.path in this repo (no `backend.` prefix).
from flowsheet_templates import (  # type: ignore[import-not-found]
    TEMPLATES,
    get_template_by_code,
    get_templates_grouped,
)
from equipment_catalog import EQUIPMENT_CATALOG  # type: ignore[import-not-found]


# ─── Catalog-level invariants ──────────────────────────────────────────────


def test_template_count_matches_catalogue():
    # The catalogue currently ships 48 templates across 9 families
    # (the original plan targeted 28 / 8; family "I. Combinaisons modernes"
    # was added later). The exact count is asserted to catch accidental
    # additions/removals during refactors.
    assert len(TEMPLATES) == 48, f"Expected 48 templates, got {len(TEMPLATES)}"


def test_template_codes_are_unique():
    codes = [t["code"] for t in TEMPLATES]
    assert len(codes) == len(set(codes)), \
        f"Duplicate template codes: {sorted(c for c in codes if codes.count(c) > 1)}"


def test_every_template_has_required_keys():
    required = {"code", "name", "family", "description", "nodes"}
    for t in TEMPLATES:
        missing = required - set(t.keys())
        assert not missing, f"{t.get('code', '?')} missing keys: {missing}"


# ─── Per-template structural guards ────────────────────────────────────────


@pytest.mark.parametrize("tpl", TEMPLATES, ids=lambda t: t["code"])
def test_each_template_has_unique_root(tpl):
    roots = [n for n in tpl["nodes"] if n["parent"] is None]
    assert len(roots) == 1, f"{tpl['code']} has {len(roots)} roots"


@pytest.mark.parametrize("tpl", TEMPLATES, ids=lambda t: t["code"])
def test_each_template_terminates_in_bullion_or_concentrate(tpl):
    parents = {c["parent"] for c in tpl["nodes"]}
    leaves = [n for n in tpl["nodes"] if n["id"] not in parents]
    kinds = {l.get("product_kind") for l in leaves}
    assert "bullion" in kinds or "concentrate" in kinds, \
        f"{tpl['code']} has no terminal product (kinds: {kinds})"


@pytest.mark.parametrize("tpl", TEMPLATES, ids=lambda t: t["code"])
def test_each_template_op_code_is_in_catalog(tpl):
    catalog_codes = set(EQUIPMENT_CATALOG.keys())
    for n in tpl["nodes"]:
        assert n["op_code"] in catalog_codes, \
            f"{tpl['code']} references unknown op_code '{n['op_code']}'"


@pytest.mark.parametrize("tpl", TEMPLATES, ids=lambda t: t["code"])
def test_each_template_has_no_orphan_parents(tpl):
    ids = {n["id"] for n in tpl["nodes"]}
    for n in tpl["nodes"]:
        if n["parent"] is not None:
            assert n["parent"] in ids, \
                f"{tpl['code']} node {n['id']} references unknown parent '{n['parent']}'"


@pytest.mark.parametrize("tpl", TEMPLATES, ids=lambda t: t["code"])
def test_each_template_node_ids_are_unique(tpl):
    ids = [n["id"] for n in tpl["nodes"]]
    assert len(ids) == len(set(ids)), \
        f"{tpl['code']} has duplicate node ids: " \
        f"{sorted(i for i in ids if ids.count(i) > 1)}"


# ─── Grouping API ──────────────────────────────────────────────────────────


def test_grouping_returns_all_families():
    groups = get_templates_grouped()
    expected_families = {t["family"] for t in TEMPLATES}
    assert set(groups.keys()) == expected_families, \
        f"Mismatch — groups: {sorted(groups.keys())} vs templates: {sorted(expected_families)}"
    assert len(groups) == 9, f"Expected 9 families, got {sorted(groups.keys())}"


def test_grouping_preserves_total_count():
    groups = get_templates_grouped()
    total = sum(len(items) for items in groups.values())
    assert total == len(TEMPLATES)


def test_get_template_by_code_roundtrip():
    for t in TEMPLATES:
        found = get_template_by_code(t["code"])
        assert found is not None, f"get_template_by_code({t['code']!r}) returned None"
        assert found["code"] == t["code"]
    assert get_template_by_code("__does_not_exist__") is None
