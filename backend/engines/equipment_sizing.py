# backend/engines/equipment_sizing.py
"""
Equipment sizing engine for gold process plants.

All sizing uses industry-standard formulas. Results feed into:
  - vendor_catalog lookup (by power or volume range)
  - CAPEX estimation (Lang method)
  - Flowsheet and P&ID (dimensioned symbols)

Lang method assembly factors (gold plant):
  Installation:      35% × E
  Civil/Structural:  25% × E
  Instrumentation:   15% × E
  Piping:            20% × E
  Electrical:        10% × E
  TIC = E × 2.05
  EPCM = 12% × TIC
  Contingency = 15% × (TIC + EPCM)
  Total CAPEX = TIC + EPCM + Contingency
"""

from __future__ import annotations

import logging
import math

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM

logger = logging.getLogger(__name__)


def size_ball_mill(
    wi: float,
    tph: float,
    p80_um: float,
    f80_um: float,
    filling_frac: float = 0.35,
    speed_frac: float = 0.75,
    ld_ratio: float = 1.0,
    C: float = 7.33,
) -> dict:
    """
    Ball mill sizing using Bond energy law.

    Args:
        wi: Bond Work Index (kWh/t)
        tph: Design throughput (t/h)
        p80_um: Product 80% passing (µm)
        f80_um: Feed 80% passing (µm)
        filling_frac: Ball charge fraction (0.35 typical)
        speed_frac: Fraction of critical speed (0.75 typical)
        ld_ratio: Length/Diameter ratio (1.0 typical for primary BM)
        C: Mill constant (7.33 for overflow mills)
    Returns:
        dict: {power_kw, diameter_m, length_m, energy_kwh_t}
    """
    try:
        if p80_um <= 0 or f80_um <= 0:
            raise ValueError("Size parameters must be > 0")
        energy_kwh_t = wi * 10.0 * (1.0 / math.sqrt(p80_um) - 1.0 / math.sqrt(f80_um))
        # Installed motor power includes mechanical efficiency losses
        # Motor ~95%, gearbox ~92%, bearings ~97% → total ~85% (Metso/Allis Chalmers)
        mech_efficiency = 0.85
        power_kw = energy_kwh_t * tph / mech_efficiency
        diam = (power_kw / (C * filling_frac * (speed_frac**2.5) * ld_ratio)) ** (1.0 / 3.5)
        length = diam * ld_ratio
        return {
            "power_kw": round(power_kw, 1),
            "diameter_m": round(diam, 2),
            "length_m": round(length, 2),
            "energy_kwh_t": round(energy_kwh_t, 3),
        }
    except Exception as e:
        logger.error("size_ball_mill failed (wi=%.1f, tph=%.1f, p80=%.1f, f80=%.1f): %s", wi, tph, p80_um, f80_um, e)
        raise RuntimeError(f"size_ball_mill failed for wi={wi}, tph={tph}") from e


def size_sag_mill(spi_kwh_t: float, tph: float, ld_ratio: float = 0.40) -> dict:
    """SAG mill sizing from SPI."""
    try:
        power_kw = spi_kwh_t * tph
        C = 7.33
        diam = (power_kw / (C * 0.12 * (0.76**2.5) * ld_ratio)) ** (1.0 / 3.5)
        return {
            "power_kw": round(power_kw, 1),
            "diameter_m": round(diam, 2),
            "length_m": round(diam * ld_ratio, 2),
        }
    except Exception as e:
        logger.error("size_sag_mill failed (spi=%.1f, tph=%.1f): %s", spi_kwh_t, tph, e)
        raise RuntimeError(f"size_sag_mill failed for spi={spi_kwh_t}, tph={tph}") from e


def size_flotation(
    q_m3h: float,
    srt_min: float,
    v_unit_m3: float,
    power_intensity_kw_m3: float = 0.6,
) -> dict:
    """
    Flotation cell sizing.

    V_total = Q (m³/h) × SRT (min) / 60
    n_cells = ceil(V_total / V_unit)

    Args:
        power_intensity_kw_m3: Specific power (kW/m³). Typical range 0.4–1.0
            depending on cell type (mechanical ~0.6, column ~0.4).
    """
    try:
        v_total = q_m3h * srt_min / 60.0
        n_cells = math.ceil(v_total / v_unit_m3)
        power_kw = v_total * power_intensity_kw_m3
        return {
            "v_total_m3": round(v_total, 1),
            "n_cells": max(1, n_cells),
            "v_unit_m3": v_unit_m3,
            "power_kw": round(power_kw, 1),
        }
    except Exception as e:
        logger.error("size_flotation failed (q_m3h=%.1f, srt_min=%.1f): %s", q_m3h, srt_min, e)
        raise RuntimeError(f"size_flotation failed for q_m3h={q_m3h}") from e


