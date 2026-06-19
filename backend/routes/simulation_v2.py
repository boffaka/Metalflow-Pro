"""
MPDPMS — Simulation v2 & Mine-to-Mill routes.
Rigorous simulation, sensitivity, scenario comparison, NSGA-II optimization,
mine scheduling, LOM simulation, blend optimization, Monte Carlo, and ESG.
"""
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Depends, Body, Request

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release
except ImportError:
    from auth import project_user
    from db import qone, qall, execute, conn, release

try:
    try:
        from ..rate_limiter import limiter, HEAVY_COMPUTE_LIMIT
    except ImportError:
        from rate_limiter import limiter, HEAVY_COMPUTE_LIMIT
except Exception:
    class _NoopLimiter:
        def limit(self, *a, **kw):
            return lambda fn: fn
    limiter = _NoopLimiter()  # type: ignore
    HEAVY_COMPUTE_LIMIT = "10/minute"


def _import_process_simulator():
    try:
        from ..engines.process_simulator import simulate_circuit, run_sensitivity, compare_scenarios
    except ImportError:
        from engines.process_simulator import simulate_circuit, run_sensitivity, compare_scenarios
    return simulate_circuit, run_sensitivity, compare_scenarios


def _import_nsga2():
    try:
        from ..engines.nsga2_optimizer import nsga2_optimize
    except ImportError:
        from engines.nsga2_optimizer import nsga2_optimize
    return nsga2_optimize


def _import_mine_to_mill():
    try:
        from ..engines.mine_to_mill import generate_mine_schedule, simulate_lom, optimize_blend, monte_carlo_lom, esg_timeline
    except ImportError:
        from engines.mine_to_mill import generate_mine_schedule, simulate_lom, optimize_blend, monte_carlo_lom, esg_timeline
    return generate_mine_schedule, simulate_lom, optimize_blend, monte_carlo_lom, esg_timeline

import psycopg2.extras

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["simulation-v2"])
logger = logging.getLogger("mpdpms.simulation_v2")


