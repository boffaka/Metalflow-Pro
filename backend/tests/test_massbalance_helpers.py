"""
Unit tests for _recalc_derived() in routes/massbalance_v2.py.

Tests:
  - Nominal: typical process values produce correct derived fields
  - Boundary: zero solids, zero water, slurry_m3h=0 edge case, hours_per_day extremes
  - Precision: rounding to declared decimal places
  - Physics: slurry_sg consistent with tph/m3h ratio
"""
from __future__ import annotations

import math
import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword1!")

try:
    from backend.routes.massbalance_v2 import _recalc_derived, DEFAULT_ORE_SG, WATER_SG
except ImportError:
    from routes.massbalance_v2 import _recalc_derived, DEFAULT_ORE_SG, WATER_SG


class TestRecalcDerivedNominal(unittest.TestCase):
    """Typical operating values — PFS-grade gold-mill stream."""

    def setUp(self) -> None:
        # 1 000 t/h solids, 500 t/h water, 66.7% solids, 22 h/day
        self.result = _recalc_derived(
            solids_tph=1000.0,
            water_tph=500.0,
            pct_solids=66.7,
            h_per_d=22.0,
        )

    # ── Output keys ──────────────────────────────────────────────────────────

    def test_all_keys_present(self) -> None:
        expected_keys = {
            "solids_m3h", "water_m3h", "slurry_tph",
            "slurry_m3h", "slurry_sg",
            "solids_tpd", "water_tpd", "slurry_tpd",
        }
        self.assertEqual(set(self.result.keys()), expected_keys)

    # ── Derived values ────────────────────────────────────────────────────────

    def test_slurry_tph_equals_solids_plus_water(self) -> None:
        self.assertAlmostEqual(self.result["slurry_tph"], 1500.0, places=2)

    def test_solids_m3h(self) -> None:
        expected = round(1000.0 / DEFAULT_ORE_SG, 3)
        self.assertAlmostEqual(self.result["solids_m3h"], expected, places=3)

    def test_water_m3h(self) -> None:
        expected = round(500.0 / WATER_SG, 3)
        self.assertAlmostEqual(self.result["water_m3h"], expected, places=3)

    def test_slurry_m3h_equals_solids_plus_water_volumes(self) -> None:
        expected = round(1000.0 / DEFAULT_ORE_SG + 500.0 / WATER_SG, 3)
        self.assertAlmostEqual(self.result["slurry_m3h"], expected, places=3)

    def test_slurry_sg_formula(self) -> None:
        slurry_tph = 1500.0
        slurry_m3h = self.result["slurry_m3h"]
        expected = round(slurry_tph / slurry_m3h, 4)
        self.assertAlmostEqual(self.result["slurry_sg"], expected, places=4)

    def test_solids_tpd(self) -> None:
        self.assertAlmostEqual(self.result["solids_tpd"], round(1000.0 * 22.0, 2), places=2)

    def test_water_tpd(self) -> None:
        self.assertAlmostEqual(self.result["water_tpd"], round(500.0 * 22.0, 2), places=2)

    def test_slurry_tpd(self) -> None:
        self.assertAlmostEqual(self.result["slurry_tpd"], round(1500.0 * 22.0, 2), places=2)


