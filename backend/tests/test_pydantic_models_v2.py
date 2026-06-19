"""
Unit tests for all Pydantic validation models added in the strict-validation session.

Covers every model with:
  - Nominal  : valid inputs accepted, correct defaults applied
  - Boundary : values at or just outside declared ge/le/min_length/max_length
  - Error    : invalid types, out-of-range values, extra fields (extra="forbid"),
               missing required fields
"""
from __future__ import annotations

import os
import unittest

from pydantic import ValidationError

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword1!")

try:
    from backend.models import (
        CircuitTemplateIn, OperationIn, OperationPatch,
        CriteriaUpdateItem, BulkCriteriaUpdate,
        MbStreamPatch, MbSnapshotIn, CarbonFactorPatch,
        EquipmentV2In, EquipmentV2Patch,
        OpexInputPatch,
        ManpowerIn, ManpowerPatch,
        ReagentIn, ReagentPatch,
        MobileIn, MobilePatch,
        PowerPatch,
        FlowsheetBlockItem, FlowsheetConnectionItem, FlowsheetUpdate,
    )
except ImportError:
    from models import (
        CircuitTemplateIn, OperationIn, OperationPatch,
        CriteriaUpdateItem, BulkCriteriaUpdate,
        MbStreamPatch, MbSnapshotIn, CarbonFactorPatch,
        EquipmentV2In, EquipmentV2Patch,
        OpexInputPatch,
        ManpowerIn, ManpowerPatch,
        ReagentIn, ReagentPatch,
        MobileIn, MobilePatch,
        PowerPatch,
        FlowsheetBlockItem, FlowsheetConnectionItem, FlowsheetUpdate,
    )


# =============================================================================
# CircuitTemplateIn
# =============================================================================

class TestCircuitTemplateIn(unittest.TestCase):
    def test_nominal(self) -> None:
        m = CircuitTemplateIn(name="PFS Circuit")
        self.assertEqual(m.name, "PFS Circuit")

    def test_whitespace_stripped(self) -> None:
        m = CircuitTemplateIn(name="  Flotation  ")
        self.assertEqual(m.name, "Flotation")

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CircuitTemplateIn(name="")

    def test_whitespace_only_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CircuitTemplateIn(name="   ")

    def test_name_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CircuitTemplateIn(name="x" * 201)

    def test_max_length_accepted(self) -> None:
        CircuitTemplateIn(name="x" * 200)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CircuitTemplateIn(name="A", unknown_field="oops")


# =============================================================================
# OperationIn
# =============================================================================

class TestOperationIn(unittest.TestCase):
    def test_nominal(self) -> None:
        m = OperationIn(op_code="SAG_MILL")
        self.assertEqual(m.op_code, "SAG_MILL")

    def test_empty_op_code_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OperationIn(op_code="")

    def test_op_code_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OperationIn(op_code="A" * 101)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OperationIn(op_code="CIL", extra="x")


# =============================================================================
# OperationPatch
# =============================================================================

class TestOperationPatch(unittest.TestCase):
    def test_all_none_accepted(self) -> None:
        m = OperationPatch()
        self.assertIsNone(m.sort_order)
        self.assertIsNone(m.enabled)

    def test_sort_order_zero_accepted(self) -> None:
        m = OperationPatch(sort_order=0)
        self.assertEqual(m.sort_order, 0)

    def test_sort_order_positive(self) -> None:
        m = OperationPatch(sort_order=5)
        self.assertEqual(m.sort_order, 5)

    def test_sort_order_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OperationPatch(sort_order=-1)

    def test_enabled_boolean(self) -> None:
        m = OperationPatch(enabled=True)
        self.assertTrue(m.enabled)
        m2 = OperationPatch(enabled=False)
        self.assertFalse(m2.enabled)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OperationPatch(sort_order=1, bad_field="x")


# =============================================================================
# CriteriaUpdateItem / BulkCriteriaUpdate
# =============================================================================

