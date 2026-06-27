# backend/engines/nsga2_optimizer.py
"""
NSGA-II Multi-Objective Optimizer for MetalFlow Pro.

Implements a simplified NSGA-II (Non-dominated Sorting Genetic Algorithm II)
for simultaneous optimization of conflicting metallurgical objectives:
  f1: Maximize NPV ($M)
  f2: Minimize CAPEX ($M)
  f3: Minimize CO2 per ounce (kgCO2/oz)

Decision variables:
  x[0] = P80 grinding target (53-150 um)
  x[1] = CIL/CIP residence time (16-48 h)
  x[2] = Flotation mass pull (3-12 %)
  x[3] = NaCN consumption (0.3-2.0 kg/t)
  x[4] = Carbon concentration CIP (10-50 g/L)
  x[5] = Number of CIP/CIL tanks (4-12)

Constraints:
  - Recovery > 85%
  - AISC < 1500 $/oz
  - WAD CN < 0.5 mg/L (regulatory)

Reference: Deb et al. (2002) "A Fast and Elitist Multiobjective Genetic Algorithm: NSGA-II"
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import numpy as np

try:
    from .. import config as cfg
except ImportError:
    import config as cfg

logger = logging.getLogger("mpdpms.nsga2_optimizer")

# ============================================================================
# Decision variable bounds
# ============================================================================

VARIABLE_NAMES = [
    "p80_um",
    "srt_h",
    "mass_pull_pct",
    "nacn_kg_t",
    "carbon_conc_g_l",
    "n_tanks",
]

VARIABLE_BOUNDS = np.array([
    [53.0,  150.0],   # P80 grinding target (um)
    [16.0,   48.0],   # CIL/CIP residence time (h)
    [ 3.0,   12.0],   # Flotation mass pull (%)
    [ 0.3,    2.0],   # NaCN consumption (kg/t)
    [10.0,   50.0],   # Carbon concentration (g/L)
    [ 4.0,   12.0],   # Number of CIP/CIL tanks
])

N_VARIABLES = len(VARIABLE_NAMES)


def resolve_nsga2_bounds(job_variables: list[dict] | None) -> np.ndarray:
    """Return a copy of decision-variable bounds, optionally narrowed by job `variables`.

    Each job entry may contain ``param``, ``min``, ``max`` (same shape as sweep jobs).
    Bounds are clamped to the global ``VARIABLE_BOUNDS`` envelope for safety.
    """
    out = VARIABLE_BOUNDS.astype(float).copy()
    if not job_variables:
        return out
    idx_by_name = {n: i for i, n in enumerate(VARIABLE_NAMES)}
    for v in job_variables:
        if not isinstance(v, dict):
            continue
        p = v.get("param")
        if p not in idx_by_name:
            continue
        try:
            lo = float(v["min"])
            hi = float(v["max"])
        except (KeyError, TypeError, ValueError):
            continue
        idx = idx_by_name[p]
        glo, ghi = float(VARIABLE_BOUNDS[idx, 0]), float(VARIABLE_BOUNDS[idx, 1])
        lo = max(glo, min(lo, ghi))
        hi = min(ghi, max(hi, glo))
        if hi <= lo:
            lo, hi = glo, ghi
        out[idx, 0] = lo
        out[idx, 1] = hi
    return out


# Default constraint thresholds (overridable via optimization_constraints dict)
DEFAULT_DEFAULT_MIN_RECOVERY = 85.0       # %
DEFAULT_DEFAULT_MAX_AISC = 1500.0          # $/oz
DEFAULT_DEFAULT_MAX_WAD_CN = 0.5           # mg/L (IFC/WHO guideline)

# Module-level aliases used throughout the optimizer functions
DEFAULT_MIN_RECOVERY = DEFAULT_DEFAULT_MIN_RECOVERY
DEFAULT_MAX_AISC = DEFAULT_DEFAULT_MAX_AISC
DEFAULT_MAX_WAD_CN = DEFAULT_DEFAULT_MAX_WAD_CN


# ============================================================================
# Simplified fitness evaluation
# ============================================================================

def _evaluate_individual(x: np.ndarray, project_id: str,
                         template_id: str, cursor,
                         econ: dict, dc_params: dict) -> dict:
    """
    Evaluate a single individual using simulate_circuit with param overrides.

    Returns dict with objectives, constraint violations, and raw metrics.
    """
    try:
        from engines.process_simulator import simulate_circuit
    except ImportError:
        from .process_simulator import simulate_circuit

    # Build parameter override from decision variables
    params_override = {
        "p80_um": float(x[0]),
        "srt_h": float(x[1]),
        "mass_pull_pct": float(x[2]),
        "nacn_kg_t": float(x[3]),
        "carbon_conc_g_l": float(x[4]),
        "n_tanks": int(round(x[5])),
    }

    try:
        result = simulate_circuit(project_id, template_id,
                                  params_override=params_override, cursor=cursor)
        ov = result.get("overall", {})
    except Exception as exc:
        logger.debug("Evaluation failed for individual: %s", exc)
        return {
            "objectives": [0.0, 1e9, 1e9],  # terrible fitness
            "feasible": False,
            "violation": 1e6,
            "metrics": {},
        }

    recovery = ov.get("total_recovery_pct", 0.0)
    npv_musd = ov.get("npv_musd", 0.0)
    capex_musd = ov.get("capex_musd", 0.0) or (econ.get("capex_musd") or 150.0)
    co2_per_oz = ov.get("co2_per_oz", 0.0)
    annual_oz = ov.get("annual_gold_oz", 0.0)

    # Rough AISC estimate
    opex_per_t = ov.get("opex_usd_t", 0.0)
    annual_tonnes = ov.get("annual_tonnes", 0.0)
    opex_annual = opex_per_t * annual_tonnes if opex_per_t and annual_tonnes else 0
    sustaining = capex_musd * 1e6 * 0.03
    gold_price = econ.get("gold_price_usd_oz") or cfg.DEFAULT_GOLD_PRICE_USD_OZ
    royalty = annual_oz * gold_price * 0.05 if annual_oz > 0 else 0
    aisc = (opex_annual + sustaining + royalty) / annual_oz if annual_oz > 0 else 9999.0

    # WAD CN estimate: higher NaCN -> higher residual, detox brings it down
    wad_cn_est = float(x[3]) * 0.15  # rough: 15% of NaCN remains as WAD CN

    # Constraint violations (sum of violations for constraint-dominated sorting)
    violation = 0.0
    if recovery < DEFAULT_MIN_RECOVERY:
        violation += (DEFAULT_MIN_RECOVERY - recovery) / DEFAULT_MIN_RECOVERY
    if aisc > DEFAULT_MAX_AISC:
        violation += (aisc - DEFAULT_MAX_AISC) / DEFAULT_MAX_AISC
    if wad_cn_est > DEFAULT_MAX_WAD_CN:
        violation += (wad_cn_est - DEFAULT_MAX_WAD_CN) / DEFAULT_MAX_WAD_CN

    # CAPEX adjustment: more tanks / finer grind -> higher CAPEX
    base_capex = capex_musd
    tank_factor = 1.0 + 0.02 * (float(x[5]) - 6)  # 2% per extra tank
    grind_factor = 1.0 + 0.10 * max(0, (75.0 - float(x[0])) / 75.0)  # finer grind costs more
    adjusted_capex = base_capex * tank_factor * grind_factor

    # Objectives: [maximize NPV (negate), minimize CAPEX, minimize CO2/oz]
    # All stored as minimization (negate NPV)
    objectives = [
        -npv_musd,         # f1: minimize -NPV (i.e., maximize NPV)
        adjusted_capex,    # f2: minimize CAPEX ($M)
        co2_per_oz,        # f3: minimize CO2 per oz
    ]

    return {
        "objectives": objectives,
        "feasible": violation == 0.0,
        "violation": violation,
        "metrics": {
            "npv_musd": npv_musd,
            "capex_musd": round(adjusted_capex, 2),
            "co2_per_oz": round(co2_per_oz, 1),
            "recovery_pct": round(recovery, 2),
            "aisc_usd_oz": round(aisc, 2),
            "wad_cn_mg_l": round(wad_cn_est, 3),
            "annual_gold_oz": round(annual_oz, 0),
        },
    }


# ============================================================================
# NSGA-II operators
# ============================================================================

def _fast_non_dominated_sort(population: list[dict]) -> list[list[int]]:
    """
    Fast non-dominated sorting (Deb 2002).

    Constraint-domination: feasible solutions dominate infeasible;
    among infeasible, lower violation dominates.

    Returns list of fronts, each front is a list of indices.
    """
    try:
        n = len(population)
        domination_count = [0] * n
        dominated_set: list[list[int]] = [[] for _ in range(n)]
        fronts: list[list[int]] = [[]]

        for p in range(n):
            for q in range(n):
                if p == q:
                    continue
                if _dominates(population[p], population[q]):
                    dominated_set[p].append(q)
                elif _dominates(population[q], population[p]):
                    domination_count[p] += 1

            if domination_count[p] == 0:
                population[p]["rank"] = 0
                fronts[0].append(p)

        i = 0
        while fronts[i]:
            next_front = []
            for p in fronts[i]:
                for q in dominated_set[p]:
                    domination_count[q] -= 1
                    if domination_count[q] == 0:
                        population[q]["rank"] = i + 1
                        next_front.append(q)
            i += 1
            fronts.append(next_front)

        # Remove empty last front
        if not fronts[-1]:
            fronts.pop()

        return fronts
    except Exception as e:
        logger.error("_fast_non_dominated_sort failed for population_size=%d: %s", len(population), e)
        raise RuntimeError(f"_fast_non_dominated_sort failed: {e}") from e


def _dominates(p: dict, q: dict) -> bool:
    """Check if p dominates q (constraint-domination)."""
    p_feas = p.get("feasible", False)
    q_feas = q.get("feasible", False)

    # Both feasible: standard Pareto dominance
    if p_feas and q_feas:
        p_obj = p["objectives"]
        q_obj = q["objectives"]
        at_least_one_better = False
        for a, b in zip(p_obj, q_obj):
            if a > b:
                return False
            if a < b:
                at_least_one_better = True
        return at_least_one_better

    # p feasible, q not: p dominates
    if p_feas and not q_feas:
        return True

    # p not feasible, q feasible: p does not dominate
    if not p_feas and q_feas:
        return False

    # Both infeasible: lower violation dominates
    return p.get("violation", 0) < q.get("violation", 0)


def _crowding_distance(population: list[dict], front: list[int]) -> None:
    """Assign crowding distance to individuals in a front."""
    n = len(front)
    if n <= 2:
        for idx in front:
            population[idx]["crowding_distance"] = float("inf")
        return

    for idx in front:
        population[idx]["crowding_distance"] = 0.0

    n_obj = len(population[front[0]]["objectives"])

    for m in range(n_obj):
        sorted_front = sorted(front, key=lambda i: population[i]["objectives"][m])
        population[sorted_front[0]]["crowding_distance"] = float("inf")
        population[sorted_front[-1]]["crowding_distance"] = float("inf")

        obj_range = (population[sorted_front[-1]]["objectives"][m] -
                     population[sorted_front[0]]["objectives"][m])
        if obj_range == 0:
            continue

        for k in range(1, n - 1):
            idx = sorted_front[k]
            prev_obj = population[sorted_front[k - 1]]["objectives"][m]
            next_obj = population[sorted_front[k + 1]]["objectives"][m]
            population[idx]["crowding_distance"] += (next_obj - prev_obj) / obj_range


def _tournament_selection(population: list[dict], rng: np.random.Generator) -> int:
    """Binary tournament selection based on rank and crowding distance."""
    n = len(population)
    i, j = int(rng.integers(n)), int(rng.integers(n))
    while j == i and n > 1:
        j = int(rng.integers(n))

    ri = population[i].get("rank", 999)
    rj = population[j].get("rank", 999)

    if ri < rj:
        return i
    elif rj < ri:
        return j
    else:
        # Same rank: prefer higher crowding distance
        ci = population[i].get("crowding_distance", 0)
        cj = population[j].get("crowding_distance", 0)
        return i if ci >= cj else j


def _sbx_crossover(p1: np.ndarray, p2: np.ndarray,
                   rng: np.random.Generator, bounds: np.ndarray,
                   eta: float = 20.0) -> tuple:
    """Simulated Binary Crossover (SBX) with distribution index eta."""
    c1 = p1.copy()
    c2 = p2.copy()

    for i in range(len(p1)):
        if rng.random() > 0.5:
            continue
        if abs(p1[i] - p2[i]) < 1e-14:
            continue

        lo = bounds[i, 0]
        hi = bounds[i, 1]

        if p1[i] < p2[i]:
            y1, y2 = p1[i], p2[i]
        else:
            y1, y2 = p2[i], p1[i]

        u = rng.random()

        # Beta calculation
        beta1 = 1.0 + 2.0 * (y1 - lo) / (y2 - y1 + 1e-14)
        alpha1 = 2.0 - beta1 ** (-(eta + 1.0))
        if u <= 1.0 / alpha1:
            betaq1 = (u * alpha1) ** (1.0 / (eta + 1.0))
        else:
            betaq1 = (1.0 / (2.0 - u * alpha1)) ** (1.0 / (eta + 1.0))

        beta2 = 1.0 + 2.0 * (hi - y2) / (y2 - y1 + 1e-14)
        alpha2 = 2.0 - beta2 ** (-(eta + 1.0))
        if u <= 1.0 / alpha2:
            betaq2 = (u * alpha2) ** (1.0 / (eta + 1.0))
        else:
            betaq2 = (1.0 / (2.0 - u * alpha2)) ** (1.0 / (eta + 1.0))

        c1[i] = np.clip(0.5 * ((y1 + y2) - betaq1 * (y2 - y1)), lo, hi)
        c2[i] = np.clip(0.5 * ((y1 + y2) + betaq2 * (y2 - y1)), lo, hi)

    return c1, c2


def _polynomial_mutation(x: np.ndarray, rng: np.random.Generator, bounds: np.ndarray,
                         prob: float = None, eta: float = 20.0) -> np.ndarray:
    """Polynomial mutation with distribution index eta."""
    if prob is None:
        prob = 1.0 / N_VARIABLES

    y = x.copy()
    for i in range(len(x)):
        if rng.random() > prob:
            continue

        lo = bounds[i, 0]
        hi = bounds[i, 1]
        delta = hi - lo
        if delta < 1e-14:
            continue

        u = rng.random()
        if u < 0.5:
            delta_q = (2.0 * u) ** (1.0 / (eta + 1.0)) - 1.0
        else:
            delta_q = 1.0 - (2.0 * (1.0 - u)) ** (1.0 / (eta + 1.0))

        y[i] = np.clip(x[i] + delta_q * delta, lo, hi)

    # Enforce integer for n_tanks
    y[5] = round(y[5])
    return y


# ============================================================================
# Pareto front utilities
# ============================================================================

def _find_knee_point(pareto: list[dict]) -> dict:
    """
    Find the knee point of the Pareto front — the solution with the best
    balanced trade-off (maximum distance from the utopia-nadir line).
    """
    if not pareto:
        return {}
    if len(pareto) == 1:
        return pareto[0]

    # Normalize objectives to [0, 1]
    objs = np.array([p["objectives"] for p in pareto])
    mins = objs.min(axis=0)
    maxs = objs.max(axis=0)
    ranges = maxs - mins
    ranges[ranges == 0] = 1.0
    normalized = (objs - mins) / ranges

    # Knee point: minimum sum of normalized objectives
    sums = normalized.sum(axis=1)
    knee_idx = int(np.argmin(sums))
    return pareto[knee_idx]


# ============================================================================
# Main NSGA-II optimizer
# ============================================================================

def nsga2_optimize(project_id: str, template_id: str, cursor,
                   population_size: int = 50, n_generations: int = 100,
                   objectives: list[str] | None = None,
                   constraints: dict | None = None,
                   job_variables: list[dict] | None = None) -> dict:
    """
    NSGA-II multi-objective optimization.

    Steps:
      1. Initialize random population within variable bounds
      2. For each generation:
         a. Evaluate fitness (run simulate_circuit for each individual)
         b. Non-dominated sorting
         c. Crowding distance assignment
         d. Tournament selection
         e. Crossover (SBX) + Mutation (polynomial)
      3. Extract Pareto front
      4. Save results to optimization_solutions table

    Args:
        project_id: UUID of the project
        template_id: UUID of the circuit template
        cursor: database cursor (psycopg2 RealDictCursor)
        population_size: Population size (default 50)
        n_generations: Number of generations (default 100)
        objectives: Optional list of objective names (default: NPV, CAPEX, CO2)
        constraints: Optional dict overriding constraint thresholds
        job_variables: Optional list of ``{param, min, max}`` dicts (same as sweep jobs)
            to narrow the NSGA-II search box for known ``VARIABLE_NAMES``.

    Returns:
        {
            run_id, generations_run, pareto_front: [{objectives, variables}],
            best_npv: {objectives, variables},
            best_balanced: {objectives, variables}
        }
    """
    try:
        return _nsga2_optimize_impl(project_id, template_id, cursor,
                                     population_size, n_generations, objectives, constraints,
                                     job_variables)
    except Exception as e:
        logger.error("nsga2_optimize failed for project_id=%s, template_id=%s, pop=%d, gen=%d: %s",
                     project_id, template_id, population_size, n_generations, e)
        raise RuntimeError(f"nsga2_optimize failed for project {project_id}: {e}") from e


def _nsga2_optimize_impl(project_id: str, template_id: str, cursor,
                          population_size: int = 50, n_generations: int = 100,
                          objectives: list[str] | None = None,
                          constraints: dict | None = None,
                          job_variables: list[dict] | None = None) -> dict:
    """Internal implementation of nsga2_optimize."""
    t0 = time.time()
    run_id = str(uuid.uuid4())
    rng = np.random.default_rng(seed=42)
    bounds = resolve_nsga2_bounds(job_variables)

    # Load project economics for evaluation
    try:
        from engines.process_simulator import _load_project_economics, _load_dc_params
    except ImportError:
        from .process_simulator import _load_project_economics, _load_dc_params

    econ = _load_project_economics(project_id, cursor)
    dc_params = _load_dc_params(template_id, cursor)

    logger.info("NSGA-II: Starting optimization with pop=%d, gen=%d",
                population_size, n_generations)

    # ---- Step 1: Initialize population ----
    population: list[dict] = []
    for _ in range(population_size):
        x = np.array([
            rng.uniform(lo, hi) for lo, hi in bounds
        ])
        # Enforce integer for n_tanks
        x[5] = round(x[5])

        eval_result = _evaluate_individual(
            x, project_id, template_id, cursor, econ, dc_params
        )
        individual = {
            "variables": x,
            "objectives": eval_result["objectives"],
            "feasible": eval_result["feasible"],
            "violation": eval_result["violation"],
            "metrics": eval_result["metrics"],
            "rank": 0,
            "crowding_distance": 0.0,
        }
        population.append(individual)

    logger.info("NSGA-II: Initial population evaluated")

    # ---- Step 2: Generational loop ----
    for gen in range(n_generations):
        # Non-dominated sorting
        fronts = _fast_non_dominated_sort(population)
        for front in fronts:
            _crowding_distance(population, front)

        # Create offspring via selection + crossover + mutation
        offspring: list[dict] = []
        while len(offspring) < population_size:
            p1_idx = _tournament_selection(population, rng)
            p2_idx = _tournament_selection(population, rng)

            c1_x, c2_x = _sbx_crossover(
                population[p1_idx]["variables"],
                population[p2_idx]["variables"],
                rng,
                bounds,
            )
            c1_x = _polynomial_mutation(c1_x, rng, bounds)
            c2_x = _polynomial_mutation(c2_x, rng, bounds)

            for cx in [c1_x, c2_x]:
                if len(offspring) >= population_size:
                    break
                eval_result = _evaluate_individual(
                    cx, project_id, template_id, cursor, econ, dc_params
                )
                offspring.append({
                    "variables": cx,
                    "objectives": eval_result["objectives"],
                    "feasible": eval_result["feasible"],
                    "violation": eval_result["violation"],
                    "metrics": eval_result["metrics"],
                    "rank": 0,
                    "crowding_distance": 0.0,
                })

        # Merge parent + offspring
        combined = population + offspring

        # Non-dominated sorting on combined
        fronts = _fast_non_dominated_sort(combined)

        # Select next generation
        new_population: list[dict] = []
        for front in fronts:
            if len(new_population) + len(front) <= population_size:
                _crowding_distance(combined, front)
                for idx in front:
                    new_population.append(combined[idx])
            else:
                # Partial front: sort by crowding distance descending
                _crowding_distance(combined, front)
                remaining = population_size - len(new_population)
                sorted_front = sorted(
                    front,
                    key=lambda i: combined[i].get("crowding_distance", 0),
                    reverse=True,
                )
                for idx in sorted_front[:remaining]:
                    new_population.append(combined[idx])
                break

        population = new_population

        if (gen + 1) % 10 == 0 or gen == 0:
            n_feasible = sum(1 for p in population if p.get("feasible", False))
            best_npv = max((-p["objectives"][0] for p in population), default=0)
            logger.info("NSGA-II gen %d/%d: feasible=%d/%d, best NPV=%.1f $M",
                        gen + 1, n_generations, n_feasible, population_size, best_npv)

    # ---- Step 3: Extract Pareto front ----
    fronts = _fast_non_dominated_sort(population)
    pareto_indices = fronts[0] if fronts else []

    pareto_front = []
    for idx in pareto_indices:
        ind = population[idx]
        variables_dict = {
            name: float(ind["variables"][i])
            for i, name in enumerate(VARIABLE_NAMES)
        }
        # Integer for n_tanks
        variables_dict["n_tanks"] = int(round(variables_dict["n_tanks"]))

        pareto_front.append({
            "objectives": {
                "npv_musd": round(-ind["objectives"][0], 2),
                "capex_musd": round(ind["objectives"][1], 2),
                "co2_per_oz": round(ind["objectives"][2], 1),
            },
            "variables": variables_dict,
            "metrics": ind.get("metrics", {}),
            "feasible": ind.get("feasible", False),
        })

    # ---- Best NPV solution ----
    best_npv_sol = max(pareto_front, key=lambda s: s["objectives"]["npv_musd"],
                       default=None)

    # ---- Knee point (best balanced) ----
    # Rebuild with raw objectives for knee calculation
    pareto_raw = [{"objectives": population[idx]["objectives"]}
                  for idx in pareto_indices]
    knee_raw = _find_knee_point(pareto_raw)
    if knee_raw and "objectives" in knee_raw:
        knee_idx_in_pareto = None
        for pi, idx in enumerate(pareto_indices):
            if population[idx]["objectives"] == knee_raw["objectives"]:
                knee_idx_in_pareto = pi
                break
        best_balanced = pareto_front[knee_idx_in_pareto] if knee_idx_in_pareto is not None else pareto_front[0]
    else:
        best_balanced = pareto_front[0] if pareto_front else None

    # ---- Step 4: Save to database ----
    duration = time.time() - t0

    # Save run metadata to simulation_runs_v2
    cursor.execute(
        "INSERT INTO simulation_runs_v2 "
        "(id, project_id, template_id, run_type, status, params, results, duration_s) "
        "VALUES (%s, %s, %s, 'nsga2_optimization', 'completed', %s::jsonb, %s::jsonb, %s)",
        (
            run_id, project_id, template_id,
            _to_json({
                "population_size": population_size,
                "n_generations": n_generations,
                "variable_bounds": {name: [float(bounds[i, 0]), float(bounds[i, 1])]
                                    for i, name in enumerate(VARIABLE_NAMES)},
                "constraints": {
                    "min_recovery": DEFAULT_MIN_RECOVERY,
                    "max_aisc": DEFAULT_MAX_AISC,
                    "max_wad_cn": DEFAULT_MAX_WAD_CN,
                },
            }),
            _to_json({
                "n_pareto_solutions": len(pareto_front),
                "best_npv": best_npv_sol,
                "best_balanced": best_balanced,
            }),
            round(duration, 2),
        ),
    )

    # Save Pareto front solutions to optimization_solutions
    for si, sol in enumerate(pareto_front):
        cursor.execute(
            "INSERT INTO optimization_solutions "
            "(run_id, generation, solution_index, objectives, variables, is_pareto) "
            "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, true)",
            (
                run_id, n_generations, si,
                _to_json(sol["objectives"]),
                _to_json(sol["variables"]),
            ),
        )

    logger.info("NSGA-II completed: %d Pareto solutions in %.1fs",
                len(pareto_front), duration)

    return {
        "run_id": run_id,
        "generations_run": n_generations,
        "population_size": population_size,
        "pareto_front": pareto_front,
        "n_pareto_solutions": len(pareto_front),
        "best_npv": best_npv_sol,
        "best_balanced": best_balanced,
        "duration_s": round(duration, 2),
    }


# ============================================================================
# Utility
# ============================================================================

def _to_json(obj: Any) -> str:
    """Serialize to JSON string for psycopg2 JSONB."""
    import json
    return json.dumps(obj, default=str)