class TestRecalcDerivedBoundary(unittest.TestCase):
    """Edge cases: zero flows, extreme hours, custom ore SG."""

    # ── Zero solids ───────────────────────────────────────────────────────────

    def test_zero_solids_slurry_tph_equals_water(self) -> None:
        r = _recalc_derived(0.0, 200.0, 0.0, 22.0)
        self.assertAlmostEqual(r["slurry_tph"], 200.0, places=3)

    def test_zero_solids_m3h_is_zero(self) -> None:
        r = _recalc_derived(0.0, 200.0, 0.0, 22.0)
        self.assertEqual(r["solids_m3h"], 0.0)

    def test_zero_solids_tpd_is_zero(self) -> None:
        r = _recalc_derived(0.0, 200.0, 0.0, 22.0)
        self.assertEqual(r["solids_tpd"], 0.0)

    # ── Zero water ────────────────────────────────────────────────────────────

    def test_zero_water_slurry_tph_equals_solids(self) -> None:
        r = _recalc_derived(500.0, 0.0, 100.0, 22.0)
        self.assertAlmostEqual(r["slurry_tph"], 500.0, places=3)

    def test_zero_water_m3h_is_zero(self) -> None:
        r = _recalc_derived(500.0, 0.0, 100.0, 22.0)
        self.assertEqual(r["water_m3h"], 0.0)

    def test_zero_water_tpd_is_zero(self) -> None:
        r = _recalc_derived(500.0, 0.0, 100.0, 22.0)
        self.assertEqual(r["water_tpd"], 0.0)

    # ── Both flows zero ───────────────────────────────────────────────────────

    def test_all_zero_flows_returns_zero_sg_fallback(self) -> None:
        r = _recalc_derived(0.0, 0.0, 0.0, 22.0)
        self.assertEqual(r["slurry_tph"], 0.0)
        # slurry_m3h = 0 → SG fallback = 1.0
        self.assertEqual(r["slurry_sg"], 1.0)

    def test_all_zero_tpd_values(self) -> None:
        r = _recalc_derived(0.0, 0.0, 0.0, 24.0)
        for field in ("solids_tpd", "water_tpd", "slurry_tpd"):
            self.assertEqual(r[field], 0.0)

    # ── Hours per day extremes ────────────────────────────────────────────────

    def test_zero_hours_per_day_gives_zero_tpd(self) -> None:
        r = _recalc_derived(1000.0, 500.0, 66.7, 0.0)
        for field in ("solids_tpd", "water_tpd", "slurry_tpd"):
            self.assertEqual(r[field], 0.0)

    def test_24_hours_per_day(self) -> None:
        r = _recalc_derived(1000.0, 500.0, 66.7, 24.0)
        self.assertAlmostEqual(r["solids_tpd"], 24_000.0, places=1)

    def test_fractional_hours(self) -> None:
        r = _recalc_derived(1000.0, 0.0, 100.0, 22.08)
        self.assertAlmostEqual(r["solids_tpd"], round(1000.0 * 22.08, 2), places=2)

    # ── Custom ore SG ─────────────────────────────────────────────────────────

    def test_custom_ore_sg_affects_solids_m3h(self) -> None:
        ore_sg = 3.0
        r = _recalc_derived(300.0, 100.0, 75.0, 22.0, ore_sg=ore_sg)
        self.assertAlmostEqual(r["solids_m3h"], round(300.0 / ore_sg, 3), places=3)

    def test_zero_ore_sg_does_not_raise(self) -> None:
        # ore_sg=0 → solids_m3h = 0 (guarded by `if ore_sg > 0 else 0.0`)
        r = _recalc_derived(500.0, 200.0, 71.4, 22.0, ore_sg=0.0)
        self.assertEqual(r["solids_m3h"], 0.0)

    def test_low_ore_sg_pyrite(self) -> None:
        ore_sg = 4.9  # pyrite-like
        r = _recalc_derived(500.0, 200.0, 71.4, 22.0, ore_sg=ore_sg)
        self.assertAlmostEqual(r["solids_m3h"], round(500.0 / ore_sg, 3), places=3)


class TestRecalcDerivedPrecision(unittest.TestCase):
    """Verify rounding to the declared decimal places."""

    def setUp(self) -> None:
        self.r = _recalc_derived(1517.3, 758.65, 66.7, 22.08)

    def test_solids_m3h_rounded_to_3dp(self) -> None:
        val = self.r["solids_m3h"]
        self.assertEqual(val, round(val, 3))

    def test_slurry_sg_rounded_to_4dp(self) -> None:
        val = self.r["slurry_sg"]
        self.assertEqual(val, round(val, 4))

    def test_solids_tpd_rounded_to_2dp(self) -> None:
        val = self.r["solids_tpd"]
        self.assertEqual(val, round(val, 2))

    def test_slurry_tph_rounded_to_3dp(self) -> None:
        val = self.r["slurry_tph"]
        self.assertEqual(val, round(val, 3))


class TestRecalcDerivedPhysics(unittest.TestCase):
    """Sanity-check physical consistency of computed values."""

    def test_slurry_sg_between_water_and_ore_sg(self) -> None:
        ore_sg = DEFAULT_ORE_SG
        r = _recalc_derived(800.0, 400.0, 66.7, 22.0, ore_sg=ore_sg)
        self.assertGreaterEqual(r["slurry_sg"], WATER_SG)
        self.assertLessEqual(r["slurry_sg"], ore_sg)

    def test_slurry_tpd_equals_solids_plus_water_tpd(self) -> None:
        r = _recalc_derived(1000.0, 333.0, 75.0, 20.0)
        self.assertAlmostEqual(
            r["slurry_tpd"],
            r["solids_tpd"] + r["water_tpd"],
            places=1,
        )

    def test_slurry_tph_equals_solids_plus_water_tph(self) -> None:
        r = _recalc_derived(600.0, 300.0, 66.7, 22.0)
        self.assertAlmostEqual(r["slurry_tph"], 900.0, places=2)

    def test_slurry_m3h_equals_solids_plus_water_m3h(self) -> None:
        r = _recalc_derived(600.0, 300.0, 66.7, 22.0)
        self.assertAlmostEqual(
            r["slurry_m3h"],
            r["solids_m3h"] + r["water_m3h"],
            places=3,
        )

    def test_high_solids_gives_sg_closer_to_ore(self) -> None:
        """Very high solids content → slurry SG approaches ore SG."""
        r_high = _recalc_derived(9900.0, 100.0, 99.0, 22.0)
        r_low = _recalc_derived(100.0, 9900.0, 1.0, 22.0)
        self.assertGreater(r_high["slurry_sg"], r_low["slurry_sg"])

    def test_increasing_hours_scales_tpd_linearly(self) -> None:
        r12 = _recalc_derived(1000.0, 500.0, 66.7, 12.0)
        r24 = _recalc_derived(1000.0, 500.0, 66.7, 24.0)
        ratio = r24["solids_tpd"] / r12["solids_tpd"]
        self.assertAlmostEqual(ratio, 2.0, places=5)


if __name__ == "__main__":
    unittest.main()