def _coerce_uuid(value) -> str | None:
    """Normalize UUID fields for DB inserts (reject empty strings)."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _active_template(pid: str):
    """Return the active circuit template for a project, or raise 404."""
    tpl = qone(
        "SELECT id, name FROM circuit_templates WHERE project_id = %s "
        "ORDER BY is_active DESC NULLS LAST, updated_at DESC LIMIT 1",
        (pid,),
    )
    if not tpl:
        raise HTTPException(404, "No circuit template found for this project. Create one first.")
    return tpl


def _resolve_template_for_run(pid: str) -> tuple[str, str | None]:
    """Return (template_id, compilation_id) for a simulation run.

    Priority:
      1. If projects.feature_flags.sim_active_source is set → compile that source.
      2. Else compile the latest flowsheet (matches Simulation et Optimisation canvas).
      3. Else legacy active circuit_template.
      4. Else → raise 404.
    """
    try:
        from ..engines.compile import compile_flowsheet
    except ImportError:
        from engines.compile import compile_flowsheet

    # 1) active-source in feature_flags
    row = qone(
        "SELECT feature_flags -> 'sim_active_source' AS active_source "
        "FROM projects WHERE id = %s",
        (pid,),
    )
    active_source = row.get("active_source") if row else None
    if active_source:
        data = active_source if isinstance(active_source, dict) else json.loads(active_source)
        if data.get("source_type"):
            result = compile_flowsheet(
                project_id=pid,
                source_type=data["source_type"],
                source_id=data.get("source_id"),
            )
            return _coerce_uuid(result["template_id"]), _coerce_uuid(result["compilation_id"])

    # 2) compile latest flowsheet
    fs = qone(
        "SELECT id FROM flowsheets WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if fs:
        result = compile_flowsheet(project_id=pid, source_type="flowsheet", source_id=str(fs["id"]))
        return _coerce_uuid(result["template_id"]), _coerce_uuid(result["compilation_id"])

    # 3) legacy: active circuit_template
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE project_id = %s AND is_active = TRUE "
        "ORDER BY updated_at DESC LIMIT 1",
        (pid,),
    )
    if tpl:
        return str(tpl["id"]), None

    raise HTTPException(404, "No circuit_template and no flowsheet found for this project")


def _require_block_model(pid: str):
    """Ensure a block model exists for the project, or raise 404."""
    bm = qone("SELECT id FROM block_model_configs WHERE project_id = %s LIMIT 1", (pid,))
    if not bm:
        raise HTTPException(404, "No block model found for this project. Upload one first.")
    return bm


# =============================================================================
# SIMULATION — run / list / get
# =============================================================================

# =============================================================================
# GOLD PROCESS — dynamic simulator (any Au treatment route from flowsheet)
# =============================================================================

@router.get("/simulation-v2/catalog-coverage")
def get_catalog_coverage(user=Depends(project_user)):
    """Report mapping of all 60 unit_operations_catalog codes to kinetic models."""
    try:
        from ..engines.op_model_registry import catalog_coverage_report
    except ImportError:
        from engines.op_model_registry import catalog_coverage_report
    return catalog_coverage_report()


@router.get("/simulation-v2/gold-process/profile")
def get_gold_process_profile(pid: str, user=Depends(project_user)):
    """Discover route, model coverage, and levers without running simulation."""
    try:
        from ..engines.gold_process_simulator import build_gold_process_profile
    except ImportError:
        from engines.gold_process_simulator import build_gold_process_profile
    try:
        return build_gold_process_profile(pid, compile_if_needed=True)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.exception("gold-process profile failed project=%s", pid)
        raise HTTPException(500, detail=f"Profil simulation indisponible: {e}") from e


@router.get("/simulation-v2/gold-process/presets")
def get_gold_process_presets(user=Depends(project_user)):
    """Industrial gold flowsheet templates (48 routes, 8 families)."""
    try:
        from ..engines.gold_process_simulator import list_gold_presets
    except ImportError:
        from engines.gold_process_simulator import list_gold_presets
    grouped = list_gold_presets()
    return {
        "families": sorted(grouped.keys()),
        "presets": grouped,
        "count": sum(len(v) for v in grouped.values()),
    }


@router.post("/simulation-v2/gold-process/run")
def run_gold_process_simulation(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Run rigorous simulation on the resolved gold process route (flowsheet-first)."""
    params_override = body.get("params_override")
    try:
        from ..engines.gold_process_simulator import run_gold_process
    except ImportError:
        from engines.gold_process_simulator import run_gold_process

    import psycopg2.extras

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        results = run_gold_process(
            pid,
            params_override=params_override,
            compile_if_needed=body.get("compile_if_needed", True),
            cursor=cur,
        )
        profile = results.pop("gold_process_profile", {})
        template_id = _coerce_uuid(
            results.get("template_id") or profile.get("template_id"),
        )
        compilation_id = _coerce_uuid(
            results.get("compilation_id") or profile.get("compilation_id"),
        )
        if not template_id:
            raise ValueError(
                "Template de simulation introuvable après compilation — recompilez le flowsheet."
            )
        try:
            from ..engines.plant_design_advisor import assess_simulation_qa
        except ImportError:
            from engines.plant_design_advisor import assess_simulation_qa
        proj = qone("SELECT status FROM projects WHERE id=%s", (pid,)) or {}
        qa = assess_simulation_qa(pid, project_status=proj.get("status"))
        results["simulation_qa"] = {
            "score": qa.get("score"),
            "can_run_rigorous": qa.get("can_run_rigorous"),
            "warnings": qa.get("warnings", [])[:12],
            "study_level": qa.get("study_level"),
        }
        run_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO simulation_runs_v2 "
            "(id, project_id, template_id, run_type, run_mode, ops_simulated, "
            " feed_source, feed_stream, product_stream, params, results, "
            " label, created_by, compilation_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                run_id,
                pid,
                template_id,
                "rigorous",
                "gold_process",
                profile.get("route", {}).get("op_codes"),
                None,
                None,
                None,
                json.dumps(params_override) if params_override else None,
                json.dumps(results, default=str),
                body.get("label") or "Simulateur or dynamique",
                user.get("id"),
                compilation_id,
            ),
        )
        c.commit()
        return {
            "run_id": run_id,
            "template_id": template_id,
            "compilation_id": compilation_id,
            "gold_process_profile": profile,
            **results,
        }
    except ValueError as e:
        c.rollback()
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        c.rollback()
        raise
    except psycopg2.OperationalError:
        c.rollback()
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        c.rollback()
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except Exception as e:
        c.rollback()
        logger.exception("gold-process run failed project=%s", pid)
        raise HTTPException(500, detail=f"Échec simulation: {e}") from e
    finally:
        release(c)


