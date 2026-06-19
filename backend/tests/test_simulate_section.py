"""Tests for section simulation functions in process_simulator."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from engines.process_simulator import (
    _check_contiguity,
    _make_stream,
    _resolve_section_feed,
    resolve_op_codes_for_sections,
    simulate_section,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _ops_list():
    """Sample operations list in sort_order."""
    return [
        {"op_code": "GIRATOIRE", "sort_order": 1, "label": "Gyratory", "category": "concassage"},
        {"op_code": "HPGR", "sort_order": 2, "label": "HPGR", "category": "broyage"},
        {"op_code": "BALL_MILL", "sort_order": 3, "label": "Ball Mill", "category": "broyage"},
        {"op_code": "HYDROCYCLONE", "sort_order": 4, "label": "Cyclone", "category": "classification"},
        {"op_code": "GRAVITY_CONC", "sort_order": 5, "label": "Gravity Conc", "category": "concentration"},
        {"op_code": "FLOTATION_ROUGHER", "sort_order": 6, "label": "Rougher Flotation", "category": "concentration"},
        {"op_code": "LEACH_CUVES", "sort_order": 7, "label": "Leach Tanks", "category": "lixiviation"},
        {"op_code": "CIP", "sort_order": 8, "label": "CIP", "category": "lixiviation"},
    ]


# ---------------------------------------------------------------------------
# resolve_op_codes_for_sections
# ---------------------------------------------------------------------------

class TestResolveOpCodesForSections:

    def test_comminution_maps_correctly(self):
        ops = _ops_list()
        result = resolve_op_codes_for_sections(["comminution"], ops)
        assert "GIRATOIRE" in result
        assert "HPGR" in result
        assert "BALL_MILL" in result
        assert "HYDROCYCLONE" in result
        # Should not include gravity or flotation
        assert "GRAVITY_CONC" not in result
        assert "FLOTATION_ROUGHER" not in result

    def test_gravity_disambiguated(self):
        ops = _ops_list()
        result = resolve_op_codes_for_sections(["gravity"], ops)
        assert "GRAVITY_CONC" in result
        assert "FLOTATION_ROUGHER" not in result

    def test_flotation_disambiguated(self):
        ops = _ops_list()
        result = resolve_op_codes_for_sections(["flotation"], ops)
        assert "FLOTATION_ROUGHER" in result
        assert "GRAVITY_CONC" not in result

    def test_multiple_sections(self):
        ops = _ops_list()
        result = resolve_op_codes_for_sections(["gravity", "leaching"], ops)
        assert "GRAVITY_CONC" in result
        assert "LEACH_CUVES" in result
        assert "CIP" in result

    def test_empty_sections(self):
        result = resolve_op_codes_for_sections([], _ops_list())
        assert result == []

    def test_unknown_section(self):
        result = resolve_op_codes_for_sections(["nonexistent"], _ops_list())
        assert result == []


# ---------------------------------------------------------------------------
# _check_contiguity
# ---------------------------------------------------------------------------

class TestCheckContiguity:

    def test_contiguous_no_warnings(self):
        ops = _ops_list()
        selected = [ops[0], ops[1], ops[2]]  # GIRATOIRE, HPGR, BALL_MILL
        warnings = _check_contiguity(selected, ops)
        assert warnings == []

    def test_non_contiguous_warns(self):
        ops = _ops_list()
        # GIRATOIRE (idx 0) and GRAVITY_CONC (idx 4) — gap of 3 ops
        selected = [ops[0], ops[4]]
        warnings = _check_contiguity(selected, ops)
        assert len(warnings) == 1
        assert "Non-contiguous" in warnings[0]
        assert "HPGR" in warnings[0]

    def test_single_op_no_warning(self):
        ops = _ops_list()
        warnings = _check_contiguity([ops[3]], ops)
        assert warnings == []

    def test_empty_selected_no_warning(self):
        warnings = _check_contiguity([], _ops_list())
        assert warnings == []


# ---------------------------------------------------------------------------
# _resolve_section_feed
# ---------------------------------------------------------------------------

class TestResolveSectionFeed:

    def test_user_override_priority(self):
        user_feed = {"solids_tph": 2000.0, "au_g_t": 2.0, "pct_solids": 60.0}
        stream, source = _resolve_section_feed("p1", "t1", 1, user_feed, None)
        assert source == "user_override"
        assert stream["solids_tph"] == 2000.0
        assert stream["au_g_t"] == 2.0
        assert stream["pct_solids"] == 60.0

    def test_project_defaults_fallback(self):
        cursor = MagicMock()
        # First query: simulation_runs_v2 returns nothing
        # Second query: projects returns defaults
        cursor.fetchone = MagicMock(side_effect=[
            None,  # no global run
            {"target_tph": 1200, "gold_grade_g_t": 1.8},  # project defaults
        ])
        stream, source = _resolve_section_feed("p1", "t1", 1, None, cursor)
        assert source == "project_defaults"
        assert stream["solids_tph"] == 1200.0
        assert stream["au_g_t"] == 1.8

    def test_last_global_run(self):
        cursor = MagicMock()
        cursor.fetchone = MagicMock(return_value={
            "results": {"operations": []},
            "product_stream": {"solids_tph": 1400.0, "au_g_t": 1.3, "pct_solids": 45.0, "p80_um": 75.0},
        })
        stream, source = _resolve_section_feed("p1", "t1", 1, None, cursor)
        assert source == "last_global_run"
        assert stream["solids_tph"] == 1400.0

    def test_ultimate_fallback_no_cursor(self):
        stream, source = _resolve_section_feed("p1", "t1", 1, None, None)
        assert source == "project_defaults"
        assert stream["solids_tph"] == 1500.0
        assert stream["au_g_t"] == 1.5


# ---------------------------------------------------------------------------
# simulate_section
# ---------------------------------------------------------------------------

class TestSimulateSection:

    def test_filters_ops_by_op_codes(self):
        ops = _ops_list()
        result = simulate_section(
            pid="p1",
            template_id="t1",
            op_codes=["GIRATOIRE", "HPGR"],
            feed_override={"solids_tph": 1500, "au_g_t": 1.5},
            operations_override=ops,
            cursor=None,
        )
        assert result["mode"] == "section"
        assert result["ops_simulated"] == ["GIRATOIRE", "HPGR"]
        assert result["feed_source"] == "user_override"
        assert len(result["section_results"]) == 2

    def test_operations_override_used(self):
        ops = [
            {"op_code": "CIP", "sort_order": 1, "label": "CIP", "category": "lixiviation"},
        ]
        result = simulate_section(
            pid="p1",
            template_id="t1",
            op_codes=["CIP"],
            feed_override={"solids_tph": 1000, "au_g_t": 2.0},
            operations_override=ops,
            cursor=None,
        )
        assert result["ops_simulated"] == ["CIP"]
        assert len(result["section_results"]) == 1

    def test_no_matching_ops(self):
        ops = _ops_list()
        result = simulate_section(
            pid="p1",
            template_id="t1",
            op_codes=["NONEXISTENT"],
            feed_override={"solids_tph": 1500, "au_g_t": 1.5},
            operations_override=ops,
            cursor=None,
        )
        assert result["ops_simulated"] == []
        assert any("None of the requested" in w for w in result["warnings"])

    def test_empty_operations(self):
        result = simulate_section(
            pid="p1",
            template_id="t1",
            op_codes=["CIP"],
            feed_override={"solids_tph": 1500, "au_g_t": 1.5},
            operations_override=[],
            cursor=None,
        )
        assert result["ops_simulated"] == []
        assert any("No enabled operations" in w for w in result["warnings"])

    def test_run_id_generated(self):
        result = simulate_section(
            pid="p1",
            template_id="t1",
            op_codes=["GIRATOIRE"],
            feed_override={"solids_tph": 1500, "au_g_t": 1.5},
            operations_override=_ops_list(),
            cursor=None,
        )
        assert result["run_id"] is not None
        assert len(result["run_id"]) == 36  # UUID format
