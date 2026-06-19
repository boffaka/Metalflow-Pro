"""
GMIE PRD — Predictive Recovery Degradation over Life-of-Mine.

Combines GADE domain models with block model extraction sequence,
Monte Carlo uncertainty, and multi-threshold critical period detection.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

try:
    from ..db import qone
except ImportError:  # pragma: no cover
    from db import qone

logger = logging.getLogger("mpdpms.recovery_forecast")

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM

DEFAULT_THRESHOLDS = {
    "recovery_min_pct": 82.0,
    "bwi_max_kwh_t": 16.0,
    "cn_max_kg_t": 0.8,
    "production_min_oz": 0.0,
}

BOOTSTRAP_ITERATIONS = 200
MONTE_CARLO_ITERATIONS = 1000


def _load_blocks_with_domains(pid: str, domain_result: dict) -> list[dict]:
    try:
        from .geomet_predictor import classify_blocks
    except ImportError:
        from engines.geomet_predictor import classify_blocks
    return classify_blocks(pid, domain_result)


def _get_project_params(pid: str) -> dict:
    cfg = qone(
        "SELECT mine_life_years, target_tph, availability_pct, capacity_mtpa, operating_hours_day, "
        "gold_price_usd_oz "
        "FROM projects WHERE id = %s",
        (pid,),
    )
    if not cfg:
        return {"mine_life_years": 15, "annual_tonnage_mt": 3.0, "gold_price_usd_oz": 3200.0}

    mine_life = int(cfg.get("mine_life_years") or 15)
    avail = float(cfg.get("availability_pct") or 92) / 100
    annual_mt = float(cfg.get("capacity_mtpa") or 0)
    if annual_mt <= 0:
        tph = float(cfg.get("target_tph") or 8000)
        op_h = float(cfg.get("operating_hours_day") or 22.1)
        annual_mt = tph * op_h * 365 * avail / 1_000_000

    return {
        "mine_life_years": mine_life,
        "annual_tonnage_mt": annual_mt,
        "gold_price_usd_oz": float(cfg.get("gold_price_usd_oz") or 3200),
    }


def _allocate_blocks_to_periods(blocks: list[dict], annual_mt: float, mine_life: int) -> list[list[dict]]:
    periods: list[list[dict]] = [[] for _ in range(mine_life)]
    block_idx = 0
    annual_limit = annual_mt * 1_000_000

    for yr in range(mine_life):
        yr_tonnage = 0.0
        while block_idx < len(blocks) and yr_tonnage < annual_limit:
            blk = blocks[block_idx]
            tonnage = float(blk.get("tonnage", 0))
            if tonnage <= 0:
                block_idx += 1
                continue
            periods[yr].append(blk)
            yr_tonnage += tonnage
            block_idx += 1
        if block_idx >= len(blocks):
            break
    return periods


def _compute_period_stats(period_blocks: list[dict], year: int) -> dict:
    if not period_blocks:
        return {
            "year": year + 1,
            "tonnage_mt": 0,
            "grade_avg_g_t": 0,
            "recovery_pct": 0,
            "bwi_kwh_t": 0,
            "nacn_kg_t": 0,
            "annual_oz": 0,
            "domain_mix": {},
        }

    tonnages = np.array([float(b.get("tonnage", 0)) for b in period_blocks])
    grades = np.array([float(b.get("grade_au", 0)) for b in period_blocks])
    recoveries = np.array([float(b.get("predicted_recovery_pct", 90)) for b in period_blocks])
    bwis = np.array([float(b.get("bwi_kwh_t", 14)) for b in period_blocks])
    nacns = np.array([float(b.get("nacn_kg_t", 0.35)) for b in period_blocks])

    total_t = float(tonnages.sum())
    if total_t == 0:
        return {
            "year": year + 1,
            "tonnage_mt": 0,
            "grade_avg_g_t": 0,
            "recovery_pct": 0,
            "bwi_kwh_t": 0,
            "nacn_kg_t": 0,
            "annual_oz": 0,
            "domain_mix": {},
        }

    wt_grade = float(np.average(grades, weights=tonnages))
    wt_recovery = float(np.average(recoveries, weights=tonnages))
    wt_bwi = float(np.average(bwis, weights=tonnages))
    wt_nacn = float(np.average(nacns, weights=tonnages))

    annual_oz = total_t * wt_grade * (wt_recovery / 100) * TROY_OZ_PER_GRAM

    domain_counts: dict[str, float] = {}
    for b in period_blocks:
        d = b.get("domain", "Unknown")
        domain_counts[d] = domain_counts.get(d, 0) + float(b.get("tonnage", 0))
    domain_mix = {d: round(t / total_t * 100, 1) for d, t in domain_counts.items()}

    return {
        "year": year + 1,
        "tonnage_mt": round(total_t / 1_000_000, 3),
        "grade_avg_g_t": round(wt_grade, 3),
        "recovery_pct": round(wt_recovery, 2),
        "bwi_kwh_t": round(wt_bwi, 2),
        "nacn_kg_t": round(wt_nacn, 3),
        "annual_oz": round(annual_oz, 0),
        "domain_mix": domain_mix,
    }


def _bootstrap_confidence(period_blocks: list[dict], n_iter: int = BOOTSTRAP_ITERATIONS) -> dict:
    if len(period_blocks) < 3:
        rec = float(np.mean([b.get("predicted_recovery_pct", 90) for b in period_blocks])) if period_blocks else 90
        return {"p10": round(rec - 3, 2), "p50": round(rec, 2), "p90": round(rec + 3, 2)}

    recoveries = np.array([float(b.get("predicted_recovery_pct", 90)) for b in period_blocks])
    tonnages = np.array([float(b.get("tonnage", 0)) for b in period_blocks])
    rng = np.random.RandomState(42)
    boot = []
    n = len(recoveries)
    for _ in range(n_iter):
        idx = rng.choice(n, n, replace=True)
        boot.append(float(np.average(recoveries[idx], weights=tonnages[idx])))
    boot = np.array(boot)
    return {
        "p10": round(float(np.percentile(boot, 10)), 2),
        "p50": round(float(np.percentile(boot, 50)), 2),
        "p90": round(float(np.percentile(boot, 90)), 2),
    }


def _monte_carlo_period(
    period_blocks: list[dict],
    stats: dict,
    n_iter: int = MONTE_CARLO_ITERATIONS,
) -> dict:
    if not period_blocks or stats["tonnage_mt"] <= 0:
        return {"production_oz": stats.get("confidence", {})}

    tonnage = stats["tonnage_mt"] * 1_000_000
    grade_mu = stats["grade_avg_g_t"]
    rec_mu = stats["recovery_pct"]
    rec_sigma = max(1.0, rec_mu * 0.02)
    grade_sigma = max(0.05, grade_mu * 0.08)

    rng = np.random.RandomState(42)
    oz_samples = []
    rec_samples = []
    for _ in range(n_iter):
        g = max(0.01, rng.lognormal(np.log(max(grade_mu, 0.01)), grade_sigma / max(grade_mu, 0.01)))
        r = np.clip(rng.normal(rec_mu, rec_sigma), 50, 99)
        thr = tonnage * rng.normal(1.0, 0.05)
        oz_samples.append(thr * g * (r / 100) * TROY_OZ_PER_GRAM)
        rec_samples.append(r)

    oz_arr = np.array(oz_samples)
    rec_arr = np.array(rec_samples)
    return {
        "production_oz": {
            "p10": round(float(np.percentile(oz_arr, 10)), 0),
            "p50": round(float(np.percentile(oz_arr, 50)), 0),
            "p90": round(float(np.percentile(oz_arr, 90)), 0),
        },
        "recovery_pct": {
            "p10": round(float(np.percentile(rec_arr, 10)), 2),
            "p50": round(float(np.percentile(rec_arr, 50)), 2),
            "p90": round(float(np.percentile(rec_arr, 90)), 2),
        },
    }


def _forecast_lom_core(
    pid: str,
    domain_result: dict,
    thresholds: Optional[dict] = None,
) -> dict:
    blocks = _load_blocks_with_domains(pid, domain_result)
    if not blocks:
        return {
            "status": "no_blocks",
            "engine": "GMIE-PRD-v1",
            "message": "No block model data available for forecasting",
            "forecast": [],
        }

    params = _get_project_params(pid)
    mine_life = params["mine_life_years"]
    annual_mt = params["annual_tonnage_mt"]
    periods = _allocate_blocks_to_periods(blocks, annual_mt, mine_life)

    forecast = []
    cumulative_oz = 0.0
    for yr, period_blocks in enumerate(periods):
        stats = _compute_period_stats(period_blocks, yr)
        stats["confidence"] = _bootstrap_confidence(period_blocks)
        stats["monte_carlo"] = _monte_carlo_period(period_blocks, stats)
        cumulative_oz += stats["annual_oz"]
        stats["cumulative_oz"] = round(cumulative_oz, 0)
        forecast.append(stats)

    active = [f for f in forecast if f["tonnage_mt"] > 0]
    avg_recovery = float(np.mean([f["recovery_pct"] for f in active])) if active else 0.0

    return {
        "status": "ok",
        "engine": "GMIE-PRD-v1",
        "mine_life_years": mine_life,
        "annual_capacity_mt": annual_mt,
        "n_blocks_processed": len(blocks),
        "total_production_oz": round(cumulative_oz, 0),
        "avg_lom_recovery_pct": round(avg_recovery, 2),
        "thresholds": {**DEFAULT_THRESHOLDS, **(thresholds or {})},
        "forecast": forecast,
    }


def forecast_lom(
    pid: str,
    domain_result: dict,
    thresholds: Optional[dict] = None,
) -> dict:
    result = _forecast_lom_core(pid, domain_result, thresholds)
    if result["status"] == "ok":
        critical = _identify_critical_from_forecast(result["forecast"], result["thresholds"])
        result["n_critical_periods"] = len(critical)
    return result


def _identify_critical_from_forecast(
    forecast: list[dict],
    th: dict,
) -> list[dict]:
    critical: list[dict] = []
    consecutive_low = 0

    for period in forecast:
        if period["tonnage_mt"] == 0:
            continue

        flags: list[str] = []
        year = period["year"]

        if period["recovery_pct"] < th["recovery_min_pct"]:
            flags.append("RECOVERY_CRITICAL")
        if period.get("bwi_kwh_t", 0) > th["bwi_max_kwh_t"]:
            flags.append("COMMINUTION_CONSTRAINT")
        if period.get("nacn_kg_t", 0) > th["cn_max_kg_t"]:
            flags.append("REAGENT_COST_CRITICAL")

        mc_p10 = (period.get("monte_carlo") or {}).get("production_oz", {}).get("p10", period["annual_oz"])
        if th["production_min_oz"] > 0 and mc_p10 < th["production_min_oz"]:
            flags.append("HIGH_RISK_PERIOD")

        if period["annual_oz"] < th.get("production_min_oz", 0) and th.get("production_min_oz", 0) > 0:
            flags.append("ECONOMIC_THRESHOLD_BREACH")
            consecutive_low += 1
        else:
            consecutive_low = 0

        if not flags:
            continue

        dominant_domain = (
            max(period["domain_mix"].items(), key=lambda x: x[1])[0] if period.get("domain_mix") else "Unknown"
        )
        responsible = [d for d, pct in (period.get("domain_mix") or {}).items() if pct >= 20]

        critical.append(
            {
                "year": year,
                "flags": flags,
                "probable_causes": flags,
                "recovery_pct": period["recovery_pct"],
                "bwi_kwh_t": period.get("bwi_kwh_t"),
                "nacn_kg_t": period.get("nacn_kg_t"),
                "annual_oz": period["annual_oz"],
                "threshold_recovery_pct": th["recovery_min_pct"],
                "deficit_pct": round(th["recovery_min_pct"] - period["recovery_pct"], 2)
                if "RECOVERY_CRITICAL" in flags
                else None,
                "severity": "high" if len(flags) >= 2 or consecutive_low >= 2 else "moderate",
                "dominant_domain": dominant_domain,
                "responsible_domains": responsible,
                "domain_mix": period.get("domain_mix", {}),
                "confidence": period.get("confidence", {}),
                "monte_carlo": period.get("monte_carlo", {}),
                "mitigation_options": _suggest_mitigations(flags, period),
                "consecutive_low_production_years": consecutive_low,
            }
        )

    return critical


def identify_critical_periods(
    pid: str,
    domain_result: dict,
    threshold_pct: Optional[float] = None,
    thresholds: Optional[dict] = None,
) -> list[dict]:
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if threshold_pct is not None:
        th["recovery_min_pct"] = threshold_pct

    result = _forecast_lom_core(pid, domain_result, thresholds=th)
    if result["status"] != "ok":
        return []

    return _identify_critical_from_forecast(result["forecast"], th)


def _suggest_mitigations(flags: list[str], period: dict) -> list[str]:
    mitigations = []
    if "RECOVERY_CRITICAL" in flags:
        mitigations.append("Evaluate IMBO blend to dilute refractory domains")
        mitigations.append("Targeted infill drilling on dominant domain")
    if "COMMINUTION_CONSTRAINT" in flags:
        mitigations.append("Reduce throughput or upgrade SAG mill power")
        mitigations.append("Pre-screening / HPGR ahead of SAG for hard ore")
    if "REAGENT_COST_CRITICAL" in flags:
        mitigations.append("Pre-aeration / Cu removal before CIL")
        mitigations.append("Adjust blend to limit cyanicide domains")
    if "ECONOMIC_THRESHOLD_BREACH" in flags or "HIGH_RISK_PERIOD" in flags:
        mitigations.append("Review cut-off grade and mine sequence for Year {}".format(period["year"]))
    return mitigations