@router.post("/simulation-v2/run")
def run_simulation(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Run rigorous simulation using the active circuit template."""
    template_id, compilation_id = _resolve_template_for_run(pid)
    params_override = body.get("params_override")
    import psycopg2.extras
    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        simulate_circuit, _, _ = _import_process_simulator()
        results = simulate_circuit(pid, template_id, params_override=params_override, cursor=cur)
        try:
            from ..engines.plant_design_advisor import assess_simulation_qa
        except ImportError:
            from engines.plant_design_advisor import assess_simulation_qa
        proj = qone("SELECT status FROM projects WHERE id=%s", (pid,)) or {}
        qa = assess_simulation_qa(pid, project_status=proj.get("status"))
        results["simulation_qa"] = {
            "score": qa.get("score"),
            "can_run_rigorous": qa.get("can_run_rigorous"),
            "warnings": qa.get("warnings", [])[:12],
            "study_level": qa.get("study_level"),
        }
        run_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO simulation_runs_v2 "
            "(id, project_id, template_id, run_type, run_mode, ops_simulated, "
            " feed_source, feed_stream, product_stream, params, results, "
            " label, created_by, compilation_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, pid, template_id, "rigorous", "global",
             None,  # ops_simulated — global run simulates all
             None,  # feed_source
             None,  # feed_stream
             None,  # product_stream
             json.dumps(params_override) if params_override else None,
             json.dumps(results, default=str),
             None,  # label
             user.get("id"),
             compilation_id),
        )
        c.commit()
        return {"run_id": run_id, "template_id": template_id, **results}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        release(c)


@router.post("/simulation-v2/run-compare-o2b")
def run_simulation_compare_o2b(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Run process_simulator and ore_to_bullion on the same template for KPI comparison."""
    template_id, compilation_id = _resolve_template_for_run(pid)
    params_override = body.get("params_override")
    try:
        from ..engines.simulation_bridge import run_rigorous_o2b_comparison
    except ImportError:
        from engines.simulation_bridge import run_rigorous_o2b_comparison

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        payload = run_rigorous_o2b_comparison(pid, template_id, params_override, cur)
        run_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO simulation_runs_v2 "
            "(id, project_id, template_id, run_type, run_mode, ops_simulated, "
            " feed_source, feed_stream, product_stream, params, results, "
            " label, created_by, compilation_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                run_id, pid, template_id, "rigorous_o2b_compare", "global",
                None, None, None, None,
                json.dumps(params_override) if params_override else None,
                json.dumps(payload, default=str),
                body.get("label"),
                user.get("id"),
                compilation_id,
            ),
        )
        c.commit()
        return {"run_id": run_id, "template_id": template_id, **payload}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        release(c)


@router.get("/simulation-v2/runs")
def list_simulation_runs(pid: str, user=Depends(project_user)):
    """List all simulation runs for this project."""
    try:
        rows = qall(
            "SELECT id, template_id, run_type, created_at, created_by "
            "FROM simulation_runs_v2 WHERE project_id = %s ORDER BY created_at DESC",
            (pid,),
        )
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/simulation-v2/runs/{rid}")
def get_simulation_run(pid: str, rid: str, user=Depends(project_user)):
    """Get specific simulation run results."""
    try:
        row = qone(
            "SELECT * FROM simulation_runs_v2 WHERE id = %s AND project_id = %s",
            (rid, pid),
        )
        if not row:
            raise HTTPException(404, "Simulation run not found")
        if isinstance(row.get("results"), str):
            row["results"] = json.loads(row["results"])
        if isinstance(row.get("params"), str):
            row["params"] = json.loads(row["params"])
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# =============================================================================
# SENSITIVITY
# =============================================================================

