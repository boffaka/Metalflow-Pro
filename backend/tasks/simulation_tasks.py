"""
Celery tasks for heavy simulation workloads:
  - run_rigorous_simulation: Bond + CIL full engine run
  - run_sensitivity_analysis: tornado data generation
"""
from __future__ import annotations
import uuid, json, time, logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _get_celery():
    try:
        from celery_app import celery_app
    except ImportError:
        from backend.celery_app import celery_app
    return celery_app


def _get_db():
    try:
        from db import conn, release
    except ImportError:
        from backend.db import conn, release
    return conn, release


def _normalize_rigorous_params(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Mappe les alias API / grilles d'optimisation vers les clés moteur canoniques."""
    p = dict(raw)
    if "p80_um" not in p and "p80" in p:
        p["p80_um"] = float(p["p80"])
    if "srt_h" not in p and "srt" in p:
        p["srt_h"] = float(p["srt"])
    if "nacn_mg_l" not in p and "cn" in p:
        p["nacn_mg_l"] = float(p["cn"])
    if "do_mg_l" not in p and "do" in p:
        p["do_mg_l"] = float(p["do"])
    return p


def _run_rigorous_engine(params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute comminution + leaching engines and return combined results."""
    try:
        try:
            from engines.comminution import bond_ball_mill_energy, sag_mill_power
            from engines.leaching import cil_recovery, annual_gold_oz, effective_k_cil
        except ImportError:
            from backend.engines.comminution import bond_ball_mill_energy, sag_mill_power
            from backend.engines.leaching import cil_recovery, annual_gold_oz, effective_k_cil

        params = _normalize_rigorous_params(params)

        e_bm = bond_ball_mill_energy(
            wi=params.get("wi", 14.0),
            p80_um=params.get("p80_um", 75.0),
            f80_um=params.get("f80_um", 3000.0),
        )
        tph = params.get("tph", 500.0)
        e_sag = sag_mill_power(
            spi_kwh_t=params.get("spi_kwh_t", 10.0),
            tph=tph,
        ) / tph  # convert kW → kWh/t
        energy_kwh_t = e_bm + e_sag

        k_base = float(params.get("k_cil", 0.35))
        if "nacn_mg_l" in params or "do_mg_l" in params:
            k_leach = effective_k_cil(
                k_base,
                nacn_mg_l=params.get("nacn_mg_l"),
                do_mg_l=params.get("do_mg_l"),
                nacn_ref_mg_l=float(params.get("nacn_ref_mg_l", 325.0)),
                do_ref_mg_l=float(params.get("do_ref_mg_l", 7.5)),
                nacn_exp=float(params.get("nacn_k_exp", 0.22)),
                do_exp=float(params.get("do_k_exp", 0.12)),
            )
        else:
            k_leach = k_base

        recovery = cil_recovery(
            r_inf=params.get("r_inf", 0.90),
            k=k_leach,
            srt_h=params.get("srt_h", 24.0),
        )

        oz = annual_gold_oz(
            tph=tph,
            op_hours_day=params.get("op_hours_day", 24.0),
            avail_pct=params.get("avail_pct", 92.0),
            grade_g_t=params.get("grade_g_t", 1.5),
            recovery=recovery,
        )

        out = {
            "recovery_pct": round(recovery * 100.0, 2),
            "annual_oz": round(oz, 0),
            "energy_kwh_t": round(energy_kwh_t, 3),
            "e_bm_kwh_t": round(e_bm, 3),
            "e_sag_kwh_t": round(e_sag, 3),
        }
        if k_leach != k_base or "nacn_mg_l" in params or "do_mg_l" in params:
            out["k_cil_effective"] = round(k_leach, 5)
        return out
    except Exception as e:
        logger.error("Rigorous engine computation failed with params=%s: %s", list(params.keys()), e)
        raise RuntimeError(f"Simulation engine error: {e}") from e


def _run_sensitivity_inline(
    base_params: Dict[str, Any],
    params_to_vary: List[str],
    delta_pcts: List[float],
) -> List[Dict[str, Any]]:
    """Compute tornado sensitivity rows without DB access (used by sync route path)."""
    try:
        base = _run_rigorous_engine(base_params)
        rows = []
        for param in params_to_vary:
            base_val = base_params.get(param, 1.0)
            for delta in delta_pcts:
                for sign in [+1, -1]:
                    test_params = dict(base_params)
                    test_params[param] = base_val * (1.0 + sign * delta / 100.0)
                    result = _run_rigorous_engine(test_params)
                    rows.append({
                        "param_key": param,
                        "delta_pct": sign * delta,
                        "impact_recovery": round(result["recovery_pct"] - base["recovery_pct"], 4),
                        "impact_opex": 0.0,
                        "impact_energy": round(result["energy_kwh_t"] - base["energy_kwh_t"], 4),
                    })
        rows.sort(key=lambda x: abs(x["impact_recovery"]), reverse=True)
        for rank, row in enumerate(rows):
            row["rank"] = rank + 1
        return rows
    except Exception as e:
        logger.error("Sensitivity analysis failed for params_to_vary=%s: %s", params_to_vary, e)
        raise RuntimeError(f"Sensitivity analysis computation error: {e}") from e


celery_app = _get_celery()


@celery_app.task(bind=True, name="tasks.simulation_tasks.run_rigorous_simulation")
def run_rigorous_simulation(self, project_id: str, run_id: str, params: dict):
    """Celery task: run the full rigorous simulation engine."""
    conn, release = _get_db()
    db = conn()
    try:
        start = time.time()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='running', celery_task_id=%s WHERE id=%s",
                (self.request.id, run_id)
            )
        db.commit()

        results = _run_rigorous_engine(params)

        duration = time.time() - start
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='done', results=%s, duration_s=%s WHERE id=%s",
                (json.dumps(results), duration, run_id)
            )
        db.commit()

        try:
            import asyncio
            from ws_manager import ws_manager
            asyncio.run(ws_manager.broadcast(project_id, {
                "type": "simulation_done",
                "task_id": run_id,
                "results_url": f"/api/v1/projects/{project_id}/simulation/runs/{run_id}",
            }))
        except Exception:
            pass  # WS broadcast is best-effort

        return results
    except Exception as e:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='failed', results=%s WHERE id=%s",
                (json.dumps({"error": str(e)}), run_id)
            )
        db.commit()
        raise
    finally:
        release(db)


@celery_app.task(bind=True, name="tasks.simulation_tasks.run_nsga2_optimization")
def run_nsga2_optimization(
    self, project_id: str, run_id: str, template_id: str,
    population_size: int, n_generations: int, objectives, constraints
):
    """Celery task: NSGA-II multi-objective optimisation (can take minutes for large pop/gen)."""
    conn, release = _get_db()
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs_v2 SET status='running', celery_task_id=%s WHERE id=%s",
                (self.request.id, run_id),
            )
        db.commit()

        try:
            from engines.nsga2_optimizer import nsga2_optimize
        except ImportError:
            from backend.engines.nsga2_optimizer import nsga2_optimize

        import psycopg2.extras as _pge
        cur2 = db.cursor(cursor_factory=_pge.RealDictCursor)
        result = nsga2_optimize(
            project_id, template_id, cur2,
            population_size=population_size,
            n_generations=n_generations,
            objectives=objectives,
            constraints=constraints,
        )
        cur2.close()

        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs_v2 SET status='done', results=%s WHERE id=%s",
                (json.dumps(result), run_id),
            )
        db.commit()

        try:
            import asyncio
            from ws_manager import ws_manager
            asyncio.run(ws_manager.broadcast(project_id, {
                "type": "optimization_done",
                "run_id": run_id,
                "results_url": f"/api/v1/projects/{project_id}/simulation-v2/optimize/{run_id}",
            }))
        except Exception:
            pass

        return result
    except Exception as e:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs_v2 SET status='failed', results=%s WHERE id=%s",
                (json.dumps({"error": str(e)}), run_id),
            )
        db.commit()
        raise
    finally:
        release(db)


@celery_app.task(bind=True, name="tasks.simulation_tasks.run_monte_carlo_lom")
def run_monte_carlo_lom(
    self, project_id: str, run_id: str, template_id: str, schedule: list, n_sims: int
):
    """Celery task: Monte Carlo Life-of-Mine simulation (up to 10 000+ iterations)."""
    conn, release = _get_db()
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs_v2 SET status='running', celery_task_id=%s WHERE id=%s",
                (self.request.id, run_id),
            )
        db.commit()

        try:
            from engines.mine_to_mill import monte_carlo_lom
        except ImportError:
            from backend.engines.mine_to_mill import monte_carlo_lom

        import psycopg2.extras as _pge
        cur2 = db.cursor(cursor_factory=_pge.RealDictCursor)
        result = monte_carlo_lom(project_id, template_id, schedule, cur2, n_sims=n_sims)
        cur2.close()

        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs_v2 SET status='done', results=%s WHERE id=%s",
                (json.dumps(result), run_id),
            )
        db.commit()

        try:
            import asyncio
            from ws_manager import ws_manager
            asyncio.run(ws_manager.broadcast(project_id, {
                "type": "monte_carlo_done",
                "run_id": run_id,
                "results_url": f"/api/v1/projects/{project_id}/mine-to-mill/monte-carlo/{run_id}",
            }))
        except Exception:
            pass

        return result
    except Exception as e:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs_v2 SET status='failed', results=%s WHERE id=%s",
                (json.dumps({"error": str(e)}), run_id),
            )
        db.commit()
        raise
    finally:
        release(db)


@celery_app.task(bind=True, name="tasks.simulation_tasks.run_sensitivity_analysis")
def run_sensitivity_analysis(
    self, project_id: str, run_id: str, base_params: dict,
    params_to_vary: List[str], delta_pcts: List[float]
):
    """Tornado sensitivity analysis: vary each parameter ±delta_pct, measure impact."""
    conn, release = _get_db()
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='running', celery_task_id=%s WHERE id=%s",
                (self.request.id, run_id)
            )
        db.commit()

        base = _run_rigorous_engine(base_params)
        rows = []
        for param in params_to_vary:
            base_val = base_params.get(param, 1.0)
            for delta in delta_pcts:
                for sign in [+1, -1]:
                    test_params = dict(base_params)
                    test_params[param] = base_val * (1.0 + sign * delta / 100.0)
                    result = _run_rigorous_engine(test_params)
                    rows.append({
                        "param_key": param,
                        "delta_pct": sign * delta,
                        "impact_recovery": round(result["recovery_pct"] - base["recovery_pct"], 4),
                        "impact_opex": 0.0,
                        "impact_energy": round(result["energy_kwh_t"] - base["energy_kwh_t"], 4),
                    })

        rows.sort(key=lambda x: abs(x["impact_recovery"]), reverse=True)
        for rank, row in enumerate(rows):
            row["rank"] = rank + 1

        results_payload = json.dumps(rows)
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='done', results=%s WHERE id=%s",
                (results_payload, run_id)
            )
            for row in rows:
                cur.execute(
                    """INSERT INTO sensitivity_analyses
                       (id, run_id, param_key, delta_pct, impact_recovery, impact_opex,
                        impact_energy, rank)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (str(uuid.uuid4()), run_id, row["param_key"], row["delta_pct"],
                     row["impact_recovery"], row["impact_opex"], row["impact_energy"], row["rank"])
                )
        db.commit()
        return {"results": rows}
    except Exception as e:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE simulation_runs SET status='failed', results=%s WHERE id=%s",
                (json.dumps({"error": str(e)}), run_id)
            )
        db.commit()
        raise
    finally:
        release(db)