def size_thickener(
    tpd: float,
    ua_m2_t_d: float,
    n_units: int = 1,
    d_max_m: float = 45.0,
) -> dict:
    """
    Thickener diameter from Unit Area.

    A_total = tpd × UA
    D = 2 × √(A_total / (π × n_units))
    """
    try:
        area = tpd * ua_m2_t_d
        diam = 2.0 * math.sqrt(area / (math.pi * n_units))
        if diam > d_max_m:
            n_units = math.ceil(area / (math.pi * (d_max_m / 2.0) ** 2))
            diam = 2.0 * math.sqrt(area / (math.pi * n_units))
        return {
            "area_total_m2": round(area, 1),
            "diameter_m": round(diam, 1),
            "n_units": n_units,
        }
    except Exception as e:
        logger.error("size_thickener failed (tpd=%.1f, ua=%.3f): %s", tpd, ua_m2_t_d, e)
        raise RuntimeError(f"size_thickener failed for tpd={tpd}") from e


def size_cil_tanks(
    q_m3h: float,
    srt_h: float,
    n_tanks: int = 8,
    hd_ratio: float = 1.0,
) -> dict:
    """
    CIL tank sizing.

    V_cil = Q × SRT
    V_per_tank = V_cil / n_tanks
    D = (4 × V_per / π / (H/D))^(1/3)
    """
    try:
        v_total = q_m3h * srt_h
        v_per = v_total / n_tanks
        d_tank = (4.0 * v_per / (math.pi * hd_ratio)) ** (1.0 / 3.0)
        h_tank = d_tank * hd_ratio
        return {
            "v_total_m3": round(v_total, 1),
            "v_per_tank_m3": round(v_per, 1),
            "d_tank_m": round(d_tank, 2),
            "h_tank_m": round(h_tank, 2),
            "n_tanks": n_tanks,
        }
    except Exception as e:
        logger.error("size_cil_tanks failed (q_m3h=%.1f, srt_h=%.1f): %s", q_m3h, srt_h, e)
        raise RuntimeError(f"size_cil_tanks failed for q_m3h={q_m3h}") from e


def size_ew_cells(
    oz_per_day: float,
    j_cath_a_m2: float = 200.0,
    a_cath_m2: float = 1.0,
    cathodes_per_cell: int = 30,
    faraday_eff: float = 0.75,
) -> dict:
    """
    Electrowinning cell sizing.

    i_total = oz/day / TROY_OZ_PER_GRAM g/oz / 86400 s/day / (M_Au / (n_e × F)) / faraday_eff
    n_cathodes = ceil(i_total / (j × A))
    """
    try:
        gold_g_day = oz_per_day / TROY_OZ_PER_GRAM  # oz → g: divide by (g/oz) constant
        M_Au, n_e, F = 196.97, 3, 96485
        i_total = (gold_g_day / 86400.0) / (M_Au / (n_e * F)) / faraday_eff
        n_cathodes = math.ceil(i_total / (j_cath_a_m2 * a_cath_m2))
        n_cells = math.ceil(n_cathodes / cathodes_per_cell)
        return {
            "i_total_a": round(i_total, 1),
            "n_cathodes": max(1, n_cathodes),
            "n_cells": max(1, n_cells),
        }
    except Exception as e:
        logger.error("size_ew_cells failed (oz_per_day=%.1f): %s", oz_per_day, e)
        raise RuntimeError(f"size_ew_cells failed for oz_per_day={oz_per_day}") from e


def apply_lang_factors(equipment_cost_usd: float, location_factor: float = 1.0) -> dict:
    """
    Apply Lang method assembly factors for gold plant CAPEX.

    TIC = E × 2.05 (installation 35% + civil 25% + instruments 15% + piping 20% + electrical 10%)
    EPCM = 12% × TIC
    Contingency = 15% × (TIC + EPCM)
    Total CAPEX = TIC + EPCM + Contingency

    Returns:
        dict: Full CAPEX breakdown with all line items
    """
    try:
        E = equipment_cost_usd
        installation = 0.35 * E
        civil = 0.25 * E
        instrumentation = 0.15 * E
        piping = 0.20 * E
        electrical = 0.10 * E
        TIC = E + installation + civil + instrumentation + piping + electrical  # = E × 2.05
        # Location factor: 1.0 for North America, 1.3–1.8 for remote/African sites
        TIC *= location_factor
        EPCM = 0.12 * TIC
        contingency = 0.15 * (TIC + EPCM)
        total = TIC + EPCM + contingency
        return {
            "equipment_usd": round(E, 0),
            "installation_usd": round(installation, 0),
            "civil_usd": round(civil, 0),
            "instrumentation_usd": round(instrumentation, 0),
            "piping_usd": round(piping, 0),
            "electrical_usd": round(electrical, 0),
            "tic_usd": round(TIC, 0),
            "epcm_usd": round(EPCM, 0),
            "contingency_usd": round(contingency, 0),
            "total_capex_usd": round(total, 0),
            "lang_factor": round(total / E, 3),
        }
    except Exception as e:
        logger.error("apply_lang_factors failed (equipment_cost=%.0f): %s", equipment_cost_usd, e)
        raise RuntimeError(f"apply_lang_factors failed for equipment_cost={equipment_cost_usd}") from e