@router.post("/simulation-v2/sensitivity")
def run_sensitivity_analysis(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Run sensitivity analysis (tornado chart data).

    Body: {params_to_vary: [{key, label}], delta_pcts: [10, 20]}
    """
    tpl = _active_template(pid)
    template_id = str(tpl["id"])
    params_to_vary = body.get("params_to_vary", [])
    delta_pcts = body.get("delta_pcts", [10, 20])
    if not params_to_vary:
        raise HTTPException(400, "params_to_vary is required")

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _, run_sensitivity, _ = _import_process_simulator()
        tornado = run_sensitivity(pid, template_id, params_to_vary, delta_pcts, cur)
        # Save run metadata
        run_id = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO simulation_runs_v2 "
            "(id, project_id, template_id, run_type, params, results, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (run_id, pid, template_id, "sensitivity",
             json.dumps({"params_to_vary": params_to_vary, "delta_pcts": delta_pcts}),
             json.dumps(tornado), user.get("id")),
        )
        c.commit()
        return {"run_id": run_id, "tornado": tornado}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        release(c)


# =============================================================================
# SCENARIOS
# =============================================================================

@router.post("/simulation-v2/scenarios", status_code=201)
def create_scenario(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Create a simulation scenario.

    Body: {name, description, params_override, color,
           study_level?, capex_opex_tolerance_pct?, scenario_group?}
    """
    try:
        import psycopg2

        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        tol = body.get("capex_opex_tolerance_pct")
        return execute(
            "INSERT INTO simulation_scenarios "
            "(id, project_id, name, description, params_override, color, "
            " study_level, capex_opex_tolerance_pct, scenario_group) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                str(uuid.uuid4()),
                pid,
                name,
                body.get("description", ""),
                json.dumps(body.get("params_override")) if body.get("params_override") else None,
                body.get("color", "#3B82F6"),
                body.get("study_level"),
                float(tol) if tol is not None else None,
                body.get("scenario_group"),
            ),
        )
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.get("/simulation-v2/scenarios")
def list_scenarios(pid: str, user=Depends(project_user)):
    """List all simulation scenarios for this project."""
    try:
        rows = qall(
            "SELECT * FROM simulation_scenarios WHERE project_id = %s ORDER BY created_at DESC",
            (pid,),
        )
        for row in rows:
            if isinstance(row.get("params_override"), str):
                row["params_override"] = json.loads(row["params_override"])
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/simulation-v2/scenarios/{scenario_id}")
def delete_scenario(pid: str, scenario_id: str, user=Depends(project_user)):
    """Delete a simulation scenario."""
    try:
        row = qone(
            "SELECT id FROM simulation_scenarios WHERE id = %s AND project_id = %s",
            (scenario_id, pid),
        )
        if not row:
            raise HTTPException(404, "Scénario non trouvé")
        execute(
            "DELETE FROM simulation_scenarios WHERE id = %s AND project_id = %s",
            (scenario_id, pid),
        )
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.patch("/simulation-v2/scenarios/{scenario_id}")
def patch_scenario(pid: str, scenario_id: str, body: dict = Body(...), user=Depends(project_user)):
    """Update scenario metadata / overrides (partial)."""
    try:
        import psycopg2

        row = qone(
            "SELECT id FROM simulation_scenarios WHERE id = %s AND project_id = %s",
            (scenario_id, pid),
        )
        if not row:
            raise HTTPException(404, "Scénario non trouvé")
        updates: list[str] = []
        vals: list = []
        if "name" in body:
            nm = (body.get("name") or "").strip()
            if not nm:
                raise HTTPException(400, "name cannot be empty")
            updates.append("name = %s")
            vals.append(nm)
        if "description" in body:
            updates.append("description = %s")
            vals.append(body.get("description") or "")
        if "params_override" in body:
            po = body.get("params_override")
            updates.append("params_override = %s")
            vals.append(json.dumps(po) if po is not None else None)
        if "color" in body:
            updates.append("color = %s")
            vals.append(body.get("color"))
        if "study_level" in body:
            updates.append("study_level = %s")
            vals.append(body.get("study_level"))
        if "capex_opex_tolerance_pct" in body:
            tol = body.get("capex_opex_tolerance_pct")
            updates.append("capex_opex_tolerance_pct = %s")
            vals.append(float(tol) if tol is not None else None)
        if "scenario_group" in body:
            updates.append("scenario_group = %s")
            vals.append(body.get("scenario_group"))

        if not updates:
            out = qone(
                "SELECT * FROM simulation_scenarios WHERE id = %s AND project_id = %s",
                (scenario_id, pid),
            )
            if isinstance(out.get("params_override"), str):
                out["params_override"] = json.loads(out["params_override"] or "{}")
            return out

        vals.extend([scenario_id, pid])
        sql = (
            "UPDATE simulation_scenarios SET "
            + ", ".join(updates)
            + " WHERE id = %s AND project_id = %s RETURNING *"
        )
        out = execute(sql, tuple(vals))
        if isinstance(out.get("params_override"), str):
            out["params_override"] = json.loads(out["params_override"] or "{}")
        return out
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.get("/simulation-v2/scenarios/group/{group_name}")
def list_scenarios_by_group(pid: str, group_name: str, user=Depends(project_user)):
    """List scenarios sharing the same scenario_group (named scenario set)."""
    try:
        rows = qall(
            "SELECT * FROM simulation_scenarios WHERE project_id = %s AND scenario_group = %s "
            "ORDER BY created_at DESC",
            (pid, group_name),
        )
        for row in rows:
            if isinstance(row.get("params_override"), str):
                row["params_override"] = json.loads(row["params_override"] or "{}")
        return rows
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/simulation-v2/runs/compare-pareto")
def compare_pareto_runs(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Compare Pareto fronts from two simulation_runs_v2 rows (e.g. simulate_optimize)."""
    try:
        from ..compute.pareto_compare import compare_pareto_results
    except ImportError:
        from compute.pareto_compare import compare_pareto_results

    ra_id = body.get("run_id_a")
    rb_id = body.get("run_id_b")
    if not ra_id or not rb_id:
        raise HTTPException(400, "run_id_a and run_id_b required")

    row_a = qone(
        "SELECT id, run_type, results FROM simulation_runs_v2 WHERE id = %s AND project_id = %s",
        (ra_id, pid),
    )
    row_b = qone(
        "SELECT id, run_type, results FROM simulation_runs_v2 WHERE id = %s AND project_id = %s",
        (rb_id, pid),
    )
    if not row_a or not row_b:
        raise HTTPException(404, "One or both runs not found")

    def _as_results(raw):
        if raw is None:
            return {}
        if isinstance(raw, str):
            return json.loads(raw)
        return raw

    res_a = _as_results(row_a.get("results"))
    res_b = _as_results(row_b.get("results"))
    if not res_a.get("pareto_front") or not res_b.get("pareto_front"):
        raise HTTPException(
            400,
            "Both runs must contain results.pareto_front (e.g. simulate_optimize outputs)",
        )

    cmp_out = compare_pareto_results(res_a, res_b)
    return {
        "run_id_a": str(ra_id),
        "run_id_b": str(rb_id),
        "run_type_a": row_a.get("run_type"),
        "run_type_b": row_b.get("run_type"),
        "comparison": cmp_out,
    }


@router.post("/simulation-v2/scenarios/compare")
def compare_scenarios_endpoint(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Run all scenarios and compare results.

    Returns [{scenario_name, color, results}, ...]
    """
    scenarios = qall(
        "SELECT id, name, color FROM simulation_scenarios WHERE project_id = %s",
        (pid,),
    )
    if not scenarios:
        raise HTTPException(404, "No scenarios found. Create scenarios first.")
    scenario_ids = [str(s["id"]) for s in scenarios]
    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _, _, compare_scenarios = _import_process_simulator()
        comparison = compare_scenarios(pid, scenario_ids, cur)
        c.commit()
        return comparison
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)


