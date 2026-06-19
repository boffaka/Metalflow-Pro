"""
Standard metallurgical recovery and comminution formulas (CIM / Bond / Laplante).

Single source of truth for plant recovery combinations used in route selection,
mass balance helpers, and documentation. All recoveries in **percent (0–100)**
unless noted as fraction.
"""
from __future__ import annotations

import math
from typing import Any

try:
    from .gravity_model import GravityParams, plant_gravity_recovery_pct, resolve_gravity_params
except ImportError:
    from engines.gravity_model import GravityParams, plant_gravity_recovery_pct, resolve_gravity_params

# Conservative scale-up of lab GRG / Knelson test recovery to plant (SRK / feasibility practice).
DEFAULT_GRG_TEST_TO_PLANT_FACTOR = 0.70
ROUTE_GRG_TEST_TO_PLANT_FACTOR = 0.85  # route comparison (slightly less conservative)

RECOVERY_CEILING_PCT = 99.0


def cap_recovery_pct(value: float, ceiling: float = RECOVERY_CEILING_PCT) -> float:
    return round(max(0.0, min(float(ceiling), float(value))), 2)


def combined_gravity_leach_recovery_pct(
    gravity_plant_pct: float,
    leach_on_residue_pct: float,
) -> float:
    """
    Overall plant Au recovery (%): gravity on feed, leach on gold reporting to leach feed.

    R = R_g + (1 − R_g/100) × R_lix

    R_lix is recovery **on the leach feed** (residue after gravity), not on whole ore.
    References: Laplante & Gray (GRG); standard serial recovery on feed gold basis.
    """
    g = max(0.0, min(100.0, float(gravity_plant_pct)))
    leach = max(0.0, min(100.0, float(leach_on_residue_pct)))
    return cap_recovery_pct(g + (1.0 - g / 100.0) * leach)


def concentrate_leach_recovery_pct(
    flotation_recovery_pct: float,
    leach_on_concentrate_pct: float,
) -> float:
    """
    Flotation concentrate leached only (flotation tails not leached).

    R = (R_f/100) × (R_lix/100) × 100

    Do not multiply by arbitrary uplift factors on leach recovery.
    """
    f = max(0.0, min(100.0, float(flotation_recovery_pct))) / 100.0
    leach = max(0.0, min(100.0, float(leach_on_concentrate_pct))) / 100.0
    return cap_recovery_pct(100.0 * f * leach)


def gravity_flotation_leach_recovery_pct(
    gravity_plant_pct: float,
    flotation_recovery_pct: float,
    leach_on_concentrate_pct: float,
) -> float:
    """
    Gravity on feed, flotation on residue, leach on flotation concentrate.

    R = R_g + (1 − R_g/100) × (R_f/100) × (R_lix/100) × 100
    """
    g = max(0.0, min(100.0, float(gravity_plant_pct))) / 100.0
    f = max(0.0, min(100.0, float(flotation_recovery_pct))) / 100.0
    leach = max(0.0, min(100.0, float(leach_on_concentrate_pct))) / 100.0
    return cap_recovery_pct(100.0 * (g + (1.0 - g) * f * leach))


def lims_grg_test_to_plant_gravity_pct(
    grg_test_recovery_pct: float,
    *,
    plant_factor: float = DEFAULT_GRG_TEST_TO_PLANT_FACTOR,
) -> float:
    """
    Scale lab GRG / Knelson test recovery to forecast plant gravity recovery.

    Testwork often overstates plant performance; 60–80 % of lab recovery is typical
    for PFS (use ``plant_factor`` 0.60–0.80).
    """
    if grg_test_recovery_pct <= 0:
        return 0.0
    factor = max(0.0, min(1.0, float(plant_factor)))
    return cap_recovery_pct(float(grg_test_recovery_pct) * factor, ceiling=50.0)


def plant_gravity_from_grg_characterization_pct(
    grg_content_pct: float,
    *,
    slip_pct: float = 30.0,
    knelson_pct: float | None = None,
    ilr_pct: float = 95.0,
) -> float:
    """
    Plant gravity recovery from GRG **content** (% of feed Au as GRG), not lab test recovery.

    R_g,plant = (GRG/100) × (slip/100) × (Knelson/100) × (ILR/100) × 100
    """
    kn = knelson_pct if knelson_pct is not None else min(75.0, 35.0 + 0.5 * float(grg_content_pct))
    gp = GravityParams(
        grg_pct=float(grg_content_pct),
        gravity_slip_pct=float(slip_pct),
        knelson_unit_recovery_pct=float(kn),
        ilr_recovery_pct=float(ilr_pct),
        gravity_mass_pull_pct=0.2,
    )
    return cap_recovery_pct(plant_gravity_recovery_pct(gp), ceiling=50.0)


