"""
Dynamic gold metallurgical process simulator.

Orchestrates flowsheet → compile → rigorous simulate_circuit for any Au treatment
route (CIL/CIP, heap leach, flotation-concentrate, gravity, refractory, etc.).

Single entry point for API, Simulation et Optimisation, and UI profile.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

try:
    from ..db import qall, qone
except ImportError:
    from db import qall, qone

logger = logging.getLogger("mpdpms.gold_process_simulator")


def _optional_uuid(value: Any) -> Optional[str]:
    """Return a UUID string or None — never pass '' to PostgreSQL UUID columns."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None

# Human-readable route families (gold-focused)
ROUTE_FAMILY_LABELS: dict[str, dict[str, str]] = {
    "cil_cip": {"fr": "CIL / CIP", "en": "CIL / CIP"},
    "heap_leach": {"fr": "Lixiviation en tas", "en": "Heap leach"},
    "flotation_concentrate": {"fr": "Flottation → concentré", "en": "Flotation concentrate"},
    "gravity_only": {"fr": "Gravité seule", "en": "Gravity only"},
    "refractory_cil": {"fr": "Réfractaire + CIL", "en": "Refractory + CIL"},
    "sx_ew": {"fr": "SX-EW", "en": "SX-EW"},
    "comminution_heavy": {"fr": "Comminution dominante", "en": "Comminution-heavy"},
    "generic": {"fr": "Procédé générique", "en": "Generic route"},
}


def _coerce_json(val) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return None
    return val


def _active_source(pid: str) -> Optional[dict[str, str]]:
    row = qone(
        "SELECT feature_flags -> 'sim_active_source' AS src FROM projects WHERE id = %s",
        (pid,),
    )
    raw = _coerce_json((row or {}).get("src"))
    if isinstance(raw, dict) and raw.get("source_type") and raw.get("source_id"):
        return {
            "source_type": str(raw["source_type"]),
            "source_id": str(raw["source_id"]),
        }
    return None