class TestCriteriaUpdateItem(unittest.TestCase):
    def _base(self, **kw):
        return CriteriaUpdateItem(id="crit-1", version=0, **kw)

    def test_nominal(self) -> None:
        m = self._base(design_value=14.5)
        self.assertEqual(m.design_value, 14.5)

    def test_all_optionals_none(self) -> None:
        m = self._base()
        self.assertIsNone(m.design_value)
        self.assertIsNone(m.nominal_value)
        self.assertIsNone(m.author)

    def test_version_zero_accepted(self) -> None:
        m = self._base()
        self.assertEqual(m.version, 0)

    def test_version_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CriteriaUpdateItem(id="x", version=-1)

    def test_source_code_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(source_code="X" * 11)

    def test_author_max_length_accepted(self) -> None:
        self._base(author="A" * 100)

    def test_author_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(author="A" * 101)

    def test_comments_max_length_accepted(self) -> None:
        self._base(comments="x" * 2000)

    def test_comments_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(comments="x" * 2001)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CriteriaUpdateItem(id="x", version=0, bad="x")


class TestBulkCriteriaUpdate(unittest.TestCase):
    def _item(self):
        return {"id": "c1", "version": 0, "design_value": 10.0}

    def test_nominal(self) -> None:
        m = BulkCriteriaUpdate(updates=[self._item()])
        self.assertEqual(len(m.updates), 1)

    def test_multiple_items(self) -> None:
        items = [{"id": f"c{i}", "version": 0} for i in range(5)]
        m = BulkCriteriaUpdate(updates=items)
        self.assertEqual(len(m.updates), 5)

    def test_empty_list_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            BulkCriteriaUpdate(updates=[])

    def test_missing_updates_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            BulkCriteriaUpdate()

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            BulkCriteriaUpdate(updates=[self._item()], bad="x")


# =============================================================================
# MbStreamPatch
# =============================================================================

class TestMbStreamPatch(unittest.TestCase):
    def test_nominal(self) -> None:
        m = MbStreamPatch(version=3, solids_tph=500.0, water_tph=250.0)
        self.assertEqual(m.version, 3)
        self.assertEqual(m.solids_tph, 500.0)

    def test_version_zero_accepted(self) -> None:
        MbStreamPatch(version=0)

    def test_version_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=-1)

    def test_version_required(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(solids_tph=100.0)

    def test_all_optionals_none(self) -> None:
        m = MbStreamPatch(version=0)
        self.assertIsNone(m.solids_tph)
        self.assertIsNone(m.water_tph)
        self.assertIsNone(m.au_gt)

    def test_solids_tph_at_zero(self) -> None:
        MbStreamPatch(version=0, solids_tph=0)

    def test_solids_tph_at_max(self) -> None:
        MbStreamPatch(version=0, solids_tph=1_000_000)

    def test_solids_tph_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=0, solids_tph=-1)

    def test_solids_tph_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=0, solids_tph=1_000_001)

    def test_slurry_pct_w_at_bounds(self) -> None:
        MbStreamPatch(version=0, slurry_pct_w=0)
        MbStreamPatch(version=0, slurry_pct_w=100)

    def test_slurry_pct_w_over_100_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=0, slurry_pct_w=100.1)

    def test_hours_per_day_at_24(self) -> None:
        MbStreamPatch(version=0, hours_per_day=24)

    def test_hours_per_day_over_24_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=0, hours_per_day=24.1)

    def test_au_gt_at_max(self) -> None:
        MbStreamPatch(version=0, au_gt=50_000)

    def test_au_gt_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=0, au_gt=50_001)

    def test_source_max_length_accepted(self) -> None:
        MbStreamPatch(version=0, source="x" * 50)

    def test_source_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=0, source="x" * 51)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbStreamPatch(version=0, bad_field="x")


# =============================================================================
# MbSnapshotIn
# =============================================================================

class TestMbSnapshotIn(unittest.TestCase):
    def test_nominal(self) -> None:
        m = MbSnapshotIn(name="PFS Rev A")
        self.assertEqual(m.name, "PFS Rev A")

    def test_whitespace_stripped(self) -> None:
        m = MbSnapshotIn(name="  Rev B  ")
        self.assertEqual(m.name, "Rev B")

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbSnapshotIn(name="")

    def test_name_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbSnapshotIn(name="x" * 201)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MbSnapshotIn(name="A", bad="x")


# =============================================================================
# CarbonFactorPatch
# =============================================================================

