# backend/engines/comminution.py
"""
Comminution engine — deterministic, auditable calculations for PFS/FS standard.

All energy values in kWh/t unless noted.
All size values in µm unless noted.

References:
  - Bond (1952) "The Third Theory of Comminution"
  - Morrell (2004) "Predicting the specific energy of autogenous and semi-autogenous mills"
  - Starkey & Dobby (1996) "Application of the MinnovEX SAG Power Index Test"
  - Napier-Munn et al. (1996) "Mineral Comminution Circuits"
  - JKMRC (2006) "JKSimMet Manual"
"""
from __future__ import annotations
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Bond Work Index — Ball Mill ─────────────────────────────────────────────

def bond_ball_mill_energy(wi: float, p80_um: float, f80_um: float) -> float:
    """
    Bond energy law for ball mill.

    W = Wi × 10 × (1/√P80 − 1/√F80)

    Args:
        wi: Bond work index (kWh/t)
        p80_um: Product 80% passing size (µm)
        f80_um: Feed 80% passing size (µm)
    Returns:
        Specific energy (kWh/t)
    Raises:
        ValueError: if p80_um <= 0 or f80_um <= 0
    """
    try:
        if p80_um <= 0:
            raise ValueError(f"p80_um must be > 0, got {p80_um}")
        if f80_um <= 0:
            raise ValueError(f"f80_um must be > 0, got {f80_um}")
        if p80_um >= f80_um:
            raise ValueError(f"Product size ({p80_um} µm) must be < feed size ({f80_um} µm)")
        return wi * 10.0 * (1.0 / math.sqrt(p80_um) - 1.0 / math.sqrt(f80_um))
    except Exception as e:
        logger.error(
            "bond_ball_mill_energy failed (wi=%.1f, p80=%.1f, f80=%.1f): %s",
            wi, p80_um, f80_um, e,
        )
        raise RuntimeError(
            f"bond_ball_mill_energy failed for wi={wi}, p80={p80_um}, f80={f80_um}"
        ) from e


def bond_ball_mill_energy_with_ef(
    wi: float,
    p80_um: float,
    f80_um: float,
    ef1: float = 1.0,
    ef2: float = 1.0,
    ef3: float = 1.0,
    ef4: float = 1.0,
    ef5: float = 1.0,
    ef6: float = 1.0,
    ef7: float = 1.0,
    ef8: float = 1.0,
) -> float:
    """
    Bond energy law with Rowland & Kjos efficiency factors (EF1–EF8).

    W = Wi × 10 × (1/√P80 − 1/√F80) × EF1 × EF2 × ... × EF8

    Common EF factors for gold plants:
      EF3 = 1.2 for diameter correction (mills < 3.81m)
      EF4 = 1.0–1.3 for oversized feed
      EF5 = 1.0–1.3 for fine product (P80 < 75 µm)

    Args:
        wi: Bond work index (kWh/t)
        p80_um: Product 80% passing size (µm)
        f80_um: Feed 80% passing size (µm)
        ef1–ef8: Rowland & Kjos efficiency factors (default 1.0)
    Returns:
        Corrected specific energy (kWh/t)
    """
    base = bond_ball_mill_energy(wi, p80_um, f80_um)
    ef_total = ef1 * ef2 * ef3 * ef4 * ef5 * ef6 * ef7 * ef8
    return base * ef_total


# ─── SAG Mill ────────────────────────────────────────────────────────────────

def sag_mill_power(spi_kwh_t: float, tph: float) -> float:
    """
    SAG mill power estimate using SPI (SAG Power Index).

    P_SAG = SPI × tph

    Args:
        spi_kwh_t: SAG Power Index (kWh/t)
        tph: Throughput (t/h)
    Returns:
        Power draw (kW)
    """
    return spi_kwh_t * tph


def sag_mill_power_starkey(spi_min: float, tph: float) -> float:
    """
    SAG mill power using Starkey SPI (minutes).

    P = TPH × SPI / 60 / 0.65

    Args:
        spi_min: SAG Power Index in minutes (from Starkey test)
        tph: Throughput (t/h)
    Returns:
        Power draw (kW)
    """
    if spi_min <= 0:
        raise ValueError(f"spi_min must be > 0, got {spi_min}")
    return tph * spi_min / 60.0 / 0.65


