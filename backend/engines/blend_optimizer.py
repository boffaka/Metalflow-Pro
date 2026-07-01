"""
GMIE IMBO — Intelligent Metallurgical Blend Optimizer.

LP / NLP blend optimization with production, margin, and NPV objective modes,
shadow price sensitivity, and plant constraint hierarchy per GMIE v1.0.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from scipy.optimize import linprog, minimize

try:
    from ..db import qone
except ImportError:  # pragma: no cover
    from db import qone

logger = logging.getLogger("mpdpms.blend_optimizer")

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM

DEFAULT_BWI_MAX = 16.0
# Deleterious-copper watch level for gold cyanidation. 50 ppm (0.005%) was far
# below the default assumed ore copper (0.02% = 200 ppm), so every default blend
# was infeasible on copper. Cyanide-soluble Cu typically only matters above
# ~0.05% (500 ppm); use that as the default cap so the constraint is meaningful.
DEFAULT_CU_MAX_PPM = 500.0
DEFAULT_S_MAX_PCT = 2.0
DEFAULT_MIN_RECOVERY_PCT = 88.0
DEFAULT_DISCOUNT_RATE = 0.08
DEFAULT_GOLD_PRICE = 3200.0

OBJECTIVE_MODES = ("production", "margin", "npv")


def _get_project_constraints(pid: str) -> dict:
    cfg = qone("SELECT gold_price_usd_oz FROM projects WHERE id = %s", (pid,))
    gold_price = float(cfg.get("gold_price_usd_oz") or DEFAULT_GOLD_PRICE) if cfg else DEFAULT_GOLD_PRICE

    s_max = DEFAULT_S_MAX_PCT
    try:
        from config import FLOTATION_S_THRESHOLD_PCT

        s_max = FLOTATION_S_THRESHOLD_PCT
    except (ImportError, AttributeError):
        pass

    return {
        "bwi_max_kwh_t": DEFAULT_BWI_MAX,
        "cu_max_ppm": DEFAULT_CU_MAX_PPM,
        "cu_max_pct": DEFAULT_CU_MAX_PPM / 10000.0,
        "s_max_pct": s_max,
        "min_recovery_pct": DEFAULT_MIN_RECOVERY_PCT,
        "gold_price_usd_oz": gold_price,
        "discount_rate": DEFAULT_DISCOUNT_RATE,
        "objective_mode": "production",
        "throughput_tpa": 3_000_000,
    }


def _extract_domain_vectors(domains: list[dict]) -> dict:
    n = len(domains)
    tonnages = np.zeros(n)
    grades = np.zeros(n)
    recoveries = np.zeros(n)
    bwi = np.zeros(n)
    sulphur = np.zeros(n)
    copper = np.zeros(n)
    nacn = np.zeros(n)
    cao = np.zeros(n)

    for i, d in enumerate(domains):
        p = d.get("profile", {})
        tonnages[i] = max(d.get("n_samples", 1) * 10000, 50000)
        grades[i] = float(p.get("au_g_t") or 0)
        recoveries[i] = float(p.get("au_recovery_pct") or d.get("avg_recovery_pct") or 90) / 100
        bwi[i] = float(p.get("bwi_kwh_t") or d.get("avg_bwi_kwh_t") or 14)
        sulphur[i] = float(p.get("s_total_pct") or 1)
        copper[i] = float(p.get("cu_pct") or 0.02)
        nacn[i] = float(p.get("nacn_consumption_kg_t") or d.get("avg_nacn_kg_t") or 0.35)
        cao[i] = float(p.get("cao_consumption_kg_t") or 0.5)

    return {
        "tonnages": tonnages,
        "grades": grades,
        "recoveries": recoveries,
        "bwi": bwi,
        "sulphur": sulphur,
        "copper": copper,
        "nacn": nacn,
        "cao": cao,
    }


def _build_blend_result(
    domains: list[dict],
    vecs: dict,
    ratios: np.ndarray,
    constraints: dict,
    solver: str,
    objective_mode: str,
    shadow_prices: Optional[dict] = None,
) -> dict:
    blend_details = []
    total_production_factor = 0.0

    for i, d in enumerate(domains):
        ratio = float(ratios[i])
        if ratio < 0.001:
            continue
        prod_factor = ratio * float(vecs["grades"][i]) * float(vecs["recoveries"][i])
        total_production_factor += prod_factor
        blend_details.append(
            {
                "domain_id": d["domain_id"],
                "domain_name": d["domain_name"],
                "ore_class": d.get("ore_class", "unknown"),
                "ratio_pct": round(ratio * 100, 2),
                "grade_contribution_g_t": round(ratio * float(vecs["grades"][i]), 4),
                "recovery_pct": round(float(vecs["recoveries"][i]) * 100, 2),
                "bwi_contribution_kwh_t": round(ratio * float(vecs["bwi"][i]), 2),
                "s_contribution_pct": round(ratio * float(vecs["sulphur"][i]), 3),
            }
        )

    blended_bwi = float(np.dot(ratios, vecs["bwi"]))
    blended_grade = float(np.dot(ratios, vecs["grades"]))
    blended_recovery = float(np.dot(ratios, vecs["recoveries"]))
    blended_s = float(np.dot(ratios, vecs["sulphur"]))
    blended_nacn = float(np.dot(ratios, vecs["nacn"]))
    blended_cao = float(np.dot(ratios, vecs["cao"]))
    blended_cu = float(np.dot(ratios, vecs["copper"]))

    throughput = float(constraints.get("throughput_tpa", 3_000_000))
    gold_price = float(constraints.get("gold_price_usd_oz", DEFAULT_GOLD_PRICE))
    annual_oz = throughput * blended_grade * blended_recovery * TROY_OZ_PER_GRAM
    opex_per_t = blended_nacn * 2.5 + blended_cao * 0.15 + blended_bwi * 0.08
    annual_margin = annual_oz * gold_price - opex_per_t * throughput

    binding = []
    if blended_bwi >= constraints.get("bwi_max_kwh_t", DEFAULT_BWI_MAX) * 0.98:
        binding.append("bwi_max")
    if blended_s >= constraints.get("s_max_pct", DEFAULT_S_MAX_PCT) * 0.98:
        binding.append("s_max")
    if blended_cu >= constraints.get("cu_max_pct", DEFAULT_CU_MAX_PPM / 10000.0) * 0.98:
        binding.append("cu_max")
    if blended_recovery <= constraints.get("min_recovery_pct", DEFAULT_MIN_RECOVERY_PCT) / 100 * 1.02:
        binding.append("min_recovery")

    return {
        "status": "optimal",
        "engine": "GMIE-IMBO-v1",
        "solver": solver,
        "objective_mode": objective_mode,
        "constraints_used": constraints,
        "optimal_blend": blend_details,
        "blended_kpis": {
            "grade_g_t": round(blended_grade, 3),
            "recovery_pct": round(blended_recovery * 100, 2),
            "bwi_kwh_t": round(blended_bwi, 1),
            "s_total_pct": round(blended_s, 3),
            "cu_pct": round(blended_cu, 4),
            "nacn_kg_t": round(blended_nacn, 3),
            "cao_kg_t": round(blended_cao, 3),
            "annual_oz": round(annual_oz, 0),
            "annual_margin_usd": round(annual_margin, 0),
        },
        "production_index": round(total_production_factor, 4),
        "binding_constraints": binding,
        "shadow_prices": shadow_prices or {},
    }


def _compute_shadow_prices(
    domains: list[dict],
    vecs: dict,
    constraints: dict,
    base_ratios: np.ndarray,
) -> dict:
    """Finite-difference shadow prices for active constraints."""
    eps = 0.05
    shadows: dict[str, dict] = {}
    base_result = _build_blend_result(domains, vecs, base_ratios, constraints, "sensitivity", "production")
    base_oz = base_result["blended_kpis"].get("annual_oz", 0)

    relaxations = {
        "bwi_max_kwh_t": constraints.get("bwi_max_kwh_t", DEFAULT_BWI_MAX) + eps,
        "s_max_pct": constraints.get("s_max_pct", DEFAULT_S_MAX_PCT) + eps * 0.1,
        "min_recovery_pct": constraints.get("min_recovery_pct", DEFAULT_MIN_RECOVERY_PCT) - eps,
    }

    for key, relaxed_val in relaxations.items():
        relaxed = {**constraints, key: relaxed_val, "_skip_shadows": True}
        retry = optimize_blend("", {"domains": domains}, relaxed)
        if retry.get("status") != "optimal":
            continue
        new_oz = retry["blended_kpis"].get("annual_oz", 0)
        delta_oz = new_oz - base_oz
        if abs(delta_oz) > 0.1:
            shadows[key] = {
                "delta_annual_oz": round(delta_oz, 0),
                "delta_pct": round(delta_oz / max(base_oz, 1) * 100, 2),
                "interpretation": f"Relaxing {key} by {eps} → +{round(delta_oz, 0)} oz/yr",
            }

    return shadows


def optimize_blend(
    pid: str,
    domain_result: dict,
    constraints: Optional[dict] = None,
) -> dict:
    """Optimize ore blend under metallurgical constraints (LP primary, NLP fallback)."""
    domains = domain_result.get("domains", [])
    if len(domains) < 2:
        return {
            "status": "insufficient_domains",
            "engine": "GMIE-IMBO-v1",
            "message": "Need at least 2 domains for blend optimization",
            "optimal_blend": None,
        }

    if constraints is None:
        constraints = _get_project_constraints(pid)
    else:
        base = _get_project_constraints(pid)
        base.update(constraints)
        constraints = base

    objective_mode = constraints.get("objective_mode", "production")
    if objective_mode not in OBJECTIVE_MODES:
        objective_mode = "production"

    bwi_max = float(constraints.get("bwi_max_kwh_t", DEFAULT_BWI_MAX))
    cu_max = float(constraints.get("cu_max_pct", DEFAULT_CU_MAX_PPM / 10000))
    s_max = float(constraints.get("s_max_pct", DEFAULT_S_MAX_PCT))
    min_rec = float(constraints.get("min_recovery_pct", DEFAULT_MIN_RECOVERY_PCT)) / 100
    gold_price = float(constraints.get("gold_price_usd_oz", DEFAULT_GOLD_PRICE))
    throughput = float(constraints.get("throughput_tpa", 3_000_000))

    vecs = _extract_domain_vectors(domains)
    n = len(domains)

    if objective_mode == "production":
        c = -(vecs["tonnages"] * vecs["grades"] * vecs["recoveries"])
    elif objective_mode == "margin":
        margin_per_ratio = throughput * vecs["grades"] * vecs[
            "recoveries"
        ] * TROY_OZ_PER_GRAM * gold_price - throughput * (vecs["nacn"] * 2.5 + vecs["cao"] * 0.15 + vecs["bwi"] * 0.08)
        c = -margin_per_ratio
    else:
        c = -(vecs["tonnages"] * vecs["grades"] * vecs["recoveries"])

    A_ub = [
        vecs["bwi"].tolist(),
        vecs["sulphur"].tolist(),
        vecs["copper"].tolist(),
        (-vecs["recoveries"]).tolist(),
    ]
    b_ub = [bwi_max, s_max, cu_max, -min_rec]
    A_eq = [np.ones(n).tolist()]
    b_eq = [1.0]
    bounds = [(0.0, 1.0) for _ in range(n)]

    try:
        result = linprog(
            c=c,
            A_ub=A_ub,
            b_ub=b_ub,
            A_eq=A_eq,
            b_eq=b_eq,
            bounds=bounds,
            method="highs",
        )
    except Exception as e:
        logger.warning("LP solver failed: %s", e)
        result = None

    if result is not None and result.success:
        ratios = result.x
        shadows = {}
        if not constraints.get("_skip_shadows"):
            shadows = _compute_shadow_prices(domains, vecs, constraints, ratios)
        out = _build_blend_result(domains, vecs, ratios, constraints, "linprog_highs", objective_mode, shadows)
        out["solver_iterations"] = result.nit if hasattr(result, "nit") else None
        return out

    def objective(x: np.ndarray) -> float:
        grade = float(np.dot(x, vecs["grades"]))
        rec = float(np.dot(x, vecs["recoveries"]))
        if objective_mode == "margin":
            nacn = float(np.dot(x, vecs["nacn"]))
            cao = float(np.dot(x, vecs["cao"]))
            bwi = float(np.dot(x, vecs["bwi"]))
            oz = throughput * grade * rec * TROY_OZ_PER_GRAM
            opex = throughput * (nacn * 2.5 + cao * 0.15 + bwi * 0.08)
            return -(oz * gold_price - opex)
        return -(throughput * grade * rec * TROY_OZ_PER_GRAM)

    cons = [
        {"type": "eq", "fun": lambda x: np.sum(x) - 1.0},
        {"type": "ineq", "fun": lambda x: bwi_max - np.dot(x, vecs["bwi"])},
        {"type": "ineq", "fun": lambda x: s_max - np.dot(x, vecs["sulphur"])},
        {"type": "ineq", "fun": lambda x: cu_max - np.dot(x, vecs["copper"])},
        {"type": "ineq", "fun": lambda x: np.dot(x, vecs["recoveries"]) - min_rec},
    ]
    x0 = np.ones(n) / n

    try:
        nlp = minimize(objective, x0, method="SLSQP", bounds=bounds, constraints=cons)
    except Exception as e:
        logger.warning("NLP solver failed: %s", e)
        return {"status": "solver_error", "engine": "GMIE-IMBO-v1", "message": str(e), "optimal_blend": None}

    if not nlp.success:
        return {
            "status": "infeasible",
            "engine": "GMIE-IMBO-v1",
            "message": f"No feasible blend found: {nlp.message}",
            "constraints_used": constraints,
            "optimal_blend": None,
        }

    ratios = np.clip(nlp.x, 0, 1)
    ratios = ratios / max(ratios.sum(), 1e-9)
    shadows = {}
    if not constraints.get("_skip_shadows"):
        shadows = _compute_shadow_prices(domains, vecs, constraints, ratios)
    return _build_blend_result(domains, vecs, ratios, constraints, "SLSQP", objective_mode, shadows)


def generate_blend_schedule(
    pid: str,
    domain_result: dict,
    constraints: Optional[dict] = None,
) -> list[dict]:
    try:
        from .recovery_forecast import forecast_lom
    except ImportError:
        from engines.recovery_forecast import forecast_lom

    lom = forecast_lom(pid, domain_result)
    if lom["status"] != "ok" or not lom["forecast"]:
        return []

    if constraints is None:
        constraints = _get_project_constraints(pid)

    schedule = []
    for period in lom["forecast"]:
        if period["tonnage_mt"] == 0:
            continue

        blend_result = optimize_blend(pid, domain_result, constraints)
        period_entry = {
            "year": period["year"],
            "tonnage_mt": period["tonnage_mt"],
            "unblended_recovery_pct": period["recovery_pct"],
            "domain_mix": period["domain_mix"],
        }

        if blend_result["status"] == "optimal":
            period_entry["optimized_blend"] = blend_result["optimal_blend"]
            period_entry["optimized_recovery_pct"] = blend_result["blended_kpis"]["recovery_pct"]
            period_entry["recovery_uplift_pct"] = round(
                blend_result["blended_kpis"]["recovery_pct"] - period["recovery_pct"], 2
            )
            period_entry["blended_kpis"] = blend_result["blended_kpis"]
            period_entry["binding_constraints"] = blend_result.get("binding_constraints", [])
        else:
            period_entry["optimized_blend"] = None
            period_entry["optimized_recovery_pct"] = period["recovery_pct"]
            period_entry["recovery_uplift_pct"] = 0
            period_entry["solver_message"] = blend_result.get("message", "")

        schedule.append(period_entry)

    return schedule


def evaluate_blend_impact(
    pid: str,
    domain_result: dict,
    constraints: Optional[dict] = None,
) -> dict:
    try:
        from .recovery_forecast import forecast_lom
    except ImportError:
        from engines.recovery_forecast import forecast_lom

    lom = forecast_lom(pid, domain_result)
    if lom["status"] != "ok":
        return {"status": "no_forecast", "message": "Cannot generate LOM forecast"}

    schedule = generate_blend_schedule(pid, domain_result, constraints)
    cfg = _get_project_constraints(pid)
    gold_price = cfg["gold_price_usd_oz"]
    discount = cfg["discount_rate"]

    baseline_oz = sum(p["annual_oz"] for p in lom["forecast"])
    optimized_oz = 0.0
    npv_baseline = 0.0
    npv_optimized = 0.0

    for period_sched in schedule:
        yr = period_sched["year"]
        lom_period = next((p for p in lom["forecast"] if p["year"] == yr), None)
        if not lom_period:
            continue
        tonnage_t = period_sched["tonnage_mt"] * 1_000_000
        grade = lom_period["grade_avg_g_t"]
        opt_rec = period_sched["optimized_recovery_pct"] / 100
        oz = tonnage_t * grade * opt_rec * TROY_OZ_PER_GRAM
        optimized_oz += oz
        df = (1 + discount) ** yr
        npv_baseline += lom_period["annual_oz"] * gold_price / df
        npv_optimized += oz * gold_price / df

    uplift_oz = optimized_oz - baseline_oz
    uplift_pct = (uplift_oz / baseline_oz * 100) if baseline_oz > 0 else 0

    return {
        "status": "ok",
        "engine": "GMIE-IMBO-v1",
        "baseline": {
            "total_oz": round(baseline_oz, 0),
            "total_revenue_musd": round(baseline_oz * gold_price / 1_000_000, 2),
            "npv_musd": round(npv_baseline / 1_000_000, 2),
        },
        "optimized": {
            "total_oz": round(optimized_oz, 0),
            "total_revenue_musd": round(optimized_oz * gold_price / 1_000_000, 2),
            "npv_musd": round(npv_optimized / 1_000_000, 2),
        },
        "uplift": {
            "additional_oz": round(uplift_oz, 0),
            "additional_revenue_musd": round(uplift_oz * gold_price / 1_000_000, 2),
            "uplift_pct": round(uplift_pct, 2),
            "npv_incremental_musd": round((npv_optimized - npv_baseline) / 1_000_000, 2),
        },
        "gold_price_used_usd_oz": gold_price,
        "discount_rate": discount,
    }
