"""
MPDPMS — Shared project-parameter helpers.

Provides a single, authoritative source for key metallurgical parameters
that are consumed by multiple modules (design, massbalance, working_capital,
simulation, etc.).  All functions accept a project_id string and, optionally,
pre-fetched data structures to avoid redundant DB queries.

Priority chain for every parameter:
  1. LIMS measured data  (highest confidence)
  2. simulation_params table
  3. projects table fields
  4. Hard-coded industry default  (last resort — logged as warning)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("mpdpms.helpers")

# ─── Local DB access (same try/except pattern used throughout the app) ─────────
try:
    from .db import qone, qall
    from .settings import get_settings
except ImportError:
    from db import qone, qall
    from settings import get_settings


SETTINGS = get_settings()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Gold recovery
# ─────────────────────────────────────────────────────────────────────────────

def get_recovery_pct(pid: str, d1_rows: Optional[list] = None) -> float:
    """
    Return overall Au recovery in % (e.g. 88.5).

    Priority:
      1. Latest simulation_runs_v2 overall.total_recovery_pct (rigorous circuit)
      2. simulation_params overall_recovery_pct
      3. Average of lims_d1.au_recovery_pct  (actual leach test data)
      4. simulation_params leaching/rec_baseline
      5. simulation_params comminution/rec_baseline
      6. Default 89.0 %
    """
    try:
        sim_row = qone(
            """
            SELECT results FROM simulation_runs_v2
            WHERE project_id = %s AND results IS NOT NULL
            ORDER BY created_at DESC LIMIT 1
            """,
            (pid,),
        )
        if sim_row and sim_row.get("results"):
            results = sim_row["results"]
            if isinstance(results, str):
                results = json.loads(results)
            overall = results.get("overall") if isinstance(results, dict) else None
            if isinstance(overall, dict) and overall.get("total_recovery_pct") is not None:
                rec = float(overall["total_recovery_pct"])
                logger.debug(
                    "project %s: recovery=%.1f%% (simulation_runs_v2 overall)",
                    pid, rec,
                )
                return round(rec, 2)

        sp_overall = qone(
            "SELECT param_value FROM simulation_params "
            "WHERE project_id=%s AND param_key='overall_recovery_pct' "
            "AND param_value IS NOT NULL",
            (pid,),
        )
        if sp_overall and sp_overall.get("param_value") is not None:
            rec = float(sp_overall["param_value"])
            logger.debug(
                "project %s: recovery=%.1f%% (simulation_params.overall_recovery_pct)",
                pid, rec,
            )
            return round(rec, 2)

        # 3. LIMS D1 leach tests
        rows = d1_rows if d1_rows is not None else qall(
            "SELECT au_recovery_pct FROM lims_d1 WHERE project_id=%s", (pid,)
        )
        vals = [float(r["au_recovery_pct"]) for r in rows
                if r.get("au_recovery_pct") not in (None, "", 0)]
        if vals:
            rec = sum(vals) / len(vals)
            logger.debug("project %s: recovery=%.1f%% (LIMS D1 avg, n=%d)", pid, rec, len(vals))
            return round(rec, 2)

        # 2. simulation_params — leaching category
        sp = qone(
            "SELECT param_value FROM simulation_params "
            "WHERE project_id=%s AND category='leaching' AND param_key='rec_baseline'",
            (pid,),
        )
        if sp and sp.get("param_value") is not None:
            rec = float(sp["param_value"])
            logger.debug("project %s: recovery=%.1f%% (sim_params leaching/rec_baseline)", pid, rec)
            return round(rec, 2)

        # 3. simulation_params — comminution category (legacy)
        sp2 = qone(
            "SELECT param_value FROM simulation_params "
            "WHERE project_id=%s AND category='comminution' AND param_key='rec_baseline'",
            (pid,),
        )
        if sp2 and sp2.get("param_value") is not None:
            rec = float(sp2["param_value"])
            logger.debug("project %s: recovery=%.1f%% (sim_params comminution/rec_baseline)", pid, rec)
            return round(rec, 2)

        logger.warning(
            "project %s: recovery defaulting to %.1f%% — no LIMS D1 or sim_params data",
            pid,
            SETTINGS.default_recovery_pct,
        )
        return SETTINGS.default_recovery_pct
    except Exception as e:
        logger.error("project %s: failed to resolve recovery_pct: %s — returning default %.1f%%", pid, e, SETTINGS.default_recovery_pct)
        return SETTINGS.default_recovery_pct


def combined_plant_recovery_pct(
    gravity_plant_pct: float,
    leach_on_residue_pct: float,
) -> float:
    """Plant Au recovery: gravity on feed + leach on gold remaining (% units)."""
    try:
        from .engines.metallurgical_formulas import combined_gravity_leach_recovery_pct
    except ImportError:
        from engines.metallurgical_formulas import combined_gravity_leach_recovery_pct
    return combined_gravity_leach_recovery_pct(gravity_plant_pct, leach_on_residue_pct)


def _load_sim_params_index(pid: str) -> dict[str, float]:
    rows = qall(
        "SELECT param_key, param_value FROM simulation_params "
        "WHERE project_id=%s AND param_value IS NOT NULL",
        (pid,),
    )
    try:
        from .engines.gravity_model import simulation_params_index
    except ImportError:
        from engines.gravity_model import simulation_params_index
    return simulation_params_index(rows)


def _active_circuit_op_codes(pid: str) -> set[str]:
    rows = qall(
        """
        SELECT UPPER(co.op_code) AS op_code
        FROM circuit_operations co
        JOIN circuit_templates ct ON ct.id = co.template_id
        WHERE ct.project_id = %s AND ct.is_active = TRUE AND co.enabled = TRUE
        """,
        (pid,),
    )
    return {str(r["op_code"]) for r in rows if r.get("op_code")}


def _op_is_gravity(op_code: str) -> bool:
    c = (op_code or "").upper()
    return any(
        token in c
        for token in ("GRAVIT", "KNELSON", "FALCON", "GEMENI", "ILR", "CONC_ILR")
    )


def _op_is_leach(op_code: str) -> bool:
    c = (op_code or "").upper()
    return (
        c.startswith("LEACH")
        or c.startswith("CIL")
        or c.startswith("CIP")
        or "LIXIV" in c
    )


def _parse_simulation_operations(operations: list) -> dict[str, Any]:
    """Extract gravity (plant feed) and leach (stage feed) recoveries from a sim run."""
    gravity_pct: float | None = None
    leach_pct: float | None = None
    for op in operations or []:
        if not isinstance(op, dict):
            continue
        code = str(op.get("op_code") or "")
        perf = op.get("performance") if isinstance(op.get("performance"), dict) else {}
        model = str(op.get("model_used") or "").lower()
        rec = perf.get("recovery_pct")
        if rec is None:
            continue
        try:
            rec_f = float(rec)
        except (TypeError, ValueError):
            continue
        if model == "gravity_plant_recovery" or "gravity" in model or _op_is_gravity(code):
            gravity_pct = rec_f
        elif _op_is_leach(code) or any(x in model for x in ("leach", "cil", "cip")):
            leach_pct = rec_f
    return {"gravity_recovery_pct": gravity_pct, "leach_recovery_pct": leach_pct}


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _gravity_recovery_from_sim_params(sim: dict[str, float]) -> float | None:
    try:
        from .engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params
    except ImportError:
        from engines.gravity_model import plant_gravity_recovery_pct, resolve_gravity_params
    keys = (
        "gravity_grg", "gravity_slip", "gravity_rec", "gravity_ilr", "gravity_mass_pull",
        "grg_pct", "gravity_slip_pct", "knelson_unit_recovery_pct", "ilr_recovery_pct",
    )
    if not any(k in sim for k in keys):
        return None
    return round(plant_gravity_recovery_pct(resolve_gravity_params(sim)), 2)


RECOVERY_SOURCE_LABELS: dict[str, str] = {
    "simulation_runs_v2": "Simulation circuit",
    "mass_balance": "Bilan massique",
    "simulation_params.overall_recovery_pct": "Paramètre overall_recovery_pct",
    "simulation_params.components": "Paramètres gravité + lixiviation",
    "lims_d1_avg": "LIMS D1 (lixiviation)",
    "lims_or_sim_baseline": "Baseline simulation / LIMS",
    "lims_defaults": "LIMS (cinétique + gravité)",
    "industry_defaults": "Référence industrie (simulation/defaults)",
    "default": "Valeur par défaut application",
    "computed_formula": "Calcul R_grav + (1−R_grav)×R_leach",
}


def _load_simulation_defaults_pack(pid: str) -> dict[str, dict]:
    try:
        from .routes.simulation_defaults import build_project_simulation_defaults
    except ImportError:
        from routes.simulation_defaults import build_project_simulation_defaults
    return build_project_simulation_defaults(pid)


def resolve_dashboard_project_fields(pid: str, project: Optional[dict] = None) -> dict[str, Any]:
    """Effective project scalars for dashboard (avoids ORM factory sentinels)."""
    if project is None:
        project = qone("SELECT * FROM projects WHERE id=%s", (pid,)) or {}
    pack = _load_simulation_defaults_pack(pid)

    def _val(key: str) -> tuple[float, str]:
        entry = pack.get(key) or {}
        return float(entry.get("value") or 0), str(entry.get("source") or "default")

    gold_price, gp_src = _val("gold_price")
    mine_life, ml_src = _val("mine_life")
    avail, av_src = _val("availability_pct")
    hours, h_src = _val("operating_hours_day")
    discount, dr_src = _val("discount_rate")

    return {
        "name": project.get("project_name"),
        "code": project.get("project_code"),
        "target_tph": float(project.get("target_tph") or pack["feed_tph"]["value"]),
        "gold_grade": float(project.get("gold_grade_g_t") or pack["head_grade_au"]["value"]),
        "availability_pct": avail,
        "gold_price": gold_price,
        "mine_life_years": int(mine_life) if mine_life else 0,
        "operating_hours_day": hours,
        "discount_rate_pct": discount,
        "field_sources": {
            "gold_price": gp_src,
            "mine_life_years": ml_src,
            "availability_pct": av_src,
            "operating_hours_day": h_src,
            "discount_rate_pct": dr_src,
        },
    }


def resolve_recovery_breakdown(pid: str, project: Optional[dict] = None) -> dict[str, Any]:
    """
    Plant recovery KPI with gravity / leach decomposition for dashboard PLM.

    - ``gravity_recovery_pct``: % of plant feed Au to gravity (ILR path included in model).
    - ``leach_recovery_pct``: % Au recovered on leach/CIL **feed** (after gravity tails).
    - ``plant_recovery_pct``: overall plant (mass balance on feed), not grav% + leach%.
    - ``plant_formula_pct``: R_grav + (1−R_grav/100)×R_leach when both components known.
    """
    if project is None:
        project = qone("SELECT * FROM projects WHERE id=%s", (pid,)) or {}

    op_codes = _active_circuit_op_codes(pid)
    has_gravity_circuit = any(_op_is_gravity(c) for c in op_codes)
    has_leach_circuit = any(_op_is_leach(c) for c in op_codes)
    flags = get_circuit_flags(pid)
    if not has_gravity_circuit:
        has_gravity_circuit = bool(flags.get("has_gravity"))

    plant_pct: float | None = None
    plant_source = "default"
    gravity_pct: float | None = None
    gravity_source: str | None = None
    leach_pct: float | None = None
    leach_source: str | None = None
    plant_formula_pct: float | None = None

    sim_row = qone(
        """
        SELECT results FROM simulation_runs_v2
        WHERE project_id = %s AND results IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (pid,),
    )
    if sim_row and sim_row.get("results"):
        results = sim_row["results"]
        if isinstance(results, str):
            results = json.loads(results)
        if isinstance(results, dict):
            overall = results.get("overall") if isinstance(results.get("overall"), dict) else {}
            if overall.get("total_recovery_pct") is not None:
                plant_pct = round(float(overall["total_recovery_pct"]), 2)
                plant_source = "simulation_runs_v2"
            parsed = _parse_simulation_operations(results.get("operations") or [])
            if parsed.get("gravity_recovery_pct") is not None:
                gravity_pct = parsed["gravity_recovery_pct"]
                gravity_source = "simulation_runs_v2"
            if parsed.get("leach_recovery_pct") is not None:
                leach_pct = parsed["leach_recovery_pct"]
                leach_source = "simulation_runs_v2"

    sim = _load_sim_params_index(pid)
    mb_snap = qone(
        "SELECT param_value, param_value_text FROM simulation_params "
        "WHERE project_id=%s AND category='process' AND param_key='recovery_snapshot_source'",
        (pid,),
    )
    _mb_source = ""
    if mb_snap:
        _mb_source = str(
            mb_snap.get("param_value_text") or mb_snap.get("param_value") or ""
        )
    if _mb_source == "mass_balance":
        if plant_pct is None and sim.get("overall_recovery_pct") is not None:
            plant_pct = round(float(sim["overall_recovery_pct"]), 2)
            plant_source = "mass_balance"
        if gravity_pct is None and sim.get("gravity_recovery_pct") is not None:
            gravity_pct = round(float(sim["gravity_recovery_pct"]), 2)
            gravity_source = "mass_balance"
        if leach_pct is None and sim.get("leach_recovery_pct") is not None:
            leach_pct = round(float(sim["leach_recovery_pct"]), 2)
            leach_source = "mass_balance"
        if sim.get("plant_formula_recovery_pct") is not None and plant_formula_pct is None:
            plant_formula_pct = round(float(sim["plant_formula_recovery_pct"]), 2)

    if gravity_pct is None:
        g_from_params = _gravity_recovery_from_sim_params(sim)
        if g_from_params is not None:
            gravity_pct = g_from_params
            gravity_source = "simulation_params.components"
    if leach_pct is None:
        for key in ("cil_recovery_pct", "leaching_recovery_pct", "leach_recovery_pct"):
            if key in sim:
                leach_pct = round(float(sim[key]), 2)
                leach_source = "simulation_params.components"
                break
        if leach_pct is None:
            sp = qone(
                "SELECT param_value FROM simulation_params "
                "WHERE project_id=%s AND category='leaching' AND param_key='rec_baseline'",
                (pid,),
            )
            if sp and sp.get("param_value") is not None:
                leach_pct = round(float(sp["param_value"]), 2)
                leach_source = "simulation_params.components"

    if plant_pct is None and sim.get("overall_recovery_pct") is not None:
        plant_pct = round(float(sim["overall_recovery_pct"]), 2)
        plant_source = "simulation_params.overall_recovery_pct"

    if plant_pct is None:
        d1_rows = qall("SELECT au_recovery_pct FROM lims_d1 WHERE project_id=%s", (pid,))
        vals = [
            float(r["au_recovery_pct"])
            for r in d1_rows
            if r.get("au_recovery_pct") not in (None, "", 0)
        ]
        if vals:
            plant_pct = round(sum(vals) / len(vals), 2)
            plant_source = "lims_d1_avg"
            if leach_pct is None and has_leach_circuit and not has_gravity_circuit:
                leach_pct = plant_pct
                leach_source = "lims_d1_avg"

    if plant_pct is None:
        defaults_pack = _load_simulation_defaults_pack(pid)
        grav_d = _f((defaults_pack.get("grav_rec_au") or {}).get("value"))
        leach_d = _f((defaults_pack.get("cil_rec_au") or {}).get("value"))
        if grav_d is not None and leach_d is not None:
            plant_formula_pct = combined_plant_recovery_pct(grav_d, leach_d)
            plant_pct = plant_formula_pct
            plant_source = "lims_defaults"
            gravity_pct = gravity_pct if gravity_pct is not None else grav_d
            gravity_source = gravity_source or "lims_defaults"
            leach_pct = leach_pct if leach_pct is not None else leach_d
            leach_source = leach_source or "lims_defaults"
        elif leach_d is not None and not has_gravity_circuit:
            plant_pct = leach_d
            plant_source = "lims_defaults"
            leach_pct = leach_pct if leach_pct is not None else leach_d
            leach_source = leach_source or "lims_defaults"
        else:
            baseline = get_recovery_pct(pid)
            plant_pct = baseline
            if baseline == SETTINGS.default_recovery_pct:
                ind = _f((defaults_pack.get("cil_rec_au") or {}).get("value"))
                if ind is not None:
                    plant_pct = ind
                    plant_source = "industry_defaults"
                else:
                    plant_source = "default"
            else:
                plant_source = "lims_or_sim_baseline"

    if plant_formula_pct is None and gravity_pct is not None and leach_pct is not None:
        plant_formula_pct = combined_plant_recovery_pct(gravity_pct, leach_pct)

    note: str | None = None
    if plant_source == "default":
        note = (
            f"Récupération usine par défaut ({SETTINGS.default_recovery_pct:.0f} %) — "
            "lancez une simulation, un bilan massique, ou complétez les essais LIMS D1/C2."
        )
    elif plant_source == "industry_defaults":
        note = (
            "Récupération issue des références industrie / LIMS partiels — "
            "validez avec simulation ou bilan massique."
        )
    elif plant_source == "lims_d1_avg" and has_gravity_circuit:
        note = (
            "La moyenne LIMS D1 reflète surtout la lixiviation; la gravité/ILR n’est pas incluse "
            "dans ce chiffre unique."
        )
    elif gravity_pct is not None and leach_pct is not None and plant_formula_pct is not None:
        if abs(plant_formula_pct - plant_pct) > 2.0:
            note = (
                f"R usine ({plant_pct} %) diffère du modèle combiné ({plant_formula_pct} %) "
                "— la simulation inclut flottation/autres étapes."
            )

    return {
        "plant_recovery_pct": plant_pct,
        "plant_source": plant_source,
        "plant_source_label": RECOVERY_SOURCE_LABELS.get(plant_source, plant_source),
        "gravity_recovery_pct": gravity_pct,
        "gravity_source": gravity_source,
        "gravity_source_label": RECOVERY_SOURCE_LABELS.get(gravity_source or "", gravity_source),
        "leach_recovery_pct": leach_pct,
        "leach_source": leach_source,
        "leach_source_label": RECOVERY_SOURCE_LABELS.get(leach_source or "", leach_source),
        "plant_formula_pct": plant_formula_pct,
        "has_gravity_circuit": has_gravity_circuit,
        "has_leach_circuit": has_leach_circuit or bool(leach_pct),
        "note": note,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. Ore specific gravity
# ─────────────────────────────────────────────────────────────────────────────

def get_ore_sg(pid: str, sim_params: Optional[Dict[str, float]] = None) -> float:
    """
    Return ore specific gravity (dimensionless, e.g. 2.75).

    Priority:
      1. sim_params dict (if already fetched by caller)
      2. simulation_params table key 'ore_sg'
      3. Default 2.75 (typical gold ore / siliceous host)
    """
    try:
        if sim_params is not None and "ore_sg" in sim_params:
            return float(sim_params["ore_sg"])

        sp = qone(
            "SELECT param_value FROM simulation_params "
            "WHERE project_id=%s AND param_key='ore_sg' LIMIT 1",
            (pid,),
        )
        if sp and sp.get("param_value") is not None:
            return float(sp["param_value"])

        logger.debug("project %s: ore_sg defaulting to %.2f", pid, SETTINGS.default_ore_sg)
        return SETTINGS.default_ore_sg
    except Exception as e:
        logger.error("project %s: failed to resolve ore_sg: %s — returning default %.2f", pid, e, SETTINGS.default_ore_sg)
        return SETTINGS.default_ore_sg


# ─────────────────────────────────────────────────────────────────────────────
# 3. Plant availability %
# ─────────────────────────────────────────────────────────────────────────────

def get_availability_pct(
    pid: str,
    project: Optional[dict] = None,
    sim_params: Optional[Dict[str, float]] = None,
) -> float:
    """
    Return plant availability in % (e.g. 92.0).

    Authoritative source is the projects table (set by project manager).
    sim_params 'avail_pct' is a mirror used only when the project record is absent.

    Priority:
      1. project dict (projects.availability_pct)
      2. simulation_params financier/avail_pct
      3. Default 92.0 %
    """
    try:
        if project is not None:
            v = project.get("availability_pct")
            if v is not None:
                return float(v)

        # Fetch project row if not provided
        p = qone("SELECT availability_pct FROM projects WHERE id=%s", (pid,))
        if p and p.get("availability_pct") is not None:
            return float(p["availability_pct"])

        if sim_params is not None and "avail_pct" in sim_params:
            return float(sim_params["avail_pct"])

        sp = qone(
            "SELECT param_value FROM simulation_params "
            "WHERE project_id=%s AND category='financier' AND param_key='avail_pct'",
            (pid,),
        )
        if sp and sp.get("param_value") is not None:
            return float(sp["param_value"])

        return SETTINGS.default_availability_pct
    except Exception as e:
        logger.error("project %s: failed to resolve availability_pct: %s — returning default %.1f%%", pid, e, SETTINGS.default_availability_pct)
        return SETTINGS.default_availability_pct


def get_operating_hours_day(project: Optional[dict] = None) -> float:
    """Return scheduled operating hours/day from project row or configured default."""
    if project is not None and project.get("operating_hours_day") is not None:
        return float(project["operating_hours_day"])
    return SETTINGS.default_operating_hours_day


# ─────────────────────────────────────────────────────────────────────────────
# 4. Annual throughput  (consistent formula, used by all modules)
# ─────────────────────────────────────────────────────────────────────────────

def compute_annual_t(tph: float, op_hours_day: float, availability_pct: float) -> float:
    """
    Annual ore throughput in tonnes.

    Formula:  tph × op_hours_day × 365 × (availability_pct / 100)

    Note: op_hours_day is the SCHEDULED hours per day (e.g. 22.08 for two 11-h
    shifts); availability_pct is the mechanical/electrical availability applied
    on top of scheduled hours.  Together they define the effective annual hours.
    """
    try:
        if tph <= 0 or op_hours_day <= 0 or availability_pct <= 0:
            logger.warning("compute_annual_t called with invalid inputs: tph=%.1f op_hours=%.1f avail=%.1f", tph, op_hours_day, availability_pct)
            return 0.0
        return tph * op_hours_day * 365.0 * (availability_pct / 100.0)
    except (TypeError, ValueError) as e:
        logger.error("compute_annual_t calculation error: tph=%s op_hours=%s avail=%s — %s", tph, op_hours_day, availability_pct, e)
        return 0.0


def compute_annual_gold_oz(
    tph: float,
    op_hours_day: float,
    availability_pct: float,
    grade_g_t: float,
    recovery_pct: float,
) -> float:
    """
    Annual gold production (troy oz) — canonical economics / metallurgy path.

    Uses the same formula as ``engines.leaching.annual_gold_oz``:
    ``tph × h/j × 365 × dispo × grade × R`` → grammes → onces.
    """
    try:
        try:
            from .engines.leaching import annual_gold_oz as _annual_oz
        except ImportError:
            from engines.leaching import annual_gold_oz as _annual_oz
        return _annual_oz(tph, op_hours_day, availability_pct, grade_g_t, recovery_pct)
    except Exception as e:
        logger.error(
            "compute_annual_gold_oz failed (tph=%.1f grade=%.2f rec=%.1f): %s",
            tph, grade_g_t, recovery_pct, e,
        )
        return 0.0


def resolve_process_production(
    pid: str,
    project: Optional[dict] = None,
) -> dict[str, Any]:
    """
    Unified plant production KPIs for economics, dashboard, and DCF.

    ``annual_gold_oz`` is always derived from the same recovery path as
    ``recovery_pct`` via :func:`compute_annual_gold_oz` (never an independent
  fallback constant unless recovery itself is missing).
    """
    if project is None:
        project = qone(
            "SELECT target_tph, gold_grade_g_t, operating_hours_day, availability_pct "
            "FROM projects WHERE id=%s",
            (pid,),
        ) or {}

    breakdown = resolve_recovery_breakdown(pid, project)
    tph = float(project.get("target_tph") or 0)
    grade = float(project.get("gold_grade_g_t") or 0)
    op_h = get_operating_hours_day(project)
    avail = get_availability_pct(pid, project)
    recovery = float(breakdown["plant_recovery_pct"])

    sim_row = qone(
        """
        SELECT results FROM simulation_runs_v2
        WHERE project_id = %s AND results IS NOT NULL
        ORDER BY created_at DESC LIMIT 1
        """,
        (pid,),
    )
    source = breakdown.get("plant_source") or "lims_or_sim_baseline"
    if sim_row and sim_row.get("results"):
        results = sim_row["results"]
        if isinstance(results, str):
            results = json.loads(results)
        overall = results.get("overall") if isinstance(results, dict) else None
        if isinstance(overall, dict):
            if overall.get("feed_tph"):
                tph = float(overall["feed_tph"])
            if overall.get("feed_grade_au"):
                grade = float(overall["feed_grade_au"])
            if overall.get("total_recovery_pct") is not None:
                source = "simulation_runs_v2"
    elif qone(
        "SELECT 1 FROM simulation_params WHERE project_id=%s "
        "AND param_key='overall_recovery_pct' AND param_value IS NOT NULL",
        (pid,),
    ):
        source = "simulation_params.overall_recovery_pct"

    annual_t = compute_annual_t(tph, op_h, avail)
    annual_oz = compute_annual_gold_oz(tph, op_h, avail, grade, recovery)

    return {
        "feed_tph": round(tph, 1),
        "feed_grade_g_t": round(grade, 3),
        "recovery_pct": round(recovery, 2),
        "overall_recovery_pct": round(recovery, 2),
        "annual_tonnes": round(annual_t, 0),
        "annual_gold_oz": round(annual_oz, 0),
        "operating_hours_day": round(op_h, 2),
        "availability_pct": round(avail, 1),
        "production_source": source,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. Daily OPEX rate  (single formula, calendar-day basis for BFR/WC)
# ─────────────────────────────────────────────────────────────────────────────

def compute_daily_opex(annual_opex_usd: float) -> float:
    """
    Convert annual OPEX (USD/year) to daily rate (USD/day) on a calendar basis.

    BFR / working-capital calculations require calendar-day rates because cash
    is tied up over calendar time regardless of plant operating schedule.
    """
    return annual_opex_usd / 365.0 if annual_opex_usd > 0 else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 6. Slurry percent-solids for mill circuit water balance
# ─────────────────────────────────────────────────────────────────────────────

def get_mill_circuit_pct_solids(sim_params: Optional[Dict[str, float]] = None) -> float:
    """
    Return the slurry % solids for the mill-circuit water balance (e.g. 40.0 %).

    Uses 'cil_pct_solids' (the CIL/leach circuit density, typically 45 %) as the
    representative density for the combined grinding + leach circuit.

    NOTE: 'bm_filling' (ball charge %) must NOT be used here — it is the volumetric
    fraction of the mill occupied by grinding media, not the slurry density.
    """
    if sim_params is not None:
        return float(sim_params.get("cil_pct_solids", 45.0))
    return 45.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. Circuit topology flags — single source of truth for all modules
# ─────────────────────────────────────────────────────────────────────────────

def get_circuit_flags(
    pid: str,
    a1_rows: Optional[list] = None,
    b1_rows: Optional[list] = None,
    c2_rows: Optional[list] = None,
    g1_rows: Optional[list] = None,
) -> dict:
    """
    Return the process circuit topology flags used consistently by:
      - flowsheets.py  (block generation)
      - design.py      (conditional DC sections)
      - massbalance.py (stream list)

    Thresholds are identical to those in flowsheets.py auto_generate_flowsheet.

    Returns:
        {
            "has_gravity":   bool,   # Gravity concentration (GRG ≥ 10 %)
            "has_flotation": bool,   # Flotation (S > 2.5 % or flot_rec > 50 %, low Corg)
            "has_hpgr":      bool,   # HPGR (BWI > 16 kWh/t)
            "has_isamill":   bool,   # IsaMill regrind (only when flotation present)
            "avg_grg":       float,  # Average GRG recovery %
            "avg_s":         float,  # Average S_total %
            "avg_bwi":       float,  # Average BWI kWh/t
            "avg_flot":      float,  # Average flotation Au recovery %
            "avg_c_org":     float,  # Average organic carbon %
        }
    """
    try:
        def _load(rows, table, cols="*"):
            if rows is not None:
                return rows
            return qall(f"SELECT {cols} FROM {table} WHERE project_id=%s", (pid,))

        a1 = _load(a1_rows, "lims_a1")
        b1 = _load(b1_rows, "lims_b1")
        c2 = _load(c2_rows, "lims_c2")
        try:
            g1 = _load(g1_rows, "lims_flotation")
        except Exception:
            g1 = []

        def _avg(rows, field, default=0.0):
            vals = [float(r[field]) for r in rows if r.get(field) not in (None, "", 0)]
            return sum(vals) / len(vals) if vals else default

        avg_grg   = _avg(c2, "au_recovery_pct", 0.0)
        avg_s     = _avg(a1, "s_total_pct", 0.0) or _avg(a1, "s_sulfide_pct", 0.0)
        avg_c_org = _avg(a1, "c_organic_pct", 0.0)
        avg_bwi   = _avg(b1, "bwi_kwh_t", 14.0)
        avg_flot  = _avg(g1, "au_recovery_pct", 0.0)

        has_gravity   = avg_grg >= 10.0
        has_flotation = (avg_s > 2.5 or avg_flot > 50.0) and avg_c_org < 0.3
        has_hpgr      = avg_bwi > 16.0
        has_isamill   = has_flotation   # IsaMill only exists as concentrate regrind

        logger.debug(
            "project %s circuit flags: gravity=%s flot=%s hpgr=%s isa=%s "
            "(grg=%.1f s=%.2f bwi=%.1f flot=%.1f corg=%.2f)",
            pid, has_gravity, has_flotation, has_hpgr, has_isamill,
            avg_grg, avg_s, avg_bwi, avg_flot, avg_c_org,
        )
        return {
            "has_gravity":   has_gravity,
            "has_flotation": has_flotation,
            "has_hpgr":      has_hpgr,
            "has_isamill":   has_isamill,
            "avg_grg":       avg_grg,
            "avg_s":         avg_s,
            "avg_bwi":       avg_bwi,
            "avg_flot":      avg_flot,
            "avg_c_org":     avg_c_org,
        }
    except Exception as e:
        logger.error("project %s: failed to compute circuit flags: %s — returning conservative defaults", pid, e)
        return {
            "has_gravity": False, "has_flotation": False, "has_hpgr": False, "has_isamill": False,
            "avg_grg": 0.0, "avg_s": 0.0, "avg_bwi": 14.0, "avg_flot": 0.0, "avg_c_org": 0.0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 8. CIL vs CIP leach circuit selection
# ─────────────────────────────────────────────────────────────────────────────

def select_leach_circuit(
    pid: str,
    a1_rows: Optional[list] = None,
    d1_rows: Optional[list] = None,
    project: Optional[dict] = None,
    sim_params: Optional[Dict[str, float]] = None,
) -> dict:
    """
    Determine whether the project should use CIL or CIP based on:
      - Ore mineralogy (preg-robbing carbon, reactive sulfides)
      - Penalty elements (As, Sb, Cu cyanicide)
      - Leach kinetics (NaCN consumption, recovery variability)
      - Grade level and feed variability

    Priority chain:
      1. Manual override in simulation_params.leach_type (param_value_text = 'CIL' | 'CIP')
      2. LIMS-derived score (see scoring below)
      3. Default CIL (more conservative for uncharacterised ore)

    Scoring: positive → CIL, negative → CIP. Threshold: score ≤ -1 → CIP else CIL.

    Returns:
        {
            "circuit_type": "CIL" | "CIP",
            "score": int | None,        # None when manually overridden
            "reasons": list[str],
            "confidence": "high" | "medium" | "low"
        }
    """
    # 1. Check manual override stored in param_value_text
    override = qone(
        "SELECT param_value_text FROM simulation_params "
        "WHERE project_id=%s AND category='leaching' AND param_key='leach_type'",
        (pid,),
    )
    if override:
        txt = (override.get("param_value_text") or "").strip().upper()
        if txt in ("CIL", "CIP"):
            logger.info("project %s: leach circuit = %s (manual override)", pid, txt)
            return {
                "circuit_type": txt,
                "score": None,
                "reasons": [f"Sélection manuelle depuis les paramètres de simulation : {txt}"],
                "confidence": "high",
            }

    # 2. Stange (1999) CIP/CIL advisor — LIMS-driven
    try:
        from engines.cip_cil_advisor import recommend_cip_cil, _avg as _cip_avg
    except ImportError:
        from .engines.cip_cil_advisor import recommend_cip_cil, _avg as _cip_avg

    rows_a1 = a1_rows if a1_rows is not None else qall(
        "SELECT c_organic_pct, s_sulfide_pct, s_total_pct, as_ppm, sb_ppm, cu_pct "
        "FROM lims_a1 WHERE project_id=%s", (pid,)
    )
    rows_d1 = d1_rows if d1_rows is not None else qall(
        "SELECT au_recovery_pct, nacn_consumption_kg_t, leach_rec_48h_pct, leach_rec_24h_pct "
        "FROM lims_d1 WHERE project_id=%s", (pid,)
    )
    try:
        rows_a3 = qall("SELECT * FROM lims_a3 WHERE project_id=%s", (pid,)) or []
    except Exception:
        rows_a3 = []

    if not rows_a1 and not rows_d1:
        logger.warning(
            "project %s: no LIMS for CIP/CIL selection — defaulting to CIL", pid,
        )

    rec_vals = [
        float(r["au_recovery_pct"])
        for r in rows_d1
        if r.get("au_recovery_pct") not in (None, "", 0)
    ]
    leach_cv = None
    if len(rec_vals) >= 2:
        mean_r = sum(rec_vals) / len(rec_vals)
        std_r = (sum((x - mean_r) ** 2 for x in rec_vals) / len(rec_vals)) ** 0.5
        leach_cv = std_r / mean_r if mean_r > 0 else 0.0

    leach_rec = _cip_avg(rows_d1, "au_recovery_pct")
    if leach_rec is None:
        leach_rec = _cip_avg(rows_d1, "leach_rec_48h_pct") or _cip_avg(rows_d1, "leach_rec_24h_pct")

    p = project if project is not None else qone(
        "SELECT gold_grade_g_t FROM projects WHERE id=%s", (pid,)
    )

    result = recommend_cip_cil(
        c_organic_pct=_cip_avg(rows_a1, "c_organic_pct"),
        s_total_pct=_cip_avg(rows_a1, "s_total_pct"),
        s_sulfide_pct=_cip_avg(rows_a1, "s_sulfide_pct"),
        as_ppm=_cip_avg(rows_a1, "as_ppm"),
        sb_ppm=_cip_avg(rows_a1, "sb_ppm"),
        cu_pct=_cip_avg(rows_a1, "cu_pct"),
        nacn_kg_t=_cip_avg(rows_d1, "nacn_consumption_kg_t"),
        leach_recovery_pct=leach_rec,
        preg_rob_index=_cip_avg(rows_a3, "preg_rob_index") or _cip_avg(rows_a3, "au_preg_rob_pct"),
        gold_grade_g_t=float((p or {}).get("gold_grade_g_t") or 1.5),
        leach_recovery_cv=leach_cv,
        has_lims_a1=bool(rows_a1),
        has_lims_d1=bool(rows_d1),
    )

    logger.info(
        "project %s: leach circuit → %s (score=%s, confidence=%s)",
        pid, result["circuit_type"], result["score"], result["confidence"],
    )
    return {
        "circuit_type": result["circuit_type"],
        "score": result["score"],
        "reasons": result["reasons"],
        "confidence": result["confidence"],
        "reference": result.get("reference"),
        "stange_summary": result.get("stange_summary"),
    }


def get_opex_defaults(sim_params: Optional[Dict[str, float]] = None) -> dict[str, float]:
    """Return centralized OPEX pricing/consumption fallbacks for process models."""
    try:
        sim = sim_params or {}
        return {
            "energy_rate": float(sim.get("energy_rate", SETTINGS.default_energy_rate)),
            "nacn_price": float(sim.get("nacn_price", SETTINGS.default_nacn_price)),
            "cao_price": float(sim.get("cao_price", SETTINGS.default_cao_price)),
            "aux_energy_kwh_t": float(sim.get("opex_aux_energy_kwh_t", SETTINGS.default_aux_energy_kwh_t)),
            "sag_specific_energy": float(sim.get("sag_specific_energy", SETTINGS.default_sag_specific_energy)),
            "bm_specific_energy": float(sim.get("bm_specific_energy", SETTINGS.default_bm_specific_energy)),
            "nacn_kg_t": float(sim.get("nacn_kg_t", SETTINGS.default_nacn_consumption_kg_t)),
            "cao_kg_t": float(sim.get("cao_kg_t", SETTINGS.default_cao_consumption_kg_t)),
            "opex_other_reag_usd_t": float(sim.get("opex_other_reag_usd_t", 0.80)),
            "opex_media_usd_t": float(sim.get("opex_media_usd_t", 2.00)),
            "opex_liners_usd_t": float(sim.get("opex_liners_usd_t", 1.20)),
            "opex_labor_usd_t": float(sim.get("opex_labor_usd_t", 4.20)),
            "opex_maint_usd_t": float(sim.get("opex_maint_usd_t", 2.00)),
            "opex_lab_usd_t": float(sim.get("opex_lab_usd_t", 0.80)),
            "opex_ga_usd_t": float(sim.get("opex_ga_usd_t", 1.50)),
        }
    except (TypeError, ValueError) as e:
        logger.error("Failed to resolve OPEX defaults from sim_params: %s — returning hard-coded defaults", e)
        return {
            "energy_rate": 0.08, "nacn_price": 3.50, "cao_price": 0.12,
            "aux_energy_kwh_t": 5.0, "sag_specific_energy": 8.0, "bm_specific_energy": 7.0,
            "nacn_kg_t": 0.5, "cao_kg_t": 1.2, "opex_other_reag_usd_t": 0.80,
            "opex_media_usd_t": 2.00, "opex_liners_usd_t": 1.20, "opex_labor_usd_t": 4.20,
            "opex_maint_usd_t": 2.00, "opex_lab_usd_t": 0.80, "opex_ga_usd_t": 1.50,
        }
