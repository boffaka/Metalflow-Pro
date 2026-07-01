# backend/engines/process_simulator.py
"""
MetalFlow Pro — Rigorous Process Simulator Engine.

Predicts plant performance from design criteria and LIMS data using
first-principles kinetic models for each unit operation.

Models implemented:
  - Comminution: Bond 3rd Law, Starkey SPI, HPGR pressing force
  - Flotation: First-order kinetics
  - Leaching: Shrinking-core / first-order with CN/DO/pH/T modifiers
  - CIP/CIL: Freundlich adsorption isotherm
  - Elution: First-order desorption
  - Thickener: Kynch settling theory
  - Detoxification: INCO SO2 stoichiometry

Reference basis: BMMC plant — 1517 t/h, 1.5 g/t Au, refractory ore.
"""
from __future__ import annotations

import logging
import math
import time
import uuid
from typing import Any, Optional

try:
    from .dcf import build_cashflows, compute_npv, compute_irr
    from .dc_generator import get_lims_summary
except ImportError:
    from dcf import build_cashflows, compute_npv, compute_irr
    from dc_generator import get_lims_summary

logger = logging.getLogger("mpdpms.process_simulator")

# ============================================================================
# Constants
# ============================================================================

try:
    from ..settings import get_settings
except ImportError:
    from settings import get_settings

_SETTINGS = get_settings()

try:
    from .. import config as _cfg
    _DEFAULT_AVAIL_PCT = float(_cfg.DEFAULT_AVAILABILITY_PCT)
    _DEFAULT_GOLD_PRICE = float(_cfg.DEFAULT_GOLD_PRICE_USD_OZ)
except (ImportError, AttributeError):
    try:
        import config as _cfg
        _DEFAULT_AVAIL_PCT = float(_cfg.DEFAULT_AVAILABILITY_PCT)
        _DEFAULT_GOLD_PRICE = float(_cfg.DEFAULT_GOLD_PRICE_USD_OZ)
    except (ImportError, AttributeError):
        _DEFAULT_AVAIL_PCT = 92.0
        _DEFAULT_GOLD_PRICE = 2200.0
DEFAULT_ORE_SG = _SETTINGS.default_ore_sg
GRID_CO2_KG_KWH = _SETTINGS.grid_co2_kg_kwh

# ============================================================================
# Unit operation model registry — maps op_code prefixes to model functions
# ============================================================================

try:
    from .op_model_registry import (
        OP_MODEL_MAP as _OP_MODEL_MAP,
        resolve_op_model,
        is_expected_passthrough,
    )
except ImportError:
    from engines.op_model_registry import (
        OP_MODEL_MAP as _OP_MODEL_MAP,
        resolve_op_model,
        is_expected_passthrough,
    )

_OP_MODEL_MAP = _OP_MODEL_MAP  # backwards compatibility for tests importing _OP_MODEL_MAP

# ============================================================================
# Industry default parameters (fallback when DC and LIMS are missing)
# ============================================================================

INDUSTRY_DEFAULTS: dict[str, dict[str, float]] = {
    "crushing": {
        "f80_mm": 600.0, "p80_mm": 150.0, "efficiency": 0.95,
    },
    "sag_milling": {
        "spi_min": 65.0, "f80_um": 150000.0, "p80_um": 2000.0,
    },
    "ball_milling": {
        "bwi_kwh_t": 18.0, "f80_um": 2000.0, "p80_um": 75.0,
        "ef1": 1.0, "ef2": 1.0, "ef3": 1.0, "ef4": 1.0,
    },
    "hpgr": {
        "spf_n_mm2": 3.5, "f80_mm": 32.0, "p80_mm": 6.0,
    },
    "regrind": {
        "sig_kwh_t": 12.0, "f80_um": 75.0, "p80_um": 20.0,
    },
    "flotation": {
        "k_rate": 1.5, "residence_time_min": 20.0, "r_max_pct": 90.0,
        "mass_pull_pct": 8.0,
    },
    "leaching": {
        "k_leach": 0.12, "time_h": 24.0, "cn_ppm": 300.0,
        "do_mg_l": 8.0, "ph": 10.5, "temp_c": 25.0,
    },
    "cil": {
        "k_leach": 0.12, "srt_h": 24.0, "cn_ppm": 300.0,
        "do_mg_l": 8.0, "ph": 10.5, "temp_c": 25.0,
        "carbon_conc_g_l": 20.0, "k_ads": 10.0, "n_freu": 0.5,
    },
    "cip": {
        # CIP total leach SRT aligned with CIL default and simulation_params cip_srt (24 h).
        # The 8 h previously here was carbon-contact time only, not total circuit SRT.
        "k_leach": 0.10, "srt_h": 24.0,
        "carbon_conc_g_l": 25.0, "k_ads": 10.0, "n_freu": 0.5,
    },
    "elution": {
        # AARL default (config.py AARL_TEMP_THRESHOLD_C = 130 °C).
        # Previously 110 °C (ZADRA); default method is AARL throughout the app.
        "temp_c": 130.0, "time_h": 8.0, "cn_pct": 1.0, "naoh_pct": 1.0,
    },
    "thickener": {
        "settling_flux_t_m2h": 0.25,
        "underflow_pct_solids": 55.0, "flocculant_g_t": 25.0,
    },
    "detox_inco": {
        "wad_cn_mg_l": 50.0, "so2_ratio": 6.0,
    },
    "gravity": {
        "grg_pct": 35.0,
        "gravity_slip_pct": 30.0,
        "knelson_recovery_pct": 50.0,
        "ilr_recovery_pct": 95.0,
        "mass_pull_pct": 0.2,
    },
    "preaeration": {
        "time_h": 4.0, "do_target_mg_l": 8.0,
    },
    "reagents": {
        # Aligned with industry_defaults.yaml and settings.py defaults.
        # Previously 0.8 (NaCN) and 2.5 (CaO) — both were too high as fallbacks.
        "nacn_kg_t": 0.5, "cao_kg_t": 1.2, "flocculant_g_t": 25.0,
    },
}


# ============================================================================
# Kinetic model functions
# ============================================================================

def bond_energy(wi: float, f80_um: float, p80_um: float,
                ef_factors: dict[str, float] | None = None) -> float:
    """Bond Work Index equation: W = 10 * Wi * (1/sqrt(P80) - 1/sqrt(F80)).

    Args:
        wi: Bond Work Index (kWh/t)
        f80_um: Feed 80% passing size (microns)
        p80_um: Product 80% passing size (microns)
        ef_factors: dict of efficiency factors (EF1-EF4)

    Returns:
        Specific energy (kWh/t)

    Raises:
        ValueError: if any size parameter is non-positive
    """
    if p80_um <= 0 or f80_um <= 0:
        raise ValueError(f"Size parameters must be > 0: f80={f80_um}, p80={p80_um}")
    ef = 1.0
    if ef_factors:
        for _k, v in ef_factors.items():
            ef *= v
    return 10.0 * wi * (1.0 / math.sqrt(p80_um) - 1.0 / math.sqrt(f80_um)) * ef


def sag_power(spi_min: float, throughput_tph: float) -> float:
    """Starkey SAG model: P = TPH * SPI / 60 / 0.65.

    Args:
        spi_min: SAG Power Index (minutes)
        throughput_tph: Throughput (t/h)

    Returns:
        Power draw (kW)
    """
    return throughput_tph * spi_min / 60.0 / 0.65


def hpgr_energy(spf_n_mm2: float, f80_mm: float, p80_mm: float) -> float:
    """HPGR specific energy from pressing force.

    Empirical model: E = 0.5 * SPF * ln(F80/P80)
    Typical SPF: 2-5 N/mm2

    Args:
        spf_n_mm2: Specific pressing force (N/mm2)
        f80_mm: Feed 80% passing size (mm)
        p80_mm: Product 80% passing size (mm)

    Returns:
        Specific energy (kWh/t)
    """
    if f80_mm <= 0 or p80_mm <= 0:
        raise ValueError(f"Size parameters must be > 0: f80={f80_mm}, p80={p80_mm}")
    return 0.5 * spf_n_mm2 * math.log(f80_mm / p80_mm)


def flotation_recovery(k_rate: float, residence_time_min: float,
                        r_max: float = 100.0) -> float:
    """First-order flotation model: R = Rmax * (1 - exp(-k*t)).

    Args:
        k_rate: Rate constant (1/min), typically 0.5-3.0 for gold sulfides
        residence_time_min: Total flotation time (min)
        r_max: Maximum recovery (%)

    Returns:
        Recovery (%)
    """
    if k_rate <= 0 or residence_time_min <= 0:
        return 0.0
    return r_max * (1.0 - math.exp(-k_rate * residence_time_min))


def leach_recovery(k_leach: float, time_h: float, cn_ppm: float,
                   do_mg_l: float, ph: float, temp_c: float = 25.0,
                   **kwargs) -> float:
    """Leaching kinetics with CN/DO/pH/temperature modifiers.

    Base model: R = 100 * (1 - exp(-k * t))
    Modified rate: k_eff = k * k_CN * k_DO * k_pH * k_T

    Modifier definitions:
      - k_CN = 1.0 if CN > 300, 0.85 if CN > 150, else 0.65
      - k_DO = 1.0 if DO > 6, 0.90 if DO > 4, else 0.75
      - k_pH = 1.0 if 10 <= pH <= 11.5, else 0.85
      - k_T  = 1.0 + 0.03 * (T - 25)  (Arrhenius approximation)

    Args:
        k_leach: Base rate constant (1/h)
        time_h: Leaching time (hours)
        cn_ppm: Cyanide concentration (ppm)
        do_mg_l: Dissolved oxygen (mg/L)
        ph: Slurry pH
        temp_c: Temperature (Celsius)

    Returns:
        Recovery (%)
    """
    if k_leach <= 0 or time_h <= 0:
        return 0.0

    # Cyanide modifier — thresholds configurable via kwargs
    cn_high = kwargs.get("cn_high_ppm", 300)
    cn_mid = kwargs.get("cn_mid_ppm", 150)
    if cn_ppm > cn_high:
        k_cn = 1.0
    elif cn_ppm > cn_mid:
        k_cn = kwargs.get("k_cn_mid", 0.85)
    else:
        k_cn = kwargs.get("k_cn_low", 0.65)

    # Dissolved oxygen modifier — thresholds configurable via kwargs
    do_high = kwargs.get("do_high_mg_l", 6.0)
    do_mid = kwargs.get("do_mid_mg_l", 4.0)
    if do_mg_l > do_high:
        k_do = 1.0
    elif do_mg_l > do_mid:
        k_do = kwargs.get("k_do_mid", 0.90)
    else:
        k_do = kwargs.get("k_do_low", 0.75)

    # pH modifier — optimal range configurable via kwargs
    ph_low = kwargs.get("ph_opt_low", 10.0)
    ph_high = kwargs.get("ph_opt_high", 11.5)
    k_ph = 1.0 if ph_low <= ph <= ph_high else kwargs.get("k_ph_off", 0.85)

    # Temperature modifier — Arrhenius equation
    # Ea configurable (default 40 kJ/mol for Au cyanide leaching)
    Ea_kJ = kwargs.get("activation_energy_kj_mol", 40.0)
    Ea_R = Ea_kJ * 1000.0 / 8.314
    T_ref = 298.15  # 25°C in Kelvin
    T_abs = temp_c + 273.15
    if T_abs > 0:
        k_t = math.exp(Ea_R * (1.0 / T_ref - 1.0 / T_abs))
    else:
        k_t = 1.0
    k_t = max(0.5, min(k_t, 5.0))  # clamp to reasonable range

    k_eff = k_leach * k_cn * k_do * k_ph * k_t
    return 100.0 * (1.0 - math.exp(-k_eff * time_h))


