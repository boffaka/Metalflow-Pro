"""Simulator-specific constants and thresholds."""
from __future__ import annotations
import math

# Single source of truth — imported from the canonical backend constants module.
# Previously duplicated as `1 / 31.1035`; importing avoids any future drift.
try:
    from ...constants import TROY_OZ_PER_GRAM as _TROY_OZ_PER_GRAM
    from ...constants import WATER_SG as _WATER_SG
except ImportError:  # pragma: no cover - direct script imports
    from constants import TROY_OZ_PER_GRAM as _TROY_OZ_PER_GRAM
    from constants import WATER_SG as _WATER_SG

TROY_OZ_PER_GRAM = _TROY_OZ_PER_GRAM
WATER_SG = _WATER_SG

# ── Alert thresholds ──
GRINDING_ENERGY_WARNING_KWH_T = 25.0
TOTAL_ENERGY_WARNING_KWH_T = 35.0
LEACH_RECOVERY_WARNING_PCT = 80.0
ELUTION_EFFICIENCY_WARNING_PCT = 90.0
FLOTATION_RECOVERY_WARNING_PCT = 70.0
OVERALL_RECOVERY_CRITICAL_PCT = 60.0
WAD_CN_IFC_LIMIT_MG_L = 0.5
GRG_RECOMMEND_THRESHOLD_PCT = 20.0
GRG_NOT_JUSTIFIED_PCT = 5.0

# ── Stoichiometric ratios ──
SO2_PER_WAD_CN_RATIO = 6.0  # g SO2 per g WAD CN (INCO process)
CUSO4_CATALYST_MG_L = 50.0  # CuSO4 catalyst concentration (mg/L)

# ── Faraday's law constants ──
FARADAY_CONSTANT = 96485.0  # C/mol
AU_MOLAR_MASS = 196.97  # g/mol
AU_ELECTRONS = 3  # Au³⁺ → Au⁰
FARADAY_EFFICIENCY = 0.92  # typical EW efficiency
EW_CELL_VOLTAGE = 3.5  # V

# ── Equipment sizing constants ──
CARBON_BULK_DENSITY_KG_M3 = 500.0
AGITATOR_SPECIFIC_POWER_KW_M3 = 0.10  # kW/m³ for CIL/CIP
FLOTATION_SPECIFIC_POWER_KW_M3 = 0.60  # kW/m³ mechanical cells
CRUSHER_MECHANICAL_EFFICIENCY = 0.93
MILL_MECHANICAL_EFFICIENCY = 0.85
SAG_EFFICIENCY_FACTOR = 0.65  # Starkey SPI model

# ── Bond/Morrell equations ──
def bond_energy(wi: float, p80_um: float, f80_um: float) -> float:
    """Bond Third Law: W = 10 × Wi × (1/√P80 - 1/√F80). Returns kWh/t."""
    if p80_um <= 0 or f80_um <= 0 or f80_um <= p80_um:
        return 0.0
    return 10.0 * wi * (1.0 / math.sqrt(p80_um) - 1.0 / math.sqrt(f80_um))

def starkey_sag_power(tph: float, spi_min: float) -> float:
    """Starkey SAG power: P = TPH × SPI / 60 / 0.65. Returns kW."""
    return tph * spi_min / 60.0 / SAG_EFFICIENCY_FACTOR

def hpgr_energy(spf: float, f80_mm: float, p80_mm: float) -> float:
    """HPGR specific energy: E = 0.5 × SPF × ln(F80/P80). Returns kWh/t."""
    if f80_mm <= 0 or p80_mm <= 0 or f80_mm <= p80_mm:
        return 0.0
    return 0.5 * spf * math.log(f80_mm / p80_mm)

def flotation_recovery(rmax_pct: float, k: float, t_min: float) -> float:
    """First-order kinetics: R = Rmax × (1 - exp(-k×t)). Returns %."""
    return rmax_pct * (1.0 - math.exp(-k * t_min))

def leach_recovery(k_eff: float, time_h: float) -> float:
    """Modified first-order: R = 100 × (1 - exp(-k_eff × t)). Returns %."""
    return 100.0 * (1.0 - math.exp(-k_eff * time_h))

def ew_current(gold_g_day: float) -> float:
    """Faraday's law for gold EW. Returns total current (A)."""
    gold_g_s = gold_g_day / 86400.0
    moles_per_s = gold_g_s / AU_MOLAR_MASS
    return moles_per_s * AU_ELECTRONS * FARADAY_CONSTANT / FARADAY_EFFICIENCY

def inco_so2_consumption(wad_cn_mg_l: float, flow_m3h: float) -> float:
    """INCO SO2 consumption: WAD_CN × flow × 6 / 1000. Returns kg/h."""
    return wad_cn_mg_l * flow_m3h * SO2_PER_WAD_CN_RATIO / 1000.0
