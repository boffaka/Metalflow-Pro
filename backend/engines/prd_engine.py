# backend/engines/prd_engine.py
"""PRD — Predictive Recovery Degradation over LOM.

Fonction pure : pas d'accès DB. Monte Carlo vectorisé NumPy.
< 10s pour 500 runs × 15 ans.
"""
from __future__ import annotations
import logging

import numpy as np

logger = logging.getLogger("mpdpms.prd_engine")

TROY_OZ_PER_GRAM = 1 / 31.1035
DEFAULT_RECOVERY = 0.89  # fraction


def compute_lom(
    domain_mix_by_year: list[dict],
    recovery_by_domain: dict[str, float],
    base_grade_g_t: float = 1.5,
    throughput_tph: float = 100.0,
    hours_per_year: float = 8000.0,
    mc_runs: int = 500,
    seed: int = 42,
) -> list[dict]:
    """
    Calcule les prédictions métallurgiques annuelles avec Monte Carlo vectorisé.

    domain_mix_by_year : [{"year": int, "mix": {"D01": 0.6, "D02": 0.4}, "grade_g_t": float, ...}]
    recovery_by_domain : {"D01": 0.89, "D02": 0.82}  (fractions 0-1)

    Retourne : [{year, domain_mix, blended_recovery_p50, blended_recovery_p10,
                  blended_recovery_p90, gold_oz_p50, gold_oz_p10, gold_oz_p90,
                  feed_grade_g_t, tonnage_t, blended_bwi, blended_cn_kg_t}]
    """
    if not domain_mix_by_year:
        return []

    n_years = len(domain_mix_by_year)
    rng = np.random.default_rng(seed)

    # Récupération annuelle blendée (fraction)
    rec_annual = np.zeros(n_years)
    for i, yr in enumerate(domain_mix_by_year):
        mix = yr.get("mix") or {}
        if mix:
            rec_annual[i] = sum(
                pct * recovery_by_domain.get(dom, DEFAULT_RECOVERY)
                for dom, pct in mix.items()
            )
        else:
            rec_annual[i] = DEFAULT_RECOVERY

    # Monte Carlo vectorisé : shape (mc_runs, n_years)
    grade_noise  = rng.lognormal(0.0, 0.08, (mc_runs, n_years))        # ±8% grade
    rec_noise    = rng.normal(0.0, 0.02, (mc_runs, n_years))            # ±2% récupération
    ton_noise    = rng.normal(1.0, 0.05, (mc_runs, n_years))            # ±5% tonnage

    annual_tonnage = throughput_tph * hours_per_year  # t/an

    results = []
    for i, yr_data in enumerate(domain_mix_by_year):
        grade = yr_data.get("grade_g_t") or base_grade_g_t
        rec_mu = rec_annual[i]

        oz_mc = (
            annual_tonnage
            * grade * grade_noise[:, i]
            * np.clip(rec_mu + rec_noise[:, i], 0.01, 0.999)
            * TROY_OZ_PER_GRAM
            * ton_noise[:, i]
        )

        p10, p50, p90 = float(np.percentile(oz_mc, 10)), float(np.percentile(oz_mc, 50)), float(np.percentile(oz_mc, 90))

        # Récupération MC
        rec_mc = np.clip(rec_mu + rec_noise[:, i], 0.01, 0.999)
        rp10, rp50, rp90 = float(np.percentile(rec_mc * 100, 10)), float(np.percentile(rec_mc * 100, 50)), float(np.percentile(rec_mc * 100, 90))

        results.append({
            "year": yr_data.get("year", i + 1),
            "domain_mix": yr_data.get("mix") or {},
            "feed_grade_g_t": round(grade, 3),
            "tonnage_t": round(annual_tonnage, 0),
            "blended_recovery_p50": round(rp50, 2),
            "blended_recovery_p10": round(rp10, 2),
            "blended_recovery_p90": round(rp90, 2),
            "gold_oz_p50": round(p50, 0),
            "gold_oz_p10": round(p10, 0),
            "gold_oz_p90": round(p90, 0),
            "blended_bwi": yr_data.get("blended_bwi"),
            "blended_cn_kg_t": yr_data.get("blended_cn_kg_t"),
            "is_critical": False,  # mis à jour par detect_critical_periods
            "critical_reasons": [],
        })

    return results


