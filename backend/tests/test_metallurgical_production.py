"""annual_gold_oz must follow the same recovery path as process metallurgy."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.helpers import compute_annual_gold_oz, compute_annual_t
    from backend.engines.leaching import annual_gold_oz as leaching_annual_oz
    from backend.engines import mass_balance_engine as mb
except ImportError:
    from helpers import compute_annual_gold_oz, compute_annual_t
    from engines.leaching import annual_gold_oz as leaching_annual_oz
    from engines import mass_balance_engine as mb


def test_compute_annual_gold_oz_matches_leaching_engine():
    tph, h, avail, grade, rec = 1500.0, 22.1, 92.0, 1.5, 88.5
    assert compute_annual_gold_oz(tph, h, avail, grade, rec) == pytest.approx(
        leaching_annual_oz(tph, h, avail, grade, rec),
    )


def test_compute_annual_gold_oz_from_recovery_pct_not_fraction():
    tph, h, avail, grade = 1000.0, 22.0, 91.0, 2.0
    as_pct = compute_annual_gold_oz(tph, h, avail, grade, 90.0)
    as_frac = compute_annual_gold_oz(tph, h, avail, grade, 0.9)
    assert as_pct == pytest.approx(as_frac)


def test_mass_balance_plant_summary_uses_plant_feed_and_leach_recovery():
    pp = {"target_tph": 1596.0, "ore_sg": 2.75, "gold_grade": 1.5, "availability": 92.0}
    dc = {
        "plant_h_per_d": 22.1,
        "flot_mass_pull_pct": 8.0,
        "flot_au_recovery_pct": 92.0,
        "scav_mass_pull_pct": 3.0,
        "scav_au_recovery_pct": 30.0,
        "cil_recovery_pct": 94.0,
        "nacn_consumption_kg_t": 0.5,
        "cao_consumption_kg_t": 1.5,
        "cil_gland_m3h": 1.2,
    }
    carry = {
        "grind_product_tph": 420.0,
        "grind_pct_sol": 35.0,
        "grind_product_au": 2.4,
        "au_gt": 2.4,
    }
    mb._gen_flotation(pp, dc, carry)
    mb._gen_cil(pp, dc, carry)

    plant_feed_au_g_h = pp["target_tph"] * pp["gold_grade"]
    expected_rec = carry["leach_au_recovered_g_h"] / plant_feed_au_g_h * 100.0
    expected_oz = compute_annual_gold_oz(
        pp["target_tph"], dc["plant_h_per_d"], pp["availability"],
        pp["gold_grade"], expected_rec,
    )

    assert carry["leach_au_recovered_g_h"] > 0
    assert expected_rec > 0
    assert expected_oz > 0
    assert expected_rec < 100.0