# =============================================================================
# OPTIMIZATION (NSGA-II)
# =============================================================================

@router.post("/simulation-v2/optimize", status_code=202)
@limiter.limit(HEAVY_COMPUTE_LIMIT)
def run_optimization(request: Request, pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Queue NSGA-II multi-objective optimisation as a Celery task (202 Accepted).

    Body: {population_size: 50, n_generations: 100}
    Poll GET /simulation-v2/optimize/{run_id} for results.
    """
    tpl = _active_template(pid)
    template_id = str(tpl["id"])
    pop_size = int(body.get("population_size", 50))
    n_gen = int(body.get("n_generations", 100))
    objectives = body.get("objectives")
    constraints = body.get("constraints")

    run_id = str(uuid.uuid4())
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO simulation_runs_v2 "
                "(id, project_id, template_id, run_type, params, status, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (run_id, pid, template_id, "optimization",
                 json.dumps({"population_size": pop_size, "n_generations": n_gen}),
                 "queued", user.get("id")),
            )
        db.commit()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    finally:
        release(db)

    try:
        try:
            from ..tasks.simulation_tasks import run_nsga2_optimization
        except ImportError:
            from tasks.simulation_tasks import run_nsga2_optimization
        run_nsga2_optimization.delay(
            pid, run_id, template_id, pop_size, n_gen, objectives, constraints
        )
    except Exception as e:  # intentional: log task dispatch failure and return queued status
        logger.error("Task dispatch failed for nsga2 run %s: %s", run_id, e)

    return {
        "run_id": run_id,
        "status": "queued",
        "poll_url": f"/api/v1/projects/{pid}/simulation-v2/optimize/{run_id}",
    }


@router.get("/simulation-v2/optimize/{rid}")
def get_optimization_results(pid: str, rid: str, user=Depends(project_user)):
    """Get optimization results and Pareto solutions."""
    try:
        row = qone(
            "SELECT * FROM simulation_runs_v2 "
            "WHERE id = %s AND project_id = %s AND run_type = 'optimization'",
            (rid, pid),
        )
        if not row:
            raise HTTPException(404, "Optimization run not found")
        if isinstance(row.get("results"), str):
            row["results"] = json.loads(row["results"])
        # Also fetch Pareto solutions if stored separately
        solutions = qall(
            "SELECT * FROM optimization_solutions WHERE run_id = %s ORDER BY generation, solution_index",
            (rid,),
        )
        for sol in solutions:
            if isinstance(sol.get("objectives"), str):
                sol["objectives"] = json.loads(sol["objectives"])
            if isinstance(sol.get("variables"), str):
                sol["variables"] = json.loads(sol["variables"])
        row["pareto_solutions"] = solutions
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# =============================================================================
# MINE-TO-MILL — schedule
# =============================================================================

@router.post("/mine-to-mill/schedule")
def create_mine_schedule(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Generate mine schedule from block model.

    Body: {n_years: 15, period_type: 'year'}
    """
    _require_block_model(pid)
    n_years = body.get("n_years", 15)
    period_type = body.get("period_type", "year")

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        generate_mine_schedule, _, _, _, _ = _import_mine_to_mill()
        periods = generate_mine_schedule(pid, cur, n_years=n_years, period_type=period_type)
        c.commit()
        return {"periods_created": len(periods), "periods": periods}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)


@router.get("/mine-to-mill/schedule")
def get_mine_schedule(pid: str, user=Depends(project_user)):
    """Get current mine schedule."""
    try:
        rows = qall(
            "SELECT * FROM mine_schedule WHERE project_id = %s ORDER BY period_order",
            (pid,),
        )
        if not rows:
            raise HTTPException(404, "No mine schedule found. Generate one first.")
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# =============================================================================
# MINE-TO-MILL — LOM simulation
# =============================================================================