def detect_critical_periods(
    predictions: list[dict],
    thresholds: dict | None = None,
) -> list[dict]:
    """
    Identifie et fusionne les périodes critiques consécutives.

    thresholds (optionnel) :
      recovery_min_pct : float  (défaut 85.0)
      bwi_max_kwh_t    : float  (défaut 18.0)
      cn_max_kg_t      : float  (défaut 1.0)
    """
    thr = thresholds or {}
    rec_min  = float(thr.get("recovery_min_pct", 85.0))
    bwi_max  = float(thr.get("bwi_max_kwh_t", 18.0))
    cn_max   = float(thr.get("cn_max_kg_t", 1.0))

    critical_years: list[dict] = []
    for pred in predictions:
        reasons: list[str] = []
        if (pred.get("blended_recovery_p10") or 100) < rec_min:
            reasons.append(f"Récupération P10 = {pred.get('blended_recovery_p10'):.1f}% < {rec_min}%")
        if pred.get("blended_bwi") and pred["blended_bwi"] > bwi_max:
            reasons.append(f"BWI blend = {pred['blended_bwi']:.1f} kWh/t > {bwi_max}")
        if pred.get("blended_cn_kg_t") and pred["blended_cn_kg_t"] > cn_max:
            reasons.append(f"CN blend = {pred['blended_cn_kg_t']:.3f} kg/t > {cn_max}")

        if reasons:
            pred["is_critical"] = True
            pred["critical_reasons"] = reasons
            critical_years.append(pred)

    if not critical_years:
        return []

    # Fusionner périodes consécutives
    periods: list[dict] = []
    start = critical_years[0]
    prev_year = start["year"]

    for yr in critical_years[1:]:
        if yr["year"] == prev_year + 1:
            prev_year = yr["year"]
        else:
            periods.append(_build_period(start, prev_year, predictions))
            start = yr
            prev_year = yr["year"]
    periods.append(_build_period(start, prev_year, predictions))

    return periods


def _build_period(start: dict, year_end: int, all_predictions: list[dict]) -> dict:
    year_start = start["year"]
    span = [p for p in all_predictions if year_start <= p["year"] <= year_end]
    avg_rec = float(np.mean([p["blended_recovery_p50"] for p in span if p.get("blended_recovery_p50")]) or 0)
    total_oz_loss = sum(
        (p.get("gold_oz_p50") or 0) * 0.05  # estimation simplifiée perte 5%
        for p in span
    )
    drop = 90.0 - avg_rec
    severity = "critical" if drop > 15 else "high" if drop > 10 else "medium" if drop > 5 else "low"

    return {
        "year_start": year_start,
        "year_end": year_end,
        "n_years": year_end - year_start + 1,
        "severity": severity,
        "trigger_types": list({r for p in span for r in p.get("critical_reasons", [])}),
        "avg_recovery_p50": round(avg_rec, 2),
        "estimated_oz_loss": round(total_oz_loss, 0),
        "recommended_actions": _get_recommendations(span),
    }


def _get_recommendations(span: list[dict]) -> list[str]:
    recs: list[str] = []
    avg_rec = float(np.mean([p.get("blended_recovery_p50", 90) for p in span]))
    if avg_rec < 80:
        recs.append("Envisager prétraitement (biooxydation ou POX) pour cette période.")
    if avg_rec < 85:
        recs.append("Optimiser le mélange via IMBO pour diluer les domaines difficiles.")
    recs.append("Simuler avec le module Simulation pour valider les paramètres opératoires.")
    return recs


def compute_what_if(
    base_predictions: list[dict],
    overrides: dict,
) -> list[dict]:
    """
    Recalcule les prédictions avec des overrides (blend_recovery, grade_multiplier).
    Retourne les prédictions modifiées.
    """
    blend_rec = overrides.get("blend_recovery_pct")  # override global récup (%)
    grade_mult = overrides.get("grade_multiplier", 1.0)

    results = []
    for pred in base_predictions:
        p = dict(pred)
        if blend_rec:
            p["blended_recovery_p50"] = round(float(blend_rec), 2)
            p["blended_recovery_p10"] = round(float(blend_rec) * 0.95, 2)
            p["blended_recovery_p90"] = round(float(blend_rec) * 1.05, 2)
        if grade_mult != 1.0:
            for key in ["gold_oz_p50","gold_oz_p10","gold_oz_p90"]:
                if p.get(key):
                    p[key] = round(p[key] * grade_mult, 0)
            if p.get("feed_grade_g_t"):
                p["feed_grade_g_t"] = round(p["feed_grade_g_t"] * grade_mult, 3)
        results.append(p)
    return results


def compute_lom_summary(predictions: list[dict]) -> dict:
    """Agrège les prédictions annuelles en résumé LOM."""
    if not predictions:
        return {}
    recs = [p["blended_recovery_p50"] for p in predictions if p.get("blended_recovery_p50")]
    oz = [p["gold_oz_p50"] for p in predictions if p.get("gold_oz_p50")]
    critical = [p for p in predictions if p.get("is_critical")]
    best = max(predictions, key=lambda p: p.get("gold_oz_p50") or 0) if oz else None
    worst = min(predictions, key=lambda p: p.get("blended_recovery_p50") or 100) if recs else None
    return {
        "total_gold_oz_p50": round(sum(oz), 0),
        "average_recovery_p50": round(float(np.mean(recs)), 2) if recs else None,
        "recovery_range": {"min": round(min(recs), 2), "max": round(max(recs), 2)} if recs else {},
        "n_critical_periods": len(critical),
        "best_year": best["year"] if best else None,
        "worst_year": worst["year"] if worst else None,
        "n_years": len(predictions),
    }
