"""Tests for MetPlant / SLA plant design & simulation QA advisor."""
from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.no_db

from engines.plant_design_advisor import (  # noqa: E402
    assess_simulation_qa,
    assess_testwork_program,
    normalize_study_level,
    validate_before_simulation,
)


def test_normalize_study_level():
    assert normalize_study_level("PFS") == "pfs"
    assert normalize_study_level("FEASIBILITY") == "fs"
    assert normalize_study_level("SCOPING") == "scoping"


@patch("engines.plant_design_advisor._regclass_exists", return_value=False)
def test_assess_testwork_empty_project(_exists):
    out = assess_testwork_program("pid-1", study_level="pfs")
    assert out["study_level"] == "pfs"
    assert out["score"] < 100
    assert any(g["code"] == "LIMS_A1_LOW" for g in out["gaps"])


@patch("engines.plant_design_advisor._active_grinding_route", return_value="sag_ball")
@patch("engines.plant_design_advisor._bwi_variability", return_value={"n": 6, "cv": 0.35, "avg": 14.0})
@patch("engines.plant_design_advisor._count_samples", return_value=(20, 2))
@patch("engines.plant_design_advisor._count_lims_tables")
@patch("engines.plant_design_advisor._regclass_exists", return_value=True)
def test_assess_testwork_sag_gap(_exists, lims, samples, bwi, route):
    lims.return_value = {"a1": 10, "b1": 3, "d1": 2, "g1": 2, "a3": 0}
    out = assess_testwork_program("pid-1", study_level="fs")
    codes = {g["code"] for g in out["gaps"]}
    assert "COMMINUTION_HIERARCHY_GAP" in codes
    assert "BWI_VARIABILITY_UNMAPPED" in codes


@patch("engines.plant_design_advisor.assess_testwork_program")
@patch("engines.plant_design_advisor._regclass_exists", return_value=False)
def test_simulation_qa_structure(_exists, tw):
    tw.return_value = {
        "score": 40,
        "gaps": [{"severity": "high", "message": "gap"}],
        "lims_counts": {"a1": 1},
    }
    qa = assess_simulation_qa("pid-1", project_status="PFS")
    assert qa["kind"] == "simulation_qa"
    assert "stages" in qa
    assert qa["study_level"] == "pfs"


@patch("engines.plant_design_advisor.assess_simulation_qa")
@patch("engines.plant_design_advisor.qone", return_value={"status": "PFS"})
def test_validate_before_simulation(_qone, qa):
    qa.return_value = {
        "can_run_rigorous": False,
        "blockers": ["flowsheet_missing"],
        "warnings": ["warn1"],
        "testwork": {"score": 30, "lims_counts": {"g1": 0, "b1": 1}},
    }
    w = validate_before_simulation("pid-1", op_codes=["SAG_MILL", "FLOTATION"])
    codes = {x["code"] for x in w}
    assert "GIGO_TESTWORK" in codes
    assert "SAG_COMMINUTION_TESTWORK" in codes