def sag_mill_specific_energy_morrell(
    mia_kwh_t: float,
    f80_mm: float,
    t80_mm: float,
) -> float:
    """
    SAG mill specific energy using Morrell Mi model (M3 simplified).

    Ecs_SAG = Mia × (f(t80) - f(f80))
    where f(x) = x^(-0.295)

    Args:
        mia_kwh_t: Morrell Mi parameter for AG/SAG (kWh/t)
        f80_mm: Feed 80% passing size (mm)
        t80_mm: Transfer size 80% passing (mm) — SAG product
    Returns:
        Specific energy (kWh/t)
    """
    try:
        if f80_mm <= 0 or t80_mm <= 0:
            raise ValueError("Size parameters must be > 0")
        if t80_mm >= f80_mm:
            raise ValueError(f"Transfer size ({t80_mm} mm) must be < feed size ({f80_mm} mm)")
        # Morrell f(x) = x^(-0.295) where x is in mm
        f_t80 = t80_mm ** (-0.295)
        f_f80 = f80_mm ** (-0.295)
        return mia_kwh_t * (f_t80 - f_f80)
    except Exception as e:
        logger.error(
            "sag_mill_specific_energy_morrell failed (mia=%.2f, f80=%.1f, t80=%.1f): %s",
            mia_kwh_t, f80_mm, t80_mm, e,
        )
        raise RuntimeError(f"sag_mill_specific_energy_morrell failed: {e}") from e


# ─── HPGR ────────────────────────────────────────────────────────────────────

def hpgr_specific_energy(mih_kwh_t: float, f80_um: float, p80_um: float) -> float:
    """
    HPGR specific energy using simplified Morrell M3 model.

    Ecs = Mih × ln(f80/p80)

    Args:
        mih_kwh_t: Morrell Mi parameter for HPGR (kWh/t)
        f80_um: Feed 80% passing size (µm)
        p80_um: Product 80% passing size (µm)
    Returns:
        Specific energy (kWh/t)
    """
    try:
        if p80_um <= 0 or f80_um <= 0:
            raise ValueError("Size parameters must be > 0")
        if p80_um >= f80_um:
            raise ValueError(f"Product size ({p80_um} µm) must be < feed size ({f80_um} µm)")
        return mih_kwh_t * math.log(f80_um / p80_um)
    except Exception as e:
        logger.error(
            "hpgr_specific_energy failed (mih=%.1f, f80=%.1f, p80=%.1f): %s",
            mih_kwh_t, f80_um, p80_um, e,
        )
        raise RuntimeError(f"hpgr_specific_energy failed for mih={mih_kwh_t}") from e


def hpgr_specific_energy_spf(
    spf_n_mm2: float,
    f80_mm: float,
    p80_mm: float,
) -> float:
    """
    HPGR specific energy from specific pressing force (empirical).

    Ecs = 0.5 × SPF × ln(F80/P80)

    Args:
        spf_n_mm2: Specific pressing force (N/mm²), typical 2–5 N/mm²
        f80_mm: Feed 80% passing size (mm)
        p80_mm: Product 80% passing size (mm)
    Returns:
        Specific energy (kWh/t)
    """
    try:
        if f80_mm <= 0 or p80_mm <= 0:
            raise ValueError("Size parameters must be > 0")
        if p80_mm >= f80_mm:
            raise ValueError(f"Product size ({p80_mm} mm) must be < feed size ({f80_mm} mm)")
        return 0.5 * spf_n_mm2 * math.log(f80_mm / p80_mm)
    except Exception as e:
        logger.error(
            "hpgr_specific_energy_spf failed (spf=%.2f, f80=%.1f, p80=%.1f): %s",
            spf_n_mm2, f80_mm, p80_mm, e,
        )
        raise RuntimeError(f"hpgr_specific_energy_spf failed: {e}") from e


# ─── IsaMill / Vertimill (Regrind) ───────────────────────────────────────────

