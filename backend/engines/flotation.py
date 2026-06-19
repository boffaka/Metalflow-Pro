# backend/engines/flotation.py
"""
Flotation engine — first-order kinetic model (or / sulfides).

R = Rmax × (1 − exp(−k×τ)), τ en minutes — aligné sur ``process_simulator.flotation_recovery``.
"""
from __future__ import annotations
import logging
import math

logger = logging.getLogger(__name__)


def _as_recovery_cap(value: float) -> float:
    """Rmax en fraction (0–1) ou pourcent (0–100) si value > 1."""
    x = float(value)
    if x > 1.0:
        x = x / 100.0
    return max(0.0, min(x, 1.0))


def flotation_recovery(r_max: float, k: float, tau_min: float) -> float:
    """
    Récupération flottation — cinétique du 1er ordre.

    Paramètres
    ----------
    r_max : float
        Récupération asymptotique, fraction 0–1 (ou % si > 1).
    k : float
        Constante cinétique (1/min), ordre de grandeur typ. 0,1–2 pour Au sulfures.
    tau_min : float
        Temps de séjour total flottation (min).

    Returns
    -------
    float
        Récupération en fraction 0–Rmax.
    """
    try:
        if tau_min <= 0:
            return 0.0
        cap = _as_recovery_cap(r_max)
        kk = max(0.0, float(k))
        out = cap * (1.0 - math.exp(-kk * float(tau_min)))
        return max(0.0, min(cap, out))
    except Exception as e:
        logger.error("flotation_recovery failed (r_max=%.4f, k=%.4f, tau_min=%.1f): %s", r_max, k, tau_min, e)
        return 0.0


def mass_pull(
    collector_g_t: float,
    frother_g_t: float,
    air_flow_factor: float = 1.0,
) -> float:
    """
    Estimate flotation mass pull (% of feed to concentrate).
    Empirical linear model for design purposes only.

    Note: Coefficients are empirical approximations. Calibrate against
    plant or pilot data for site-specific accuracy.
    """
    try:
        base_pull = 0.06 * collector_g_t + 0.04 * frother_g_t
        # Clamp to physically reasonable range (0.5–30% mass pull)
        return max(0.5, min(30.0, base_pull * air_flow_factor))
    except Exception as e:
        logger.error("mass_pull failed (collector=%.1f, frother=%.1f): %s", collector_g_t, frother_g_t, e)
        return 0.5


def concentrate_grade(
    feed_grade_g_t: float,
    recovery: float,
    mass_pull_pct: float,
) -> float:
    """
    Teneur en or du concentré de flottation (g/t).

    Conc = feed_grade × R / (mass_pull/100), avec R en fraction (ou % si R > 1)
    et mass_pull en % masse aliment → concentré.
    """
    try:
        if mass_pull_pct <= 0:
            return 0.0
        r = float(recovery) / 100.0 if float(recovery) > 1.0 else float(recovery)
        r = max(0.0, min(r, 1.0))
        return feed_grade_g_t * r / (mass_pull_pct / 100.0)
    except Exception as e:
        logger.error("concentrate_grade failed (feed_grade=%.2f, recovery=%.4f, mass_pull=%.1f): %s", feed_grade_g_t, recovery, mass_pull_pct, e)
        return 0.0
