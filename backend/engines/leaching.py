# backend/engines/leaching.py
"""
Leach / CIP — moteur cinétique de lixiviation (données LIMS D1).

Modèle principal : R(t) = R∞ × (1 − exp(−k × t)).
Les paramètres k et R∞ sont ajustés sur les cinétiques bottle-roll D1.
"""
from __future__ import annotations
import logging
import math
from typing import Tuple, List

logger = logging.getLogger(__name__)

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:
    from constants import TROY_OZ_PER_GRAM


def _as_recovery_fraction(value: float) -> float:
    """Accept R∞ as fraction (0–1) or percent (0–100) from LIMS / ingénierie."""
    x = float(value)
    if x > 1.0:
        x = x / 100.0
    return max(0.0, min(x, 1.0))


def effective_k_cil(
    k_base: float,
    *,
    nacn_mg_l: float | None = None,
    do_mg_l: float | None = None,
    nacn_ref_mg_l: float = 325.0,
    do_ref_mg_l: float = 7.5,
    nacn_exp: float = 0.22,
    do_exp: float = 0.12,
    factor_min: float = 0.5,
    factor_max: float = 2.0,
) -> float:
    """
    Facteur correctif du taux cinétique k (1/h) à partir des conditions CIL/CIP courantes.

    Corrélation volontairement conservative (exposants faibles + clamp), à calibrer sur
    essais LIMS / pilote — alignée avec l'idée MetPlant 2008 que les hypothèses doivent
    être traçables et vérifiables contre données expérimentales.

    NaCN typiquement en mg/L ; DO souvent rapporté en mg/L ~ ppm dans la pulpe.
    Si un opérant est omis, la valeur de référence neutre est utilisée pour ce terme.
    """
    kb = max(0.0, float(k_base))
    if nacn_mg_l is None and do_mg_l is None:
        return kb

    cn = float(nacn_mg_l) if nacn_mg_l is not None else float(nacn_ref_mg_l)
    dox = float(do_mg_l) if do_mg_l is not None else float(do_ref_mg_l)
    cn = max(cn, 1e-6)
    dox = max(dox, 1e-6)
    cref = max(float(nacn_ref_mg_l), 1e-6)
    dref = max(float(do_ref_mg_l), 1e-6)

    factor = (cn / cref) ** float(nacn_exp) * (dox / dref) ** float(do_exp)
    factor = max(float(factor_min), min(float(factor_max), factor))
    return kb * factor


def cil_recovery(r_inf: float, k: float, srt_h: float) -> float:
    """
    Récupération Leach à un temps de résidence donné (même base que cinétique LIMS D1).

    R(t) = R∞ × (1 − exp(−k × SRT)), avec R∞ en fraction métallurgique (0–1).
    Si R∞ > 1, interprétation en pourcent (ex. 92 → 0,92).

    Returns
    -------
    float
        Récupération en fraction (0–1).
    """
    try:
        if srt_h <= 0:
            return 0.0
        ri = _as_recovery_fraction(r_inf)
        kk = max(0.0, float(k))
        out = ri * (1.0 - math.exp(-kk * float(srt_h)))
        return max(0.0, min(ri, out))
    except Exception as e:
        logger.error("cil_recovery failed (r_inf=%.4f, k=%.4f, srt_h=%.1f): %s", r_inf, k, srt_h, e)
        return 0.0


def fit_kinetic_params(
    times_h: List[float],
    recoveries: List[float],
) -> Tuple[float, float]:
    """
    Fit k (1/h) et R∞ (fraction 0–1) from LIMS D1 kinetics bottle-roll data.

    Si les récupérations sont saisies en % (p.ex. 85 au lieu de 0,85), elles
    sont normalisées automatiquement lorsque max(R) > 1,5.
    Returns (k, r_inf).
    """
    rec = [float(x) for x in recoveries]
    if rec and max(rec) > 1.5:
        rec = [x / 100.0 for x in rec]
    rec = [max(0.0, min(x, 1.0)) for x in rec]

    try:
        from scipy.optimize import curve_fit
        import numpy as np

        def model(t, k, r_inf):
            return r_inf * (1.0 - np.exp(-k * np.array(t)))

        popt, _ = curve_fit(
            model, times_h, rec,
            p0=[0.3, min(0.95, max(rec) if rec else 0.9)],
            bounds=([0.0, 0.0], [10.0, 1.0]),
            maxfev=5000,
        )
        return float(popt[0]), float(popt[1])
    except Exception:
        r_inf = max(rec) if rec else 0.0
        r_inf = max(0.0, min(r_inf, 1.0))
        if len(times_h) >= 2 and times_h[0] > 0:
            r0, t0 = rec[0], times_h[0]
            # Guard: r0 must be < r_inf to avoid log(0) or log(negative)
            if r_inf > 0 and r0 < r_inf:
                k = -math.log(1.0 - r0 / r_inf) / t0
            else:
                k = 0.3
        else:
            k = 0.3
        return k, r_inf


def pregnant_solution_grade(feed_grade_g_t: float, tph: float, recovery: float) -> float:
    """
    Débit d'or dissous dans la pulpe / solution riche (g Au/h).

    Formule : teneur (g/t) × débit solide (t/h) × R, avec R en fraction ;
    si R > 1, interprétation en pourcent.
    """
    try:
        r = _as_recovery_fraction(recovery)
        return feed_grade_g_t * tph * r
    except Exception as e:
        logger.error("pregnant_solution_grade failed (feed_grade=%.2f, tph=%.1f): %s", feed_grade_g_t, tph, e)
        return 0.0


def annual_gold_oz(
    tph: float,
    op_hours_day: float,
    avail_pct: float,
    grade_g_t: float,
    recovery: float,
) -> float:
    """Production annuelle d'or (onces troy) — R en fraction ou en % (>1 → %)."""
    try:
        r = _as_recovery_fraction(recovery)
        annual_t = tph * op_hours_day * 365.0 * (avail_pct / 100.0)
        gold_g = annual_t * grade_g_t * r
        return gold_g * TROY_OZ_PER_GRAM
    except Exception as e:
        logger.error("annual_gold_oz failed (tph=%.1f, grade=%.2f): %s", tph, grade_g_t, e)
        return 0.0