class TestCarbonFactorPatch(unittest.TestCase):
    def test_nominal(self) -> None:
        m = CarbonFactorPatch(factor_value=0.5)
        self.assertEqual(m.factor_value, 0.5)

    def test_zero_accepted(self) -> None:
        CarbonFactorPatch(factor_value=0)

    def test_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CarbonFactorPatch(factor_value=-0.01)

    def test_large_value_accepted(self) -> None:
        CarbonFactorPatch(factor_value=999999)

    def test_required_field(self) -> None:
        with self.assertRaises(ValidationError):
            CarbonFactorPatch()

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            CarbonFactorPatch(factor_value=0.5, bad="x")


# =============================================================================
# EquipmentV2In
# =============================================================================

class TestEquipmentV2In(unittest.TestCase):
    def _base(self, **kw):
        return EquipmentV2In(wbs_code="1100", eq_type="PUMP", equipment_name="Feed Pump", **kw)

    def test_nominal(self) -> None:
        m = self._base()
        self.assertEqual(m.wbs_code, "1100")
        self.assertEqual(m.quantity, 1)
        self.assertFalse(m.is_long_lead)

    def test_whitespace_stripped(self) -> None:
        m = EquipmentV2In(wbs_code=" 1100 ", eq_type=" PUMP ", equipment_name=" Feed Pump ")
        self.assertEqual(m.wbs_code, "1100")
        self.assertEqual(m.equipment_name, "Feed Pump")

    def test_wbs_code_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentV2In(wbs_code="", eq_type="PUMP", equipment_name="X")

    def test_wbs_code_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentV2In(wbs_code="X" * 21, eq_type="PUMP", equipment_name="X")

    def test_quantity_min_1(self) -> None:
        self._base(quantity=1)

    def test_quantity_below_min_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(quantity=0)

    def test_quantity_max_accepted(self) -> None:
        self._base(quantity=999)

    def test_quantity_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(quantity=1000)

    def test_installed_kw_at_zero(self) -> None:
        self._base(installed_kw=0)

    def test_installed_kw_at_max(self) -> None:
        self._base(installed_kw=100_000)

    def test_installed_kw_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(installed_kw=100_001)

    def test_lead_time_at_max(self) -> None:
        self._base(lead_time_weeks=260)

    def test_lead_time_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(lead_time_weeks=261)

    def test_price_cad_zero_accepted(self) -> None:
        self._base(price_cad=0)

    def test_price_cad_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(price_cad=-100)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(bad_field="x")


# =============================================================================
# EquipmentV2Patch
# =============================================================================

