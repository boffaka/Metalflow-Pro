"""Flowsheet → circuit_template snapshot compilation.

This is the bridge between the visual flowsheet (blocks + connections) and
the simulation engine (circuit_templates + circuit_operations).

Pipeline:
  1. Load flowsheet from DB
  2. Hash its canonical form
  3. If compilation exists for (project, hash) → return cached
  4. Else:
     a. Detect branches
     b. Topological sort of op_codes
     c. Validate op_codes against unit_operations_catalog
     d. Create snapshot circuit_template + operations + copy DC v2
     e. Insert circuit_compilations row
"""
from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict, deque
from typing import Optional

try:
    from ..db import qone, qall, execute
    from .circuit_hash import compute_blocks_hash
    from .branch_detection import detect_branches
except ImportError:
    from db import qone, qall, execute
    from engines.circuit_hash import compute_blocks_hash
    from engines.branch_detection import detect_branches

logger = logging.getLogger("mpdpms.compile")


def _coerce_json_list(value) -> list:
    """Return a list from a JSONB column value which may be list, str, or None."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value or "[]")
    return list(value)


def block_op_code(block: dict) -> str | None:
    """Resolve unit op code from a flowsheet block (op_code or legacy type field)."""
    raw = block.get("op_code") or block.get("type")
    if raw is None:
        return None
    code = str(raw).strip().upper()
    return code or None


def _load_flowsheet(project_id: str, source_type: str, source_id: Optional[str]) -> tuple[list[dict], list[dict]]:
    """Load blocks + connections from flowsheets or scenario_flowsheets table."""
    if source_type == "flowsheet":
        if source_id:
            row = qone(
                "SELECT blocks, connections FROM flowsheets WHERE id = %s AND project_id = %s",
                (source_id, project_id),
            )
        else:
            row = qone(
                "SELECT blocks, connections FROM flowsheets "
                "WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            )
    elif source_type == "scenario_flowsheet":
        row = qone(
            "SELECT blocks, connections FROM scenario_flowsheets WHERE id = %s",
            (source_id,),
        )
    else:
        raise ValueError(f"Unsupported source_type: {source_type}")

    if not row:
        raise ValueError(f"No {source_type} found for project {project_id}, source_id={source_id}")

    blocks = _coerce_json_list(row.get("blocks"))
    connections = _coerce_json_list(row.get("connections"))
    return blocks, connections


def load_compilation_graph(
    project_id: str,
    template_id: str,
) -> tuple[list[dict], list[dict]]:
    """Load blocks + connections for a template via its latest circuit_compilations row.

    circuit_compilations stores source_type/source_id, not blocks JSON — graph data
    lives on flowsheets / scenario_flowsheets.
    """
    row = qone(
        "SELECT source_type, source_id FROM circuit_compilations "
        "WHERE template_id = %s AND project_id = %s "
        "ORDER BY created_at DESC NULLS LAST LIMIT 1",
        (template_id, project_id),
    )
    if not row or not row.get("source_type"):
        return [], []
    try:
        return _load_flowsheet(
            project_id,
            str(row["source_type"]),
            str(row["source_id"]) if row.get("source_id") else None,
        )
    except ValueError as exc:
        logger.warning(
            "load_compilation_graph failed project=%s template=%s: %s",
            project_id,
            template_id,
            exc,
        )
        return [], []


def _topo_sort(blocks: list[dict], connections: list[dict]) -> list[str]:
    """Return op_codes in topological order (Kahn's algorithm).

    If the flowsheet contains a cycle, returns only the op_codes that could
    be ordered before the cycle blocked progress — the caller can detect
    the cycle by comparing the returned set vs the full set of enabled ops.
    """
    id_to_op = {b["id"]: block_op_code(b) or "UNKNOWN" for b in blocks if b.get("id")}
    in_degree: dict[str, int] = {b["id"]: 0 for b in blocks}
    out_edges: dict[str, list[str]] = defaultdict(list)
    for c in connections:
        src, dst = c["from"], c["to"]
        if src in in_degree and dst in in_degree:
            out_edges[src].append(dst)
            in_degree[dst] += 1

    queue = deque([n for n, d in in_degree.items() if d == 0])
    order: list[str] = []
    while queue:
        n = queue.popleft()
        order.append(id_to_op[n])
        for v in out_edges[n]:
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    # If not all nodes visited → cycle; return partial order
    return order


def _resolve_sections(op_codes: list[str]) -> list[str]:
    """Map op_codes to their sections via unit_operations_catalog."""
    if not op_codes:
        return []
    rows = qall(
        "SELECT DISTINCT category FROM unit_operations_catalog WHERE op_code = ANY(%s::text[])",
        (list(set(op_codes)),),
    )
    return sorted([r["category"] for r in rows if r.get("category")])


def _validate_op_codes(op_codes: list[str]) -> None:
    """Raise ValueError if any op_code is not in unit_operations_catalog."""
    if not op_codes:
        return
    rows = qall(
        "SELECT op_code FROM unit_operations_catalog WHERE op_code = ANY(%s::text[])",
        (list(set(op_codes)),),
    )
    known = {r["op_code"] for r in rows}
    unknown = [c for c in set(op_codes) if c not in known]
    if unknown:
        raise ValueError(f"Unknown op_code(s): {', '.join(unknown)}")


def _find_or_create_snapshot_template(project_id: str, blocks_hash: str, op_codes: list[str]) -> tuple[str, bool]:
    """Return (template_id, is_new)."""
    name = f"__snap_{blocks_hash[:12]}"
    existing = qone(
        "SELECT id FROM circuit_templates WHERE project_id = %s AND name = %s",
        (project_id, name),
    )
    if existing:
        return str(existing["id"]), False

    tpl_id = str(uuid.uuid4())
    execute(
        "INSERT INTO circuit_templates (id, project_id, name, is_active) VALUES (%s, %s, %s, FALSE)",
        (tpl_id, project_id, name),
    )
    for idx, op in enumerate(op_codes):
        execute(
            "INSERT INTO circuit_operations (template_id, op_code, enabled, sort_order) "
            "VALUES (%s, %s, TRUE, %s) ON CONFLICT (template_id, op_code) DO NOTHING",
            (tpl_id, op, idx),
        )
    _copy_design_criteria_from_active(project_id, tpl_id, op_codes)
    return tpl_id, True


def _copy_design_criteria_from_active(project_id: str, snapshot_tpl_id: str, op_codes: list[str]) -> None:
    """Copy design_criteria_v2 rows from the project's most recent active template."""
    active = qone(
        "SELECT id FROM circuit_templates WHERE project_id = %s AND is_active = TRUE "
        "ORDER BY updated_at DESC LIMIT 1",
        (project_id,),
    )
    if not active:
        return
    src_id = str(active["id"])
    execute(
        """
        INSERT INTO design_criteria_v2
            (project_id, template_id, op_code, ref_number, section_title, item, unit,
             design_value, nominal_value, min_value, max_value, source_code, revision,
             author, comments, lims_value, industry_default, enabled, sort_order, version)
        SELECT project_id, %s, op_code, ref_number, section_title, item, unit,
               design_value, nominal_value, min_value, max_value, source_code, revision,
               author, comments, lims_value, industry_default, enabled, sort_order, version
        FROM design_criteria_v2
        WHERE template_id = %s AND op_code = ANY(%s::text[])
        ON CONFLICT (template_id, ref_number) DO NOTHING
        """,
        (snapshot_tpl_id, src_id, list(set(op_codes))),
    )


def _detect_compile_warnings(blocks: list[dict], op_codes: list[str]) -> list[dict]:
    warnings: list[dict] = []
    has_feed = any(block_op_code(b) == "FEED" for b in blocks)
    if not has_feed:
        warnings.append({
            "code": "NO_FEED",
            "message": "Aucun bloc FEED défini — le feed sera déduit du LIMS / défauts",
            "severity": "info",
        })
    try:
        from .op_model_registry import resolve_op_model, is_expected_passthrough
    except ImportError:
        from engines.op_model_registry import resolve_op_model, is_expected_passthrough

    unmodeled = [
        op for op in op_codes
        if op not in ("FEED", "PRODUCT")
        and not is_expected_passthrough(op)
        and resolve_op_model(op) is None
    ]
    if unmodeled:
        warnings.append({
            "code": "NO_SIM_MODEL",
            "message": (
                "Opérations sans modèle cinétique (seront en passthrough à la simulation) : "
                + ", ".join(sorted(set(unmodeled)))
            ),
            "severity": "warning",
        })
    return warnings


def compile_flowsheet(
    project_id: str,
    source_type: str = "flowsheet",
    source_id: Optional[str] = None,
) -> dict:
    """Compile a flowsheet into an immutable circuit_template snapshot.

    Returns a dict compatible with CompileResponse.
    """
    blocks, connections = _load_flowsheet(project_id, source_type, source_id)
    blocks_hash = compute_blocks_hash(blocks, connections)

    # Dedup check
    existing = qone(
        "SELECT id, template_id, sections_resolved, branches_detected, topo_order, compile_warnings "
        "FROM circuit_compilations WHERE project_id = %s AND blocks_hash = %s",
        (project_id, blocks_hash),
    )
    if existing:
        logger.info("Compile cache hit project=%s hash=%s", project_id, blocks_hash[:12])
        return {
            "compilation_id": str(existing["id"]),
            "template_id": str(existing["template_id"]),
            "blocks_hash": blocks_hash,
            "cached": True,
            "sections_resolved": _coerce_json_list(existing.get("sections_resolved")),
            "branches_detected": _coerce_json_list(existing.get("branches_detected")),
            "topo_order": _coerce_json_list(existing.get("topo_order")),
            "warnings": _coerce_json_list(existing.get("compile_warnings")),
        }

    enabled_ops = [
        code
        for b in blocks
        if b.get("enabled", True)
        for code in [block_op_code(b)]
        if code and code not in ("FEED", "PRODUCT")
    ]
    _validate_op_codes(enabled_ops)

    topo_order_raw = _topo_sort(blocks, connections)
    # Filter topo_order to keep only enabled ops (de-dup while preserving order)
    seen: set[str] = set()
    topo_order = [op for op in topo_order_raw if op in enabled_ops and not (op in seen or seen.add(op))]

    # Cycle detection: topo_sort returns partial order on cycles. If the
    # set of op_codes we managed to order is strictly smaller than enabled_ops,
    # there's a cycle somewhere — flag it but continue.
    #
    # Note: the spec (§9) states cycles should be blocking. The plan keeps them
    # as warnings because closed-circuit grinding with recirculation is common
    # in metallurgy and users may model it as a true cycle. The executor can
    # flip this to a ValueError raise if user feedback indicates the permissive
    # behavior causes confusion. For now: permissive + tested.
    enabled_op_set = set(enabled_ops)
    ordered_op_set = set(topo_order)

    sections = _resolve_sections(enabled_ops)
    branch_result = detect_branches(blocks, connections)
    warnings = _detect_compile_warnings(blocks, enabled_ops)
    if enabled_op_set != ordered_op_set:
        missing = enabled_op_set - ordered_op_set
        warnings.append({
            "code": "CYCLE_DETECTED",
            "message": f"Cycle détecté dans le flowsheet — ops non ordonnables : {', '.join(sorted(missing))}",
            "severity": "warning",
        })
    if branch_result["warning"]:
        warnings.append({
            "code": "BRANCH_DETECTION",
            "message": branch_result["warning"],
            "severity": "warning",
        })

    template_id, _ = _find_or_create_snapshot_template(project_id, blocks_hash, enabled_ops)

    try:
        from .simulation_bridge import gravity_grg_warning_if_missing
    except ImportError:
        from engines.simulation_bridge import gravity_grg_warning_if_missing

    sim_grg = qone(
        "SELECT param_value FROM simulation_params "
        "WHERE project_id = %s AND param_key = 'gravity_grg' "
        "AND param_value IS NOT NULL AND param_value > 0",
        (project_id,),
    )
    dc_grg = qone(
        """
        SELECT design_value FROM design_criteria_v2
        WHERE template_id = %s
          AND op_code IN (
              'GRAVITE_KNELSON', 'GRAVITE_FALCON', 'GRAVITE_GEMENI', 'GRAVITY', 'GRAVITY_CONC'
          )
          AND enabled = TRUE
          AND (LOWER(item) LIKE '%%grg%%' OR LOWER(section_title) LIKE '%%grg%%')
          AND design_value IS NOT NULL AND design_value > 0
        LIMIT 1
        """,
        (template_id,),
    )
    warnings.extend(
        gravity_grg_warning_if_missing(enabled_ops, bool(sim_grg), bool(dc_grg)),
    )

    comp_id = str(uuid.uuid4())
    execute(
        """
        INSERT INTO circuit_compilations
            (id, project_id, source_type, source_id, template_id, blocks_hash,
             sections_resolved, branches_detected, topo_order, compile_warnings)
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb)
        ON CONFLICT (project_id, blocks_hash) DO NOTHING
        """,
        (
            comp_id, project_id, source_type, source_id, template_id, blocks_hash,
            json.dumps(sections), json.dumps(branch_result["branches"]),
            json.dumps(topo_order), json.dumps(warnings),
        ),
    )

    # If conflict, fetch the existing row
    final = qone(
        "SELECT id FROM circuit_compilations WHERE project_id = %s AND blocks_hash = %s",
        (project_id, blocks_hash),
    )
    comp_id = str(final["id"])

    return {
        "compilation_id": comp_id,
        "template_id": template_id,
        "blocks_hash": blocks_hash,
        "cached": False,
        "sections_resolved": sections,
        "branches_detected": branch_result["branches"],
        "topo_order": topo_order,
        "warnings": warnings,
    }