def _latest_flowsheet_id(pid: str) -> Optional[str]:
    row = qone(
        "SELECT id::text AS id FROM flowsheets WHERE project_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    return str(row["id"]) if row else None


def _template_operations(template_id: str) -> list[dict[str, Any]]:
    rows = qall(
        "SELECT op_code, enabled, sort_order FROM circuit_template_operations "
        "WHERE template_id = %s ORDER BY sort_order",
        (template_id,),
    )
    return rows or []


def _active_template_row(pid: str) -> Optional[dict]:
    return qone(
        "SELECT id::text AS id, name FROM circuit_templates WHERE project_id = %s "
        "AND is_active = TRUE ORDER BY updated_at DESC LIMIT 1",
        (pid,),
    )


def resolve_simulation_ops(
    project_id: str,
    *,
    compile_if_needed: bool = True,
) -> dict[str, Any]:
    """
    Resolve the operation list used for simulation on this gold project.

    Priority:
      1. projects.feature_flags.sim_active_source → compile snapshot
      2. Latest flowsheet → compile (if compile_if_needed) — matches Simulation UI canvas
      3. Active circuit_template (legacy fallback)
    """
    source: dict[str, Any] = {"kind": "none"}
    template_id: Optional[str] = None
    compilation_id: Optional[str] = None
    topo_order: list[str] = []
    branches: list[dict] = []
    compile_warnings: list[dict] = []
    blocks_hash: Optional[str] = None

    active = _active_source(project_id)
    if active and compile_if_needed:
        try:
            from .compile import compile_flowsheet
        except ImportError:
            from engines.compile import compile_flowsheet
        comp = compile_flowsheet(
            project_id=project_id,
            source_type=active["source_type"],
            source_id=active["source_id"],
        )
        template_id = _optional_uuid(comp.get("template_id"))
        compilation_id = _optional_uuid(comp.get("compilation_id"))
        topo_order = list(comp.get("topo_order") or [])
        branches = list(comp.get("branches_detected") or [])
        compile_warnings = list(comp.get("warnings") or [])
        blocks_hash = comp.get("blocks_hash")
        source = {
            "kind": "compiled_flowsheet",
            "source_type": active["source_type"],
            "source_id": active["source_id"],
            "cached": comp.get("cached", False),
        }
    elif compile_if_needed:
        fs_id = _latest_flowsheet_id(project_id)
        if fs_id:
            try:
                from .compile import compile_flowsheet
            except ImportError:
                from engines.compile import compile_flowsheet
            comp = compile_flowsheet(
                project_id=project_id,
                source_type="flowsheet",
                source_id=fs_id,
            )
            template_id = _optional_uuid(comp.get("template_id"))
            compilation_id = _optional_uuid(comp.get("compilation_id"))
            topo_order = list(comp.get("topo_order") or [])
            branches = list(comp.get("branches_detected") or [])
            compile_warnings = list(comp.get("warnings") or [])
            blocks_hash = comp.get("blocks_hash")
            source = {
                "kind": "compiled_flowsheet",
                "source_type": "flowsheet",
                "source_id": fs_id,
                "cached": comp.get("cached", False),
            }
    if not template_id:
        tpl = _active_template_row(project_id)
        if tpl:
            template_id = str(tpl["id"])
            ops_rows = _template_operations(template_id)
            topo_order = [
                r["op_code"]
                for r in ops_rows
                if r.get("enabled", True) and r.get("op_code")
            ]
            source = {
                "kind": "active_template",
                "template_name": tpl.get("name"),
            }

    op_codes = [c for c in topo_order if c not in ("FEED", "PRODUCT")]
    return {
        "template_id": template_id,
        "compilation_id": compilation_id,
        "source": source,
        "topo_order": topo_order,
        "op_codes": op_codes,
        "branches": branches,
        "compile_warnings": compile_warnings,
        "blocks_hash": blocks_hash,
    }


def classify_operations(op_codes: list[str]) -> dict[str, Any]:
    """Split ops into kinetic models vs passthrough vs gaps."""
    try:
        from .op_model_registry import resolve_op_model, is_expected_passthrough
    except ImportError:
        from engines.op_model_registry import resolve_op_model, is_expected_passthrough

    modeled: list[dict[str, str]] = []
    passthrough: list[str] = []
    gaps: list[str] = []

    production_ops = [
        c for c in op_codes
        if c not in ("FEED", "PRODUCT") and c
    ]
    for op in production_ops:
        model = resolve_op_model(op)
        if model:
            modeled.append({"op_code": op, "model": model})
        elif is_expected_passthrough(op):
            passthrough.append(op)
        else:
            gaps.append(op)

    n_prod = len(production_ops) or 1
    coverage_pct = round(100.0 * len(modeled) / n_prod, 1)
    try:
        from .recirculation_solver import detect_recirculation_segments
    except ImportError:
        from engines.recirculation_solver import detect_recirculation_segments

    pseudo_ops = [{"op_code": c} for c in op_codes]
    recirc = detect_recirculation_segments(pseudo_ops)

    return {
        "modeled": modeled,
        "passthrough": passthrough,
        "gaps": gaps,
        "coverage_pct": coverage_pct,
        "can_run_rigorous": len(modeled) > 0,
        "recirculation_segments": recirc,
        "graph_loops": [],
        "loop_entry_indices": [],
    }


def _graph_loops_for_template(
    template_id: Optional[str],
    project_id: str,
    op_codes: list[str],
) -> tuple[list[dict], list[dict], list[int]]:
    """Load compiled graph and return (graph_loops, merged_linear_segments, entry_indices)."""
    if not template_id:
        return [], [], []

    try:
        from .generic_loop_solver import (
            detect_graph_recirculation_loops,
            merge_recirculation_plans,
        )
        from .recirculation_solver import detect_recirculation_segments
    except ImportError:
        from engines.generic_loop_solver import (
            detect_graph_recirculation_loops,
            merge_recirculation_plans,
        )
        from engines.recirculation_solver import detect_recirculation_segments

    try:
        from .compile import load_compilation_graph
    except ImportError:
        from engines.compile import load_compilation_graph

    try:
        blocks, connections = load_compilation_graph(project_id, template_id)
        if not blocks or not connections:
            pseudo = [{"op_code": c} for c in op_codes]
            return [], detect_recirculation_segments(pseudo), []

        operations = [{"op_code": c} for c in op_codes]
        graph_loops = detect_graph_recirculation_loops(blocks, connections, operations)
        seq = detect_recirculation_segments(operations)
        linear, loop_by_entry = merge_recirculation_plans(seq, graph_loops)
        return graph_loops, linear, sorted(loop_by_entry.keys())
    except Exception as exc:
        logger.warning(
            "graph loop detection failed project=%s template=%s: %s",
            project_id,
            template_id,
            exc,
            exc_info=True,
        )
        pseudo = [{"op_code": c} for c in op_codes]
        return [], detect_recirculation_segments(pseudo), []


def build_gold_process_profile(
    project_id: str,
    *,
    compile_if_needed: bool = True,
) -> dict[str, Any]:
    """Profile of the dynamic gold route without running simulation."""
    proj = qone(
        "SELECT project_name, project_code, commodity, target_tph, gold_grade_g_t, status "
        "FROM projects WHERE id = %s",
        (project_id,),
    ) or {}
    resolved = resolve_simulation_ops(project_id, compile_if_needed=compile_if_needed)
    op_codes = resolved.get("op_codes") or []
    op_set = set(op_codes)

    try:
        from .metallurgical_levers import _detect_flowsheet_family, discover_project_levers
    except ImportError:
        from engines.metallurgical_levers import _detect_flowsheet_family, discover_project_levers

    family = _detect_flowsheet_family(op_set)
    classification = classify_operations(op_codes)
    graph_loops, linear_recirc, loop_entries = _graph_loops_for_template(
        resolved.get("template_id"), project_id, op_codes,
    )
    if graph_loops or linear_recirc:
        classification["graph_loops"] = [
            {
                "type": g["type"],
                "op_codes": g["op_codes"],
                "source": g.get("source", "flowsheet_graph"),
                "entry_index": g.get("entry_index"),
                "recirc_edge": g.get("recirc_edge"),
            }
            for g in graph_loops
        ]
        classification["recirculation_segments"] = linear_recirc
        classification["loop_entry_indices"] = loop_entries
    try:
        lever_pack = discover_project_levers(project_id)
    except Exception as exc:
        logger.warning("discover_project_levers failed project=%s: %s", project_id, exc)
        lever_pack = {"levers": {}, "levers_meta": [], "active_lever_ids": []}

    commodity = (proj.get("commodity") or "Au").strip()
    warnings = list(resolved.get("compile_warnings") or [])
    if classification["gaps"]:
        warnings.append({
            "code": "SIM_GAPS",
            "message": (
                "Opérations sans modèle cinétique : "
                + ", ".join(classification["gaps"][:8])
            ),
            "severity": "warning",
        })

    return {
        "project": {
            "id": project_id,
            "name": proj.get("project_name"),
            "code": proj.get("project_code"),
            "commodity": commodity,
            "target_tph": proj.get("target_tph"),
            "gold_grade_g_t": proj.get("gold_grade_g_t"),
            "status": proj.get("status"),
        },
        "source": resolved.get("source"),
        "template_id": resolved.get("template_id"),
        "compilation_id": resolved.get("compilation_id"),
        "topo_order": resolved.get("topo_order") or [],
        "branches": resolved.get("branches") or [],
        "route": {
            "family": family,
            "family_label": ROUTE_FAMILY_LABELS.get(family, ROUTE_FAMILY_LABELS["generic"]),
            "n_operations": len(op_codes),
            "op_codes": op_codes,
        },
        "simulation_coverage": classification,
        "levers": lever_pack.get("levers"),
        "levers_meta": lever_pack.get("levers_meta"),
        "active_lever_ids": lever_pack.get("active_lever_ids"),
        "warnings": warnings,
        "ready": bool(resolved.get("template_id")) and classification["can_run_rigorous"],
    }


def run_gold_process(
    project_id: str,
    params_override: Optional[dict] = None,
    *,
    compile_if_needed: bool = True,
    cursor=None,
) -> dict[str, Any]:
    """Compile (if needed), run rigorous gold circuit simulation, attach profile."""
    try:
        from .process_simulator import simulate_circuit
    except ImportError:
        from engines.process_simulator import simulate_circuit

    profile = build_gold_process_profile(project_id, compile_if_needed=compile_if_needed)
    template_id = profile.get("template_id")
    if not template_id:
        raise ValueError(
            "Aucune source de simulation — créez un flowsheet ou un template circuit actif."
        )
    if not profile.get("simulation_coverage", {}).get("can_run_rigorous"):
        raise ValueError(
            "Aucune opération avec modèle cinétique — élargissez le flowsheet (broyage, flottation, CIL…)."
        )

    results = simulate_circuit(
        project_id,
        template_id,
        params_override=params_override,
        cursor=cursor,
    )
    results["gold_process_profile"] = profile
    results["template_id"] = template_id
    results["compilation_id"] = profile.get("compilation_id")
    return results


def list_gold_presets() -> dict[str, list[dict]]:
    """48 industrial gold flowsheet templates grouped by family."""
    try:
        from ..flowsheet_templates import get_templates_grouped
    except ImportError:
        from flowsheet_templates import get_templates_grouped
    return get_templates_grouped()