def cip_loading(au_solution_mg_l: float, carbon_conc_g_l: float,
                k_ads: float = 10.0, n_freu: float = 0.5,
                saturation_cap_g_t: float = 500.0) -> float:
    """Freundlich adsorption isotherm: q = K * C^(1/n).

    Args:
        au_solution_mg_l: Gold concentration in solution (mg/L)
        carbon_conc_g_l: Activated carbon concentration (g/L)
        k_ads: Freundlich K constant (ore-specific, from LIMS testwork)
        n_freu: Freundlich n exponent (ore-specific, from LIMS testwork)
        saturation_cap_g_t: Maximum carbon loading capacity (g Au/t carbon)

    Returns:
        Carbon loading (g Au / t carbon)
    """
    if au_solution_mg_l <= 0 or carbon_conc_g_l <= 0:
        return 0.0
    # q in mg/g then convert to g/t
    q_mg_g = k_ads * (au_solution_mg_l ** (1.0 / n_freu))
    # Convert mg Au per g carbon -> g Au per tonne carbon
    # mg/g = g/kg = 1000 g/t  (since 1 t = 1000 kg)
    q_g_t = q_mg_g * 1000.0
    # Saturation cap: activated carbon typically saturates at ~500 g Au/t
    q_g_t = min(q_g_t, saturation_cap_g_t)
    return round(q_g_t, 2)


def elution_efficiency(temp_c: float, time_h: float,
                       cn_pct: float, naoh_pct: float) -> float:
    """Elution efficiency based on temperature and reagent concentrations.

    Higher temperature + more CN + more NaOH = better elution.
    Model: eff = base * f_temp * f_cn * f_naoh * (1 - exp(-0.4 * t))

    Args:
        temp_c: Elution temperature (Celsius)
        time_h: Elution time (hours)
        cn_pct: Cyanide concentration in eluant (% w/v)
        naoh_pct: Caustic soda concentration in eluant (% w/v)

    Returns:
        Elution efficiency (%)
    """
    # Temperature factor: optimal at 110-130C for AARL, 90-95 for Zadra
    if temp_c >= 110:
        f_temp = 1.0
    elif temp_c >= 90:
        f_temp = 0.85 + 0.15 * (temp_c - 90) / 20.0
    elif temp_c >= 70:
        f_temp = 0.65 + 0.20 * (temp_c - 70) / 20.0
    else:
        f_temp = 0.50

    # CN factor: optimal at 1-2%
    f_cn = min(1.0, 0.6 + 0.4 * cn_pct / 1.0) if cn_pct > 0 else 0.6

    # NaOH factor: optimal at 1-2%
    f_naoh = min(1.0, 0.7 + 0.3 * naoh_pct / 1.0) if naoh_pct > 0 else 0.7

    # Time factor: first-order approach to completion
    f_time = 1.0 - math.exp(-0.4 * time_h) if time_h > 0 else 0.0

    return min(99.5, 100.0 * f_temp * f_cn * f_naoh * f_time)


def thickener_area(feed_tph: float, settling_flux_t_m2h: float) -> float:
    """Kynch settling theory: A = Feed / Flux.

    Args:
        feed_tph: Solids feed rate (t/h)
        settling_flux_t_m2h: Unit settling flux (t/m2/h)

    Returns:
        Required thickener area (m2)
    """
    if settling_flux_t_m2h <= 0:
        raise ValueError("Settling flux must be > 0")
    return feed_tph / settling_flux_t_m2h


def detox_so2_consumption(wad_cn_mg_l: float, flow_m3h: float,
                          so2_ratio: float = 6.0) -> float:
    """INCO process: SO2/CN stoichiometry.

    Default ratio: 6 g SO2 per g CN WAD.

    Args:
        wad_cn_mg_l: WAD cyanide concentration (mg/L)
        flow_m3h: Tailings flow rate (m3/h)
        so2_ratio: SO2 to CN mass ratio

    Returns:
        SO2 consumption (kg/h)
    """
    # CN mass rate = concentration * flow = mg/L * m3/h = g/h (since 1 mg/L = 1 g/m3)
    cn_mass_g_h = wad_cn_mg_l * flow_m3h
    so2_g_h = cn_mass_g_h * so2_ratio
    return so2_g_h / 1000.0  # kg/h


# ============================================================================
# Parameter resolution
# ============================================================================

def _get_param(op_code: str, param_name: str,
               dc_params: dict, lims_data: dict,
               default: float) -> tuple[float, str]:
    """Resolve a parameter value with priority: DC > LIMS > default.

    Args:
        op_code: Operation code (e.g. 'BALL_MILL')
        param_name: Parameter name to look up
        dc_params: Design criteria dict keyed by (op_code, item_lower)
        lims_data: LIMS summary dict from get_lims_summary
        default: Industry default fallback

    Returns:
        (value, source) where source is 'DC', 'LIMS', or 'DEFAULT'
    """
    # 1. Try design criteria
    dc_key = (op_code, param_name.lower())
    if dc_key in dc_params and dc_params[dc_key] is not None:
        return float(dc_params[dc_key]), "DC"

    # Also try without op_code (generic params)
    generic_key = (None, param_name.lower())
    if generic_key in dc_params and dc_params[generic_key] is not None:
        return float(dc_params[generic_key]), "DC"

    # 2. Try LIMS data
    if lims_data:
        lims_val = lims_data.get(param_name)
        if lims_val is not None:
            return float(lims_val), "LIMS"

    # 3. Fall back to default
    return float(default), "DEFAULT"


def _load_simulation_param_overrides(project_id: str, cursor, base: dict | None = None) -> dict:
    """Merge simulation_params (gravity, leach overrides) into params_override."""
    merged = dict(base or {})
    if cursor is None:
        return merged
    try:
        cursor.execute(
            "SELECT param_key, param_value FROM simulation_params "
            "WHERE project_id = %s AND param_value IS NOT NULL",
            (project_id,),
        )
        sim = {
            row["param_key"]: float(row["param_value"])
            for row in cursor.fetchall()
            if row.get("param_key") is not None
        }
    except Exception as exc:
        logger.debug("simulation_params not loaded: %s", exc)
        return merged

    try:
        from .gravity_model import gravity_dc_from_simulation
    except ImportError:
        from engines.gravity_model import gravity_dc_from_simulation

    for key, val in gravity_dc_from_simulation(sim).items():
        merged.setdefault(key, val)
    for key in (
        "gravity_grg",
        "gravity_slip",
        "gravity_rec",
        "gravity_ilr",
        "gravity_mass_pull",
    ):
        if key in sim:
            merged.setdefault(key, sim[key])
    return merged


def _load_dc_params(template_id: str, cursor) -> dict:
    """Load all design criteria values for a template into a lookup dict.

    Returns: dict keyed by (op_code, item_lower) -> design_value
    """
    try:
        cursor.execute(
            "SELECT op_code, item, design_value, lims_value "
            "FROM design_criteria_v2 "
            "WHERE template_id = %s AND enabled = true "
            "ORDER BY sort_order",
            (template_id,),
        )
        params = {}
        for row in cursor.fetchall():
            key = (row["op_code"], (row["item"] or "").lower())
            val = row["design_value"]
            if val is None:
                val = row["lims_value"]
            if val is not None:
                params[key] = float(val)
        return params
    except Exception as e:
        logger.error("_load_dc_params failed for template_id=%s: %s", template_id, e)
        return {}