def regrind_specific_energy(
    sig_kwh_t: float,
    f80_um: float,
    p80_um: float,
) -> float:
    """
    Regrind mill specific energy (IsaMill / Vertimill / SMD).

    Uses simplified Morrell model: Ecs = Sig × ln(F80/P80)

    Args:
        sig_kwh_t: Specific intensity of grinding (kWh/t), from testwork
        f80_um: Feed 80% passing size (µm)
        p80_um: Product 80% passing size (µm)
    Returns:
        Specific energy (kWh/t)
    """
    try:
        if p80_um <= 0 or f80_um <= 0:
            raise ValueError("Size parameters must be > 0")
        if p80_um >= f80_um:
            raise ValueError(f"Product size ({p80_um} µm) must be < feed size ({f80_um} µm)")
        return sig_kwh_t * math.log(f80_um / p80_um)
    except Exception as e:
        logger.error(
            "regrind_specific_energy failed (sig=%.1f, f80=%.1f, p80=%.1f): %s",
            sig_kwh_t, f80_um, p80_um, e,
        )
        raise RuntimeError(f"regrind_specific_energy failed: {e}") from e


# ─── Total Comminution Energy ─────────────────────────────────────────────────

def total_comminution_energy(
    e_sag: float = 0.0,
    e_bm: float = 0.0,
    e_hpgr: float = 0.0,
    e_isamill: float = 0.0,
    e_aux: float = 0.0,
) -> float:
    """Total installed comminution power (kW)."""
    return e_sag + e_bm + e_hpgr + e_isamill + e_aux


# ─── CO₂ Footprint ───────────────────────────────────────────────────────────

def comminution_co2(
    total_kw: float,
    tph: float,
    grid_factor_kg_kwh: float = 0.5,
) -> float:
    """
    CO₂ footprint from comminution energy (kgCO₂/t).

    Args:
        total_kw: Total comminution power (kW)
        tph: Throughput (t/h)
        grid_factor_kg_kwh: Grid emission factor (kgCO₂/kWh), site-specific
    Returns:
        CO₂ intensity (kgCO₂/t ore processed)
    """
    if tph <= 0:
        return 0.0
    return (total_kw / tph) * grid_factor_kg_kwh


# ─── Circulating Load ─────────────────────────────────────────────────────────

def circulating_load_pct(
    cyclone_feed_tph: float,
    cyclone_overflow_tph: float,
) -> float:
    """
    Circulating load percentage.

    CL% = (Cyclone Feed - Cyclone O/F) / Cyclone O/F × 100

    Typical range: 200–400% for gold plant ball mills.

    Args:
        cyclone_feed_tph: Total cyclone feed (t/h)
        cyclone_overflow_tph: Cyclone overflow (product) (t/h)
    Returns:
        Circulating load (%)
    """
    if cyclone_overflow_tph <= 0:
        return 0.0
    return (cyclone_feed_tph - cyclone_overflow_tph) / cyclone_overflow_tph * 100.0


# ─── P80 Estimation ──────────────────────────────────────────────────────────

def estimate_p80_from_energy(
    wi: float,
    energy_kwh_t: float,
    f80_um: float,
) -> float:
    """
    Estimate P80 from specific energy using Bond equation (inverse).

    P80 = 1 / (energy / (10 × Wi) + 1/√F80)²

    Args:
        wi: Bond Work Index (kWh/t)
        energy_kwh_t: Applied specific energy (kWh/t)
        f80_um: Feed 80% passing size (µm)
    Returns:
        Estimated P80 (µm)
    """
    try:
        if wi <= 0 or energy_kwh_t <= 0 or f80_um <= 0:
            raise ValueError("All parameters must be > 0")
        term = energy_kwh_t / (10.0 * wi) + 1.0 / math.sqrt(f80_um)
        if term <= 0:
            raise ValueError("Computed term is non-positive — check input values")
        return 1.0 / (term ** 2)
    except Exception as e:
        logger.error(
            "estimate_p80_from_energy failed (wi=%.1f, energy=%.2f, f80=%.1f): %s",
            wi, energy_kwh_t, f80_um, e,
        )
        raise RuntimeError(f"estimate_p80_from_energy failed: {e}") from e


# ─── Abrasion Index ──────────────────────────────────────────────────────────