@router.post("/mine-to-mill/simulate")
def simulate_lom_endpoint(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Simulate Life of Mine using mine schedule + circuit template.

    Reads schedule from mine_schedule table, runs each period through the circuit.
    """
    tpl = _active_template(pid)
    template_id = str(tpl["id"])

    schedule = qall(
        "SELECT * FROM mine_schedule WHERE project_id = %s ORDER BY period_order",
        (pid,),
    )
    if not schedule:
        raise HTTPException(404, "No mine schedule found. Generate one first.")

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _, simulate_lom, _, _, _ = _import_mine_to_mill()
        result = simulate_lom(pid, template_id, schedule, cur)
        c.commit()
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)


@router.get("/mine-to-mill/profile")
def get_lom_profile(pid: str, user=Depends(project_user)):
    """Get latest LOM profile."""
    try:
        latest_run = qone(
            "SELECT id FROM simulation_runs_v2 "
            "WHERE project_id = %s AND run_type = 'lom' ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        if not latest_run:
            raise HTTPException(404, "No LOM profile found. Run LOM simulation first.")
        rows = qall(
            "SELECT * FROM lom_profiles WHERE run_id = %s ORDER BY period_order",
            (str(latest_run["id"]),),
        )
        if not rows:
            raise HTTPException(404, "No LOM profile found. Run LOM simulation first.")
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# =============================================================================
# MINE-TO-MILL — blend optimization
# =============================================================================

@router.post("/mine-to-mill/optimize-blend")
def optimize_blend_endpoint(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Run blend optimizer on current mine schedule."""
    schedule = qall(
        "SELECT * FROM mine_schedule WHERE project_id = %s ORDER BY period_order",
        (pid,),
    )
    if not schedule:
        raise HTTPException(404, "No mine schedule found. Generate one first.")

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _, _, optimize_blend, _, _ = _import_mine_to_mill()
        result = optimize_blend(pid, schedule, cur)
        c.commit()
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)


# =============================================================================
# MINE-TO-MILL — Monte Carlo
# =============================================================================