class TestEquipmentV2Patch(unittest.TestCase):
    def test_nominal(self) -> None:
        m = EquipmentV2Patch(version=2, equipment_name="New Name")
        self.assertEqual(m.version, 2)
        self.assertEqual(m.equipment_name, "New Name")

    def test_version_required(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentV2Patch(equipment_name="X")

    def test_all_optionals_none(self) -> None:
        m = EquipmentV2Patch(version=0)
        self.assertIsNone(m.equipment_name)
        self.assertIsNone(m.quantity)

    def test_quantity_zero_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentV2Patch(version=0, quantity=0)

    def test_installed_kw_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentV2Patch(version=0, installed_kw=-1)

    def test_weight_kg_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentV2Patch(version=0, weight_kg=-0.1)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            EquipmentV2Patch(version=0, unknown_col="x")


# =============================================================================
# OpexInputPatch
# =============================================================================

class TestOpexInputPatch(unittest.TestCase):
    def test_nominal(self) -> None:
        m = OpexInputPatch(param_value=12345.0)
        self.assertEqual(m.param_value, 12345.0)

    def test_negative_value_accepted(self) -> None:
        m = OpexInputPatch(param_value=-5.0)
        self.assertEqual(m.param_value, -5.0)

    def test_zero_accepted(self) -> None:
        OpexInputPatch(param_value=0)

    def test_missing_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OpexInputPatch()

    def test_non_float_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OpexInputPatch(param_value="not-a-number")

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            OpexInputPatch(param_value=1.0, extra="x")


# =============================================================================
# ManpowerIn
# =============================================================================

class TestManpowerIn(unittest.TestCase):
    def _base(self, **kw):
        return ManpowerIn(department="Operations", description="Shift Boss", **kw)

    def test_nominal_defaults(self) -> None:
        m = self._base()
        self.assertEqual(m.category, "Staff")
        self.assertEqual(m.schedule, "Office")
        self.assertEqual(m.num_employees, 1)
        self.assertEqual(m.bonus_pct, 5)
        self.assertEqual(m.benefits_pct, 20)

    def test_department_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ManpowerIn(department="", description="X")

    def test_description_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ManpowerIn(department="Ops", description="")

    def test_num_employees_zero_accepted(self) -> None:
        self._base(num_employees=0)

    def test_num_employees_over_9999_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(num_employees=10000)

    def test_base_salary_at_max(self) -> None:
        self._base(base_salary_hourly=10_000)

    def test_base_salary_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(base_salary_hourly=10_001)

    def test_bonus_at_100(self) -> None:
        self._base(bonus_pct=100)

    def test_bonus_over_100_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(bonus_pct=101)

    def test_overtime_at_200(self) -> None:
        self._base(overtime_pct=200)

    def test_overtime_over_200_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(overtime_pct=201)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(bad_field="x")


# =============================================================================
# ManpowerPatch
# =============================================================================

class TestManpowerPatch(unittest.TestCase):
    def test_all_none_accepted(self) -> None:
        m = ManpowerPatch()
        self.assertIsNone(m.department)
        self.assertIsNone(m.description)

    def test_partial_update(self) -> None:
        m = ManpowerPatch(num_employees=3, bonus_pct=10)
        self.assertEqual(m.num_employees, 3)

    def test_num_employees_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ManpowerPatch(num_employees=10000)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ManpowerPatch(bad="x")


# =============================================================================
# ReagentIn
# =============================================================================

class TestReagentIn(unittest.TestCase):
    def _base(self, **kw):
        return ReagentIn(category="Cyanuration", description="NaCN", **kw)

    def test_nominal_defaults(self) -> None:
        m = self._base()
        self.assertEqual(m.unit_consumption, "kg/t")
        self.assertEqual(m.consumption_rate, 0)
        self.assertEqual(m.unit_cost_cad, 0)
        self.assertEqual(m.source, "A")

    def test_category_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ReagentIn(category="", description="X")

    def test_description_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ReagentIn(category="X", description="")

    def test_consumption_rate_zero_accepted(self) -> None:
        self._base(consumption_rate=0)

    def test_consumption_rate_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(consumption_rate=-0.01)

    def test_unit_cost_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(unit_cost_cad=-1)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(extra="x")


# =============================================================================
# ReagentPatch
# =============================================================================

class TestReagentPatch(unittest.TestCase):
    def test_all_none_accepted(self) -> None:
        m = ReagentPatch()
        self.assertIsNone(m.category)

    def test_consumption_rate_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ReagentPatch(consumption_rate=-1)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ReagentPatch(bad="x")


# =============================================================================
# MobileIn
# =============================================================================

class TestMobileIn(unittest.TestCase):
    def _base(self, **kw):
        return MobileIn(description="CAT 793", **kw)

    def test_nominal_defaults(self) -> None:
        m = self._base()
        self.assertEqual(m.quantity, 1)
        self.assertEqual(m.operating_hours_year, 0)
        self.assertEqual(m.cost_per_hour, 0)

    def test_description_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MobileIn(description="")

    def test_quantity_zero_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(quantity=0)

    def test_quantity_at_max(self) -> None:
        self._base(quantity=999)

    def test_quantity_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(quantity=1000)

    def test_hours_at_max(self) -> None:
        self._base(operating_hours_year=8760)

    def test_hours_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(operating_hours_year=8761)

    def test_cost_per_hour_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(cost_per_hour=-1)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(bad="x")


# =============================================================================
# MobilePatch
# =============================================================================

class TestMobilePatch(unittest.TestCase):
    def test_all_none_accepted(self) -> None:
        MobilePatch()

    def test_quantity_zero_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MobilePatch(quantity=0)

    def test_hours_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MobilePatch(operating_hours_year=8761)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            MobilePatch(bad="x")


# =============================================================================
# PowerPatch
# =============================================================================

class TestPowerPatch(unittest.TestCase):
    def test_all_none_accepted(self) -> None:
        PowerPatch()

    def test_operating_kw_at_max(self) -> None:
        PowerPatch(operating_kw=500_000)

    def test_operating_kw_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PowerPatch(operating_kw=500_001)

    def test_electrical_efficiency_at_bounds(self) -> None:
        PowerPatch(electrical_efficiency=0)
        PowerPatch(electrical_efficiency=1)

    def test_electrical_efficiency_over_1_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PowerPatch(electrical_efficiency=1.01)

    def test_load_factor_at_bounds(self) -> None:
        PowerPatch(load_factor=0)
        PowerPatch(load_factor=1)

    def test_load_factor_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PowerPatch(load_factor=-0.01)

    def test_area_availability_at_bounds(self) -> None:
        PowerPatch(area_availability=0)
        PowerPatch(area_availability=1)

    def test_hours_per_day_at_24(self) -> None:
        PowerPatch(hours_per_day=24)

    def test_hours_per_day_over_24_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PowerPatch(hours_per_day=24.1)

    def test_wbs_description_max_accepted(self) -> None:
        PowerPatch(wbs_description="x" * 200)

    def test_wbs_description_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PowerPatch(wbs_description="x" * 201)

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            PowerPatch(bad="x")


# =============================================================================
# FlowsheetBlockItem
# =============================================================================

class TestFlowsheetBlockItem(unittest.TestCase):
    def _base(self, x: float = 100, y: float = 200, **kw):
        return FlowsheetBlockItem(id="b1", type="SAG_MILL", label="SAG Mill", x=x, y=y, **kw)

    def test_nominal(self) -> None:
        m = self._base()
        self.assertEqual(m.id, "b1")
        self.assertEqual(m.x, 100)

    def test_id_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FlowsheetBlockItem(id="", type="X", label="X", x=0, y=0)

    def test_x_at_zero(self) -> None:
        self._base(x=0, y=0)

    def test_x_at_max(self) -> None:
        self._base(x=10_000, y=0)

    def test_x_over_max_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(x=10_001, y=0)

    def test_y_negative_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self._base(x=0, y=-1)

    def test_extra_fields_allowed(self) -> None:
        m = self._base(color="blue", custom_data={"k": "v"})
        self.assertEqual(m.model_extra["color"], "blue")


# =============================================================================
# FlowsheetConnectionItem
# =============================================================================

class TestFlowsheetConnectionItem(unittest.TestCase):
    def test_nominal_via_alias(self) -> None:
        m = FlowsheetConnectionItem(**{"from": "b1", "to": "b2"})
        self.assertEqual(m.from_, "b1")
        self.assertEqual(m.to, "b2")

    def test_from_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FlowsheetConnectionItem(**{"from": "", "to": "b2"})

    def test_to_empty_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FlowsheetConnectionItem(**{"from": "b1", "to": ""})

    def test_from_too_long_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FlowsheetConnectionItem(**{"from": "x" * 101, "to": "b2"})

    def test_extra_fields_allowed(self) -> None:
        m = FlowsheetConnectionItem(**{"from": "b1", "to": "b2", "flow_type": "pulp"})
        self.assertEqual(m.model_extra["flow_type"], "pulp")


# =============================================================================
# FlowsheetUpdate
# =============================================================================

class TestFlowsheetUpdate(unittest.TestCase):
    def _block(self, bid="b1"):
        return {"id": bid, "type": "MILL", "label": "Mill", "x": 100, "y": 100}

    def _conn(self, frm="b1", to="b2"):
        return {"from": frm, "to": to}

    def test_nominal(self) -> None:
        m = FlowsheetUpdate(blocks=[self._block()], connections=[self._conn()])
        self.assertEqual(len(m.blocks), 1)
        self.assertEqual(len(m.connections), 1)

    def test_empty_blocks_accepted(self) -> None:
        FlowsheetUpdate(blocks=[], connections=[])

    def test_multiple_blocks_and_connections(self) -> None:
        blocks = [self._block(f"b{i}") for i in range(3)]
        conns = [self._conn(f"b{i}", f"b{i+1}") for i in range(2)]
        m = FlowsheetUpdate(blocks=blocks, connections=conns)
        self.assertEqual(len(m.blocks), 3)
        self.assertEqual(len(m.connections), 2)

    def test_missing_blocks_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FlowsheetUpdate(connections=[])

    def test_missing_connections_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FlowsheetUpdate(blocks=[self._block()])

    def test_extra_field_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FlowsheetUpdate(blocks=[], connections=[], bad="x")

    def test_block_serialization_via_alias(self) -> None:
        m = FlowsheetUpdate(
            blocks=[self._block()],
            connections=[self._conn()],
        )
        conn_dict = m.connections[0].model_dump(by_alias=True)
        self.assertIn("from", conn_dict)
        self.assertNotIn("from_", conn_dict)


if __name__ == "__main__":
    unittest.main()
