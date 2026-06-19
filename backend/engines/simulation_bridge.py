"""Bridge compiled circuit templates ↔ ore_to_bullion rigorous simulator."""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mpdpms.simulation_bridge")

GRAVITY_OP_CODES = frozenset({
    "GRAVITE_KNELSON", "GRAVITE_FALCON", "GRAVITE_GEMENI", "GRAVITY", "GRAVITY_CONC",
})

REFRACTORY_OP_CODES = frozenset({"BIOX", "POX", "ROASTING", "UFG"})


def gravity_grg_warning_if_missing(
    op_codes: list[str],
    sim_has_grg: bool,
    dc_has_grg: bool,
) -> list[dict]:
    """Compile-time warning when gravity ops exist without GRG configuration."""
    if not any(op in GRAVITY_OP_CODES for op in op_codes):
        return []
    if sim_has_grg or dc_has_grg:
        return []
    return [{
        "code": "GRAVITY_GRG_MISSING",
        "message": (
            "Circuit gravité présent sans GRG renseigné "
            "(simulation_params.gravity_grg ou critère DC GRAVITE_* / GRG). "
            "La récupération gravimétrique utilisera les défauts (35 %)."
        ),
        "severity": "warning",
    }]


def build_o2b_inputs(
    project_id: str,
    template_id: str,
    enabled_op_codes: list[str],
    econ: dict[str, float],
    sim_params: dict[str, float],
    lims_data: dict | None = None,
) -> tuple[Any, Any]:
    """Build FeedParameters + CircuitConfig aligned with a compiled template."""
    try:
        from .ore_to_bullion.models import FeedParameters, CircuitConfig
        from .gravity_model import resolve_gravity_params
    except ImportError:
        from engines.ore_to_bullion.models import FeedParameters, CircuitConfig
        from engines.gravity_model import resolve_gravity_params

    ops = set(enabled_op_codes)
    lims = lims_data or {}
    gp = resolve_gravity_params({**sim_params, **lims})

    feed = FeedParameters(
        feed_rate_tph=float(econ.get("target_tph") or sim_params.get("feed_tph") or 1500.0),
        gold_grade_g_t=float(econ.get("gold_grade_g_t") or sim_params.get("head_grade_au") or 1.5),
        ore_sg=float(sim_params.get("ore_sg") or 2.75),
        bwi_kwh_t=float(sim_params.get("feed_bwi") or sim_params.get("bm_bwi") or 14.0),
        cwi_kwh_t=float(sim_params.get("cwi_kwh_t") or 12.0),
        axb=float(sim_params.get("axb") or 45.0),
        target_recovery_pct=float(sim_params.get("cil_rec_au") or sim_params.get("leaching_recovery_pct") or 92.0),
        availability_pct=float(econ.get("availability_pct") or sim_params.get("availability_pct") or 92.0),
        operating_hours_day=float(econ.get("operating_hours_day") or sim_params.get("operating_hours_day") or 22.1),
    )

    grinding_type = "hpgr_ball"
    if "SAG_MILL" in ops and "HPGR" not in ops:
        grinding_type = "sag_ball"
    elif "BALL_MILL" in ops and "HPGR" not in ops and "SAG_MILL" not in ops:
        grinding_type = "ball_only"
    if any(o.startswith("VERTIMILL") for o in ops):
        grinding_type = "hpgr_ball_verti" if "HPGR" in ops else "sag_ball_verti"

    leaching_type = "cil"
    if "CIP" in ops and "CIL" not in ops:
        leaching_type = "cip"
    elif "LEACH_CUVES" in ops and "CIL" not in ops and "CIP" not in ops:
        leaching_type = "leach_only"

    elution_type = "aarl"
    if "ELUTION_ZADRA" in ops and "ELUTION_AARL" not in ops:
        elution_type = "zadra"

    detox_process = "inco"
    if "DETOX_CARO" in ops:
        detox_process = "caro"
    elif "DETOX_PEROXIDE" in ops:
        detox_process = "peroxide"

    config = CircuitConfig(
        crushing_enabled=bool(ops & {"GIRATOIRE", "CONE", "JAW_CRUSHER", "CONE_CRUSHER"}),
        grinding_type=grinding_type,
        grinding_target_p80_um=float(sim_params.get("grind_p80") or sim_params.get("bm_p80") or 75.0),
        gravity_enabled=bool(ops & GRAVITY_OP_CODES),
        grg_pct=gp.grg_pct,
        gravity_slip_pct=gp.gravity_slip_pct,
        knelson_unit_recovery_pct=gp.knelson_unit_recovery_pct,
        ilr_recovery_pct=gp.ilr_recovery_pct,
        gravity_mass_pull_pct=gp.gravity_mass_pull_pct,
        flotation_enabled=bool(any(o.startswith("FLOTATION") for o in ops)),
        leaching_type=leaching_type,
        leaching_srt_h=float(sim_params.get("cil_time") or sim_params.get("leaching_srt_h") or 24.0),
        leaching_recovery_pct=float(sim_params.get("cil_rec_au") or sim_params.get("leaching_recovery_pct") or 92.0),
        leaching_nacn_kg_t=float(sim_params.get("nacn_consumption") or sim_params.get("leaching_nacn_kg_t") or 0.5),
        leaching_cao_kg_t=float(sim_params.get("lime_consumption") or sim_params.get("leaching_cao_kg_t") or 1.5),
        elution_type=elution_type,
        detox_process=detox_process,
        energy_rate_usd_kwh=float(sim_params.get("electricity_rate") or econ.get("electricity_rate") or 0.08),
    )
    return feed, config


