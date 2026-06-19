# backend/engines/geotech.py
"""
Geotechnical calculation engine for MPDPMS v4.

Implements slope stability (Bishop Simplified), TSF sizing,
and acid-base accounting (ABA/NAG/PAG) functions.
"""

import logging
import math
from typing import Tuple

logger = logging.getLogger("mpdpms.geotech")

# Minimum factor of safety requirements by construction method
TSF_MIN_FS = {
    "downstream": {"static": 1.5, "seismic": 1.2},
    "centreline": {"static": 1.3, "seismic": 1.1},
    "upstream":   {"static": 1.2, "seismic": 1.0},
}


def bishop_factor_of_safety(
    slope_angle_deg: float,
    slope_height_m: float,
    cohesion_kpa: float,
    friction_angle_deg: float,
    gamma_kn_m3: float,
    pore_pressure_ratio: float,
    n_slices: int = 10,
    seismic_coefficient: float = 0.1,
    max_iterations: int = 50,
    tolerance: float = 1e-4,
) -> Tuple[float, float]:
    """
    Compute static and seismic factor of safety using Bishop Simplified method.

    Parameters
    ----------
    slope_angle_deg : float
        Slope face angle in degrees.
    slope_height_m : float
        Total slope height in metres.
    cohesion_kpa : float
        Effective cohesion in kPa.
    friction_angle_deg : float
        Effective friction angle in degrees.
    gamma_kn_m3 : float
        Unit weight of soil/tailings in kN/m³.
    pore_pressure_ratio : float
        Pore pressure ratio (ru), dimensionless.
    n_slices : int
        Number of vertical slices.
    seismic_coefficient : float
        Pseudo-static horizontal seismic coefficient (kh).
    max_iterations : int
        Maximum iterations for convergence.
    tolerance : float
        Convergence tolerance on FS.

    Returns
    -------
    (fs_static, fs_seismic) : Tuple[float, float]
    """
    try:
        if not (1.0 <= slope_angle_deg <= 85.0):
            raise ValueError(f"Slope angle must be 1–85°, got {slope_angle_deg}°")
        if slope_height_m <= 0:
            raise ValueError(f"Slope height must be > 0, got {slope_height_m}")

        alpha_rad = math.radians(slope_angle_deg)
        phi = math.radians(friction_angle_deg)
        # slice base width along horizontal
        b = slope_height_m / (n_slices * math.sin(alpha_rad))

        def _iterate(kh: float) -> float:
            fs = 1.5  # initial guess
            for _ in range(max_iterations):
                numerator = 0.0
                denominator = 0.0
                for i in range(n_slices):
                    alpha_i = alpha_rad * (i + 0.5) / n_slices
                    h_i = slope_height_m * (1.0 - (i + 0.5) / n_slices)
                    W = gamma_kn_m3 * h_i * b
                    u = pore_pressure_ratio * gamma_kn_m3 * h_i
                    m_alpha = math.cos(alpha_i) * (
                        1.0 + math.tan(alpha_i) * math.tan(phi) / fs
                    )
                    resistance = cohesion_kpa * b + (W - u * b) * math.tan(phi)
                    seismic_force = kh * W * math.cos(alpha_i)
                    numerator += resistance / (m_alpha + 1e-9)
                    denominator += W * math.sin(alpha_i) + seismic_force * math.cos(alpha_i)
                fs_new = numerator / (denominator + 1e-9)
                if abs(fs_new - fs) < tolerance:
                    return fs_new
                fs = fs_new
            logger.warning("Bishop iteration did not converge for slope %.1f° (FS=%.3f after %d iterations)",
                            slope_angle_deg, fs, max_iterations)
            return fs

        fs_static = _iterate(kh=0.0)
        fs_seismic = _iterate(kh=seismic_coefficient)
        return fs_static, fs_seismic
    except Exception as e:
        logger.error("bishop_factor_of_safety failed (angle=%.1f, height=%.1f): %s", slope_angle_deg, slope_height_m, e)
        raise RuntimeError(f"bishop_factor_of_safety failed for angle={slope_angle_deg}, height={slope_height_m}") from e


def tsf_volume_capacity(total_tailings_t: float, deposition_density_t_m3: float) -> float:
    """
    Calculate tailings storage facility volume capacity.

    Parameters
    ----------
    total_tailings_t : float
        Total tailings mass in tonnes.
    deposition_density_t_m3 : float
        Deposition (in-place) density in t/m³.

    Returns
    -------
    float
        Volume in m³, rounded to 2 decimal places.
    """
    return round(total_tailings_t / deposition_density_t_m3, 2)


def tsf_raise_height(annual_volume_m3: float, embankment_area_ha: float) -> float:
    """
    Calculate annual raise height for a TSF embankment.

    Parameters
    ----------
    annual_volume_m3 : float
        Annual tailings volume deposited in m³.
    embankment_area_ha : float
        Embankment footprint area in hectares.

    Returns
    -------
    float
        Raise height in metres, rounded to 3 decimal places.
    """
    return round(annual_volume_m3 / (embankment_area_ha * 10000.0), 3)


def compute_aba(
    sulfide_s_pct: float,
    np_kg_caco3_t: float,
) -> Tuple[float, float, float]:
    """
    Acid–Base Accounting (ABA) — potentiel acide type Sobek (soufre sulfure).

    AP = %S sulfure × 31,25 → kg CaCO₃ équivalent / t (facteur pyrite/marcassite
    standard en pratique géo-environnementale ; caler sur protocole labo).

    Parameters
    ----------
    sulfide_s_pct : float
        Soufre sous forme sulfure (%), pas soufre total.
    np_kg_caco3_t : float
        Potentiel de neutralisation NP (kg CaCO₃/t).

    Returns
    -------
    (ap, nnp, npr) : Tuple[float, float, float]
        ap  – Acid Potential (kg CaCO3/t)
        nnp – Net Neutralisation Potential (kg CaCO3/t)
        npr – Neutralisation Potential Ratio (dimensionless)
    """
    try:
        ap = round(sulfide_s_pct * 31.25, 3)
        nnp = round(np_kg_caco3_t - ap, 3)
        if ap > 0:
            npr = round(np_kg_caco3_t / ap, 4)
        else:
            npr = 999.9
        return ap, nnp, npr
    except Exception as e:
        logger.error("compute_aba failed (sulfide_s=%.2f, np=%.2f): %s", sulfide_s_pct, np_kg_caco3_t, e)
        raise RuntimeError(f"compute_aba failed for sulfide_s={sulfide_s_pct}") from e


def classify_pag(nnp: float, npr: float) -> str:
    """
    Classify material as Potentially Acid Generating (PAG).

    Parameters
    ----------
    nnp : float
        Net Neutralisation Potential (kg CaCO3/t).
    npr : float
        Neutralisation Potential Ratio.

    Returns
    -------
    str
        "PAG", "Non-PAG", or "Uncertain".
    """
    if nnp < -20.0 or npr < 1.0:
        return "PAG"
    if nnp > 20.0 and npr > 2.0:
        return "Non-PAG"
    return "Uncertain"


def classify_ard_risk(pag_pct: float) -> str:
    """
    Classify Acid Rock Drainage (ARD) risk based on PAG percentage.

    Parameters
    ----------
    pag_pct : float
        Percentage of PAG material in the deposit (0–100).

    Returns
    -------
    str
        "Low", "Medium", "High", or "Critical".
    """
    if pag_pct >= 70.0:
        return "Critical"
    if pag_pct >= 40.0:
        return "High"
    if pag_pct >= 10.0:
        return "Medium"
    return "Low"
