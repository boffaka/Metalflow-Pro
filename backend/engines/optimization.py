# backend/engines/optimization.py
"""
NSGA-II multi-objective optimization using pymoo.

Objectives (all to minimize — negate recovery):
  f1 = -recovery_pct   (maximize recovery)
  f2 = energy_kwh_t    (minimize energy)

Decision variables:
  x[0] = p80_um   ∈ [50, 200]
  x[1] = srt_h    ∈ [12, 48]
  x[2] = nacn_mg_l ∈ [200, 500]
"""
from __future__ import annotations
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def run_nsga2(
    base_params: Dict[str, Any],
    n_pop: int = 50,
    n_gen: int = 100,
) -> Dict[str, Any]:
    """
    Run NSGA-II on the simulation engine.

    Args:
        base_params: Fixed parameters (wi, tph, grade, avail, etc.)
        n_pop: Population size (default 50)
        n_gen: Number of generations (default 100)
    Returns:
        dict with 'solutions': list of {params, objectives}
    """
    try:
        import numpy as np
        from pymoo.algorithms.moo.nsga2 import NSGA2
        from pymoo.core.problem import Problem
        from pymoo.optimize import minimize

        try:
            from engines.comminution import bond_ball_mill_energy, sag_mill_power
            from engines.leaching import cil_recovery
        except ImportError:
            from .comminution import bond_ball_mill_energy, sag_mill_power
            from .leaching import cil_recovery

        class SimulationProblem(Problem):
            def __init__(self):
                # x = [p80_um, srt_h, nacn_mg_l]
                super().__init__(
                    n_var=3, n_obj=2,
                    xl=np.array([50.0,  12.0, 200.0]),
                    xu=np.array([200.0, 48.0, 500.0]),
                )

            def _evaluate(self, X, out, *args, **kwargs):
                f1_list, f2_list = [], []
                for x in X:
                    p80, srt, nacn = x[0], x[1], x[2]
                    params = dict(base_params)
                    params["p80_um"] = p80
                    params["srt_h"] = srt
                    params["nacn_mg_l"] = nacn

                    try:
                        e_bm = bond_ball_mill_energy(
                            wi=params.get("wi", 14.0),
                            p80_um=p80,
                            f80_um=params.get("f80_um", 3000.0),
                        )
                        e_sag = sag_mill_power(
                            spi_kwh_t=params.get("spi_kwh_t", 10.0),
                            tph=params.get("tph", 500.0),
                        ) / params.get("tph", 500.0)
                        energy = e_bm + e_sag

                        rec = cil_recovery(
                            r_inf=params.get("r_inf", 0.90),
                            k=params.get("k_cil", 0.35),
                            srt_h=srt,
                        )

                        f1_list.append(-rec * 100.0)   # negate: maximize recovery
                        f2_list.append(energy)
                    except Exception:
                        f1_list.append(0.0)
                        f2_list.append(999.0)

                out["F"] = np.column_stack([f1_list, f2_list])

        problem = SimulationProblem()
        algorithm = NSGA2(pop_size=n_pop)

        res = minimize(
            problem, algorithm,
            ("n_gen", n_gen),
            verbose=False,
            seed=42,
        )

        solutions = []
        for x, f in zip(res.X, res.F):
            solutions.append({
                "params": {
                    "p80_um": float(x[0]),
                    "srt_h": float(x[1]),
                    "nacn_mg_l": float(x[2]),
                },
                "objectives": {
                    "recovery_pct": float(-f[0]),
                    "energy_kwh_t": float(f[1]),
                },
            })

        return {"solutions": solutions, "n_generations": n_gen, "n_solutions": len(solutions)}
    except Exception as e:
        logger.error("run_nsga2 failed (n_pop=%d, n_gen=%d): %s", n_pop, n_gen, e)
        raise RuntimeError(f"run_nsga2 failed for n_pop={n_pop}, n_gen={n_gen}") from e
