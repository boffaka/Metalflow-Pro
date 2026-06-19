"""
MetalFlow Pro — Scenario Advisor Engine.

Generates simulation scenario suggestions based on LIMS data, project economics,
simulation run history, blockmodel variability, design consistency, equipment /
cost signals, and géomet domain differentiation.

Seven rule levels (Plan 2 extension):
  1. LIMS-driven (ore characterisation → circuit config)
  2. Economic (energy / AISC / recovery thresholds)
  3. History (run patterns → next-step recommendations)
  4. Blockmodel (variability / oxide share / grade × tonnage)
  5. Design consistency (DC ↔ active op conflicts, MB convergence, P80 target)
  6. Equipment / costs (utilisation, CAPEX vs revenue, reagent OPEX)
  7. Géomet domains (per-domain dedicated scenario)

Tunable thresholds live in ``backend/industry_defaults.yaml`` under the
``advisor_thresholds`` section (Plan 2).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("mpdpms.scenario_advisor")

try:
    from .. import config as _app_config
except ImportError:  # pragma: no cover
    import config as _app_config

try:
    from .db import qall, qone
    from .dc_generator import get_lims_summary
except ImportError:
    try:
        from db import qall, qone
        from engines.dc_generator import get_lims_summary
    except ImportError:
        qall = qone = get_lims_summary = None  # type: ignore[assignment]

try:
    from db import execute as db_execute
except ImportError:
    try:
        from .db import execute as db_execute
    except ImportError:
        db_execute = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Thresholds loader — reads advisor_thresholds section from industry_defaults.yaml.
# Falls back to hard-coded defaults when the file cannot be read (CI env,
# packaged deployment, partial install).
# ---------------------------------------------------------------------------

_DEFAULT_ADVISOR_THRESHOLDS = {
    "bwi_cv_high": 0.30,
    "oxide_share_dual_circuit_pct": 20.0,
    "low_grade_heap_g_t": 0.6,
    "heap_tonnage_ktpd": 30.0,
    "p80_target_tolerance_pct": 10.0,
    "equipment_utilization_low_pct": 70.0,
    "high_capex_revenue_ratio": 0.8,
    "high_reagent_opex_usd_t": 3.0,
}


def _load_advisor_thresholds() -> dict:
    """Return {threshold_key: numeric_value} merging YAML overrides over defaults."""
    out = dict(_DEFAULT_ADVISOR_THRESHOLDS)
    yaml_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "industry_defaults.yaml",
    )
    try:
        import yaml  # type: ignore
        with open(yaml_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        section = (data.get("advisor_thresholds") or {}) if isinstance(data, dict) else {}
        for key, entry in section.items():
            if isinstance(entry, dict) and "value" in entry:
                try:
                    out[key] = float(entry["value"])
                except (TypeError, ValueError):
                    pass
    except Exception:
        # Non-fatal: stick with defaults
        logger.debug("advisor_thresholds: using defaults (yaml load failed)")
    return out


_THRESHOLDS = _load_advisor_thresholds()


# ---------------------------------------------------------------------------
# Level 1 — LIMS rules
# ---------------------------------------------------------------------------

def _lims_rules(lims: dict, active_ops: set[str]) -> list[dict]:
    """Generate suggestions from LIMS ore characterisation data."""
    suggestions: list[dict] = []

    # BWi > 16 and SAG active but no HPGR → suggest HPGR swap
    bwi = lims.get("b1.bwi_kwh_t")
    has_sag = any(op.startswith("SAG_MILL") for op in active_ops)
    has_hpgr = any(op.startswith("HPGR") for op in active_ops)
    if bwi is not None and bwi > 16 and has_sag and not has_hpgr:
        confidence = "high" if bwi > 18 else "medium"
        suggestions.append({
            "id": "auto_hpgr_swap",
            "title": "Replace SAG with HPGR for hard ore",
            "category": "comminution",
            "confidence": confidence,
            "confidence_score": 0.9 if confidence == "high" else 0.7,
            "reasoning": f"BWi={bwi:.1f} kWh/t exceeds 16; HPGR is more energy-efficient for hard ores.",
            "lims_basis": {"b1.bwi_kwh_t": bwi},
            "ops_to_add": ["HPGR"],
            "ops_to_remove": ["SAG_MILL"],
            "params_override": {},
            "estimated_impact": 8 if confidence == "high" else 5,
        })

    # GRG > 20% and no gravity circuit
    grg = lims.get("c2.grg_rec_pct")
    has_gravity = any(op.startswith("GRAVITY_") for op in active_ops)
    if grg is not None and grg > 20 and not has_gravity:
        confidence = "high" if grg > 40 else "medium"
        suggestions.append({
            "id": "auto_add_gravity",
            "title": "Add gravity recovery circuit",
            "category": "gravity",
            "confidence": confidence,
            "confidence_score": 0.9 if confidence == "high" else 0.7,
            "reasoning": f"GRG={grg:.1f}% indicates significant gravity-recoverable gold.",
            "lims_basis": {"c2.grg_rec_pct": grg},
            "ops_to_add": ["GRAVITY_CONCENTRATOR"],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 7 if confidence == "high" else 4,
        })

    # S_sulfide > 2% and no flotation
    s_sulf = lims.get("a1.s_sulfide_pct")
    has_flotation = any(op.startswith("FLOTATION_") for op in active_ops)
    if s_sulf is not None and s_sulf > 2 and not has_flotation:
        suggestions.append({
            "id": "auto_add_flotation",
            "title": "Add flotation for sulfide ore",
            "category": "flotation",
            "confidence": "high",
            "confidence_score": 0.9,
            "reasoning": f"S_sulfide={s_sulf:.1f}% exceeds 2%; flotation improves sulfide recovery.",
            "lims_basis": {"a1.s_sulfide_pct": s_sulf},
            "ops_to_add": ["FLOTATION_ROUGHER"],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 8,
        })

    # CIL active + clean ore → suggest CIP
    has_cil = any(op.startswith("CIL") or op == "CIL" for op in active_ops)
    c_org = lims.get("a1.c_organic_pct")
    nacn = lims.get("d1.nacn_consumption_kg_t")
    if has_cil:
        clean = (
            (c_org is not None and c_org < 0.05)
            and (s_sulf is None or s_sulf < 0.5)
            and (nacn is None or nacn < 0.8)
        )
        if clean:
            suggestions.append({
                "id": "auto_cip_vs_cil",
                "title": "Consider CIP instead of CIL for clean ore",
                "category": "leaching",
                "confidence": "medium",
                "confidence_score": 0.7,
                "reasoning": "Low organics, sulfide, and NaCN consumption indicate CIP may be more cost-effective.",
                "lims_basis": {"a1.c_organic_pct": c_org, "a1.s_sulfide_pct": s_sulf, "d1.nacn_consumption_kg_t": nacn},
                "ops_to_add": ["CIP"],
                "ops_to_remove": ["CIL"],
                "params_override": {},
                "estimated_impact": 4,
            })

    # NaCN > 2 kg/t
    if nacn is not None and nacn > 2:
        suggestions.append({
            "id": "auto_pretreat_high_nacn",
            "title": "Add pre-treatment for high cyanide consumption",
            "category": "leaching",
            "confidence": "medium",
            "confidence_score": 0.7,
            "reasoning": f"NaCN consumption={nacn:.1f} kg/t exceeds 2 kg/t; pre-aeration or pre-oxidation may reduce reagent cost.",
            "lims_basis": {"d1.nacn_consumption_kg_t": nacn},
            "ops_to_add": ["PRE_OXIDATION"],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 5,
        })

    # As > 1000 ppm and no detox
    as_ppm = lims.get("a1.as_ppm")
    has_detox = any(op.startswith("DETOX_") for op in active_ops)
    if as_ppm is not None and as_ppm > 1000 and not has_detox:
        suggestions.append({
            "id": "auto_add_detox",
            "title": "Add detoxification circuit for arsenic",
            "category": "environmental",
            "confidence": "high",
            "confidence_score": 0.9,
            "reasoning": f"As={as_ppm:.0f} ppm exceeds 1000 ppm; detox is required for tailings compliance.",
            "lims_basis": {"a1.as_ppm": as_ppm},
            "ops_to_add": ["DETOX_CARO"],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 9,
        })

    return suggestions


# ---------------------------------------------------------------------------
# Level 2 — Economic rules
# ---------------------------------------------------------------------------

def _economic_rules(project: dict, last_run: dict | None) -> list[dict]:
    """Generate suggestions from project economics and last simulation run."""
    suggestions: list[dict] = []

    results = (last_run or {}).get("results") or {}
    sim_params = (last_run or {}).get("simulation_params") or {}

    energy = results.get("energy_kwh_t") or sim_params.get("energy_kwh_t")
    au_price = (project.get("economics") or {}).get("au_price_usd_oz") or results.get("au_price_usd_oz")
    aisc = results.get("aisc_usd_oz")
    recovery = results.get("recovery_pct") or sim_params.get("recovery_pct")

    if energy is not None and au_price is not None and energy > 25 and au_price < _app_config.LOW_GOLD_PRICE_ALERT_THR_USD_OZ:
        suggestions.append({
            "id": "auto_reduce_energy",
            "title": "Reduce energy consumption",
            "category": "energy",
            "confidence": "high",
            "confidence_score": 0.85,
            "reasoning": f"Energy={energy:.1f} kWh/t at Au price=${au_price:.0f}/oz makes energy savings critical.",
            "lims_basis": {},
            "ops_to_add": [],
            "ops_to_remove": [],
            "params_override": {"target_energy_kwh_t": 20},
            "estimated_impact": 7,
        })

    if aisc is not None and aisc > 1200:
        suggestions.append({
            "id": "auto_optimize_opex",
            "title": "Optimise operating costs (AISC > $1200/oz)",
            "category": "economics",
            "confidence": "high",
            "confidence_score": 0.85,
            "reasoning": f"AISC=${aisc:.0f}/oz exceeds $1200/oz threshold.",
            "lims_basis": {},
            "ops_to_add": [],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 8,
        })

    if recovery is not None and recovery < 85:
        suggestions.append({
            "id": "auto_improve_recovery",
            "title": "Improve gold recovery (below 85%)",
            "category": "recovery",
            "confidence": "high",
            "confidence_score": 0.85,
            "reasoning": f"Recovery={recovery:.1f}% is below the 85% threshold.",
            "lims_basis": {},
            "ops_to_add": [],
            "ops_to_remove": [],
            "params_override": {"target_recovery_pct": 90},
            "estimated_impact": 9,
        })

    return suggestions


# ---------------------------------------------------------------------------
# Level 3 — History rules
# ---------------------------------------------------------------------------

def _history_rules(runs: list[dict], sensitivity_data: dict | None = None) -> list[dict]:
    """Generate suggestions from simulation run history."""
    suggestions: list[dict] = []

    rigorous_count = sum(1 for r in runs if (r.get("run_type") or "").startswith("rigorous"))
    has_mc = any((r.get("run_type") or "") == "monte_carlo_lom" for r in runs)

    if rigorous_count >= 5 and not has_mc:
        suggestions.append({
            "id": "auto_run_monte_carlo",
            "title": "Run Monte Carlo LOM simulation",
            "category": "analysis",
            "confidence": "medium",
            "confidence_score": 0.7,
            "reasoning": f"{rigorous_count} rigorous runs completed; Monte Carlo analysis will quantify risk.",
            "lims_basis": {},
            "ops_to_add": [],
            "ops_to_remove": [],
            "params_override": {"run_type": "monte_carlo_lom", "iterations": 1000},
            "estimated_impact": 6,
        })

    # Sensitivity-driven optimisation
    if sensitivity_data:
        for param, impact in sensitivity_data.items():
            if isinstance(impact, (int, float)) and impact > 2:
                suggestions.append({
                    "id": f"auto_optimize_{param}",
                    "title": f"Optimise {param} (sensitivity impact {impact:.1f}%)",
                    "category": "optimisation",
                    "confidence": "high",
                    "confidence_score": 0.85,
                    "reasoning": f"Sensitivity analysis shows {param} has >{impact:.1f}% impact on NPV.",
                    "lims_basis": {},
                    "ops_to_add": [],
                    "ops_to_remove": [],
                    "params_override": {f"optimize_{param}": True},
                    "estimated_impact": 7,
                })

    return suggestions


# ---------------------------------------------------------------------------
# Level 4 — Blockmodel rules (variabilité minerai / ore variability)
# ---------------------------------------------------------------------------

def _blockmodel_rules(blockmodel_stats: dict | None, active_ops: set[str]) -> list[dict]:
    """Generate suggestions from blockmodel statistics.

    Expected keys (all optional, any missing → rule silently skipped):
      - ``bwi_cv``              (float) coefficient of variation of Bond Work Index
      - ``ox_share_pct``        (float) oxide share of LOM tonnage
      - ``avg_grade_g_t``       (float) average Au grade
      - ``tonnage_ktpd``        (float) planned production tonnage

    Graceful degradation: None / empty dict → returns [].
    """
    if not blockmodel_stats:
        return []

    suggestions: list[dict] = []
    bwi_cv = blockmodel_stats.get("bwi_cv")
    ox_share = blockmodel_stats.get("ox_share_pct")
    grade = blockmodel_stats.get("avg_grade_g_t")
    tonnage = blockmodel_stats.get("tonnage_ktpd")

    cv_thresh = _THRESHOLDS["bwi_cv_high"]
    ox_thresh = _THRESHOLDS["oxide_share_dual_circuit_pct"]
    grade_thresh = _THRESHOLDS["low_grade_heap_g_t"]
    tonnage_thresh = _THRESHOLDS["heap_tonnage_ktpd"]

    has_hpgr = any(op.startswith("HPGR") for op in active_ops)
    has_heap = any("HEAP" in op for op in active_ops)

    # Rule 4a — high CV(BWi) favours HPGR
    if bwi_cv is not None and bwi_cv > cv_thresh and not has_hpgr:
        confidence = "high" if bwi_cv > cv_thresh * 1.5 else "medium"
        suggestions.append({
            "id": "auto_blockmodel_hpgr_variability",
            "title": "Adopt HPGR to tolerate ore hardness variability",
            "category": "comminution",
            "confidence": confidence,
            "confidence_score": 0.85 if confidence == "high" else 0.7,
            "reasoning": (
                f"CV(BWi)={bwi_cv:.2f} exceeds {cv_thresh:.2f}; HPGR copes "
                "better with ore-hardness variability than SAG."
            ),
            "blockmodel_basis": {"bwi_cv": bwi_cv},
            "ops_to_add": ["HPGR"],
            "ops_to_remove": [op for op in active_ops if op.startswith("SAG_MILL")],
            "params_override": {},
            "estimated_impact": 7 if confidence == "high" else 5,
        })

    # Rule 4b — high oxide share favours dual oxide/sulfide circuit
    if ox_share is not None and ox_share > ox_thresh:
        suggestions.append({
            "id": "auto_blockmodel_dual_circuit",
            "title": "Consider dual oxide/sulfide circuit",
            "category": "holistic",
            "confidence": "medium",
            "confidence_score": 0.7,
            "reasoning": (
                f"Oxide share={ox_share:.1f}% exceeds {ox_thresh:.1f}%; a dual "
                "circuit keeps oxide recovery high while sulfide flotation runs"
                " in parallel."
            ),
            "blockmodel_basis": {"ox_share_pct": ox_share},
            "ops_to_add": ["HEAP_LEACH", "FLOTATION_ROUGHER"],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 6,
        })

    # Rule 4c — low grade + high tonnage → heap leach option
    if (
        grade is not None and grade < grade_thresh
        and tonnage is not None and tonnage > tonnage_thresh
        and not has_heap
    ):
        suggestions.append({
            "id": "auto_blockmodel_heap_leach",
            "title": "Evaluate heap leach for low-grade / high-tonnage ore",
            "category": "leaching",
            "confidence": "medium",
            "confidence_score": 0.7,
            "reasoning": (
                f"Average grade={grade:.2f} g/t < {grade_thresh:.2f} and "
                f"tonnage={tonnage:.0f} ktpd > {tonnage_thresh:.0f}; heap leach"
                " may outperform CIL economics."
            ),
            "blockmodel_basis": {
                "avg_grade_g_t": grade,
                "tonnage_ktpd": tonnage,
            },
            "ops_to_add": ["HEAP_LEACH"],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 7,
        })

    return suggestions


# ---------------------------------------------------------------------------
# Level 5 — Design consistency / mass balance
# ---------------------------------------------------------------------------

def _design_consistency_rules(
    dc: list[dict] | None,
    mb: dict | None,
    active_ops: set[str],
) -> list[dict]:
    """Generate suggestions from design criteria (v2) + mass balance state.

    Inputs:
      - ``dc`` : list of ``design_criteria_v2`` rows (at minimum ``op_code``)
      - ``mb`` : dict with keys ``converged`` (bool), ``residual`` (float, optional),
                 ``p80_target_um`` / ``p80_actual_um`` (optional)

    Graceful degradation: returns ``[]`` when both are None / empty and active_ops is empty.
    """
    suggestions: list[dict] = []
    dc = dc or []
    mb = mb or {}

    # Rule 5a — DC references an op that is NOT in active_ops
    if dc and active_ops:
        dc_ops = {row.get("op_code") for row in dc if isinstance(row, dict)}
        conflicts = dc_ops - active_ops
        conflicts.discard(None)
        if conflicts:
            first = sorted(conflicts)[0]
            suggestions.append({
                "id": "auto_design_dc_op_conflict",
                "title": f"DC references op '{first}' not present in active circuit",
                "category": "holistic",
                "confidence": "high",
                "confidence_score": 0.85,
                "reasoning": (
                    f"Design criteria were defined for {sorted(conflicts)} but "
                    "none of those ops are enabled in the current template."
                ),
                "design_basis": {"conflicting_ops": sorted(conflicts)},
                "ops_to_add": sorted(conflicts),
                "ops_to_remove": [],
                "params_override": {},
                "estimated_impact": 7,
            })

    # Rule 5b — Mass balance unconverged
    if mb.get("converged") is False:
        residual = mb.get("residual")
        suggestions.append({
            "id": "auto_design_mb_unconverged",
            "title": "Mass balance unconverged — review missing operations",
            "category": "holistic",
            "confidence": "high",
            "confidence_score": 0.9,
            "reasoning": (
                "Mass balance residual exceeds tolerance"
                + (f" ({residual:.3f})" if isinstance(residual, (int, float)) else "")
                + "; a missing reconciliation op or stream may be the cause."
            ),
            "design_basis": {"mb_converged": False, "residual": residual},
            "ops_to_add": [],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 8,
        })

    # Rule 5c — P80 target not met (outside tolerance)
    target = mb.get("p80_target_um")
    actual = mb.get("p80_actual_um")
    tol_pct = _THRESHOLDS["p80_target_tolerance_pct"]
    if (
        isinstance(target, (int, float)) and target > 0
        and isinstance(actual, (int, float))
    ):
        deviation_pct = abs(actual - target) / target * 100.0
        if deviation_pct > tol_pct:
            suggestions.append({
                "id": "auto_design_p80_unmet",
                "title": f"P80 target not met (actual {actual:.0f} µm vs target {target:.0f} µm)",
                "category": "comminution",
                "confidence": "high",
                "confidence_score": 0.85,
                "reasoning": (
                    f"P80 deviation {deviation_pct:.1f}% > tolerance {tol_pct:.1f}%;"
                    " add regrind capacity or adjust ball-mill sizing."
                ),
                "design_basis": {
                    "p80_target_um": target,
                    "p80_actual_um": actual,
                    "deviation_pct": deviation_pct,
                },
                "ops_to_add": ["REGRIND_MILL"],
                "ops_to_remove": [],
                "params_override": {"target_p80_um": target},
                "estimated_impact": 6,
            })

    return suggestions


# ---------------------------------------------------------------------------
# Level 6 — Equipment / cost signals
# ---------------------------------------------------------------------------

def _equipment_economic_rules(
    equipment: list[dict] | None,
    costs: dict | None,
    active_ops: set[str],
) -> list[dict]:
    """Generate suggestions from equipment sizing + cost signals.

    Inputs:
      - ``equipment`` : list of rows with ``op_code`` and ``utilization_pct`` (0–100)
      - ``costs`` : dict with ``capex_usd_m``, ``revenue_usd_m_y``, ``reagent_opex_usd_t``

    Graceful degradation: None / empty → [].
    """
    suggestions: list[dict] = []
    equipment = equipment or []
    costs = costs or {}

    util_thresh = _THRESHOLDS["equipment_utilization_low_pct"]
    capex_ratio_thresh = _THRESHOLDS["high_capex_revenue_ratio"]
    reagent_thresh = _THRESHOLDS["high_reagent_opex_usd_t"]

    # Rule 6a — under-utilised equipment → debottleneck elsewhere
    for eq in equipment:
        if not isinstance(eq, dict):
            continue
        op = eq.get("op_code")
        util = eq.get("utilization_pct")
        if op and isinstance(util, (int, float)) and util < util_thresh:
            suggestions.append({
                "id": f"auto_equipment_debottleneck_{op}",
                "title": f"{op} under-utilised ({util:.0f}%) — debottleneck upstream",
                "category": "holistic",
                "confidence": "medium",
                "confidence_score": 0.7,
                "reasoning": (
                    f"{op} utilization {util:.0f}% < {util_thresh:.0f}%; rather "
                    "than enlarge equipment, debottleneck upstream throughput."
                ),
                "equipment_basis": {"op_code": op, "utilization_pct": util},
                "ops_to_add": [],
                "ops_to_remove": [],
                "params_override": {},
                "estimated_impact": 5,
            })

    # Rule 6b — high CAPEX vs revenue → reduce CAPEX scenario
    capex = costs.get("capex_usd_m")
    revenue = costs.get("revenue_usd_m_y")
    if (
        isinstance(capex, (int, float)) and isinstance(revenue, (int, float))
        and revenue > 0 and capex / revenue > capex_ratio_thresh
    ):
        suggestions.append({
            "id": "auto_equipment_reduce_capex",
            "title": "Explore reduced-CAPEX variant of the circuit",
            "category": "economics",
            "confidence": "high",
            "confidence_score": 0.8,
            "reasoning": (
                f"CAPEX/Revenue ratio {capex/revenue:.2f} exceeds "
                f"{capex_ratio_thresh:.2f}; a simpler flowsheet may improve NPV."
            ),
            "equipment_basis": {
                "capex_usd_m": capex,
                "revenue_usd_m_y": revenue,
                "ratio": capex / revenue,
            },
            "ops_to_add": [],
            "ops_to_remove": [],
            "params_override": {"capex_reduction_target_pct": 20},
            "estimated_impact": 8,
        })

    # Rule 6c — high reagent OPEX → optimise leach / flotation reagents
    reagent = costs.get("reagent_opex_usd_t")
    if isinstance(reagent, (int, float)) and reagent > reagent_thresh:
        has_cil_or_cip = any(op in active_ops for op in ("CIL", "CIP", "HEAP_LEACH"))
        suggestions.append({
            "id": "auto_equipment_optimize_reagents",
            "title": "Optimise reagent consumption (NaCN / collectors)",
            "category": "leaching" if has_cil_or_cip else "flotation",
            "confidence": "medium",
            "confidence_score": 0.75,
            "reasoning": (
                f"Reagent OPEX ${reagent:.1f}/t exceeds ${reagent_thresh:.1f}/t; "
                "consider pre-leach flotation or NaCN optimisation."
            ),
            "equipment_basis": {"reagent_opex_usd_t": reagent},
            "ops_to_add": ["FLOTATION_ROUGHER"] if not has_cil_or_cip else [],
            "ops_to_remove": [],
            "params_override": {"target_reagent_usd_t": reagent_thresh},
            "estimated_impact": 6,
        })

    return suggestions


# ---------------------------------------------------------------------------
# Level 7 — Géomet / domain-specific scenarios
# ---------------------------------------------------------------------------

def _geomet_domain_rules(
    campaigns: list[dict] | None,
    domains: list[dict] | None,
    active_ops: set[str],
) -> list[dict]:
    """Generate one dedicated scenario per géomet domain.

    ``domains`` is a list of {name, bwi_kwh_t, grg_rec_pct, s_sulfide_pct, ...}.
    ``campaigns`` is currently used only to annotate confidence (more data → higher).

    Graceful degradation: absent domains → []. No error when campaigns is None.
    """
    if not domains:
        return []

    suggestions: list[dict] = []
    campaigns = campaigns or []
    for d in domains:
        if not isinstance(d, dict):
            continue
        name = d.get("name")
        if not name:
            continue

        bwi = d.get("bwi_kwh_t")
        grg = d.get("grg_rec_pct")
        s_sulf = d.get("s_sulfide_pct")

        ops_to_add: list[str] = []
        ops_to_remove: list[str] = []
        title_bits: list[str] = []

        # Branch selection per domain characteristics
        if isinstance(bwi, (int, float)) and bwi < 13:
            title_bits.append("gravity-first, ball-mill only")
            ops_to_add.append("BALL_MILL")
            if isinstance(grg, (int, float)) and grg > 20:
                ops_to_add.append("GRAVITY_CONCENTRATOR")
        elif isinstance(bwi, (int, float)) and bwi > 17:
            title_bits.append("HPGR + ball mill")
            ops_to_add.append("HPGR")
            ops_to_add.append("BALL_MILL")

        if isinstance(s_sulf, (int, float)) and s_sulf > 2:
            title_bits.append("flotation + cyanuration")
            if "FLOTATION_ROUGHER" not in ops_to_add:
                ops_to_add.append("FLOTATION_ROUGHER")
            ops_to_add.append("CIL")

        if not ops_to_add:
            # Default safe choice
            ops_to_add.append("CIL")
            title_bits.append("standard CIL")

        # Confidence boosted by campaign count for this domain
        domain_campaigns = [
            c for c in campaigns
            if isinstance(c, dict) and c.get("domain") == name
        ]
        confidence = "high" if len(domain_campaigns) >= 2 else "medium"
        confidence_score = 0.8 if confidence == "high" else 0.65

        suggestions.append({
            "id": f"auto_geomet_domain_{name}",
            "title": f"Dedicated scenario for domain {name}: " + " + ".join(title_bits),
            "category": "holistic",
            "confidence": confidence,
            "confidence_score": confidence_score,
            "reasoning": (
                f"Domain '{name}' has distinct characteristics (BWi={bwi}, "
                f"GRG={grg}%, S_sulfide={s_sulf}%); a dedicated circuit can "
                "improve recovery vs a one-size-fits-all flowsheet."
            ),
            "geomet_basis": {
                "domain": name,
                "bwi_kwh_t": bwi,
                "grg_rec_pct": grg,
                "s_sulfide_pct": s_sulf,
                "campaigns_in_domain": len(domain_campaigns),
            },
            "ops_to_add": ops_to_add,
            "ops_to_remove": ops_to_remove,
            "params_override": {"geomet_domain": name},
            "estimated_impact": 6,
        })

    return suggestions


# ---------------------------------------------------------------------------
# Merge across levels — keeps the richest "basis" payload on duplicate ids.
# ---------------------------------------------------------------------------

_BASIS_KEYS = (
    "lims_basis", "blockmodel_basis", "design_basis",
    "equipment_basis", "geomet_basis", "history_basis",
)


def _basis_richness(s: dict) -> int:
    """Number of non-empty basis dicts on a suggestion — used as tie-break."""
    return sum(1 for k in _BASIS_KEYS if s.get(k))


def _merge_all_levels(level_outputs: list[list[dict]], tested_ids: set[str]) -> list[dict]:
    """Flatten + deduplicate multiple level-output lists, keeping the suggestion
    with the richest basis payload when two share the same id."""
    by_id: dict[str, dict] = {}
    for lvl in level_outputs:
        if not lvl:
            continue
        for s in lvl:
            sid = s.get("id")
            if not sid:
                continue
            if sid not in by_id:
                by_id[sid] = dict(s)
                continue
            existing = by_id[sid]
            if _basis_richness(s) > _basis_richness(existing):
                # Keep the new one (richer basis); carry over fields missing on the new one
                merged = dict(existing)
                merged.update(s)
                by_id[sid] = merged
            else:
                # Enrich existing with any basis fields it did not have
                for k in _BASIS_KEYS:
                    if k in s and k not in existing:
                        existing[k] = s[k]

    suggestions = list(by_id.values())
    for s in suggestions:
        s["already_tested"] = s.get("id") in tested_ids
    return suggestions


# ---------------------------------------------------------------------------
# De-duplication & prioritisation
# ---------------------------------------------------------------------------

def _deduplicate(suggestions: list[dict], tested_ids: set[str]) -> list[dict]:
    """Mark suggestions that have already been tested."""
    seen: set[str] = set()
    deduped: list[dict] = []
    for s in suggestions:
        sid = s["id"]
        if sid in seen:
            continue
        seen.add(sid)
        s["already_tested"] = sid in tested_ids
        deduped.append(s)
    return deduped


def _prioritize(suggestions: list[dict]) -> list[dict]:
    """Sort suggestions by composite score (highest first) and assign priority numbers."""
    def _score(s: dict) -> float:
        cs = s.get("confidence_score", 0.5)
        impact = s.get("estimated_impact", 5)
        tested_penalty = 0.5 if s.get("already_tested") else 1.0
        # Small tiebreaker from id hash for deterministic ordering
        tiebreaker = (hash(s.get("id", "")) % 1000) / 100000
        return cs * (impact / 10) * tested_penalty + tiebreaker

    suggestions.sort(key=_score, reverse=True)
    for i, s in enumerate(suggestions, 1):
        s["priority"] = i
    return suggestions


# ---------------------------------------------------------------------------
# Safe data loaders (niveaux 4-7) — each returns a benign default if the source
# table is missing, the column is NULL, or the DB helpers are unavailable.
# ---------------------------------------------------------------------------

def _load_blockmodel_stats_safe(project_id: str) -> dict:
    """Try the dedicated ``blockmodel_stats`` table, then fall back to
    ``projects.blockmodel_stats`` JSONB column. Returns {} on any failure."""
    if qone is None:
        return {}
    try:
        row = qone(
            "SELECT bwi_cv, ox_share_pct, avg_grade_g_t, tonnage_ktpd "
            "FROM blockmodel_stats WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        )
        if row:
            return {k: row.get(k) for k in row}
    except Exception:
        pass
    try:
        row = qone("SELECT blockmodel_stats FROM projects WHERE id = %s", (project_id,))
        if row and row.get("blockmodel_stats"):
            raw = row["blockmodel_stats"]
            if isinstance(raw, dict):
                return raw
            try:
                import json
                return json.loads(raw)
            except Exception:
                return {}
    except Exception:
        pass
    return {}


def _load_design_criteria_safe(project_id: str) -> list[dict]:
    if qall is None:
        return []
    try:
        rows = qall(
            "SELECT op_code, ref_number, item FROM design_criteria_v2 WHERE project_id = %s",
            (project_id,),
        )
        return list(rows or [])
    except Exception:
        return []


def _load_mass_balance_state_safe(project_id: str, last_run: dict | None) -> dict:
    """Derive a lightweight {converged, residual, p80_target_um, p80_actual_um}
    from the most recent run or from ``mass_balance_runs``."""
    if last_run:
        results = last_run.get("results") or {}
        mb = results.get("mass_balance") if isinstance(results, dict) else None
        if isinstance(mb, dict):
            return {
                "converged": mb.get("converged"),
                "residual": mb.get("residual"),
                "p80_target_um": mb.get("p80_target_um") or results.get("p80_target_um"),
                "p80_actual_um": mb.get("p80_actual_um") or results.get("p80_actual_um"),
            }
    if qone is not None:
        try:
            row = qone(
                "SELECT converged, residual, p80_target_um, p80_actual_um "
                "FROM mass_balance_runs WHERE project_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            )
            if row:
                return dict(row)
        except Exception:
            pass
    return {}


def _load_equipment_safe(project_id: str) -> list[dict]:
    if qall is None:
        return []
    try:
        rows = qall(
            "SELECT op_code, utilization_pct FROM equipment_sizing WHERE project_id = %s",
            (project_id,),
        )
        return list(rows or [])
    except Exception:
        return []


def _load_costs_safe(project: dict, last_run: dict | None) -> dict:
    out: dict = {}
    econ = (project or {}).get("economics") or {}
    if isinstance(econ, dict):
        out["capex_usd_m"] = econ.get("capex_usd_m")
        out["revenue_usd_m_y"] = econ.get("revenue_usd_m_y")
        out["reagent_opex_usd_t"] = econ.get("reagent_opex_usd_t")
    if last_run:
        results = last_run.get("results") or {}
        for key in ("capex_usd_m", "revenue_usd_m_y", "reagent_opex_usd_t"):
            if out.get(key) in (None, 0) and isinstance(results, dict) and results.get(key) is not None:
                out[key] = results.get(key)
    return out


def _load_geomet_domains_safe(project_id: str) -> list[dict]:
    if qall is None:
        return []
    try:
        rows = qall(
            "SELECT name, bwi_kwh_t, grg_rec_pct, s_sulfide_pct "
            "FROM geomet_domains WHERE project_id = %s",
            (project_id,),
        )
        return list(rows or [])
    except Exception:
        return []


def _load_campaigns_safe(project_id: str) -> list[dict]:
    if qall is None:
        return []
    try:
        rows = qall(
            "SELECT domain FROM campaigns WHERE project_id = %s",
            (project_id,),
        )
        return list(rows or [])
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Level 8 — Testwork program maturity (MetPlant 2008 / SLA simulation QA)
# ---------------------------------------------------------------------------

def _testwork_program_rules(project_id: str, project: dict) -> list[dict]:
    """Suggest closing testwork gaps before advanced simulation scenarios."""
    try:
        from .plant_design_advisor import testwork_gap_suggestions
    except ImportError:
        from engines.plant_design_advisor import testwork_gap_suggestions

    suggestions: list[dict] = []
    try:
        raw = testwork_gap_suggestions(project_id, project_status=project.get("status"))
    except Exception:
        logger.debug("testwork_gap_suggestions failed", exc_info=True)
        return suggestions

    for idx, item in enumerate(raw):
        title = item.get("title", "Programme essais")
        sid = f"testwork_{item.get('category', 'gap')}_{idx}"
        conf = float(item.get("confidence", 0.7))
        suggestions.append({
            "id": sid,
            "title": title,
            "category": item.get("category", "testwork_program"),
            "confidence": "high" if conf >= 0.8 else "medium",
            "confidence_score": conf,
            "reasoning": item.get("rationale", ""),
            "lims_basis": {},
            "ops_to_add": [],
            "ops_to_remove": [],
            "params_override": {},
            "estimated_impact": 6 if conf >= 0.8 else 4,
            "reference": item.get("reference"),
        })
    return suggestions


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def suggest(project_id: str) -> dict:
    """Generate scenario suggestions for a project.

    Returns: {"scenarios": [...], "meta": {"generated_at": ..., "project_id": ...}}
    """
    if qall is None or qone is None:
        raise RuntimeError("Database helpers not available")

    # 1. Load LIMS summary
    from db import conn, release
    c = conn()
    try:
        cur = c.cursor()
        lims = get_lims_summary(project_id, cur)
        cur.close()
    finally:
        release(c)

    # 2. Load active template ops
    template = qone(
        "SELECT id FROM circuit_templates WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    )
    active_ops: set[str] = set()
    if template:
        ops_rows = qall(
            "SELECT op_code FROM circuit_operations WHERE template_id = %s AND enabled = true",
            (template["id"],),
        )
        active_ops = {r["op_code"] for r in ops_rows}

    # 3. Load last 50 runs
    runs = qall(
        "SELECT id, run_type, status, results, simulation_params, created_at "
        "FROM simulation_runs_v2 WHERE project_id = %s ORDER BY created_at DESC LIMIT 50",
        (project_id,),
    )
    last_run = runs[0] if runs else None

    # 4. Load project row
    project = qone("SELECT * FROM projects WHERE id = %s", (project_id,)) or {}

    # 5. Get tested suggestion ids
    tested_rows = qall(
        "SELECT suggestion_id FROM scenario_suggestions_log WHERE project_id = %s",
        (project_id,),
    )
    tested_ids = {r["suggestion_id"] for r in tested_rows}

    # 6. Run all 7 rule levels
    #    Niveaux 1-3 : existants. Niveaux 4-7 : extensions Plan 2.
    sensitivity_data: dict | None = None
    for run in runs:
        res = run.get("results") or {}
        if "sensitivity" in res:
            sensitivity_data = res["sensitivity"]
            break

    # Tolerant data loaders for niveaux 4-7 — each returns {} / [] when its
    # source table is not populated or missing, so the pipeline degrades gracefully.
    blockmodel_stats = _load_blockmodel_stats_safe(project_id)
    dc_rows = _load_design_criteria_safe(project_id)
    mb_state = _load_mass_balance_state_safe(project_id, last_run)
    equipment_rows = _load_equipment_safe(project_id)
    costs = _load_costs_safe(project, last_run)
    domains = _load_geomet_domains_safe(project_id)
    campaigns = _load_campaigns_safe(project_id)

    level_outputs = [
        _lims_rules(lims, active_ops),
        _economic_rules(project, last_run),
        _history_rules(runs, sensitivity_data),
        _blockmodel_rules(blockmodel_stats, active_ops),
        _design_consistency_rules(dc_rows, mb_state, active_ops),
        _equipment_economic_rules(equipment_rows, costs, active_ops),
        _geomet_domain_rules(campaigns, domains, active_ops),
        _testwork_program_rules(project_id, project),
    ]

    all_suggestions = _merge_all_levels(level_outputs, tested_ids)
    all_suggestions = _prioritize(all_suggestions)

    # 8. Log new suggestions
    if db_execute is not None:
        for s in all_suggestions:
            if not s.get("already_tested"):
                try:
                    db_execute(
                        "INSERT INTO scenario_suggestions_log (project_id, suggestion_id, title, category, confidence) "
                        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (project_id, s["id"], s["title"], s["category"], s["confidence"]),
                    )
                except Exception:
                    logger.warning("Failed to log suggestion %s", s["id"], exc_info=True)

    # 9. Return
    return {
        "scenarios": all_suggestions,
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "lims_keys_used": len(lims),
            "active_ops": len(active_ops),
            "runs_analysed": len(runs),
        },
    }
