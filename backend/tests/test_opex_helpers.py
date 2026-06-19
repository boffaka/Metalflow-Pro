"""
Unit tests for pure-Python helper functions in routes/opex_v2.py:

  _calc_manpower_row(row)          → total_salary, total_cost
  _calc_power_row(row, power_cost, annual_tp)
  _calc_reagent_row(row, annual_tp)

Each function is tested with:
  - Nominal  : realistic production values
  - Boundary : zero inputs, maximum values, edge fractions
  - Error    : None / missing fields (guarded by `or 0`)
  - Math     : cross-checks on formula correctness
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword1!")

try:
    from backend.routes.opex_v2 import (
        _calc_manpower_row,
        _calc_power_row,
        _calc_reagent_row,
        _opex_reagent_summary_bucket,
        SHIFT_HOURS_YEAR,
        OFFICE_HOURS_YEAR,
    )
except ImportError:
    from routes.opex_v2 import (
        _calc_manpower_row,
        _calc_power_row,
        _calc_reagent_row,
        _opex_reagent_summary_bucket,
        SHIFT_HOURS_YEAR,
        OFFICE_HOURS_YEAR,
    )


# =============================================================================
# _calc_manpower_row
# =============================================================================

class TestCalcManpowerRow(unittest.TestCase):
    """Tests for annual salary / total cost calculation."""

    # ── Nominal ───────────────────────────────────────────────────────────────

    def test_shift_worker_uses_shift_hours(self) -> None:
        row = {
            "base_salary_hourly": 40.0,
            "schedule": "Shift",
            "num_employees": 1,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        expected_salary = 40.0 * SHIFT_HOURS_YEAR
        self.assertAlmostEqual(result["total_salary"], expected_salary, places=2)

    def test_office_worker_uses_office_hours(self) -> None:
        row = {
            "base_salary_hourly": 50.0,
            "schedule": "Office",
            "num_employees": 1,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        expected_salary = 50.0 * OFFICE_HOURS_YEAR
        self.assertAlmostEqual(result["total_salary"], expected_salary, places=2)

    def test_unrecognised_schedule_falls_back_to_office(self) -> None:
        row = {
            "base_salary_hourly": 40.0,
            "schedule": "Unknown",
            "num_employees": 1,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        expected = 40.0 * OFFICE_HOURS_YEAR
        self.assertAlmostEqual(result["total_salary"], expected, places=2)

    def test_bonus_adds_to_salary(self) -> None:
        row = {
            "base_salary_hourly": 40.0,
            "schedule": "Office",
            "num_employees": 1,
            "bonus_pct": 10.0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        base = 40.0 * OFFICE_HOURS_YEAR
        expected = round(base * 1.10, 2)
        self.assertAlmostEqual(result["total_salary"], expected, places=2)

    def test_benefits_adds_to_salary(self) -> None:
        row = {
            "base_salary_hourly": 40.0,
            "schedule": "Office",
            "num_employees": 1,
            "bonus_pct": 0,
            "benefits_pct": 20.0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        base = 40.0 * OFFICE_HOURS_YEAR
        expected = round(base * 1.20, 2)
        self.assertAlmostEqual(result["total_salary"], expected, places=2)

    def test_overtime_adds_to_salary(self) -> None:
        row = {
            "base_salary_hourly": 40.0,
            "schedule": "Office",
            "num_employees": 1,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 25.0,
        }
        result = _calc_manpower_row(row)
        base = 40.0 * OFFICE_HOURS_YEAR
        expected = round(base * 1.25, 2)
        self.assertAlmostEqual(result["total_salary"], expected, places=2)

    def test_total_cost_multiplied_by_num_employees(self) -> None:
        row = {
            "base_salary_hourly": 50.0,
            "schedule": "Office",
            "num_employees": 4,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        self.assertAlmostEqual(result["total_cost"], result["total_salary"] * 4, places=2)

    def test_combined_loadings(self) -> None:
        """Bonus 5% + benefits 20% + OT 10% all applied to base annual."""
        row = {
            "base_salary_hourly": 60.0,
            "schedule": "Shift",
            "num_employees": 2,
            "bonus_pct": 5.0,
            "benefits_pct": 20.0,
            "overtime_pct": 10.0,
        }
        result = _calc_manpower_row(row)
        base = 60.0 * SHIFT_HOURS_YEAR
        expected_salary = round(base + base * 0.05 + base * 0.20 + base * 0.10, 2)
        self.assertAlmostEqual(result["total_salary"], expected_salary, places=2)
        self.assertAlmostEqual(result["total_cost"], expected_salary * 2, places=2)

    # ── Boundary ──────────────────────────────────────────────────────────────

    def test_zero_hourly_rate(self) -> None:
        row = {
            "base_salary_hourly": 0,
            "schedule": "Shift",
            "num_employees": 5,
            "bonus_pct": 20,
            "benefits_pct": 20,
            "overtime_pct": 10,
        }
        result = _calc_manpower_row(row)
        self.assertEqual(result["total_salary"], 0.0)
        self.assertEqual(result["total_cost"], 0.0)

    def test_zero_employees_falls_back_to_one(self) -> None:
        """num_employees=0 is falsy so the `or 1` guard keeps count at 1."""
        row = {
            "base_salary_hourly": 50.0,
            "schedule": "Office",
            "num_employees": 0,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        # Guard: int(0 or 1) = 1
        self.assertAlmostEqual(result["total_cost"], result["total_salary"], places=2)

    def test_single_employee_total_cost_equals_salary(self) -> None:
        row = {
            "base_salary_hourly": 45.0,
            "schedule": "Office",
            "num_employees": 1,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        self.assertAlmostEqual(result["total_cost"], result["total_salary"], places=2)

    # ── None / missing field guards ───────────────────────────────────────────

    def test_none_hourly_treated_as_zero(self) -> None:
        row = {
            "base_salary_hourly": None,
            "schedule": "Office",
            "num_employees": 1,
            "bonus_pct": 10,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        self.assertEqual(result["total_salary"], 0.0)

    def test_none_percentages_treated_as_zero(self) -> None:
        row = {
            "base_salary_hourly": 40.0,
            "schedule": "Office",
            "num_employees": 1,
            "bonus_pct": None,
            "benefits_pct": None,
            "overtime_pct": None,
        }
        result = _calc_manpower_row(row)
        expected = round(40.0 * OFFICE_HOURS_YEAR, 2)
        self.assertAlmostEqual(result["total_salary"], expected, places=2)

    def test_none_num_employees_defaults_to_1(self) -> None:
        row = {
            "base_salary_hourly": 40.0,
            "schedule": "Office",
            "num_employees": None,
            "bonus_pct": 0,
            "benefits_pct": 0,
            "overtime_pct": 0,
        }
        result = _calc_manpower_row(row)
        self.assertAlmostEqual(result["total_cost"], result["total_salary"], places=2)

    # ── Return-type and rounding ──────────────────────────────────────────────

    def test_results_rounded_to_2_decimal_places(self) -> None:
        row = {
            "base_salary_hourly": 33.33,
            "schedule": "Shift",
            "num_employees": 3,
            "bonus_pct": 7.5,
            "benefits_pct": 19.5,
            "overtime_pct": 3.3,
        }
        result = _calc_manpower_row(row)
        self.assertEqual(result["total_salary"], round(result["total_salary"], 2))
        self.assertEqual(result["total_cost"], round(result["total_cost"], 2))


# =============================================================================
# _calc_power_row
# =============================================================================

class TestCalcPowerRow(unittest.TestCase):

    def _base_row(self, **kw):
        return {
            "operating_kw": 1000.0,
            "electrical_efficiency": 0.95,
            "load_factor": 0.85,
            "area_availability": 0.92,
            "hours_per_day": 22.0,
            **kw,
        }

    # ── Nominal ───────────────────────────────────────────────────────────────

    def test_nominal_keys_present(self) -> None:
        result = _calc_power_row(self._base_row(), power_cost=0.09, annual_tp=5_000_000)
        for k in ("hours_per_year", "consumption_kwh_year", "consumption_kwh_mt",
                  "total_cost", "unit_cost_mt"):
            self.assertIn(k, result)

    def test_hours_per_year_formula(self) -> None:
        row = self._base_row(hours_per_day=22.0, area_availability=0.92)
        result = _calc_power_row(row, 0.09, 5_000_000)
        expected_h = round(22.0 * 365 * 0.92, 1)
        self.assertAlmostEqual(result["hours_per_year"], expected_h, places=1)

    def test_consumption_formula(self) -> None:
        row = self._base_row(
            operating_kw=2000.0,
            electrical_efficiency=1.0,
            load_factor=1.0,
            area_availability=1.0,
            hours_per_day=24.0,
        )
        result = _calc_power_row(row, power_cost=0.10, annual_tp=8_000_000)
        expected_h = 24.0 * 365 * 1.0
        expected_kwh = round(2000.0 * 1.0 * 1.0 * expected_h, 0)
        self.assertAlmostEqual(result["consumption_kwh_year"], expected_kwh, places=0)

    def test_total_cost_equals_consumption_times_rate(self) -> None:
        row = self._base_row()
        power_cost = 0.08
        result = _calc_power_row(row, power_cost=power_cost, annual_tp=5_000_000)
        expected = round(result["consumption_kwh_year"] * power_cost, 2)
        self.assertAlmostEqual(result["total_cost"], expected, places=2)

    def test_kwh_mt_formula(self) -> None:
        row = self._base_row()
        annual_tp = 4_000_000
        result = _calc_power_row(row, power_cost=0.09, annual_tp=annual_tp)
        expected = round(result["consumption_kwh_year"] / annual_tp, 2)
        self.assertAlmostEqual(result["consumption_kwh_mt"], expected, places=2)

    def test_unit_cost_mt_formula(self) -> None:
        row = self._base_row()
        annual_tp = 4_000_000
        result = _calc_power_row(row, power_cost=0.09, annual_tp=annual_tp)
        expected = round(result["total_cost"] / annual_tp, 4)
        self.assertAlmostEqual(result["unit_cost_mt"], expected, places=4)

    # ── Boundary / zero ───────────────────────────────────────────────────────

    def test_zero_kw_gives_zero_cost(self) -> None:
        result = _calc_power_row(self._base_row(operating_kw=0), 0.09, 5_000_000)
        self.assertEqual(result["consumption_kwh_year"], 0.0)
        self.assertEqual(result["total_cost"], 0.0)

    def test_zero_annual_tp_gives_zero_per_tonne_metrics(self) -> None:
        result = _calc_power_row(self._base_row(), power_cost=0.09, annual_tp=0)
        self.assertEqual(result["consumption_kwh_mt"], 0)
        self.assertEqual(result["unit_cost_mt"], 0)

    def test_zero_power_cost_gives_zero_total_cost(self) -> None:
        result = _calc_power_row(self._base_row(), power_cost=0.0, annual_tp=5_000_000)
        self.assertEqual(result["total_cost"], 0.0)

    def test_none_kw_treated_as_zero(self) -> None:
        row = self._base_row(operating_kw=None)
        result = _calc_power_row(row, 0.09, 5_000_000)
        self.assertEqual(result["consumption_kwh_year"], 0.0)

    def test_default_efficiency_used_when_none(self) -> None:
        """electrical_efficiency=None → defaults to 0.92."""
        row = self._base_row(electrical_efficiency=None)
        result_default = _calc_power_row(row, 0.09, 5_000_000)
        row_explicit = self._base_row(electrical_efficiency=0.92)
        result_explicit = _calc_power_row(row_explicit, 0.09, 5_000_000)
        self.assertAlmostEqual(
            result_default["consumption_kwh_year"],
            result_explicit["consumption_kwh_year"],
            places=0,
        )

    def test_default_load_factor_used_when_none(self) -> None:
        """load_factor=None → defaults to 0.80."""
        row = self._base_row(load_factor=None)
        result_default = _calc_power_row(row, 0.09, 5_000_000)
        row_explicit = self._base_row(load_factor=0.80)
        result_explicit = _calc_power_row(row_explicit, 0.09, 5_000_000)
        self.assertAlmostEqual(
            result_default["consumption_kwh_year"],
            result_explicit["consumption_kwh_year"],
            places=0,
        )

    # ── Rounding ──────────────────────────────────────────────────────────────

    def test_hours_per_year_rounded_to_1dp(self) -> None:
        result = _calc_power_row(self._base_row(), 0.09, 5_000_000)
        self.assertEqual(result["hours_per_year"], round(result["hours_per_year"], 1))

    def test_consumption_rounded_to_0dp(self) -> None:
        result = _calc_power_row(self._base_row(), 0.09, 5_000_000)
        self.assertEqual(result["consumption_kwh_year"], round(result["consumption_kwh_year"], 0))

    def test_unit_cost_mt_rounded_to_4dp(self) -> None:
        result = _calc_power_row(self._base_row(), 0.09, 5_000_000)
        self.assertEqual(result["unit_cost_mt"], round(result["unit_cost_mt"], 4))


# =============================================================================
# _calc_reagent_row
# =============================================================================

class TestCalcReagentRow(unittest.TestCase):

    def _base_row(self, **kw):
        return {
            "consumption_rate": 0.4,
            "yearly_consumption": 0.0,
            "unit_cost_cad": 3.20,
            **kw,
        }

    # ── Nominal ───────────────────────────────────────────────────────────────

    def test_nominal_keys_present(self) -> None:
        result = _calc_reagent_row(self._base_row(), annual_tp=5_000_000)
        for k in ("yearly_consumption", "total_cost", "unit_cost_mt"):
            self.assertIn(k, result)

    def test_yearly_consumption_rate_times_annual_tp(self) -> None:
        annual_tp = 4_000_000.0
        result = _calc_reagent_row(self._base_row(consumption_rate=0.5), annual_tp=annual_tp)
        self.assertAlmostEqual(result["yearly_consumption"], round(0.5 * annual_tp, 2), places=2)

    def test_total_cost_formula(self) -> None:
        annual_tp = 4_000_000.0
        row = self._base_row(consumption_rate=0.4, unit_cost_cad=2.0)
        result = _calc_reagent_row(row, annual_tp=annual_tp)
        yearly = 0.4 * annual_tp
        expected = round(yearly * 2.0, 2)
        self.assertAlmostEqual(result["total_cost"], expected, places=2)

    def test_unit_cost_mt_formula(self) -> None:
        annual_tp = 5_000_000.0
        row = self._base_row(consumption_rate=0.3, unit_cost_cad=4.0)
        result = _calc_reagent_row(row, annual_tp=annual_tp)
        expected = round(result["total_cost"] / annual_tp, 4)
        self.assertAlmostEqual(result["unit_cost_mt"], expected, places=4)

    # ── Zero annual throughput ────────────────────────────────────────────────

    def test_zero_annual_tp_uses_yearly_consumption_field(self) -> None:
        """When annual_tp=0, falls back to row['yearly_consumption']."""
        row = self._base_row(consumption_rate=0.5, yearly_consumption=250_000.0)
        result = _calc_reagent_row(row, annual_tp=0)
        self.assertAlmostEqual(result["yearly_consumption"], 250_000.0, places=2)

    def test_zero_annual_tp_gives_zero_unit_cost(self) -> None:
        row = self._base_row(yearly_consumption=100_000.0)
        result = _calc_reagent_row(row, annual_tp=0)
        self.assertEqual(result["unit_cost_mt"], 0)

    # ── Zero reagent values ───────────────────────────────────────────────────

    def test_zero_consumption_rate_gives_zero_cost(self) -> None:
        result = _calc_reagent_row(self._base_row(consumption_rate=0.0), annual_tp=5_000_000)
        self.assertEqual(result["total_cost"], 0.0)
        self.assertEqual(result["yearly_consumption"], 0.0)

    def test_zero_unit_cost_gives_zero_total_cost(self) -> None:
        result = _calc_reagent_row(self._base_row(unit_cost_cad=0.0), annual_tp=5_000_000)
        self.assertEqual(result["total_cost"], 0.0)

    # ── None / missing field guards ───────────────────────────────────────────

    def test_none_consumption_rate_treated_as_zero(self) -> None:
        row = self._base_row(consumption_rate=None, yearly_consumption=None)
        result = _calc_reagent_row(row, annual_tp=5_000_000)
        self.assertEqual(result["yearly_consumption"], 0.0)

    def test_none_unit_cost_treated_as_zero(self) -> None:
        row = self._base_row(unit_cost_cad=None)
        result = _calc_reagent_row(row, annual_tp=5_000_000)
        self.assertEqual(result["total_cost"], 0.0)

    # ── Rounding ──────────────────────────────────────────────────────────────

    def test_yearly_consumption_rounded_to_2dp(self) -> None:
        row = self._base_row(consumption_rate=0.333)
        result = _calc_reagent_row(row, annual_tp=3_000_000)
        self.assertEqual(result["yearly_consumption"], round(result["yearly_consumption"], 2))

    def test_total_cost_rounded_to_2dp(self) -> None:
        row = self._base_row(consumption_rate=0.333, unit_cost_cad=1.777)
        result = _calc_reagent_row(row, annual_tp=3_000_000)
        self.assertEqual(result["total_cost"], round(result["total_cost"], 2))

    def test_unit_cost_mt_rounded_to_4dp(self) -> None:
        row = self._base_row(consumption_rate=0.333, unit_cost_cad=1.777)
        result = _calc_reagent_row(row, annual_tp=3_000_000)
        self.assertEqual(result["unit_cost_mt"], round(result["unit_cost_mt"], 4))

    # ── Scaling ───────────────────────────────────────────────────────────────

    def test_doubling_annual_tp_doubles_yearly_consumption(self) -> None:
        row = self._base_row(consumption_rate=0.5)
        r1 = _calc_reagent_row(row, annual_tp=2_000_000)
        r2 = _calc_reagent_row(row, annual_tp=4_000_000)
        self.assertAlmostEqual(r2["yearly_consumption"], r1["yearly_consumption"] * 2, places=2)

    def test_doubling_unit_cost_doubles_total_cost(self) -> None:
        r1 = _calc_reagent_row(self._base_row(unit_cost_cad=2.0), annual_tp=5_000_000)
        r2 = _calc_reagent_row(self._base_row(unit_cost_cad=4.0), annual_tp=5_000_000)
        self.assertAlmostEqual(r2["total_cost"], r1["total_cost"] * 2, places=2)


# =============================================================================
# _opex_reagent_summary_bucket
# =============================================================================

class TestOpexReagentSummaryBucket(unittest.TestCase):
    def test_french_grinding(self) -> None:
        self.assertEqual(_opex_reagent_summary_bucket("Médias de broyage"), "grinding")

    def test_french_reagents(self) -> None:
        self.assertEqual(_opex_reagent_summary_bucket("Réactifs de lixiviation"), "reagents")
        self.assertEqual(_opex_reagent_summary_bucket("Réactifs de flottation"), "reagents")

    def test_french_consumables(self) -> None:
        self.assertEqual(_opex_reagent_summary_bucket("Consommables"), "consumables")

    def test_legacy_english(self) -> None:
        self.assertEqual(_opex_reagent_summary_bucket("BALLS"), "grinding")
        self.assertEqual(_opex_reagent_summary_bucket("REAGENTS"), "reagents")


if __name__ == "__main__":
    unittest.main()