@router.post("/mine-to-mill/monte-carlo", status_code=202)
@limiter.limit(HEAVY_COMPUTE_LIMIT)
def run_monte_carlo(request: Request, pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Queue Monte Carlo LOM simulation as a Celery task (202 Accepted).

    Body: {n_simulations: 5000}
    Poll GET /mine-to-mill/monte-carlo/{run_id} for results.
    """
    tpl = _active_template(pid)
    template_id = str(tpl["id"])
    n_sims = int(body.get("n_simulations", 5000))

    schedule = qall(
        "SELECT * FROM mine_schedule WHERE project_id = %s ORDER BY period_order",
        (pid,),
    )
    if not schedule:
        raise HTTPException(404, "No mine schedule found. Generate one first.")

    run_id = str(uuid.uuid4())
    db = conn()
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO simulation_runs_v2 "
                "(id, project_id, template_id, run_type, params, status, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (run_id, pid, template_id, "monte_carlo_lom",
                 json.dumps({"n_simulations": n_sims}),
                 "queued", user.get("id")),
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        db.rollback()
        logger.exception("Failed to queue monte_carlo_lom run for project %s", pid)
        raise HTTPException(500, "Failed to queue Monte Carlo run")
    finally:
        release(db)

    try:
        try:
            from ..tasks.simulation_tasks import run_monte_carlo_lom
        except ImportError:
            from tasks.simulation_tasks import run_monte_carlo_lom
        run_monte_carlo_lom.delay(pid, run_id, template_id, schedule, n_sims)
    except Exception as e:  # intentional: log task dispatch failure and return queued status
        logger.error("Task dispatch failed for monte_carlo run %s: %s", run_id, e)

    return {
        "run_id": run_id,
        "status": "queued",
        "poll_url": f"/api/v1/projects/{pid}/mine-to-mill/monte-carlo/{run_id}",
    }


@router.get("/mine-to-mill/monte-carlo/{run_id}")
def get_monte_carlo_result(pid: str, run_id: str, user=Depends(project_user)):
    """Poll for Monte Carlo LOM simulation result."""
    row = qone(
        "SELECT id, status, results FROM simulation_runs_v2 "
        "WHERE id=%s AND project_id=%s AND run_type='monte_carlo_lom'",
        (run_id, pid),
    )
    if not row:
        raise HTTPException(404, "Monte Carlo run not found")
    if isinstance(row.get("results"), str):
        import json as _j
        row["results"] = _j.loads(row["results"])
    return row


# =============================================================================
# MINE-TO-MILL — ESG timeline
# =============================================================================

@router.get("/mine-to-mill/esg-timeline")
def get_esg_timeline(pid: str, user=Depends(project_user)):
    """Get ESG dashboard data from latest LOM profiles."""
    latest_run = qone(
        "SELECT id FROM simulation_runs_v2 "
        "WHERE project_id = %s AND run_type = 'lom' ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if not latest_run:
        raise HTTPException(404, "No LOM profile found. Run LOM simulation first.")
    lom_profiles = qall(
        "SELECT * FROM lom_profiles WHERE run_id = %s ORDER BY period_order",
        (str(latest_run["id"]),),
    )
    if not lom_profiles:
        raise HTTPException(404, "No LOM profile found. Run LOM simulation first.")

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        _, _, _, _, esg_timeline = _import_mine_to_mill()
        result = esg_timeline(pid, lom_profiles, cur)
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        release(c)


# =============================================================================
# MINE-TO-MILL — Critical periods
# =============================================================================

@router.get("/mine-to-mill/critical-periods")
def get_critical_periods(pid: str, user=Depends(project_user)):
    """Get critical periods from latest LOM profile.

    Returns periods where recovery drops below threshold or grade variability is high.
    """
    try:
        latest_run = qone(
            "SELECT id FROM simulation_runs_v2 "
            "WHERE project_id = %s AND run_type = 'lom' ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        if not latest_run:
            raise HTTPException(404, "No LOM profile found. Run LOM simulation first.")
        profiles = qall(
            "SELECT * FROM lom_profiles WHERE run_id = %s ORDER BY period_order",
            (str(latest_run["id"]),),
        )
        if not profiles:
            raise HTTPException(404, "No LOM profile found. Run LOM simulation first.")

        critical = []
        for p in profiles:
            reasons = []
            # Parse results if stored as JSON string
            data = p
            if isinstance(p.get("results"), str):
                data = {**p, **json.loads(p["results"])}

            recovery = data.get("overall_recovery") or data.get("recovery_pct")
            if recovery is not None and recovery < 85.0:
                reasons.append(f"Low recovery: {recovery:.1f}%")

            grade = data.get("head_grade") or data.get("feed_grade_au")
            if grade is not None and grade < 0.8:
                reasons.append(f"Low head grade: {grade:.2f} g/t")

            throughput_ratio = data.get("throughput_ratio")
            if throughput_ratio is not None and throughput_ratio < 0.9:
                reasons.append(f"Throughput constraint: {throughput_ratio:.0%}")

            if reasons:
                critical.append({
                    "period_order": p.get("period_order"),
                    "period_label": p.get("period_label", f"Year {p.get('period_order', '?')}"),
                    "reasons": reasons,
                    "severity": "high" if len(reasons) >= 2 else "medium",
                })

        return {"critical_periods": critical, "total_periods": len(profiles)}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# =============================================================================
# SIMULATION v2 — Section-level & suggested scenario endpoints
# =============================================================================

try:
    from ..models import RunByOpsRequest, RunBySectionsRequest, RunSuggestedRequest
except ImportError:
    from models import RunByOpsRequest, RunBySectionsRequest, RunSuggestedRequest


def _import_simulate_section():
    try:
        from ..engines.process_simulator import simulate_section, resolve_op_codes_for_sections, _load_enabled_operations
    except ImportError:
        from engines.process_simulator import simulate_section, resolve_op_codes_for_sections, _load_enabled_operations
    return simulate_section, resolve_op_codes_for_sections, _load_enabled_operations


def _import_scenario_advisor():
    try:
        from ..engines.scenario_advisor import suggest
    except ImportError:
        from engines.scenario_advisor import suggest
    return suggest


@router.post("/simulation-v2/run-by-ops")
def run_by_ops(pid: str, body: RunByOpsRequest, user=Depends(project_user)):
    """Run simulation on a specific subset of operations by op_codes."""
    try:
        template_id, compilation_id = _resolve_template_for_run(pid)
        simulate_section, _, _ = _import_simulate_section()

        feed_override = body.feed_override.model_dump() if body.feed_override else None
        params_override = body.params_override

        db = conn()
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            result = simulate_section(
                pid, template_id, body.op_codes,
                feed_override=feed_override,
                params_override=params_override,
                cursor=cur,
            )
            run_id = result.get("run_id", str(uuid.uuid4()))
            cur.execute(
                "INSERT INTO simulation_runs_v2 "
                "(id, project_id, template_id, run_type, run_mode, ops_simulated, "
                " feed_source, feed_stream, product_stream, params, results, "
                " label, created_by, compilation_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, pid, template_id, "rigorous", "section",
                 result.get("ops_simulated"),
                 result.get("feed_source"),
                 json.dumps(result.get("feed_stream"), default=str),
                 json.dumps(result.get("product_stream"), default=str),
                 json.dumps(params_override) if params_override else None,
                 json.dumps(result, default=str),
                 body.label, user.get("id"), compilation_id),
            )
            db.commit()
            return {"run_id": run_id, "template_id": template_id, **result}
        finally:
            cur.close()
            release(db)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.post("/simulation-v2/run-by-sections")
def run_by_sections(pid: str, body: RunBySectionsRequest, user=Depends(project_user)):
    """Run simulation on one or more named circuit sections."""
    try:
        template_id, compilation_id = _resolve_template_for_run(pid)
        simulate_section, resolve_op_codes_for_sections, _load_enabled_operations = _import_simulate_section()

        feed_override = body.feed_override.model_dump() if body.feed_override else None
        params_override = body.params_override

        db = conn()
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            all_ops = _load_enabled_operations(template_id, cur)
            op_codes = resolve_op_codes_for_sections(body.sections, all_ops)
            if not op_codes:
                raise HTTPException(400, "No operations found for the requested sections")

            result = simulate_section(
                pid, template_id, op_codes,
                feed_override=feed_override,
                params_override=params_override,
                cursor=cur,
            )
            result["mode"] = "multi_section"

            run_id = result.get("run_id", str(uuid.uuid4()))
            cur.execute(
                "INSERT INTO simulation_runs_v2 "
                "(id, project_id, template_id, run_type, run_mode, ops_simulated, "
                " feed_source, feed_stream, product_stream, params, results, "
                " label, created_by, compilation_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, pid, template_id, "rigorous", "multi_section",
                 result.get("ops_simulated"),
                 result.get("feed_source"),
                 json.dumps(result.get("feed_stream"), default=str),
                 json.dumps(result.get("product_stream"), default=str),
                 json.dumps(params_override) if params_override else None,
                 json.dumps(result, default=str),
                 body.label, user.get("id"), compilation_id),
            )
            db.commit()
            return {"run_id": run_id, "template_id": template_id, **result}
        finally:
            cur.close()
            release(db)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.get("/simulation-v2/suggest-scenarios")
def suggest_scenarios(pid: str, user=Depends(project_user)):
    """Generate scenario suggestions using the scenario advisor engine."""
    try:
        suggest = _import_scenario_advisor()
        result = suggest(pid)
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/simulation-v2/run-suggested")
def run_suggested(pid: str, body: RunSuggestedRequest, user=Depends(project_user)):
    """Run a previously suggested scenario."""
    try:
        tpl = _active_template(pid)
        template_id = str(tpl["id"])
        simulate_section, _, _load_enabled_operations = _import_simulate_section()

        # Fetch the suggestion
        suggestion = qone(
            "SELECT * FROM scenario_suggestions_log "
            "WHERE id = %s AND project_id = %s",
            (body.suggestion_id, pid),
        )
        if not suggestion:
            raise HTTPException(404, "Suggestion not found")

        # Parse suggestion data
        suggestion_data = suggestion
        if isinstance(suggestion.get("suggestion_data"), str):
            suggestion_data = {**suggestion, **json.loads(suggestion["suggestion_data"])}
        elif isinstance(suggestion.get("suggestion_data"), dict):
            suggestion_data = {**suggestion, **suggestion["suggestion_data"]}

        ops_to_add = suggestion_data.get("ops_to_add", [])
        ops_to_remove = suggestion_data.get("ops_to_remove", [])

        db = conn()
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            all_ops = _load_enabled_operations(template_id, cur)

            # Apply in-memory modifications
            remove_set = set(ops_to_remove)
            modified_ops = [op for op in all_ops if op.get("op_code") not in remove_set]
            for code in ops_to_add:
                if not any(op.get("op_code") == code for op in modified_ops):
                    modified_ops.append({"op_code": code, "sort_order": 9999, "label": code, "category": "custom"})

            op_codes = [op["op_code"] for op in modified_ops]
            params_override = suggestion_data.get("params_override")

            result = simulate_section(
                pid, template_id, op_codes,
                params_override=params_override,
                operations_override=modified_ops,
                cursor=cur,
            )

            run_id = result.get("run_id", str(uuid.uuid4()))
            cur.execute(
                "INSERT INTO simulation_runs_v2 "
                "(id, project_id, template_id, run_type, run_mode, ops_simulated, "
                " feed_source, feed_stream, product_stream, params, results, "
                " suggestion_id, label, created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, pid, template_id, "rigorous", body.run_mode,
                 result.get("ops_simulated"),
                 result.get("feed_source"),
                 json.dumps(result.get("feed_stream"), default=str),
                 json.dumps(result.get("product_stream"), default=str),
                 json.dumps(params_override) if params_override else None,
                 json.dumps(result, default=str),
                 body.suggestion_id,
                 suggestion_data.get("label") or suggestion_data.get("name"),
                 user.get("id")),
            )

            # Update suggestion status to 'tested'
            cur.execute(
                "UPDATE scenario_suggestions_log SET status = 'tested' WHERE id = %s",
                (body.suggestion_id,),
            )
            db.commit()
            return {"run_id": run_id, "template_id": template_id, **result}
        finally:
            cur.close()
            release(db)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
