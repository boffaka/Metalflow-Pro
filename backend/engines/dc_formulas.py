"""Reusable design-criteria formula library.

The functions in this module are intentionally stateless: inputs come from the
project/template context, and outputs can be used by any design-criteria row.
They mirror the engineering workbook formulas used as the current gold-plant
reference without embedding project-specific values.
"""
from __future__ import annotations

import math
from typing import Any


def as_fraction(value: Any, default: float) -> float:
    """Accept either a fraction (0.95) or a percent (95)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v > 1.5:
        v /= 100.0
    return v


def positive(value: Any, default: float | None = None) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def bond_energy_kwh_t(work_index: Any, f80_um: Any, p80_um: Any) -> float:
    """Bond 3rd law: W = 10 * Wi * (1/sqrt(P80) - 1/sqrt(F80))."""
    wi = positive(work_index)
    f80 = positive(f80_um)
    p80 = positive(p80_um)
    if wi is None or f80 is None or p80 is None:
        return 0.0
    f80 = max(f80, p80 + 1.0)
    return max(0.0, 10.0 * wi * (1.0 / math.sqrt(p80) - 1.0 / math.sqrt(f80)))


def rowland_f80_opt_um(bwi: Any) -> float | None:
    wi = positive(bwi)
    if wi is None:
        return None
    return 4000.0 * math.sqrt(13.0 / wi)


def rowland_ef4(bwi: Any, f80_um: Any, p80_um: Any) -> float:
    wi = positive(bwi)
    f80 = positive(f80_um)
    p80 = positive(p80_um)
    if wi is None or f80 is None or p80 is None:
        return 1.0
    f80_opt = rowland_f80_opt_um(wi)
    rr = f80 / max(p80, 1.0)
    if not f80_opt or f80 <= f80_opt or rr <= 0:
        return 1.0
    return (rr + (wi - 7.0) * (f80 - f80_opt) / f80_opt) / rr


def rowland_ef5(p80_um: Any) -> float:
    p80 = positive(p80_um)
    if p80 is None or p80 >= 75.0:
        return 1.0
    return (p80 + 10.3) / (1.145 * p80)


def corrected_bond_energy_kwh_t(
    bwi: Any,
    f80_um: Any,
    p80_um: Any,
    *efficiency_factors: Any,
) -> float:
    energy = bond_energy_kwh_t(bwi, f80_um, p80_um)
    factor = 1.0
    for value in efficiency_factors:
        try:
            factor *= float(value)
        except (TypeError, ValueError):
            factor *= 1.0
    return max(0.0, energy * factor)


def shaft_power_kw(specific_energy_kwh_t: Any, throughput_tph: Any) -> float:
    try:
        return max(0.0, float(specific_energy_kwh_t) * float(throughput_tph))
    except (TypeError, ValueError):
        return 0.0


def installed_power_kw(shaft_kw: Any, efficiency: Any, margin: Any = 0.0) -> float:
    shaft = positive(shaft_kw, 0.0) or 0.0
    eff = max(as_fraction(efficiency, 0.95), 0.5)
    m = as_fraction(margin, 0.0)
    return shaft / eff * (1.0 + m)


def mill_design_tph(nominal_tph: Any, design_factor_pct: Any) -> float:
    try:
        return float(nominal_tph) * (1.0 + float(design_factor_pct) / 100.0)
    except (TypeError, ValueError):
        return 0.0


def crushing_design_tph(mill_design_rate_tph: Any, grinding_avail: Any, crushing_avail: Any) -> float:
    try:
        cavail = max(as_fraction(crushing_avail, 0.75), 1e-6)
        return float(mill_design_rate_tph) * as_fraction(grinding_avail, 0.92) / cavail
    except (TypeError, ValueError):
        return 0.0


def hpgr_total_roll_tph(fresh_feed_tph: Any, recycle_pct: Any) -> float:
    try:
        return float(fresh_feed_tph) * (1.0 + as_fraction(recycle_pct, 0.25))
    except (TypeError, ValueError):
        return 0.0


def hpgr_roll_surface_m2(diameter_m: Any, length_m: Any) -> float:
    try:
        return float(diameter_m) * float(length_m)
    except (TypeError, ValueError):
        return 0.0


def hpgr_peripheral_speed_m_s(diameter_m: Any, rpm: Any) -> float:
    try:
        return math.pi * float(diameter_m) * float(rpm) / 60.0
    except (TypeError, ValueError):
        return 0.0


def hpgr_unit_capacity_tph(specific_throughput: Any, diameter_m: Any, length_m: Any, speed_m_s: Any) -> float:
    try:
        return float(specific_throughput) * float(diameter_m) * float(length_m) * float(speed_m_s)
    except (TypeError, ValueError):
        return 0.0


def hpgr_roll_force_kn(specific_force_n_mm2: Any, diameter_m: Any, length_m: Any) -> float:
    try:
        return float(specific_force_n_mm2) * float(diameter_m) * 1000.0 * float(length_m)
    except (TypeError, ValueError):
        return 0.0


def screen_area_m2(feed_tph: Any, base_capacity_tph_m2: Any, efficiency: Any, correction_factor: Any, stratification: Any = 1.0) -> float:
    try:
        denom = float(base_capacity_tph_m2) * float(efficiency) * float(correction_factor) * float(stratification)
        return float(feed_tph) / max(denom, 1e-6)
    except (TypeError, ValueError):
        return 0.0


def slurry_density_t_m3(solids_sg: Any, solids_pct: Any) -> float:
    """Pulp SG from mass-percent solids: 1 / (Cs/SGs + (1-Cs)/SGw)."""
    sg = positive(solids_sg)
    if sg is None:
        return 0.0
    cs = as_fraction(solids_pct, 0.45)
    cs = min(max(cs, 1e-6), 0.999999)
    return 1.0 / (cs / sg + (1.0 - cs))


def slurry_water_tph(solids_tph: Any, solids_pct: Any) -> float:
    try:
        cs = min(max(as_fraction(solids_pct, 0.45), 1e-6), 0.999999)
        return float(solids_tph) * (1.0 - cs) / cs
    except (TypeError, ValueError):
        return 0.0


def slurry_volume_m3h(solids_tph: Any, solids_sg: Any, solids_pct: Any) -> float:
    try:
        return float(solids_tph) / float(solids_sg) + slurry_water_tph(solids_tph, solids_pct)
    except (TypeError, ValueError):
        return 0.0


def residence_volume_m3(volumetric_flow_m3h: Any, residence_time_h: Any) -> float:
    try:
        return float(volumetric_flow_m3h) * float(residence_time_h)
    except (TypeError, ValueError):
        return 0.0


def tank_diameter_m(volume_m3: Any, height_m: Any) -> float:
    try:
        return math.sqrt((4.0 * float(volume_m3) / math.pi) / float(height_m))
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def cylindrical_volume_diameter_m(volume_m3: Any, height_to_diameter_ratio: Any) -> float:
    try:
        return (4.0 * float(volume_m3) / (math.pi * float(height_to_diameter_ratio))) ** (1.0 / 3.0)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def circular_diameter_m(area_m2: Any) -> float:
    try:
        return math.sqrt(4.0 * float(area_m2) / math.pi)
    except (TypeError, ValueError):
        return 0.0


def roundup_units(total: Any, unit_capacity: Any) -> int:
    try:
        return max(math.ceil(float(total) / max(float(unit_capacity), 1e-6)), 1)
    except (TypeError, ValueError):
        return 1