def liner_wear_rate(
    abrasion_index: float,
    mill_diameter_m: float,
    ball_charge_pct: float = 35.0,
) -> float:
    """
    Estimate liner wear rate (g/kWh) from Abrasion Index (Ai).

    Empirical model: wear = Ai × 0.35 × (D/3.81)^0.3 × (ball_charge/35)^0.5

    Args:
        abrasion_index: Bond Abrasion Index (dimensionless, 0–1)
        mill_diameter_m: Mill diameter (m)
        ball_charge_pct: Ball charge (% volume)
    Returns:
        Liner wear rate (g/kWh)
    """
    try:
        if abrasion_index < 0:
            raise ValueError("Abrasion index must be >= 0")
        if mill_diameter_m <= 0:
            raise ValueError("Mill diameter must be > 0")
        d_factor = (mill_diameter_m / 3.81) ** 0.3
        charge_factor = (ball_charge_pct / 35.0) ** 0.5
        return abrasion_index * 0.35 * d_factor * charge_factor
    except Exception as e:
        logger.error(
            "liner_wear_rate failed (ai=%.3f, d=%.2f): %s",
            abrasion_index, mill_diameter_m, e,
        )
        raise RuntimeError(f"liner_wear_rate failed: {e}") from e


# ─── Ore Hardness Classification ─────────────────────────────────────────────

def classify_ore_hardness(bwi_kwh_t: float) -> str:
    """
    Classify ore hardness from Bond Work Index.

    Classification per SME Mineral Processing Handbook:
      < 7 kWh/t  : Very Soft
      7–10        : Soft
      10–14       : Medium
      14–20       : Hard
      > 20        : Very Hard

    Args:
        bwi_kwh_t: Bond Work Index (kWh/t)
    Returns:
        Hardness class string
    """
    if bwi_kwh_t < 7:
        return "Very Soft"
    elif bwi_kwh_t < 10:
        return "Soft"
    elif bwi_kwh_t < 14:
        return "Medium"
    elif bwi_kwh_t < 20:
        return "Hard"
    else:
        return "Very Hard"


def recommend_grinding_circuit(
    bwi_kwh_t: float,
    mia_kwh_t: Optional[float] = None,
    grg_pct: Optional[float] = None,
    s_sulfide_pct: Optional[float] = None,
) -> dict:
    """
    Recommend grinding circuit configuration based on ore characterisation.

    Decision logic based on industry best practices:
      - BWi > 16 → HPGR preferred over SAG
      - BWi < 10 → AG mill viable
      - GRG > 20% → Add gravity circuit
      - S_sulfide > 2% → Add flotation + regrind

    Args:
        bwi_kwh_t: Bond Work Index (kWh/t)
        mia_kwh_t: Morrell Mi for AG/SAG (optional)
        grg_pct: Gravity Recoverable Gold (%)
        s_sulfide_pct: Sulfide sulfur content (%)
    Returns:
        dict with recommended circuit and rationale
    """
    circuit = []
    rationale = []

    # Primary grinding
    if bwi_kwh_t > 16:
        circuit.append("HPGR")
        rationale.append(f"BWi={bwi_kwh_t:.1f} kWh/t > 16 → HPGR more energy-efficient than SAG")
    elif bwi_kwh_t < 10 and mia_kwh_t is not None and mia_kwh_t < 8:
        circuit.append("AG_MILL")
        rationale.append(f"BWi={bwi_kwh_t:.1f} kWh/t < 10 and Mia={mia_kwh_t:.1f} → AG mill viable")
    else:
        circuit.append("SAG_MILL")
        rationale.append(f"BWi={bwi_kwh_t:.1f} kWh/t → SAG mill standard choice")

    circuit.append("BALL_MILL")

    # Gravity circuit
    if grg_pct is not None and grg_pct > 20:
        circuit.append("GRAVITY_CONCENTRATOR")
        rationale.append(f"GRG={grg_pct:.1f}% > 20% → Gravity circuit recommended")

    # Flotation + regrind
    if s_sulfide_pct is not None and s_sulfide_pct > 2:
        circuit.extend(["FLOTATION_ROUGHER", "ISAMILL"])
        rationale.append(
            f"S_sulfide={s_sulfide_pct:.1f}% > 2% → Flotation + regrind for sulfide recovery"
        )

    return {
        "recommended_circuit": circuit,
        "hardness_class": classify_ore_hardness(bwi_kwh_t),
        "rationale": rationale,
        "bwi_kwh_t": bwi_kwh_t,
    }