def compare_rigorous_with_o2b(
    rigorous: dict[str, Any],
    o2b_result: Any,
) -> dict[str, Any]:
    """Side-by-side KPI comparison for process_simulator vs ore_to_bullion."""
    rig_overall = rigorous.get("overall") or {}
    o2b_recovery = float(getattr(o2b_result, "overall_recovery_pct", 0) or 0)
    o2b_oz = float(getattr(o2b_result, "annual_gold_oz", 0) or 0)
    o2b_energy = float(getattr(o2b_result, "total_energy_kwh_t", 0) or 0)

    rig_recovery = float(rig_overall.get("total_recovery_pct") or 0)
    rig_oz = float(rig_overall.get("annual_gold_oz") or 0)
    rig_energy = float(rig_overall.get("total_energy_kwh_t") or 0)

    def _delta(a: float, b: float) -> float | None:
        if b == 0:
            return None
        return round((a - b) / b * 100.0, 1)

    return {
        "recovery_pct": {
            "process_simulator": rig_recovery,
            "ore_to_bullion": o2b_recovery,
            "delta_pct": _delta(rig_recovery, o2b_recovery),
        },
        "annual_gold_oz": {
            "process_simulator": rig_oz,
            "ore_to_bullion": o2b_oz,
            "delta_pct": _delta(rig_oz, o2b_oz),
        },
        "energy_kwh_t": {
            "process_simulator": rig_energy,
            "ore_to_bullion": o2b_energy,
            "delta_pct": _delta(rig_energy, o2b_energy),
        },
        "refractory_ops_in_template": list(
            REFRACTORY_OP_CODES.intersection(set(rigorous.get("ops_simulated") or []))
        ),
    }


def run_rigorous_o2b_comparison(
    project_id: str,
    template_id: str,
    params_override: dict | None,
    cursor,
) -> dict[str, Any]:
    """Run process_simulator + ore_to_bullion on the same template feed."""
    try:
        from .process_simulator import (
            simulate_circuit,
            _load_enabled_operations,
            _load_project_economics,
            _load_simulation_param_overrides,
        )
        from .ore_to_bullion import simulate_ore_to_bullion
        from .dc_generator import get_lims_summary
    except ImportError:
        from engines.process_simulator import (
            simulate_circuit,
            _load_enabled_operations,
            _load_project_economics,
            _load_simulation_param_overrides,
        )
        from engines.ore_to_bullion import simulate_ore_to_bullion
        from engines.dc_generator import get_lims_summary

    override = _load_simulation_param_overrides(project_id, cursor, params_override)
    operations = _load_enabled_operations(template_id, cursor)
    op_codes = [o["op_code"] for o in operations]
    econ = _load_project_economics(project_id, cursor)
    lims = get_lims_summary(project_id, cursor) if cursor else {}

    sim_rows: dict[str, float] = {}
    if cursor:
        cursor.execute(
            "SELECT param_key, param_value FROM simulation_params "
            "WHERE project_id = %s AND param_value IS NOT NULL",
            (project_id,),
        )
        sim_rows = {
            r["param_key"]: float(r["param_value"])
            for r in cursor.fetchall()
            if r.get("param_key")
        }

    rigorous = simulate_circuit(project_id, template_id, params_override=override, cursor=cursor)
    rigorous["ops_simulated"] = op_codes

    feed, config = build_o2b_inputs(project_id, template_id, op_codes, econ, sim_rows, lims)
    o2b = simulate_ore_to_bullion(feed, config, overrides=override)

    comparison = compare_rigorous_with_o2b(rigorous, o2b)
    return {
        "rigorous": rigorous,
        "ore_to_bullion": o2b.model_dump(),
        "comparison": comparison,
        "o2b_feed_params": feed.model_dump(),
        "o2b_circuit_config": config.model_dump(),
    }