def resolve_route_gravity_recovery_pct(
    grg_lims_avg_pct: float | None,
    sim_params: dict[str, Any] | None = None,
    *,
    plant_factor: float = ROUTE_GRG_TEST_TO_PLANT_FACTOR,
) -> float | None:
    """
    Gravity recovery for route comparison.

    Uses simulation gravity model when params exist; otherwise scales LIMS GRG test
    recovery with ``plant_factor`` (GRV-03 ``grg_rec_pct``).
    """
    if grg_lims_avg_pct is None or grg_lims_avg_pct <= 0:
        return None
    if sim_params:
        gp = resolve_gravity_params(sim_params)
        if gp.grg_pct > 0:
            return cap_recovery_pct(plant_gravity_recovery_pct(gp), ceiling=50.0)
    return lims_grg_test_to_plant_gravity_pct(grg_lims_avg_pct, plant_factor=plant_factor)


def bond_specific_energy_kwh_t(
    wi_kwh_t: float,
    f80_um: float,
    p80_um: float,
) -> float | None:
    """
    Bond standard equation (kWh/t): E = 10 × Wi × (1/√P80 − 1/√F80), µm.
    """
    if wi_kwh_t <= 0 or f80_um <= 0 or p80_um <= 0 or p80_um >= f80_um:
        return None
    e = 10.0 * float(wi_kwh_t) * (1.0 / math.sqrt(p80_um) - 1.0 / math.sqrt(f80_um))
    if not math.isfinite(e) or e <= 0:
        return None
    return round(e, 2)


def leach_kinetic_recovery_fraction(r_inf: float, k_per_h: float, time_h: float) -> float:
    """R(t) = R∞ × (1 − exp(−k×t)); R∞ and output as fraction 0–1."""
    if time_h <= 0:
        return 0.0
    ri = float(r_inf)
    if ri > 1.0:
        ri /= 100.0
    ri = max(0.0, min(1.0, ri))
    kk = max(0.0, float(k_per_h))
    out = ri * (1.0 - math.exp(-kk * float(time_h)))
    return max(0.0, min(ri, out))


def route_recovery_estimates(
    *,
    leach_whole_ore_pct: float | None,
    grg_lims_avg_pct: float | None,
    flotation_pct: float | None,
    sim_params: dict[str, Any] | None = None,
    plant_factor: float = ROUTE_GRG_TEST_TO_PLANT_FACTOR,
) -> dict[str, float | None]:
    """Estimated overall recoveries (%) for the four standard route archetypes."""
    r_lix = leach_whole_ore_pct
    r_g = resolve_route_gravity_recovery_pct(grg_lims_avg_pct, sim_params, plant_factor=plant_factor)
    direct = cap_recovery_pct(r_lix) if r_lix is not None else None
    grav_leach = (
        combined_gravity_leach_recovery_pct(r_g, r_lix)
        if r_g is not None and r_lix is not None
        else None
    )
    flot_leach = (
        concentrate_leach_recovery_pct(flotation_pct, r_lix)
        if flotation_pct is not None and r_lix is not None
        else None
    )
    grav_flot_leach = (
        gravity_flotation_leach_recovery_pct(r_g, flotation_pct, r_lix)
        if r_g is not None and flotation_pct is not None and r_lix is not None
        else None
    )
    return {
        "direct": direct,
        "grav_leach": grav_leach,
        "flot_leach": flot_leach,
        "grav_flot_leach": grav_flot_leach,
        "gravity_plant_pct": r_g,
    }


FORMULA_REFERENCES: dict[str, str] = {
    "gravity_leach": "R = R_g + (1−R_g/100)×R_lix  (Laplante; serial sur alimentation)",
    "concentrate_leach": "R = (R_f/100)×(R_lix/100)×100  (concentré flotté puis lixivié)",
    "gravity_flotation_leach": "R = R_g + (1−R_g/100)×(R_f/100)×(R_lix/100)×100",
    "bond": "E = 10×Wi×(1/√P80 − 1/√F80)  (Bond, 1961; F80,P80 en µm)",
    "leach_kinetic": "R(t) = R∞×(1−exp(−k×t))  (cinétique LIMS D1)",
    "gravity_plant": "R_g = GRG×slip×Knelson×ILR  (modèle usine partagé mass_balance)",
}
