"""Tests for CIP vs CIL advisor (Stange 1999 basis)."""
import pytest

from engines.cip_cil_advisor import recommend_cip_cil

pytestmark = pytest.mark.no_db


def test_high_organic_carbon_forces_cil():
    r = recommend_cip_cil(c_organic_pct=0.45, leach_recovery_pct=88, has_lims_a1=True)
    assert r["circuit_type"] == "CIL"
    assert r["score"] >= 3


def test_clean_ore_prefers_cip():
    r = recommend_cip_cil(
        c_organic_pct=0.03,
        s_total_pct=0.4,
        as_ppm=80,
        nacn_kg_t=0.5,
        leach_recovery_pct=90,
        has_lims_a1=True,
        has_lims_d1=True,
    )
    assert r["circuit_type"] == "CIP"
    assert r["score"] <= -1


def test_moderate_organic_recommends_cil():
    r = recommend_cip_cil(c_organic_pct=0.2, has_lims_a1=True)
    assert r["circuit_type"] == "CIL"


def test_no_lims_defaults_cil():
    r = recommend_cip_cil(has_lims_a1=False, has_lims_d1=False)
    assert r["circuit_type"] == "CIL"