def _load_project_economics(project_id: str, cursor) -> dict:
    """Load project economic parameters."""
    try:
        cursor.execute(
            "SELECT target_tph, gold_grade_g_t, capex_musd, "
            "       gold_price_usd_oz, discount_rate_pct, mine_life_years, "
            "       operating_hours_day, availability_pct, electricity_rate "
            "FROM projects WHERE id = %s",
            (project_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Project {project_id} not found")
        return {k: (float(v) if v is not None else None) for k, v in row.items()}
    except ValueError:
        raise
    except Exception as e:
        logger.error("_load_project_economics failed for project_id=%s: %s", project_id, e)
        raise RuntimeError(f"_load_project_economics failed for project {project_id}: {e}") from e


def _load_enabled_operations(template_id: str, cursor) -> list[dict]:
    """Load enabled operations for a template in process order."""
    rows: list[dict] = []
    try:
        cursor.execute(
            "SELECT co.op_code, co.sort_order, uc.label, uc.category "
            "FROM circuit_operations co "
            "JOIN unit_operations_catalog uc ON uc.op_code = co.op_code "
            "WHERE co.template_id = %s AND COALESCE(co.enabled, TRUE) = TRUE "
            "ORDER BY co.sort_order",
            (template_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        logger.debug("circuit_operations read failed template_id=%s: %s", template_id, e)

    if not rows:
        try:
            cursor.execute(
                "SELECT cto.op_code, cto.sort_order, uc.label, uc.category "
                "FROM circuit_template_operations cto "
                "JOIN unit_operations_catalog uc ON uc.op_code = cto.op_code "
                "WHERE cto.template_id = %s "
                "ORDER BY cto.sort_order",
                (template_id,),
            )
            rows = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("_load_enabled_operations failed for template_id=%s: %s", template_id, e)
    return rows


# ============================================================================
# Stream propagation helpers
# ============================================================================

def _make_stream(solids_tph: float, au_g_t: float, pct_solids: float = 50.0,
                 p80_um: float | None = None) -> dict:
    """Create a process stream dictionary."""
    return {
        "solids_tph": solids_tph,
        "au_g_t": au_g_t,
        "pct_solids": pct_solids,
        "p80_um": p80_um,
        "au_mass_g_h": solids_tph * au_g_t,
    }


def _update_stream_au(stream: dict) -> dict:
    """Recalculate gold mass flow from grade and tonnage."""
    stream["au_mass_g_h"] = stream["solids_tph"] * stream["au_g_t"]
    return stream


# ============================================================================
# Per-operation simulation functions
# ============================================================================

def _sim_crushing(stream: dict, dc: dict, lims: dict,
                  override: dict, warnings: list) -> dict:
    """Simulate crushing — size reduction, no gold recovery change."""
    _f80 = stream.get("p80_um") or 600000.0  # ROM ~600mm in microns
    p80_mm, src = _get_param("GIRATOIRE", "product p80", dc, lims, 150.0)
    p80_um = p80_mm * 1000.0  # mm to um
    eff = override.get("crushing_efficiency", 0.95)

    return {
        "model": "size_reduction",
        "product_stream": _make_stream(
            stream["solids_tph"] * eff, stream["au_g_t"],
            stream["pct_solids"], p80_um,
        ),
        "energy_kwh_t": 0.5,  # crusher energy relatively small
        "p80_um": p80_um,
    }


def _sim_sag_milling(stream: dict, dc: dict, lims: dict,
                     override: dict, warnings: list) -> dict:
    """Simulate SAG mill using Starkey SPI model."""
    spi, src = _get_param("SAG_MILL", "b1.mia_kwh_t", dc, lims, 65.0)
    if src == "DEFAULT":
        warnings.append("SAG: SPI not found in DC/LIMS — using default 65 min")

    tph = stream["solids_tph"]
    power_kw = sag_power(spi, tph)
    energy_kwh_t = power_kw / tph if tph > 0 else 0

    # SAG product P80 typically 1500-3000 um
    p80_um = override.get("sag_p80_um", 2000.0)

    return {
        "model": "starkey_spi",
        "product_stream": _make_stream(tph, stream["au_g_t"], 65.0, p80_um),
        "power_kw": round(power_kw, 1),
        "energy_kwh_t": round(energy_kwh_t, 3),
        "spi_min": spi,
    }


def _sim_ball_milling(stream: dict, dc: dict, lims: dict,
                      override: dict, warnings: list) -> dict:
    """Simulate ball mill using Bond 3rd Law."""
    wi, wi_src = _get_param("BALL_MILL", "b1.bwi_kwh_t", dc, lims,
                            INDUSTRY_DEFAULTS["ball_milling"]["bwi_kwh_t"])
    if wi_src == "DEFAULT":
        warnings.append("Ball mill: BWi not found in DC/LIMS — using default 18.0 kWh/t")

    f80 = stream.get("p80_um") or INDUSTRY_DEFAULTS["ball_milling"]["f80_um"]
    p80_dc, _ = _get_param("BALL_MILL", "b1.p80_target_um", dc, lims,
                           INDUSTRY_DEFAULTS["ball_milling"]["p80_um"])
    if override.get("p80_um") is not None:
        p80 = float(override["p80_um"])
    else:
        p80 = p80_dc

    ef_factors = {}
    for ef_name in ("ef1", "ef2", "ef3", "ef4"):
        val = override.get(ef_name)
        if val is not None:
            ef_factors[ef_name] = val

    energy = bond_energy(wi, f80, p80, ef_factors or None)
    tph = stream["solids_tph"]
    power_kw = energy * tph

    return {
        "model": "bond_3rd_law",
        "product_stream": _make_stream(tph, stream["au_g_t"], 65.0, p80),
        "power_kw": round(power_kw, 1),
        "energy_kwh_t": round(energy, 3),
        "bwi": wi,
        "f80_um": f80,
        "p80_um": p80,
    }


def _sim_hpgr(stream: dict, dc: dict, lims: dict,
              override: dict, warnings: list) -> dict:
    """Simulate HPGR using specific pressing force model."""
    defaults = INDUSTRY_DEFAULTS["hpgr"]
    spf, _ = _get_param("HPGR", "specific pressing force", dc, lims, defaults["spf_n_mm2"])
    f80_mm = (stream.get("p80_um") or defaults["f80_mm"] * 1000.0) / 1000.0
    p80_mm, _ = _get_param("HPGR", "hpgr product p80", dc, lims, defaults["p80_mm"])

    energy = hpgr_energy(spf, f80_mm, p80_mm)
    tph = stream["solids_tph"]
    p80_um = p80_mm * 1000.0

    return {
        "model": "hpgr_spf",
        "product_stream": _make_stream(tph, stream["au_g_t"], stream["pct_solids"], p80_um),
        "energy_kwh_t": round(energy, 3),
        "power_kw": round(energy * tph, 1),
        "spf_n_mm2": spf,
    }


def _sim_classification(stream: dict, dc: dict, lims: dict,
                        override: dict, warnings: list) -> dict:
    """Simulate hydrocyclone classification — split into O/F and U/F."""
    cut_size_um = override.get("cyclone_cut_um", 75.0)
    circ_load = override.get("circ_load_pct", 250.0) / 100.0

    tph = stream["solids_tph"]
    of_tph = tph / (1.0 + circ_load)
    uf_tph = tph - of_tph

    return {
        "model": "classification",
        "product_stream": _make_stream(of_tph, stream["au_g_t"], 35.0, cut_size_um),
        "recirculation_tph": round(uf_tph, 1),
        "cut_size_um": cut_size_um,
        "circ_load_pct": circ_load * 100.0,
    }


def _sim_screening(stream: dict, dc: dict, lims: dict,
                   override: dict, warnings: list) -> dict:
    """Simulate vibrating screen — pass-through with size split."""
    return {
        "model": "screening",
        "product_stream": _make_stream(
            stream["solids_tph"], stream["au_g_t"],
            stream["pct_solids"], stream.get("p80_um"),
        ),
        "energy_kwh_t": 0.1,
    }


def _sim_regrind(stream: dict, dc: dict, lims: dict,
                 override: dict, warnings: list) -> dict:
    """Simulate regrind mill (IsaMill/Vertimill/SMD)."""
    defaults = INDUSTRY_DEFAULTS["regrind"]
    sig, _ = _get_param("ISAMILL", "specific energy", dc, lims, defaults["sig_kwh_t"])
    p80_um, _ = _get_param("ISAMILL", "regrind p80", dc, lims, defaults["p80_um"])
    tph = stream["solids_tph"]

    return {
        "model": "regrind_sig",
        "product_stream": _make_stream(tph, stream["au_g_t"], stream["pct_solids"], p80_um),
        "energy_kwh_t": round(sig, 3),
        "power_kw": round(sig * tph, 1),
    }


def _sim_gravity(stream: dict, dc: dict, lims: dict,
                 override: dict, warnings: list) -> dict:
    """Simulate gravity concentration (GRG × Knelson × slip × ILR)."""
    try:
        from .gravity_model import (
            blended_head_grade_g_t,
            plant_gravity_recovery_pct,
            resolve_gravity_params,
        )
    except ImportError:
        from engines.gravity_model import (
            blended_head_grade_g_t,
            plant_gravity_recovery_pct,
            resolve_gravity_params,
        )

    defaults = INDUSTRY_DEFAULTS["gravity"]
    merged: dict = {
        "grg_pct": defaults["grg_pct"],
        "gravity_slip_pct": defaults["gravity_slip_pct"],
        "knelson_unit_recovery_pct": defaults["knelson_recovery_pct"],
        "ilr_recovery_pct": defaults["ilr_recovery_pct"],
        "gravity_mass_pull_pct": defaults["mass_pull_pct"],
    }
    for key, val in dc.items():
        if isinstance(key, tuple):
            continue
        if isinstance(val, (int, float)) and key in merged:
            merged[key] = val
    merged.update({k: v for k, v in override.items() if v is not None})

    lims_grg = lims.get("c2", {}).get("grg_rec_pct") if isinstance(lims.get("c2"), dict) else None
    if lims_grg is not None and "gravity_grg" not in override and "grg_pct" not in override:
        merged["grg_pct"] = float(lims_grg)

    gp = resolve_gravity_params(merged)
    plant_rec_pct = plant_gravity_recovery_pct(gp)
    tph = stream["solids_tph"]
    au = stream["au_g_t"]
    au_recovered_g_h = au * tph * (plant_rec_pct / 100.0)
    tails_au = blended_head_grade_g_t(au, plant_rec_pct)

    product = _make_stream(
        tph, tails_au,
        stream["pct_solids"], stream.get("p80_um"),
    )

    return {
        "model": "gravity_plant_recovery",
        "product_stream": product,
        "recovery_pct": round(plant_rec_pct, 2),
        "au_recovered_g_h": round(au_recovered_g_h, 3),
        "grg_pct": gp.grg_pct,
        "gravity_slip_pct": gp.gravity_slip_pct,
        "knelson_recovery_pct": gp.knelson_unit_recovery_pct,
        "ilr_recovery_pct": gp.ilr_recovery_pct,
    }


def _sim_flotation(stream: dict, dc: dict, lims: dict,
                   override: dict, warnings: list,
                   op_code: str = "FLOTATION_ROUGHER") -> dict:
    """Simulate flotation using first-order kinetic model.

    ``op_code`` selects which stage's design criteria to read so a scavenger
    (FLOTATION_SCAVENGER) uses its own k/r_max/mass-pull instead of re-applying
    the rougher's recovery to the rougher tails. Falls back to industry defaults
    when the stage has no dedicated DC.
    """
    defaults = INDUSTRY_DEFAULTS["flotation"]
    k_rate, _ = _get_param(op_code, "flotation k rate", dc, lims,
                           defaults["k_rate"])
    res_time, _ = _get_param(op_code, "flotation residence time", dc, lims,
                             defaults["residence_time_min"])
    r_max, _ = _get_param(op_code, "g1.au_recovery_pct", dc, lims,
                          defaults["r_max_pct"])
    mass_pull_pct, _ = _get_param(op_code, "g1.mass_pull_pct", dc, lims,
                                  defaults["mass_pull_pct"])

    rec_pct = flotation_recovery(k_rate, res_time, r_max)
    rec_frac = rec_pct / 100.0
    tph = stream["solids_tph"]
    conc_tph = tph * mass_pull_pct / 100.0
    tails_tph = tph - conc_tph

    conc_au = stream["au_g_t"] * rec_frac * tph / conc_tph if conc_tph > 0 else 0.0
    tails_au = stream["au_g_t"] * (1.0 - rec_frac) * tph / tails_tph if tails_tph > 0 else 0.0

    product = _make_stream(tails_tph, tails_au, stream["pct_solids"], stream.get("p80_um"))

    return {
        "model": "first_order_kinetics",
        "product_stream": product,
        "concentrate_stream": _make_stream(conc_tph, conc_au, 35.0, stream.get("p80_um")),
        "recovery_pct": round(rec_pct, 2),
        "mass_pull_pct": round(mass_pull_pct, 2),
        "k_rate": k_rate,
        "residence_time_min": res_time,
    }


def _sim_preaeration(stream: dict, dc: dict, lims: dict,
                     override: dict, warnings: list) -> dict:
    """Simulate preaeration — no mass change, just DO increase."""
    return {
        "model": "preaeration",
        "product_stream": _make_stream(
            stream["solids_tph"], stream["au_g_t"],
            stream["pct_solids"], stream.get("p80_um"),
        ),
        "energy_kwh_t": 0.3,
    }


def _sim_leaching(stream: dict, dc: dict, lims: dict,
                  override: dict, warnings: list) -> dict:
    """Simulate CIL/leaching using first-order kinetics with modifiers."""
    defaults = INDUSTRY_DEFAULTS["leaching"]
    raw_val, raw_src = _get_param("LEACH_CUVES", "d1.au_recovery_pct", dc, lims, defaults["k_leach"])
    time_h, _ = _get_param("LEACH_CUVES", "leach time", dc, lims, defaults["time_h"])
    cn_ppm = override.get("cn_ppm", defaults["cn_ppm"])
    do_mg_l = override.get("do_mg_l", defaults["do_mg_l"])
    ph = override.get("ph", defaults["ph"])
    temp_c = override.get("temp_c", defaults["temp_c"])

    # DC stores design recovery % (e.g. 92.0), not a kinetic rate constant.
    # Using it directly as k_leach would give exp(-92*24) ≈ 0 → always 100%.
    if raw_val > 1.0 and raw_src == "DC":
        rec_pct = min(raw_val, 99.5)
        k_leach = -math.log(1.0 - rec_pct / 100.0) / max(time_h, 1.0)
    else:
        k_leach = raw_val
        rec_pct = leach_recovery(k_leach, time_h, cn_ppm, do_mg_l, ph, temp_c)
    rec_frac = rec_pct / 100.0
    tph = stream["solids_tph"]
    tails_au = stream["au_g_t"] * (1.0 - rec_frac)

    nacn, _ = _get_param("LEACH_CUVES", "d1.nacn_consumption_kg_t", dc, lims,
                         INDUSTRY_DEFAULTS["reagents"]["nacn_kg_t"])
    cao, _ = _get_param("LEACH_CUVES", "d1.cao_consumption_kg_t", dc, lims,
                        INDUSTRY_DEFAULTS["reagents"]["cao_kg_t"])

    product = _make_stream(tph, tails_au, stream["pct_solids"], stream.get("p80_um"))

    return {
        "model": "leach_first_order_modified",
        "product_stream": product,
        "recovery_pct": round(rec_pct, 2),
        "nacn_kg_t": nacn,
        "cao_kg_t": cao,
        "k_leach": k_leach,
        "time_h": time_h,
        "cn_ppm": cn_ppm,
        "do_mg_l": do_mg_l,
    }


def _sim_cil(stream: dict, dc: dict, lims: dict,
             override: dict, warnings: list) -> dict:
    """Simulate CIL (Carbon in Leach) — combined leaching + adsorption."""
    defaults = INDUSTRY_DEFAULTS["cil"]
    raw_val, raw_src = _get_param("CIL", "d1.au_recovery_pct", dc, lims, defaults["k_leach"])
    srt_dc, _ = _get_param("CIL", "cil retention time", dc, lims, defaults["srt_h"])
    if override.get("srt_h") is not None:
        srt_h = float(override["srt_h"])
    else:
        srt_h = srt_dc
    cn_ppm = override.get("cn_ppm", defaults["cn_ppm"])
    do_mg_l = override.get("do_mg_l", defaults["do_mg_l"])
    ph = override.get("ph", defaults["ph"])
    temp_c = override.get("temp_c", defaults["temp_c"])

    # DC stores design recovery % (e.g. 92.0), not a kinetic rate constant.
    if raw_val > 1.0 and raw_src == "DC":
        rec_pct = min(raw_val, 99.5)
        k_leach = -math.log(1.0 - rec_pct / 100.0) / max(srt_h, 1.0)
    else:
        k_leach = raw_val
        rec_pct = leach_recovery(k_leach, srt_h, cn_ppm, do_mg_l, ph, temp_c)
    rec_frac = rec_pct / 100.0
    tph = stream["solids_tph"]
    tails_au = stream["au_g_t"] * (1.0 - rec_frac)

    # CIP adsorption loading
    carbon_conc = override.get("carbon_conc_g_l", defaults["carbon_conc_g_l"])
    au_solution = stream["au_g_t"] * rec_frac * 0.001  # approximate mg/L
    loading = cip_loading(au_solution, carbon_conc, defaults["k_ads"], defaults["n_freu"])

    nacn, _ = _get_param("CIL", "d1.nacn_consumption_kg_t", dc, lims,
                         INDUSTRY_DEFAULTS["reagents"]["nacn_kg_t"])
    cao, _ = _get_param("CIL", "d1.cao_consumption_kg_t", dc, lims,
                        INDUSTRY_DEFAULTS["reagents"]["cao_kg_t"])

    product = _make_stream(tph, tails_au, stream["pct_solids"], stream.get("p80_um"))

    return {
        "model": "cil_leach_freundlich",
        "product_stream": product,
        "recovery_pct": round(rec_pct, 2),
        "carbon_loading_g_t": loading,
        "nacn_kg_t": nacn,
        "cao_kg_t": cao,
        "srt_h": srt_h,
    }


def _sim_cip(stream: dict, dc: dict, lims: dict,
             override: dict, warnings: list) -> dict:
    """Simulate CIP (Carbon in Pulp) — adsorption only (after leaching)."""
    defaults = INDUSTRY_DEFAULTS["cip"]
    srt_h, _ = _get_param("CIP", "cip retention time", dc, lims, defaults["srt_h"])
    carbon_conc = override.get("carbon_conc_g_l", defaults["carbon_conc_g_l"])

    # CIP adsorbs residual gold from solution
    au_in_solution_mg_l = stream["au_g_t"] * 0.001  # approximate
    loading = cip_loading(au_in_solution_mg_l, carbon_conc,
                          defaults["k_ads"], defaults["n_freu"])

    # CIP recovery of remaining gold (typically 95-99% of solution gold)
    cip_rec = 0.97
    tails_au = stream["au_g_t"] * (1.0 - cip_rec)

    product = _make_stream(stream["solids_tph"], tails_au,
                           stream["pct_solids"], stream.get("p80_um"))

    return {
        "model": "cip_freundlich",
        "product_stream": product,
        "recovery_pct": round(cip_rec * 100, 2),
        "carbon_loading_g_t": loading,
        "srt_h": srt_h,
    }


def _sim_thickener(stream: dict, dc: dict, lims: dict,
                   override: dict, warnings: list) -> dict:
    """Simulate thickener using Kynch settling theory."""
    defaults = INDUSTRY_DEFAULTS["thickener"]
    flux, _ = _get_param("EPAISSISSEUR", "settling flux", dc, lims,
                         defaults["settling_flux_t_m2h"])
    uf_pct, _ = _get_param("EPAISSISSEUR", "e1.underflow_density_pct_solids", dc, lims,
                           defaults["underflow_pct_solids"])
    floc, _ = _get_param("EPAISSISSEUR", "e1.flocculant_dosage_g_t", dc, lims,
                         defaults["flocculant_g_t"])

    tph = stream["solids_tph"]
    area = thickener_area(tph, flux)

    product = _make_stream(tph, stream["au_g_t"], uf_pct, stream.get("p80_um"))

    return {
        "model": "kynch_settling",
        "product_stream": product,
        "area_m2": round(area, 1),
        "flocculant_g_t": floc,
        "underflow_pct_solids": uf_pct,
    }


def _sim_elution(stream: dict, dc: dict, lims: dict,
                 override: dict, warnings: list) -> dict:
    """Simulate elution — strip gold from loaded carbon."""
    defaults = INDUSTRY_DEFAULTS["elution"]
    temp, _ = _get_param("ELUTION_AARL", "h1.elution_t_c", dc, lims, defaults["temp_c"])
    time_h = override.get("elution_time_h", defaults["time_h"])
    cn_pct = override.get("elution_cn_pct", defaults["cn_pct"])
    naoh_pct = override.get("elution_naoh_pct", defaults["naoh_pct"])

    eff = elution_efficiency(temp, time_h, cn_pct, naoh_pct)

    return {
        "model": "elution_first_order",
        "product_stream": stream.copy(),
        "elution_efficiency_pct": round(eff, 2),
        "temp_c": temp,
    }


def _sim_electrowinning(stream: dict, dc: dict, lims: dict,
                        override: dict, warnings: list) -> dict:
    """Simulate electrowinning — deposits gold from pregnant eluate."""
    ew_eff = override.get("ew_efficiency", 0.995)
    return {
        "model": "electrowinning",
        "product_stream": stream.copy(),
        "ew_efficiency_pct": round(ew_eff * 100, 2),
        "energy_kwh_t": 1.5,
    }


def _sim_smelting(stream: dict, dc: dict, lims: dict,
                  override: dict, warnings: list) -> dict:
    """Simulate smelting/refining."""
    smelt_rec = override.get("smelting_recovery", 0.995)
    return {
        "model": "smelting",
        "product_stream": stream.copy(),
        "smelting_recovery_pct": round(smelt_rec * 100, 2),
    }


def _sim_detox_inco(stream: dict, dc: dict, lims: dict,
                    override: dict, warnings: list) -> dict:
    """Simulate INCO SO2/Air detoxification."""
    defaults = INDUSTRY_DEFAULTS["detox_inco"]
    wad_cn = override.get("wad_cn_mg_l", defaults["wad_cn_mg_l"])
    so2_ratio = override.get("so2_ratio", defaults["so2_ratio"])

    tph = stream["solids_tph"]
    pct_s = stream["pct_solids"]
    # Estimate flow from solids and percent solids
    if pct_s > 0:
        slurry_tph = tph / (pct_s / 100.0)
        water_tph = slurry_tph - tph
        flow_m3h = water_tph / 1.0  # water SG = 1
    else:
        flow_m3h = tph * 2.0  # rough estimate

    so2_kg_h = detox_so2_consumption(wad_cn, flow_m3h, so2_ratio)

    return {
        "model": "inco_so2",
        "product_stream": _make_stream(tph, stream["au_g_t"],
                                       stream["pct_solids"], stream.get("p80_um")),
        "so2_kg_h": round(so2_kg_h, 2),
        "wad_cn_mg_l": wad_cn,
    }


def _sim_detox_caro(stream: dict, dc: dict, lims: dict,
                    override: dict, warnings: list) -> dict:
    """Simulate Caro's acid detoxification."""
    return {
        "model": "caro_acid",
        "product_stream": _make_stream(
            stream["solids_tph"], stream["au_g_t"],
            stream["pct_solids"], stream.get("p80_um"),
        ),
    }


def _sim_detox_peroxide(stream: dict, dc: dict, lims: dict,
                        override: dict, warnings: list) -> dict:
    """Simulate hydrogen peroxide detoxification."""
    return {
        "model": "h2o2_detox",
        "product_stream": _make_stream(
            stream["solids_tph"], stream["au_g_t"],
            stream["pct_solids"], stream.get("p80_um"),
        ),
    }


_REFRACTORY_ALERTS: dict[str, str] = {
    "BIOX": (
        "BIOX: passthrough massique — valider sulfures réfractaires, "
        "libération Au et cyanuration post-BIOX en PFS."
    ),
    "POX": (
        "POX: passthrough cinétique — O2/pression/température et lixiviation "
        "post-oxydation non modélisées dans ce simulateur."
    ),
    "ROASTING": (
        "ROASTING: passthrough — calcination SO2/As et récupération Au "
        "post-roast à confirmer hors modèle."
    ),
    "UFG": (
        "UFG: passthrough — broyage ultra-fin et impact GRG/lixiviation "
        "non résolus ici (utiliser BALL_MILL + gravité)."
    ),
}


def _sim_refractory_pretreatment(
    stream: dict,
    dc: dict,
    lims: dict,
    override: dict,
    warnings: list,
    op_code: str = "BIOX",
) -> dict:
    """Explicit passthrough for refractory pretreatment with metallurgical alert."""
    msg = _REFRACTORY_ALERTS.get(op_code, _REFRACTORY_ALERTS["BIOX"])
    warnings.append(f"[{op_code}] {msg}")
    return {
        "model": "refractory_passthrough",
        "op_code": op_code,
        "product_stream": _make_stream(
            stream["solids_tph"], stream["au_g_t"],
            stream["pct_solids"], stream.get("p80_um"),
        ),
        "metallurgical_alert": msg,
        "recovery_pct": None,
    }


def _merge_feed_recirc(feed: dict, recirc: dict | None) -> dict:
    """Blend fresh feed with hydrocyclone underflow recirculation."""
    if not recirc or recirc.get("solids_tph", 0) <= 0:
        return feed.copy()
    f_tph = feed["solids_tph"]
    r_tph = recirc["solids_tph"]
    total = f_tph + r_tph
    au = (
        (feed["au_g_t"] * f_tph + recirc["au_g_t"] * r_tph) / total
        if total > 0 else feed["au_g_t"]
    )
    p80_vals = [v for v in (feed.get("p80_um"), recirc.get("p80_um")) if v]
    p80 = max(p80_vals) if p80_vals else feed.get("p80_um")
    return _make_stream(total, au, feed.get("pct_solids", 65.0), p80)


def _simulate_mill_classifier_loop(
    op_mill: dict,
    op_cy: dict,
    feed_stream: dict,
    dc_params: dict,
    lims_data: dict,
    override: dict,
    warnings: list,
    mill_fn,
    mill_label: str,
    max_inner: int = 15,
    inner_tol: float = 0.001,
) -> tuple[dict, list[dict], float, float, float]:
    """Converge mill ↔ hydrocyclone recirculation (ball or rod mill)."""
    mill_code = op_mill["op_code"]
    cy_code = op_cy["op_code"]
    recirc_stream: dict | None = None
    prev_recirc_tph: float | None = None
    inner_iters = 0
    mill_result: dict = {}
    cy_result: dict = {}
    combined: dict = feed_stream

    for inner_iters in range(1, max_inner + 1):
        combined = _merge_feed_recirc(feed_stream, recirc_stream)
        mill_result = mill_fn(combined, dc_params, lims_data, override, warnings)
        cy_result = _sim_classification(
            mill_result["product_stream"], dc_params, lims_data, override, warnings,
        )
        recirc_tph = float(cy_result.get("recirculation_tph") or 0)
        if prev_recirc_tph is not None and prev_recirc_tph > 0:
            if abs(recirc_tph - prev_recirc_tph) / prev_recirc_tph < inner_tol:
                break
        prev_recirc_tph = recirc_tph
        if recirc_tph > 0:
            mill_prod = mill_result["product_stream"]
            recirc_stream = _make_stream(
                recirc_tph,
                mill_prod["au_g_t"],
                mill_prod.get("pct_solids", 65.0),
                mill_prod.get("p80_um"),
            )
        else:
            recirc_stream = None

    product = cy_result.get("product_stream", combined)
    loop_meta = {
        "inner_iterations": inner_iters,
        "recirculation_tph": round(prev_recirc_tph or 0, 1),
        "circ_load_pct": cy_result.get("circ_load_pct"),
        "loop_type": "mill_classifier",
    }

    def _op_record(op_info: dict, result: dict, model_name: str) -> dict:
        inp = combined if op_info["op_code"] == mill_code else mill_result["product_stream"]
        prod = result.get("product_stream", inp)
        perf = {k: v for k, v in result.items() if k not in ("product_stream", "model")}
        if op_info["op_code"] == cy_code:
            perf = {**perf, **loop_meta}
        return {
            "op_code": op_info["op_code"],
            "label": op_info.get("label", op_info["op_code"]),
            "model_used": result.get("model", model_name),
            "inputs": {
                "solids_tph": round(inp["solids_tph"], 1),
                "au_g_t": round(inp["au_g_t"], 4),
                "p80_um": inp.get("p80_um"),
            },
            "outputs": {
                "solids_tph": round(prod["solids_tph"], 1),
                "au_g_t": round(prod["au_g_t"], 4),
                "p80_um": prod.get("p80_um"),
            },
            "performance": perf,
        }

    op_results = [
        _op_record(op_mill, mill_result, mill_label),
        _op_record(op_cy, cy_result, "classification"),
    ]
    energy = mill_result.get("energy_kwh_t", 0) + cy_result.get("energy_kwh_t", 0)
    return product, op_results, energy, 0.0, 0.0


def _simulate_ball_cyclone_loop(
    op_bm: dict,
    op_cy: dict,
    feed_stream: dict,
    dc_params: dict,
    lims_data: dict,
    override: dict,
    warnings: list,
    max_inner: int = 15,
    inner_tol: float = 0.001,
) -> tuple[dict, list[dict], float, float, float]:
    return _simulate_mill_classifier_loop(
        op_bm, op_cy, feed_stream, dc_params, lims_data, override, warnings,
        _sim_ball_milling, "bond_3rd_law", max_inner, inner_tol,
    )


def _simulate_sag_ball_cyclone_loop(
    op_sag: dict,
    op_bm: dict,
    op_cy: dict,
    feed_stream: dict,
    dc_params: dict,
    lims_data: dict,
    override: dict,
    warnings: list,
    max_inner: int = 15,
    inner_tol: float = 0.001,
) -> tuple[dict, list[dict], float, float, float]:
    """Converge SAG → ball → cyclone with underflow recirculated to SAG feed."""
    op_sag["op_code"]
    op_bm["op_code"]
    cy_code = op_cy["op_code"]
    recirc_stream: dict | None = None
    prev_recirc_tph: float | None = None
    inner_iters = 0
    sag_result: dict = {}
    bm_result: dict = {}
    cy_result: dict = {}
    sag_in: dict = feed_stream

    for inner_iters in range(1, max_inner + 1):
        sag_in = _merge_feed_recirc(feed_stream, recirc_stream)
        sag_result = _sim_sag_milling(sag_in, dc_params, lims_data, override, warnings)
        bm_result = _sim_ball_milling(
            sag_result["product_stream"], dc_params, lims_data, override, warnings,
        )
        cy_result = _sim_classification(
            bm_result["product_stream"], dc_params, lims_data, override, warnings,
        )
        recirc_tph = float(cy_result.get("recirculation_tph") or 0)
        if prev_recirc_tph is not None and prev_recirc_tph > 0:
            if abs(recirc_tph - prev_recirc_tph) / prev_recirc_tph < inner_tol:
                break
        prev_recirc_tph = recirc_tph
        if recirc_tph > 0:
            bm_prod = bm_result["product_stream"]
            recirc_stream = _make_stream(
                recirc_tph,
                bm_prod["au_g_t"],
                bm_prod.get("pct_solids", 65.0),
                bm_prod.get("p80_um"),
            )
        else:
            recirc_stream = None

    product = cy_result.get("product_stream", sag_in)
    loop_meta = {
        "inner_iterations": inner_iters,
        "recirculation_tph": round(prev_recirc_tph or 0, 1),
        "circ_load_pct": cy_result.get("circ_load_pct"),
        "loop_type": "sag_ball_cyclone",
    }

    def _op_record(op_info: dict, result: dict, model_name: str, inp: dict) -> dict:
        prod = result.get("product_stream", inp)
        perf = {k: v for k, v in result.items() if k not in ("product_stream", "model")}
        if op_info["op_code"] == cy_code:
            perf = {**perf, **loop_meta}
        return {
            "op_code": op_info["op_code"],
            "label": op_info.get("label", op_info["op_code"]),
            "model_used": result.get("model", model_name),
            "inputs": {
                "solids_tph": round(inp["solids_tph"], 1),
                "au_g_t": round(inp["au_g_t"], 4),
                "p80_um": inp.get("p80_um"),
            },
            "outputs": {
                "solids_tph": round(prod["solids_tph"], 1),
                "au_g_t": round(prod["au_g_t"], 4),
                "p80_um": prod.get("p80_um"),
            },
            "performance": perf,
        }

    op_results = [
        _op_record(op_sag, sag_result, "starkey_spi", sag_in),
        _op_record(op_bm, bm_result, "bond_3rd_law", sag_result["product_stream"]),
        _op_record(op_cy, cy_result, "classification", bm_result["product_stream"]),
    ]
    energy = (
        sag_result.get("energy_kwh_t", 0)
        + bm_result.get("energy_kwh_t", 0)
        + cy_result.get("energy_kwh_t", 0)
    )
    return product, op_results, energy, 0.0, 0.0


def _simulate_flotation_bank_loop(
    op_rougher: dict,
    op_scavenger: dict,
    feed_stream: dict,
    dc_params: dict,
    lims_data: dict,
    override: dict,
    warnings: list,
    max_inner: int = 12,
    inner_tol: float = 0.001,
    recirc_fraction: float = 0.35,
) -> tuple[dict, list[dict], float, float, float]:
    """Rougher + scavenger with scavenger tails recirculated to rougher feed."""
    recirc_stream: dict | None = None
    prev_recirc_tph: float | None = None
    inner_iters = 0
    rough_result: dict = {}
    scav_result: dict = {}
    combined = feed_stream

    for inner_iters in range(1, max_inner + 1):
        combined = _merge_feed_recirc(feed_stream, recirc_stream)
        rough_result = _sim_flotation(combined, dc_params, lims_data, override, warnings)
        scav_in = rough_result.get("product_stream", combined)
        scav_result = _sim_flotation(scav_in, dc_params, lims_data, override, warnings,
                                     op_code="FLOTATION_SCAVENGER")
        recirc_tph = float(scav_in.get("solids_tph", 0)) * recirc_fraction
        if prev_recirc_tph is not None and prev_recirc_tph > 0:
            if abs(recirc_tph - prev_recirc_tph) / prev_recirc_tph < inner_tol:
                break
        prev_recirc_tph = recirc_tph
        if recirc_tph > 0:
            recirc_stream = _make_stream(
                recirc_tph,
                scav_in["au_g_t"],
                scav_in.get("pct_solids", 50.0),
                scav_in.get("p80_um"),
            )
        else:
            recirc_stream = None

    # product = scavenger TAILS propagated downstream.
    # NOTE: For whole-ore CIL flowsheets this is correct (tails → CIL, concentrate → POX/BIOX).
    # For concentrate-leach flowsheets (conc → CIL) the caller must swap product/concentrate_stream.
    # The concentrate_stream is available in scav_result and rough_result for that purpose.
    product = scav_result.get("product_stream", combined)
    concentrate = scav_result.get("concentrate_stream") or rough_result.get("concentrate_stream")
    loop_meta = {
        "inner_iterations": inner_iters,
        "recirculation_tph": round(prev_recirc_tph or 0, 1),
        "loop_type": "flotation_bank",
        "recirc_fraction": recirc_fraction,
        # Expose concentrate for downstream auditing / concentrate-route callers
        "concentrate_solids_tph": round(concentrate["solids_tph"], 1) if concentrate else None,
        "concentrate_au_g_t": round(concentrate["au_g_t"], 4) if concentrate else None,
    }

    def _op_record(op_info: dict, result: dict, inp: dict) -> dict:
        prod = result.get("product_stream", inp)
        perf = {k: v for k, v in result.items() if k not in ("product_stream", "concentrate_stream", "model")}
        if op_info["op_code"] == op_scavenger["op_code"]:
            perf = {**perf, **loop_meta}
        return {
            "op_code": op_info["op_code"],
            "label": op_info.get("label", op_info["op_code"]),
            "model_used": result.get("model", "first_order_kinetics"),
            "inputs": {
                "solids_tph": round(inp["solids_tph"], 1),
                "au_g_t": round(inp["au_g_t"], 4),
            },
            "outputs": {
                "solids_tph": round(prod["solids_tph"], 1),
                "au_g_t": round(prod["au_g_t"], 4),
            },
            "performance": perf,
        }

    op_results = [
        _op_record(op_rougher, rough_result, combined),
        _op_record(op_scavenger, scav_result, rough_result.get("product_stream", combined)),
    ]
    return product, op_results, 0.0, 0.0, 0.0


def _extract_recirc_stream_from_result(
    result: dict,
    model_name: str,
    fallback_stream: dict,
    recirc_fraction: float = 0.35,
) -> tuple[Optional[dict], float]:
    """Build recirculation stream from unit op result (cyclone UF, flotation tails, etc.)."""
    if model_name == "classification":
        tph = float(result.get("recirculation_tph") or 0)
        if tph > 0:
            prod = result.get("product_stream") or fallback_stream
            return (
                _make_stream(
                    tph,
                    prod["au_g_t"],
                    prod.get("pct_solids", 65.0),
                    prod.get("p80_um"),
                ),
                tph,
            )
    if model_name == "flotation":
        prod = result.get("product_stream") or fallback_stream
        tph = float(prod.get("solids_tph") or 0) * recirc_fraction
        if tph > 0:
            return (
                _make_stream(
                    tph,
                    prod["au_g_t"],
                    prod.get("pct_solids", 50.0),
                    prod.get("p80_um"),
                ),
                tph,
            )
    return None, 0.0


def _simulate_single_operation(
    op_info: dict,
    stream: dict,
    dc_params: dict,
    lims_data: dict,
    override: dict,
    warnings: list,
) -> tuple[dict, str, float, float, float]:
    """Run one unit op; returns (result, model_name, energy, nacn, cao)."""
    op_code = op_info["op_code"]
    model_name = resolve_op_model(op_code) or "passthrough"
    if model_name == "passthrough" or model_name is None:
        return {
            "model": "passthrough",
            "product_stream": _make_stream(
                stream["solids_tph"], stream["au_g_t"],
                stream.get("pct_solids", 65.0), stream.get("p80_um"),
            ),
        }, "passthrough", 0.0, 0.0, 0.0

    sim_fn = _SIM_DISPATCH.get(model_name)
    if not sim_fn:
        warnings.append(f"No simulation function for model={model_name}")
        return {
            "model": model_name,
            "product_stream": stream,
        }, model_name, 0.0, 0.0, 0.0

    if model_name == "refractory_pretreatment":
        result = sim_fn(stream, dc_params, lims_data, override, warnings, op_code=op_code)
    else:
        result = sim_fn(stream, dc_params, lims_data, override, warnings)

    energy = float(result.get("energy_kwh_t") or 0)
    if energy == 0 and result.get("power_kw") and stream["solids_tph"] > 0:
        energy = float(result["power_kw"]) / stream["solids_tph"]
    return (
        result,
        model_name,
        energy,
        float(result.get("nacn_kg_t") or 0),
        float(result.get("cao_kg_t") or 0),
    )


def _op_result_record(op_info: dict, inp: dict, result: dict, model_name: str, extra_perf: dict | None = None) -> dict:
    prod = result.get("product_stream", inp)
    perf = {k: v for k, v in result.items() if k not in ("product_stream", "concentrate_stream", "model")}
    if extra_perf:
        perf = {**perf, **extra_perf}
    return {
        "op_code": op_info["op_code"],
        "label": op_info.get("label", op_info["op_code"]),
        "model_used": result.get("model", model_name),
        "inputs": {
            "solids_tph": round(float(inp.get("solids_tph") or 0), 1),
            "au_g_t": round(float(inp.get("au_g_t") or 0), 4),
            "p80_um": inp.get("p80_um"),
        },
        "outputs": {
            "solids_tph": round(float(prod.get("solids_tph") or 0), 1),
            "au_g_t": round(float(prod.get("au_g_t") or 0), 4),
            "p80_um": prod.get("p80_um"),
        },
        "performance": perf,
    }


def _simulate_generic_graph_loop(
    plan: dict,
    operations: list[dict],
    feed_stream: dict,
    dc_params: dict,
    lims_data: dict,
    override: dict,
    warnings: list,
    max_inner: int = 15,
    inner_tol: float = 0.001,
) -> tuple[dict, list[dict], float, float, float]:
    """
    Converge any simple cycle from compiled flowsheet connections.

    Walks op_indices in graph order; merges external feed with recirc at entry each iteration.
    """
    ordered_indices: list[int] = list(plan.get("op_indices") or [])
    if len(ordered_indices) < 2:
        return feed_stream, [], 0.0, 0.0, 0.0

    recirc_stream: dict | None = None
    prev_recirc_tph: float | None = None
    inner_iters = 0
    final_product = feed_stream
    op_results: list[dict] = []
    total_energy = 0.0
    total_nacn = 0.0
    total_cao = 0.0
    recirc_source_idx: Optional[int] = None

    for inner_iters in range(1, max_inner + 1):
        stream = _merge_feed_recirc(feed_stream, recirc_stream)
        iter_results: list[dict] = []
        iter_energy = 0.0

        for idx in ordered_indices:
            op_info = operations[idx]
            inp = dict(stream)
            try:
                result, model_name, e, n, c = _simulate_single_operation(
                    op_info, stream, dc_params, lims_data, override, warnings,
                )
            except Exception as exc:
                warnings.append(f"Graph loop error in {op_info['op_code']}: {exc}")
                result = {"model": "error", "product_stream": stream}
                model_name, e, n, c = "error", 0.0, 0.0, 0.0

            stream = _update_stream_au(result.get("product_stream", stream))
            iter_energy += e
            total_nacn += n
            total_cao += c

            recirc_candidate, recirc_tph = _extract_recirc_stream_from_result(
                result, model_name, stream,
            )
            if recirc_candidate and recirc_tph > 0:
                recirc_stream = recirc_candidate
                recirc_source_idx = idx

            iter_results.append(_op_result_record(op_info, inp, result, model_name))

        final_product = stream
        total_energy = iter_energy
        op_results = iter_results

        metric = float(recirc_stream["solids_tph"]) if recirc_stream else float(stream.get("solids_tph") or 0)
        if prev_recirc_tph is not None and prev_recirc_tph > 0:
            if abs(metric - prev_recirc_tph) / prev_recirc_tph < inner_tol:
                break
        prev_recirc_tph = metric

    loop_meta = {
        "inner_iterations": inner_iters,
        "recirculation_tph": round(prev_recirc_tph or 0, 1),
        "loop_type": "graph_cycle",
        "source": plan.get("source", "flowsheet_graph"),
        "block_ids": plan.get("block_ids"),
        "recirc_edge": plan.get("recirc_edge"),
        "recirc_source_op": (
            operations[recirc_source_idx]["op_code"]
            if recirc_source_idx is not None
            else None
        ),
    }
    if op_results:
        op_results[-1]["performance"] = {**op_results[-1].get("performance", {}), **loop_meta}

    if not recirc_stream and inner_iters == 1:
        warnings.append(
            f"Boucle graphique {plan.get('op_codes')} — pas de flux de recirc explicite; "
            "convergence sur débit de sortie."
        )

    return final_product, op_results, total_energy, total_nacn, total_cao


def _run_recirculation_segment(
    segment: dict,
    operations: list[dict],
    feed_stream: dict,
    dc_params: dict,
    lims_data: dict,
    override: dict,
    warnings: list,
) -> tuple[dict, list[dict], float, float, float] | None:
    """Execute a detected recirculation segment; None if not applicable."""
    seg_type = segment.get("type")
    op_indices = segment.get("op_indices")

    if seg_type == "graph_cycle" and op_indices:
        return _simulate_generic_graph_loop(
            segment, operations, feed_stream, dc_params, lims_data, override, warnings,
        )

    start, end = segment.get("start", 0), segment.get("end", 0)
    ops = operations[start:end]

    if seg_type == "sag_ball_cyclone" and len(ops) == 3:
        return _simulate_sag_ball_cyclone_loop(
            ops[0], ops[1], ops[2], feed_stream, dc_params, lims_data, override, warnings,
        )
    if seg_type == "mill_classifier" and len(ops) == 2:
        mill_fn = _sim_ball_milling
        label = "bond_3rd_law"
        if ops[0].get("op_code") == "ROD_MILL":
            mill_fn = _sim_ball_milling
            label = "rod_mill_as_bond"
        return _simulate_mill_classifier_loop(
            ops[0], ops[1], feed_stream, dc_params, lims_data, override, warnings,
            mill_fn, label,
        )
    if seg_type == "flotation_bank" and len(ops) == 2:
        return _simulate_flotation_bank_loop(
            ops[0], ops[1], feed_stream, dc_params, lims_data, override, warnings,
        )

    if op_indices and len(op_indices) >= 2:
        return _simulate_generic_graph_loop(
            segment, operations, feed_stream, dc_params, lims_data, override, warnings,
        )

    return None


# Dispatcher: model name -> simulation function
_SIM_DISPATCH: dict[str, Any] = {
    "crushing":       _sim_crushing,
    "screening":      _sim_screening,
    "sag_milling":    _sim_sag_milling,
    "ball_milling":   _sim_ball_milling,
    "hpgr":           _sim_hpgr,
    "classification": _sim_classification,
    "regrind":        _sim_regrind,
    "gravity":        _sim_gravity,
    "flotation":      _sim_flotation,
    "preaeration":    _sim_preaeration,
    "leaching":       _sim_leaching,
    "cil":            _sim_cil,
    "cip":            _sim_cip,
    "thickener":      _sim_thickener,
    "elution":        _sim_elution,
    "electrowinning": _sim_electrowinning,
    "smelting":       _sim_smelting,
    "detox_inco":     _sim_detox_inco,
    "detox_caro":     _sim_detox_caro,
    "detox_peroxide": _sim_detox_peroxide,
    "refractory_pretreatment": _sim_refractory_pretreatment,
}


# ============================================================================
# Main circuit simulation
# ============================================================================

def simulate_circuit(project_id: str, template_id: str,
                     params_override: dict | None = None,
                     cursor=None) -> dict:
    """Run rigorous simulation of the entire circuit.

    Steps:
        1. Read enabled operations from circuit template
        2. Read design criteria and LIMS data
        3. For each operation (in process order):
           a. Calculate performance using kinetic model
           b. Propagate output to next operation's input
        4. Iterate for recirculations until convergence (<0.1%)
        5. Calculate overall metrics

    Args:
        project_id: UUID of the project
        template_id: UUID of the circuit template
        params_override: dict to override specific parameters (for sensitivity/scenarios)
        cursor: psycopg2 RealDictCursor

    Returns:
        {
            operations: [{op_code, model_used, inputs, outputs, performance}],
            overall: {
                feed_tph, feed_grade_au, total_recovery_pct, annual_gold_oz,
                total_energy_kwh_t, total_nacn_kg_t, total_cao_kg_t, co2_per_oz
            },
            convergence: {iterations, max_residual},
            warnings: [str]
        }
    """
    try:
        return _simulate_circuit_impl(project_id, template_id, params_override, cursor)
    except Exception as e:
        logger.error("simulate_circuit failed for project_id=%s, template_id=%s: %s",
                     project_id, template_id, e)
        raise RuntimeError(f"simulate_circuit failed for project {project_id}: {e}") from e


def _simulate_circuit_impl(project_id: str, template_id: str,
                            params_override: dict | None = None,
                            cursor=None) -> dict:
    """Internal implementation of simulate_circuit."""
    t0 = time.time()
    override = _load_simulation_param_overrides(project_id, cursor, params_override)
    warnings: list[str] = []

    # 1. Load project data
    econ = _load_project_economics(project_id, cursor)
    operations = _load_enabled_operations(template_id, cursor)
    dc_params = _load_dc_params(template_id, cursor)
    lims_data = get_lims_summary(project_id, cursor)

    try:
        from .plant_design_advisor import validate_before_simulation
        op_codes = [o["op_code"] for o in operations]
        for vw in validate_before_simulation(project_id, op_codes=op_codes):
            warnings.append(vw.get("message", str(vw)))
    except Exception:
        logger.debug("plant_design pre-simulation validation skipped", exc_info=True)

    if not operations:
        return {
            "operations": [],
            "overall": {},
            "convergence": {"iterations": 0, "max_residual": 0},
            "warnings": ["No enabled operations found in template"],
        }

    try:
        from .recirculation_solver import detect_recirculation_segments
        from .generic_loop_solver import (
            detect_graph_recirculation_loops,
            merge_recirculation_plans,
        )
    except ImportError:
        from engines.recirculation_solver import detect_recirculation_segments
        from engines.generic_loop_solver import (
            detect_graph_recirculation_loops,
            merge_recirculation_plans,
        )

    sequence_segments = detect_recirculation_segments(operations)
    graph_loops: list[dict] = []
    blocks: Optional[list] = None
    connections: Optional[list] = None
    try:
        from .compile import load_compilation_graph
    except ImportError:
        from engines.compile import load_compilation_graph
    try:
        blocks, connections = load_compilation_graph(project_id, template_id)
        if blocks and connections:
            graph_loops = detect_graph_recirculation_loops(
                blocks, connections, operations,
            )
    except Exception:
        logger.debug("compilation graph loops skipped", exc_info=True)

    linear_segments, loop_by_entry = merge_recirculation_plans(
        sequence_segments, graph_loops,
    )
    graph_covered: set[int] = set()
    for g in graph_loops:
        graph_covered.update(g.get("op_indices") or [])

    # 2. Build initial feed stream
    feed_tph = override.get("feed_tph") or econ.get("target_tph") or 1500.0
    feed_grade = override.get("feed_grade_au") or econ.get("gold_grade_g_t") or 1.5
    feed_stream = _make_stream(feed_tph, feed_grade, 100.0, None)

    # 3. Iterate for recirculation convergence
    max_iterations = 20
    tolerance = 0.001  # 0.1%
    converged = False
    prev_au_out = 0.0

    op_results: list[dict] = []

    for iteration in range(1, max_iterations + 1):
        current_stream = feed_stream.copy()
        op_results = []
        total_energy = 0.0
        total_nacn = 0.0
        total_cao = 0.0
        total_recovery_factors: list[float] = []

        i = 0
        while i < len(operations):
            if i in graph_covered and i not in loop_by_entry:
                i += 1
                continue

            if i in loop_by_entry:
                plan = loop_by_entry[i]
                loop_out = _run_recirculation_segment(
                    plan, operations, current_stream, dc_params, lims_data, override, warnings,
                )
                if loop_out:
                    product, loop_results, loop_e, loop_n, loop_c = loop_out
                    op_results.extend(loop_results)
                    total_energy += loop_e
                    total_nacn += loop_n
                    total_cao += loop_c
                    current_stream = _update_stream_au(product)
                for idx in plan.get("op_indices") or []:
                    graph_covered.add(idx)
                i = max(plan.get("op_indices") or [i]) + 1
                continue

            op_info = operations[i]
            op_code = op_info["op_code"]

            seg = next((s for s in linear_segments if s["start"] == i), None)
            if seg:
                loop_out = _run_recirculation_segment(
                    seg, operations, current_stream, dc_params, lims_data, override, warnings,
                )
                if loop_out:
                    product, loop_results, loop_e, loop_n, loop_c = loop_out
                    op_results.extend(loop_results)
                    total_energy += loop_e
                    total_nacn += loop_n
                    total_cao += loop_c
                    current_stream = _update_stream_au(product)
                    i = seg["end"]
                    continue

            model_name = resolve_op_model(op_code)

            if model_name is None:
                if not is_expected_passthrough(op_code):
                    warnings.append(f"No model for op_code={op_code} — passed through")
                op_results.append({
                    "op_code": op_code,
                    "label": op_info.get("label", op_code),
                    "model_used": "passthrough",
                    "inputs": {
                        "solids_tph": round(current_stream["solids_tph"], 1),
                        "au_g_t": round(current_stream["au_g_t"], 4),
                    },
                    "outputs": {
                        "solids_tph": round(current_stream["solids_tph"], 1),
                        "au_g_t": round(current_stream["au_g_t"], 4),
                    },
                    "performance": {},
                })
                i += 1
                continue

            sim_fn = _SIM_DISPATCH.get(model_name)
            if sim_fn is None:
                warnings.append(f"No simulation function for model={model_name}")
                i += 1
                continue

            # Run the model
            try:
                if model_name == "refractory_pretreatment":
                    result = sim_fn(
                        current_stream, dc_params, lims_data, override, warnings,
                        op_code=op_code,
                    )
                else:
                    result = sim_fn(current_stream, dc_params, lims_data, override, warnings)
            except Exception as exc:
                logger.error("Simulation error in %s: %s", op_code, exc, exc_info=True)
                warnings.append(f"Error in {op_code}: {exc}")
                op_results.append({
                    "op_code": op_code,
                    "label": op_info.get("label", op_code),
                    "model_used": model_name,
                    "error": str(exc),
                })
                i += 1
                continue

            # Collect metrics
            energy = result.get("energy_kwh_t", 0)
            if energy == 0 and "power_kw" in result and current_stream["solids_tph"] > 0:
                energy = result["power_kw"] / current_stream["solids_tph"]
            total_energy += energy

            total_nacn += result.get("nacn_kg_t", 0)
            total_cao += result.get("cao_kg_t", 0)

            rec = result.get("recovery_pct")
            if rec is not None:
                total_recovery_factors.append(rec / 100.0)

            # Record result
            inputs_snapshot = {
                "solids_tph": round(current_stream["solids_tph"], 1),
                "au_g_t": round(current_stream["au_g_t"], 4),
                "p80_um": current_stream.get("p80_um"),
            }
            product = result.get("product_stream", current_stream)
            outputs_snapshot = {
                "solids_tph": round(product["solids_tph"], 1),
                "au_g_t": round(product["au_g_t"], 4),
                "p80_um": product.get("p80_um"),
            }

            perf = {k: v for k, v in result.items()
                    if k not in ("product_stream", "concentrate_stream", "model")}

            op_results.append({
                "op_code": op_code,
                "label": op_info.get("label", op_code),
                "model_used": result.get("model", model_name),
                "inputs": inputs_snapshot,
                "outputs": outputs_snapshot,
                "performance": perf,
            })

            # Propagate stream
            current_stream = _update_stream_au(product)
            i += 1

        # Check convergence on gold in tails
        au_out = current_stream.get("au_mass_g_h", 0)
        if iteration > 1 and prev_au_out > 0:
            residual = abs(au_out - prev_au_out) / prev_au_out
            if residual < tolerance:
                converged = True
                break
        elif iteration > 1 and au_out == 0 and prev_au_out == 0:
            converged = True
            break

        prev_au_out = au_out

    # 4. Calculate overall metrics
    feed_au_mass = feed_tph * feed_grade
    tails_au_mass = current_stream["solids_tph"] * current_stream["au_g_t"]
    recovered_au_mass = feed_au_mass - tails_au_mass
    total_recovery = (recovered_au_mass / feed_au_mass * 100.0) if feed_au_mass > 0 else 0.0

    # Annual production (canonical path — helpers.compute_annual_gold_oz)
    hours_day = econ.get("operating_hours_day") or 24.0
    avail = econ.get("availability_pct") or _DEFAULT_AVAIL_PCT
    annual_tonnes = feed_tph * hours_day * 365.0 * (avail / 100.0)
    try:
        from ..helpers import compute_annual_gold_oz
    except ImportError:
        from helpers import compute_annual_gold_oz
    annual_gold_oz = compute_annual_gold_oz(
        feed_tph, hours_day, avail, feed_grade, total_recovery,
    )

    # CO2 footprint
    elec_rate = econ.get("electricity_rate") or 0.075
    co2_total_kg = total_energy * annual_tonnes * GRID_CO2_KG_KWH
    co2_per_oz = co2_total_kg / annual_gold_oz if annual_gold_oz > 0 else 0.0

    overall = {
        "feed_tph": round(feed_tph, 1),
        "feed_grade_au": round(feed_grade, 3),
        # Canonical key used by all modules (recovery_forecast, routes, frontend)
        "recovery_pct": round(total_recovery, 2),
        # Legacy alias — kept for backward compatibility
        "total_recovery_pct": round(total_recovery, 2),
        "annual_gold_oz": round(annual_gold_oz, 0),
        "annual_tonnes": round(annual_tonnes, 0),
        "total_energy_kwh_t": round(total_energy, 3),
        "total_nacn_kg_t": round(total_nacn, 3),
        "total_cao_kg_t": round(total_cao, 3),
        "co2_per_oz": round(co2_per_oz, 1),
    }

    # 5. Economics (if sufficient data)
    gold_price = econ.get("gold_price_usd_oz") or _DEFAULT_GOLD_PRICE
    mine_life = int(econ.get("mine_life_years") or 10)
    discount_rate = econ.get("discount_rate_pct") or 5.0
    capex_musd = econ.get("capex_musd")

    if capex_musd and annual_gold_oz > 0:
        capex_usd = capex_musd * 1e6
        _revenue_annual = annual_gold_oz * gold_price
        # Rough OPEX estimate from reagent + energy costs
        opex_per_t = total_energy * elec_rate + total_nacn * 2.5 + total_cao * 0.15
        opex_annual = opex_per_t * annual_tonnes

        cashflows = build_cashflows(
            mine_life_years=mine_life,
            annual_oz=annual_gold_oz,
            au_price=gold_price,
            royalty_pct=5.0,
            opex_annual=opex_annual,
            sustaining_capex_annual=capex_usd * 0.03,
            tax_rate=30.0,
            discount_rate=discount_rate,
        )
        fcf_list = [cf["fcf"] for cf in cashflows]
        npv = compute_npv(fcf_list, discount_rate / 100.0, capex_usd)
        irr = compute_irr(fcf_list, capex_usd)

        overall["npv_musd"] = round(npv / 1e6, 2)
        overall["irr_pct"] = round(irr * 100, 2) if irr is not None else None
        overall["opex_usd_t"] = round(opex_per_t, 2)
        overall["capex_musd"] = round(capex_musd, 2)
        # AISC = (OPEX + sustaining CAPEX + royalties) / annual gold oz
        _royalty_annual = annual_gold_oz * gold_price * 0.05
        _sustaining_annual = capex_usd * 0.03
        overall["aisc_usd_oz"] = round(
            (opex_annual + _sustaining_annual + _royalty_annual) / annual_gold_oz, 2
        )

    duration = time.time() - t0

    return {
        "operations": op_results,
        "overall": overall,
        "convergence": {
            "iterations": iteration,
            "max_residual": round(
                abs(au_out - prev_au_out) / prev_au_out if prev_au_out > 0 else 0.0, 6
            ),
            "converged": converged,
            "recirculation_segments": [
                {"type": s["type"], "op_codes": s["op_codes"], "source": s.get("source", "sequence")}
                for s in linear_segments
            ],
            "graph_loops": [
                {
                    "type": g["type"],
                    "op_codes": g["op_codes"],
                    "source": g.get("source", "flowsheet_graph"),
                    "recirc_edge": g.get("recirc_edge"),
                    "entry_index": g.get("entry_index"),
                }
                for g in graph_loops
            ],
        },
        "warnings": warnings,
        "duration_s": round(duration, 3),
    }


# ============================================================================
# Sensitivity analysis
# ============================================================================

def run_sensitivity(project_id: str, template_id: str,
                    params_to_vary: list[dict],
                    delta_pcts: list[float],
                    cursor) -> list[dict]:
    """Run multiple simulations varying each parameter.

    Args:
        project_id: UUID of the project
        template_id: UUID of the circuit template
        params_to_vary: List of dicts with {param_key, param_label, base_value}
        delta_pcts: List of % deltas to apply (e.g. [-20, -10, 0, 10, 20])
        cursor: database cursor

    Returns:
        [{param_key, param_label, base, delta_pct, recovery, npv, irr, aisc}]
    """
    try:
        return _run_sensitivity_impl(project_id, template_id, params_to_vary, delta_pcts, cursor)
    except Exception as e:
        logger.error("run_sensitivity failed for project_id=%s, template_id=%s, n_params=%d: %s",
                     project_id, template_id, len(params_to_vary), e)
        return []


def _run_sensitivity_impl(project_id: str, template_id: str,
                           params_to_vary: list[dict],
                           delta_pcts: list[float],
                           cursor) -> list[dict]:
    """Internal implementation of run_sensitivity."""
    # Run base case
    base_result = simulate_circuit(project_id, template_id, cursor=cursor)
    base_overall = base_result["overall"]

    results = []

    for param in params_to_vary:
        p_key = param["param_key"]
        p_label = param.get("param_label", p_key)
        base_val = param.get("base_value")

        if base_val is None or base_val == 0:
            logger.warning("Skipping sensitivity for %s — no base value", p_key)
            continue

        for delta in delta_pcts:
            new_val = base_val * (1.0 + delta / 100.0)
            override = {p_key: new_val}

            try:
                sim = simulate_circuit(project_id, template_id,
                                       params_override=override, cursor=cursor)
                ov = sim["overall"]
                results.append({
                    "param_key": p_key,
                    "param_label": p_label,
                    "base": base_val,
                    "delta_pct": delta,
                    "new_value": round(new_val, 4),
                    "recovery": ov.get("total_recovery_pct"),
                    "annual_gold_oz": ov.get("annual_gold_oz"),
                    "npv": ov.get("npv_musd"),
                    "irr": ov.get("irr_pct"),
                    "energy_kwh_t": ov.get("total_energy_kwh_t"),
                })
            except Exception as exc:
                logger.error("Sensitivity error for %s at %+d%%: %s",
                             p_key, delta, exc, exc_info=True)
                results.append({
                    "param_key": p_key,
                    "param_label": p_label,
                    "base": base_val,
                    "delta_pct": delta,
                    "error": str(exc),
                })

    # Rank by NPV impact at max delta
    max_delta = max(abs(d) for d in delta_pcts) if delta_pcts else 20
    for param in params_to_vary:
        p_key = param["param_key"]
        impacts = [r for r in results
                   if r.get("param_key") == p_key
                   and r.get("npv") is not None
                   and abs(r.get("delta_pct", 0)) == max_delta]
        if impacts and base_overall.get("npv_musd"):
            for imp in impacts:
                imp["npv_impact_pct"] = round(
                    (imp["npv"] - base_overall["npv_musd"]) / abs(base_overall["npv_musd"]) * 100,
                    2,
                ) if base_overall["npv_musd"] != 0 else 0.0

    return results


# ============================================================================
# Scenario comparison
# ============================================================================

def compare_scenarios(project_id: str, scenario_ids: list[str],
                      cursor) -> list[dict]:
    """Run simulation for each scenario and compare results.

    Scenarios are stored in simulation_scenarios with params_override JSON.

    Args:
        project_id: UUID of the project
        scenario_ids: List of scenario UUIDs to compare
        cursor: database cursor

    Returns:
        [{scenario_id, scenario_name, color, results}]
    """
    try:
        return _compare_scenarios_impl(project_id, scenario_ids, cursor)
    except Exception as e:
        logger.error("compare_scenarios failed for project_id=%s, n_scenarios=%d: %s",
                     project_id, len(scenario_ids), e)
        return []


def _compare_scenarios_impl(project_id: str, scenario_ids: list[str],
                             cursor) -> list[dict]:
    """Internal implementation of compare_scenarios."""
    comparisons = []

    for sid in scenario_ids:
        cursor.execute(
            "SELECT id, name, description, params_override, is_base_case, color "
            "FROM simulation_scenarios WHERE id = %s AND project_id = %s",
            (sid, project_id),
        )
        scenario = cursor.fetchone()
        if scenario is None:
            comparisons.append({
                "scenario_id": sid,
                "error": "Scenario not found",
            })
            continue

        # Get template_id for the project (active template)
        cursor.execute(
            "SELECT id FROM circuit_templates "
            "WHERE project_id = %s AND is_active = true LIMIT 1",
            (project_id,),
        )
        tmpl_row = cursor.fetchone()
        if tmpl_row is None:
            comparisons.append({
                "scenario_id": sid,
                "scenario_name": scenario["name"],
                "error": "No active circuit template",
            })
            continue

        template_id = str(tmpl_row["id"])
        override = scenario["params_override"] or {}

        try:
            result = simulate_circuit(
                project_id, template_id,
                params_override=override, cursor=cursor,
            )
            comparisons.append({
                "scenario_id": sid,
                "scenario_name": scenario["name"],
                "color": scenario.get("color", "#3B82F6"),
                "is_base_case": scenario.get("is_base_case", False),
                "results": result["overall"],
                "warnings": result["warnings"],
            })
        except Exception as exc:
            logger.error("Scenario %s simulation error: %s", sid, exc, exc_info=True)
            comparisons.append({
                "scenario_id": sid,
                "scenario_name": scenario["name"],
                "error": str(exc),
            })

    return comparisons


# ============================================================================
# Section simulation — simulate a sub-circuit (subset of operations)
# ============================================================================

SECTION_CATEGORY_MAP: dict[str, list[str]] = {
    "comminution": ["concassage", "broyage", "classification", "rebroyage"],
    "gravity": ["concentration"],
    "flotation": ["concentration"],
    "pretreatment": ["pretraitement"],
    "leaching": ["lixiviation"],
    "desorption": ["adr"],
    "thickening": ["epaississement", "residus"],
    "detox": ["detoxification"],
    "water": ["eau"],
    "reagents": ["reactifs"],
}

_GRAVITY_PREFIXES = ("GRAVITY_", "GRAV_")
_FLOTATION_PREFIXES = ("FLOTATION_",)


def resolve_op_codes_for_sections(
    sections: list[str], all_ops: list[dict]
) -> list[str]:
    """Resolve abstract section names to concrete op_codes.

    For 'gravity' and 'flotation' which both map to the 'concentration'
    category, disambiguation is done by checking the op_code prefix.

    Args:
        sections: list of section names (e.g. ["comminution", "gravity"])
        all_ops: list of operation dicts with keys op_code, category

    Returns:
        list of matching op_codes in original sort order
    """
    matched: list[str] = []

    for op in all_ops:
        op_code = op["op_code"]
        cat = (op.get("category") or "").lower().strip()

        for section in sections:
            section_lower = section.lower().strip()
            allowed_cats = SECTION_CATEGORY_MAP.get(section_lower, [])

            if cat not in allowed_cats:
                continue

            # Disambiguate gravity vs flotation when both map to "concentration"
            if cat == "concentration":
                if section_lower == "gravity":
                    if not op_code.upper().startswith(_GRAVITY_PREFIXES):
                        continue
                elif section_lower == "flotation":
                    if not op_code.upper().startswith(_FLOTATION_PREFIXES):
                        continue

            if op_code not in matched:
                matched.append(op_code)

    return matched


def _check_contiguity(
    selected_ops: list[dict], all_ops: list[dict]
) -> list[str]:
    """Check whether selected operations are contiguous in sort_order.

    Args:
        selected_ops: subset of operations that were selected
        all_ops: all enabled operations in sort_order

    Returns:
        list of warning strings (empty if contiguous)
    """
    if len(selected_ops) <= 1:
        return []

    selected_codes = {op["op_code"] for op in selected_ops}
    indices = [
        i for i, op in enumerate(all_ops) if op["op_code"] in selected_codes
    ]

    if not indices:
        return []

    indices.sort()
    warnings: list[str] = []

    for i in range(1, len(indices)):
        if indices[i] != indices[i - 1] + 1:
            gap_ops = [
                all_ops[j]["op_code"]
                for j in range(indices[i - 1] + 1, indices[i])
            ]
            warnings.append(
                f"Non-contiguous: ops {gap_ops} skipped between "
                f"{all_ops[indices[i-1]]['op_code']} and {all_ops[indices[i]]['op_code']}"
            )

    return warnings


def _resolve_section_feed(
    pid: str,
    template_id: str,
    first_op_sort_order: int,
    feed_override: dict | None,
    cursor,
) -> tuple[dict, str]:
    """Resolve the feed stream for a section simulation.

    Priority: user_override > last global run > project defaults.

    Args:
        pid: project UUID
        template_id: circuit template UUID
        first_op_sort_order: sort_order of the first op in the section
        feed_override: optional user-supplied feed dict
        cursor: database cursor

    Returns:
        (stream_dict, feed_source) where feed_source is one of
        'user_override', 'last_global_run', 'project_defaults'
    """
    # 1. User override
    if feed_override:
        return _make_stream(
            solids_tph=feed_override.get("solids_tph", 1500.0),
            au_g_t=feed_override.get("au_g_t", 1.5),
            pct_solids=feed_override.get("pct_solids", 50.0),
            p80_um=feed_override.get("p80_um"),
        ), "user_override"

    # 2. Last global run — try to extract the product stream at the right point
    if cursor is not None:
        try:
            cursor.execute(
                "SELECT results, product_stream "
                "FROM simulation_runs_v2 "
                "WHERE project_id=%s AND run_mode='global' AND results IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 1",
                (pid,),
            )
            row = cursor.fetchone()
            if row is not None:
                # Try to find the stream at the operation just before our section
                results = row.get("results") or {}
                ops_list = results.get("operations") or []
                for op_result in ops_list:
                    out = op_result.get("outputs", {})
                    if out.get("solids_tph") and out.get("au_g_t") is not None:
                        _last_out = out
                # If we have any completed run, use its final product stream
                product = row.get("product_stream") or {}
                if product.get("solids_tph"):
                    return _make_stream(
                        solids_tph=product["solids_tph"],
                        au_g_t=product.get("au_g_t", 1.5),
                        pct_solids=product.get("pct_solids", 50.0),
                        p80_um=product.get("p80_um"),
                    ), "last_global_run"
        except Exception as e:
            logger.warning("Failed to load last global run for feed: %s", e)

    # 3. Project defaults
    if cursor is not None:
        try:
            cursor.execute(
                "SELECT target_tph, gold_grade_g_t FROM projects WHERE id=%s",
                (pid,),
            )
            proj_row = cursor.fetchone()
            if proj_row is not None:
                tph = float(proj_row["target_tph"] or 1500.0)
                grade = float(proj_row["gold_grade_g_t"] or 1.5)
                return _make_stream(tph, grade, 100.0), "project_defaults"
        except Exception as e:
            logger.warning("Failed to load project defaults for feed: %s", e)

    # Ultimate fallback
    return _make_stream(1500.0, 1.5, 100.0), "project_defaults"


def simulate_section(
    pid: str,
    template_id: str,
    op_codes: list[str],
    feed_override: dict | None = None,
    params_override: dict | None = None,
    operations_override: list[dict] | None = None,
    cursor=None,
) -> dict:
    """Simulate a subset (section) of the circuit.

    Args:
        pid: project UUID
        template_id: circuit template UUID
        op_codes: list of op_codes to simulate (in order)
        feed_override: optional feed stream dict
        params_override: optional parameter overrides
        operations_override: optional pre-loaded operations list (skip DB)
        cursor: database cursor

    Returns:
        {
            run_id, mode, ops_simulated, feed_source, feed_stream,
            product_stream, section_results, warnings
        }
    """
    t0 = time.time()
    run_id = str(uuid.uuid4())
    override = _load_simulation_param_overrides(pid, cursor, params_override)
    warnings: list[str] = []

    # 1. Load operations
    if operations_override is not None:
        all_ops = operations_override
    else:
        all_ops = _load_enabled_operations(template_id, cursor)

    if not all_ops:
        return {
            "run_id": run_id,
            "mode": "section",
            "ops_simulated": [],
            "feed_source": "none",
            "feed_stream": {},
            "product_stream": {},
            "section_results": [],
            "warnings": ["No enabled operations found"],
        }

    # 2. Filter to requested op_codes, preserving sort order
    op_code_set = set(op_codes)
    selected_ops = [op for op in all_ops if op["op_code"] in op_code_set]

    if not selected_ops:
        return {
            "run_id": run_id,
            "mode": "section",
            "ops_simulated": [],
            "feed_source": "none",
            "feed_stream": {},
            "product_stream": {},
            "section_results": [],
            "warnings": [f"None of the requested op_codes found: {op_codes}"],
        }

    # 3. Check contiguity
    contiguity_warnings = _check_contiguity(selected_ops, all_ops)
    warnings.extend(contiguity_warnings)

    # 4. Resolve feed
    first_sort = selected_ops[0].get("sort_order", 0)
    feed_stream, feed_source = _resolve_section_feed(
        pid, template_id, first_sort, feed_override, cursor
    )

    # 5. Load DC params, economics, LIMS
    dc_params = _load_dc_params(template_id, cursor) if cursor else {}
    lims_data = get_lims_summary(pid, cursor) if cursor else {}

    # 6. Run each operation
    current_stream = feed_stream.copy()
    section_results: list[dict] = []
    total_energy = 0.0
    ops_simulated: list[str] = []

    for op_info in selected_ops:
        op_code = op_info["op_code"]
        ops_simulated.append(op_code)

        model_name = resolve_op_model(op_code)

        if model_name is None:
            if not is_expected_passthrough(op_code):
                warnings.append(f"No model for op_code={op_code} — passed through")
            section_results.append({
                "op_code": op_code,
                "label": op_info.get("label", op_code),
                "model_used": "passthrough",
                "inputs": {
                    "solids_tph": round(current_stream["solids_tph"], 1),
                    "au_g_t": round(current_stream["au_g_t"], 4),
                },
                "outputs": {
                    "solids_tph": round(current_stream["solids_tph"], 1),
                    "au_g_t": round(current_stream["au_g_t"], 4),
                },
                "performance": {},
            })
            continue

        sim_fn = _SIM_DISPATCH.get(model_name)
        if sim_fn is None:
            warnings.append(f"No simulation function for model={model_name}")
            continue

        try:
            result = sim_fn(current_stream, dc_params, lims_data, override, warnings)
        except Exception as exc:
            logger.error("Section sim error in %s: %s", op_code, exc, exc_info=True)
            warnings.append(f"Error in {op_code}: {exc}")
            section_results.append({
                "op_code": op_code,
                "label": op_info.get("label", op_code),
                "model_used": model_name,
                "error": str(exc),
            })
            continue

        energy = result.get("energy_kwh_t", 0)
        if energy == 0 and "power_kw" in result and current_stream["solids_tph"] > 0:
            energy = result["power_kw"] / current_stream["solids_tph"]
        total_energy += energy

        inputs_snapshot = {
            "solids_tph": round(current_stream["solids_tph"], 1),
            "au_g_t": round(current_stream["au_g_t"], 4),
            "p80_um": current_stream.get("p80_um"),
        }
        product = result.get("product_stream", current_stream)
        outputs_snapshot = {
            "solids_tph": round(product["solids_tph"], 1),
            "au_g_t": round(product["au_g_t"], 4),
            "p80_um": product.get("p80_um"),
        }
        perf = {k: v for k, v in result.items()
                if k not in ("product_stream", "concentrate_stream", "model")}

        section_results.append({
            "op_code": op_code,
            "label": op_info.get("label", op_code),
            "model_used": result.get("model", model_name),
            "inputs": inputs_snapshot,
            "outputs": outputs_snapshot,
            "performance": perf,
        })

        current_stream = _update_stream_au(product)

    # 7. Compute section-level recovery
    feed_au = feed_stream["solids_tph"] * feed_stream["au_g_t"]
    product_au = current_stream["solids_tph"] * current_stream["au_g_t"]
    section_recovery = (
        (feed_au - product_au) / feed_au * 100.0 if feed_au > 0 else 0.0
    )

    duration = time.time() - t0

    return {
        "run_id": run_id,
        "mode": "section",
        "ops_simulated": ops_simulated,
        "feed_source": feed_source,
        "feed_stream": feed_stream,
        "product_stream": current_stream,
        "section_recovery_pct": round(section_recovery, 2),
        "total_energy_kwh_t": round(total_energy, 3),
        "section_results": section_results,
        "warnings": warnings,
        "duration_s": round(duration, 3),
    }
