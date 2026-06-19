"""
MPDPMS — Circuit Templates & Operations routes.
Composable circuit builder: catalog, templates, operations, LIMS suggestions.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel

try:
    from ..auth import project_user, current_user
    from ..db import qone, qall, execute, conn, release, build_update_sets
    from ..models import CircuitTemplateIn, OperationIn, OperationPatch, BulkCriteriaUpdate
    from ..logging_config import log_user_action
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user, current_user
    from db import qone, qall, execute, conn, release, build_update_sets
    from models import CircuitTemplateIn, OperationIn, OperationPatch, BulkCriteriaUpdate
    from logging_config import log_user_action

router = APIRouter(prefix="/api/v1", tags=["circuit"])
logger = logging.getLogger("mpdpms.circuit")


PROCESS_OP_ORDER = {
    "GIRATOIRE": 110,
    "CONE": 120,
    "CRIBLE": 130,
    "STOCKPILE": 140,
    "HPGR": 150,
    "SAG_MILL": 200,
    "BALL_MILL": 210,
    "ROD_MILL": 220,
    "VERTIMILL": 230,
    "HYDROCYCLONE": 300,
    "CRIBLE_CLASS": 310,
    "ISAMILL": 400,
    "VERTIMILL_REGRIND": 410,
    "SMD": 420,
    "GRAVITE_KNELSON": 500,
    "GRAVITE_FALCON": 510,
    "FLOTATION_ROUGHER": 520,
    "FLOTATION_SCAVENGER": 530,
    "FLOTATION_CLEANER": 540,
    "FLOTATION_COLONNE": 550,
    "BIOX": 600,
    "POX": 610,
    "ROASTING": 620,
    "UFG": 630,
    "EPAISSISSEUR": 700,
    "EPAISSISSEUR_HD": 710,
    "EPAISSISSEUR_CONC": 720,
    "PREAERATION": 800,
    "LEACH_CUVES": 810,
    "CIP": 820,
    "CIL": 830,
    "HEAP_LEACH": 840,
    "VAT_LEACH": 850,
    "ELUTION_AARL": 900,
    "ELUTION_ZADRA": 910,
    "ELECTROWINNING": 920,
    "FONDERIE": 930,
    "DETOX_INCO": 1000,
    "DETOX_CARO": 1010,
    "DETOX_PEROXIDE": 1020,
}


# =============================================================================
# 1. CATALOG
# =============================================================================

@router.get("/unit-operations-catalog")
def list_catalog(user=Depends(current_user)):
    """List all unit operations from catalog, grouped by category."""
    rows = qall("SELECT * FROM unit_operations_catalog ORDER BY sort_order")
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["category"]].append(row)
    return dict(grouped)


# =============================================================================
# 2. TEMPLATES CRUD
# =============================================================================

@router.post("/projects/{pid}/circuit-templates", status_code=201)
def create_template(pid: str, body: CircuitTemplateIn, user=Depends(project_user)):
    """Create a circuit template. Body: {name: str}"""
    name = body.name
    row = execute(
        "INSERT INTO circuit_templates (project_id, name, created_by) "
        "VALUES (%s, %s, %s) RETURNING *",
        (pid, name, user.get("id")),
    )
    log_user_action(
        "circuit_template.create",
        user_id=str(user.get("id")),
        entity_type="circuit_template",
        entity_id=str(row["id"]),
        details={"name": name, "project_id": pid},
    )
    return row


@router.get("/projects/{pid}/circuit-templates")
def list_templates(pid: str, user=Depends(project_user)):
    """List all circuit templates for project."""
    return qall(
        "SELECT * FROM circuit_templates WHERE project_id=%s "
        "ORDER BY is_active DESC, updated_at DESC NULLS LAST, created_at DESC",
        (pid,),
    )


@router.get("/projects/{pid}/circuit-templates/{tid}")
def get_template(pid: str, tid: str, user=Depends(project_user)):
    """Get template detail with operations and criteria counts."""
    tpl = qone(
        "SELECT * FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    ops = qall(
        "SELECT co.*, uoc.label, uoc.category "
        "FROM circuit_operations co "
        "JOIN unit_operations_catalog uoc ON uoc.op_code = co.op_code "
        "WHERE co.template_id=%s ORDER BY co.sort_order",
        (tid,),
    )

    criteria_counts = qall(
        "SELECT op_code, COUNT(*) AS cnt FROM design_criteria_v2 "
        "WHERE template_id=%s AND enabled=true GROUP BY op_code",
        (tid,),
    )
    cc_map = {r["op_code"]: r["cnt"] for r in criteria_counts}

    for op in ops:
        op["criteria_count"] = cc_map.get(op["op_code"], 0)

    tpl["operations"] = ops
    tpl["total_criteria"] = sum(cc_map.values())
    return tpl


@router.put("/projects/{pid}/circuit-templates/{tid}")
def update_template(pid: str, tid: str, body: CircuitTemplateIn, user=Depends(project_user)):
    """Rename template. Body: {name: str}"""
    name = body.name
    row = execute(
        "UPDATE circuit_templates SET name=%s, updated_at=NOW() "
        "WHERE id=%s AND project_id=%s RETURNING *",
        (name, tid, pid),
    )
    if not row:
        raise HTTPException(404, "Template not found")
    return row


@router.delete("/projects/{pid}/circuit-templates/{tid}")
def delete_template(pid: str, tid: str, user=Depends(project_user)):
    """Soft-delete template: mark is_active=FALSE to preserve FK integrity
    with circuit_compilations that reference this template_id."""
    execute(
        "UPDATE circuit_templates SET is_active = FALSE, updated_at = NOW() "
        "WHERE id = %s AND project_id = %s",
        (tid, pid),
    )
    return {"ok": True}


@router.post("/projects/{pid}/circuit-templates/{tid}/activate")
def activate_template(pid: str, tid: str, user=Depends(project_user)):
    """Set this template as the active one for the project.

    Deactivates all other templates for the project first, then activates
    the requested one. This ensures only one template is active at a time.
    """
    # Verify template exists and belongs to project
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    # Deactivate all templates for this project
    execute(
        "UPDATE circuit_templates SET is_active = FALSE, updated_at = NOW() "
        "WHERE project_id = %s",
        (pid,),
    )
    # Activate the requested template
    row = execute(
        "UPDATE circuit_templates SET is_active = TRUE, updated_at = NOW() "
        "WHERE id = %s AND project_id = %s RETURNING *",
        (tid, pid),
    )
    if not row:
        raise HTTPException(404, "Template not found")
    return row


# =============================================================================
# 3. OPERATIONS (check / uncheck)
# =============================================================================

def _section_number(sort_order: int) -> str:
    """Derive section number from operation sort_order.
    sort 110 -> '2.1', sort 120 -> '2.2', sort 210 -> '3.1', etc.
    Major = sort_order // 100 + 1, minor = (sort_order % 100) // 10.
    """
    major = sort_order // 100 + 1
    minor = (sort_order % 100) // 10
    return f"{major}.{minor}" if minor else f"{major}"


def _generate_default_criteria(pid, tid, op_code, user_id, sort_order):
    """Insert default criteria from catalog into design_criteria_v2.
    Returns the count of criteria inserted.
    """
    cat = qone(
        "SELECT default_criteria, label FROM unit_operations_catalog WHERE op_code=%s",
        (op_code,),
    )
    if not cat or not cat.get("default_criteria"):
        return 0

    criteria_list = cat["default_criteria"]
    if isinstance(criteria_list, str):
        criteria_list = json.loads(criteria_list)

    section = _section_number(sort_order)
    count = 0

    c = conn()
    cur = None
    try:
        import psycopg2.extras
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for crit in criteria_list:
            ref_number = f"{section}.{crit['ref_suffix']}"
            design_value = crit.get("pfs")
            industry_default = crit.get("pfs")
            # source_code tells the UI (and the cascade engine) where the value
            # comes from: L=LIMS, C=Calc, M=Manual, P=Project, D=Design, X=Default.
            # Falls back to "X" when the catalog entry doesn't specify.
            source_code = crit.get("source", "X")

            detail = crit.get("detail")
            min_value = max_value = None
            if isinstance(detail, (list, tuple)) and len(detail) >= 2:
                min_value, max_value = detail[0], detail[1]

            cur.execute(
                "INSERT INTO design_criteria_v2 "
                "(project_id, template_id, op_code, ref_number, dag_key, section_title, "
                "item, unit, design_value, nominal_value, min_value, max_value, "
                "industry_default, source_code, comments, sort_order, updated_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (template_id, ref_number) DO UPDATE SET "
                "enabled = TRUE, "
                "project_id = EXCLUDED.project_id, "
                "op_code = EXCLUDED.op_code, "
                "dag_key = COALESCE(design_criteria_v2.dag_key, EXCLUDED.dag_key), "
                "section_title = EXCLUDED.section_title, "
                "item = EXCLUDED.item, "
                "unit = EXCLUDED.unit, "
                "min_value = COALESCE(design_criteria_v2.min_value, EXCLUDED.min_value), "
                "max_value = COALESCE(design_criteria_v2.max_value, EXCLUDED.max_value), "
                "industry_default = EXCLUDED.industry_default, "
                "sort_order = EXCLUDED.sort_order, "
                "updated_by = EXCLUDED.updated_by, "
                "updated_at = NOW(), "
                "version = design_criteria_v2.version + 1 "
                "WHERE design_criteria_v2.op_code = EXCLUDED.op_code",
                (
                    pid, tid, op_code, ref_number, crit.get("dag_key"),
                    crit.get("section"), crit.get("item"), crit.get("unit"),
                    design_value, crit.get("fs"), min_value, max_value,
                    industry_default, source_code,
                    detail if isinstance(detail, str) else None,
                    sort_order + count, user_id,
                ),
            )
            count += cur.rowcount or 0

        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    return count


@router.post("/projects/{pid}/circuit-templates/{tid}/operations", status_code=201)
def add_operation(pid: str, tid: str, body: OperationIn, user=Depends(project_user)):
    """Check an operation. Body: {op_code: str}
    1. Validate op_code exists in catalog
    2. Check dependencies
    3. INSERT into circuit_operations
    4. Generate default design criteria
    5. Return operation + criteria count
    """
    op_code = body.op_code

    # Validate template belongs to project
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    # Validate op_code exists in catalog
    cat_row = qone(
        "SELECT * FROM unit_operations_catalog WHERE op_code=%s",
        (op_code,),
    )
    if not cat_row:
        raise HTTPException(400, f"Unknown op_code: {op_code}")

    # Check dependencies — advisory only. The order in which equipment is added
    # to a circuit is a UX concern, not a metallurgical constraint: a user
    # designing an HPGR-based circuit may legitimately add HPGR before adding
    # the upstream screening (CRIBLE), and a user designing a SAG/Ball circuit
    # may add Ball Mill before SAG. Blocking with 409 caused the checkbox to
    # silently revert in the legacy UI, making operations appear un-selectable.
    # Now we surface the missing deps as a warning the frontend can display.
    deps = cat_row.get("dependencies") or []
    if isinstance(deps, str):
        deps = json.loads(deps)

    dep_warnings: list[str] = []
    if deps:
        existing_ops = qall(
            "SELECT op_code FROM circuit_operations "
            "WHERE template_id=%s AND enabled=true",
            (tid,),
        )
        existing_codes = {r["op_code"] for r in existing_ops}
        missing = [d for d in deps if d not in existing_codes]
        if missing:
            dep_warnings.append(
                f"{op_code} dépend habituellement de {', '.join(missing)}. "
                "Pensez à les ajouter pour cohérence du circuit."
            )
            logger.info(
                f"add_operation: {op_code} added with missing recommended deps {missing} "
                "(advisory, not blocking)"
            )

    # Insert operation in metallurgical process order. This keeps the design
    # criteria and downstream modules stable across projects even when users
    # tick checkboxes in a different sequence.
    base_sort = PROCESS_OP_ORDER.get(op_code, int(cat_row.get("sort_order") or 9000))
    max_row = qone(
        "SELECT COALESCE(MAX(sort_order), %s - 1) AS max_so "
        "FROM circuit_operations WHERE template_id = %s "
        "AND sort_order >= %s AND sort_order < %s + 10",
        (base_sort, tid, base_sort, base_sort),
    )
    sort_order = int((max_row or {}).get("max_so", base_sort - 1)) + 1
    op_row = execute(
        "INSERT INTO circuit_operations (template_id, op_code, sort_order, created_by) "
        "VALUES (%s, %s, %s, %s) RETURNING *",
        (tid, op_code, sort_order, user.get("id")),
    )

    # Generate default criteria
    criteria_count = _generate_default_criteria(
        pid, tid, op_code, user.get("id"), sort_order,
    )

    # Auto-enrich from LIMS so any source="L" criterion immediately gets
    # the project's measured value (BWi, GRG, recovery, NaCN consumption,
    # etc.) instead of staying on the industry-default. The user can still
    # override manually after.
    enriched = 0
    recalculated = 0
    try:
        try:
            from ..engines.dc_generator import enrich_criteria_with_lims
        except ImportError:  # pragma: no cover
            from engines.dc_generator import enrich_criteria_with_lims

        c = conn()
        cur = None
        try:
            import psycopg2.extras
            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            res = enrich_criteria_with_lims(pid, tid, cur)
            enriched = res.get("updated", 0)
            c.commit()
        except Exception:  # intentional: LIMS enrichment is best-effort
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)
    except Exception as e:  # intentional: don't fail the add if enrichment fails
        logger.warning("auto-enrich on add %s failed: %s", op_code, e)

    # Recalculate derived equipment parameters immediately so any UI or API
    # consumer sees coherent design values as soon as an operation is selected.
    try:
        try:
            from ..engines.dc_calculator import recalculate_all
        except ImportError:  # pragma: no cover
            from engines.dc_calculator import recalculate_all

        c = conn()
        cur = None
        try:
            cur = c.cursor()
            calc_res = recalculate_all(pid, tid, cur)
            recalculated = int(calc_res.get("updated", 0) or 0)
            c.commit()
        except Exception:  # intentional: calculation is best-effort for add
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)
    except Exception as e:  # intentional: don't fail the add if calculation fails
        logger.warning("auto-recalculate on add %s failed: %s", op_code, e)

    op_row["criteria_count"] = criteria_count
    op_row["lims_enriched"] = enriched
    op_row["recalculated"] = recalculated
    op_row["label"] = cat_row["label"]
    op_row["category"] = cat_row["category"]
    if dep_warnings:
        op_row["warnings"] = dep_warnings
    log_user_action(
        "circuit_template.add_operation",
        user_id=str(user.get("id")),
        entity_type="circuit_operation",
        entity_id=str(op_row["id"]),
        details={
            "op_code": op_code,
            "template_id": tid,
            "project_id": pid,
            "missing_deps": dep_warnings,
        },
    )
    return op_row


@router.patch("/projects/{pid}/circuit-templates/{tid}/operations/{oid}")
def patch_operation(pid: str, tid: str, oid: str, body: OperationPatch, user=Depends(project_user)):
    """Update sort_order or enabled flag."""
    # Defense-in-depth: ensure the template belongs to this project.
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    fields, vals = build_update_sets(
        body.model_dump(exclude_none=True),
        allowed=frozenset(["sort_order", "enabled"]),
    )
    if not fields:
        raise HTTPException(400, "Nothing to update")

    vals += [oid, tid]
    row = execute(
        f"UPDATE circuit_operations SET {', '.join(fields)} "
        f"WHERE id=%s AND template_id=%s RETURNING *",
        vals,
    )
    if not row:
        raise HTTPException(404, "Operation not found")
    return row


@router.delete("/projects/{pid}/circuit-templates/{tid}/operations/{oid}")
def remove_operation(pid: str, tid: str, oid: str, user=Depends(project_user)):
    """Uncheck an operation.
    CASCADE POLICY:
    1. Check no other enabled operations depend on this one
    2. Soft-delete criteria (SET enabled=false)
    3. Return removed operation + affected criteria count
    """
    # Defense-in-depth: ensure the template belongs to this project.
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    # Fetch the operation being removed
    op_row = qone(
        "SELECT * FROM circuit_operations WHERE id=%s AND template_id=%s",
        (oid, tid),
    )
    if not op_row:
        logger.warning(f"Remove operation failed: operation not found for ID {oid}, template {tid}")
        raise HTTPException(404, "Operation not found")

    removing_code = op_row["op_code"]

    # Check if any other enabled operation depends on this one
    all_ops = qall(
        "SELECT co.op_code, uoc.dependencies "
        "FROM circuit_operations co "
        "JOIN unit_operations_catalog uoc ON uoc.op_code = co.op_code "
        "WHERE co.template_id=%s AND co.enabled=true AND co.id != %s",
        (tid, oid),
    )
    for other in all_ops:
        deps = other.get("dependencies") or []
        if isinstance(deps, str):
            deps = json.loads(deps)
        if removing_code in deps:
            logger.error(f"Remove operation conflict: cannot remove {removing_code} due to dependency from {other['op_code']}")
            raise HTTPException(
                409,
                f"Cannot remove {removing_code}: operation {other['op_code']} depends on it.",
            )

    # Soft-delete criteria
    c = conn()
    cur = None
    try:
        import psycopg2.extras
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "UPDATE design_criteria_v2 SET enabled=false "
            "WHERE template_id=%s AND op_code=%s AND enabled=true",
            (tid, removing_code),
        )
        affected_criteria = cur.rowcount

        # Delete the operation
        cur.execute(
            "DELETE FROM circuit_operations WHERE id=%s AND template_id=%s",
            (oid, tid),
        )
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    return {
        "ok": True,
        "removed": op_row,
        "affected_criteria": affected_criteria,
    }


@router.post(
    "/projects/{pid}/circuit-templates/{tid}/operations/{oid}/regenerate-criteria",
    status_code=201,
)
def regenerate_operation_criteria(
    pid: str, tid: str, oid: str,
    force: bool = False,
    user=Depends(project_user),
):
    """Re-insert default_criteria from unit_operations_catalog for an existing operation.

    Default behaviour (force=false): INSERT only — existing rows preserved
    via ON CONFLICT DO NOTHING. Useful when criteria are missing (seed drift)
    or when the catalog has gained new criteria but the user wants to keep
    their edits on the existing ones.

    With force=true: DELETE all enabled design_criteria_v2 rows for this
    template+op_code, then re-insert from the current catalog. Use this
    when you want a clean rebuild after the catalog has been updated with
    a new sub-section structure (e.g. HPGR went from 12 flat criteria to
    32 organised across 6 sub-sections). User customisations are lost.
    """
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    op = qone(
        "SELECT id, op_code, sort_order FROM circuit_operations "
        "WHERE id=%s AND template_id=%s",
        (oid, tid),
    )
    if not op:
        raise HTTPException(404, "Operation not found in this template")

    deleted = 0
    if force:
        c = conn()
        cur = None
        try:
            import psycopg2.extras
            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "DELETE FROM design_criteria_v2 "
                "WHERE template_id=%s AND op_code=%s "
                "RETURNING id",
                (tid, op["op_code"]),
            )
            deleted = cur.rowcount
            c.commit()
        except Exception:  # intentional broad catch for transaction cleanup
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)

    # Re-run the same logic the create-operation endpoint uses
    count = _generate_default_criteria(
        pid, tid, op["op_code"], user.get("id"),
        PROCESS_OP_ORDER.get(op["op_code"], op.get("sort_order", 0)),
    )
    recalculated = 0
    try:
        try:
            from ..engines.dc_calculator import recalculate_all
        except ImportError:  # pragma: no cover
            from engines.dc_calculator import recalculate_all

        c = conn()
        cur = None
        try:
            import psycopg2.extras

            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            res = recalculate_all(pid, tid, cur)
            recalculated = int(res.get("updated", 0) or 0)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)
    except Exception as e:
        logger.warning("criteria recalculation after op regenerate failed for template %s: %s", tid, e)

    log_user_action(
        "circuit_template.regenerate_criteria",
        user_id=str(user.get("id")),
        entity_type="circuit_operation",
        entity_id=str(oid),
        details={
            "op_code": op["op_code"],
            "force": force,
            "deleted": deleted,
            "criteria_inserted": count,
            "recalculated": recalculated,
        },
    )
    return {
        "ok": True,
        "op_code": op["op_code"],
        "force": force,
        "deleted": deleted,
        "criteria_inserted": count,
        "recalculated": recalculated,
    }


@router.post(
    "/projects/{pid}/circuit-templates/{tid}/criteria/regenerate-selected",
    status_code=201,
)
def regenerate_selected_operation_criteria(
    pid: str,
    tid: str,
    force: bool = False,
    user=Depends(project_user),
):
    """Regenerate criteria for every enabled operation in a template.

    This is the project-level repair path when an existing project was created
    before the equipment catalog had the richer Excel-style parameter sets.
    `force=true` rebuilds each equipment group from the current catalog.
    """
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    ops = qall(
        "SELECT id, op_code, sort_order FROM circuit_operations "
        "WHERE template_id=%s AND enabled=true ORDER BY sort_order",
        (tid,),
    )
    if not ops:
        return {"ok": True, "operations": [], "deleted": 0, "criteria_inserted": 0}

    deleted = 0
    if force:
        op_codes = [o["op_code"] for o in ops]
        c = conn()
        cur = None
        try:
            import psycopg2.extras

            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "DELETE FROM design_criteria_v2 "
                "WHERE template_id=%s AND op_code = ANY(%s::text[]) "
                "RETURNING id",
                (tid, op_codes),
            )
            deleted = cur.rowcount
            c.commit()
        except Exception:  # intentional broad catch for transaction cleanup
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)

    results = []
    inserted_total = 0
    for op in ops:
        inserted = _generate_default_criteria(
            pid, tid, op["op_code"], user.get("id"),
            PROCESS_OP_ORDER.get(op["op_code"], op.get("sort_order", 0)),
        )
        inserted_total += inserted
        results.append(
            {
                "operation_id": str(op["id"]),
                "op_code": op["op_code"],
                "criteria_inserted": inserted,
            }
        )

    enriched = 0
    try:
        try:
            from ..engines.dc_generator import enrich_criteria_with_lims
        except ImportError:  # pragma: no cover
            from engines.dc_generator import enrich_criteria_with_lims

        c = conn()
        cur = None
        try:
            import psycopg2.extras

            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            res = enrich_criteria_with_lims(pid, tid, cur)
            enriched = int(res.get("updated", 0) or 0)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)
    except Exception as e:
        logger.warning("bulk criteria LIMS/PSD enrichment failed for template %s: %s", tid, e)

    recalculated = 0
    try:
        try:
            from ..engines.dc_calculator import recalculate_all
        except ImportError:  # pragma: no cover
            from engines.dc_calculator import recalculate_all

        c = conn()
        cur = None
        try:
            import psycopg2.extras

            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            res = recalculate_all(pid, tid, cur)
            recalculated = int(res.get("updated", 0) or 0)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)
    except Exception as e:
        logger.warning("bulk criteria recalculation failed for template %s: %s", tid, e)

    log_user_action(
        "circuit_template.regenerate_selected_criteria",
        user_id=str(user.get("id")),
        entity_type="circuit_template",
        entity_id=str(tid),
        details={
            "force": force,
            "deleted": deleted,
            "criteria_inserted": inserted_total,
            "lims_enriched": enriched,
            "recalculated": recalculated,
            "operations": [r["op_code"] for r in results],
        },
    )
    return {
        "ok": True,
        "force": force,
        "deleted": deleted,
        "criteria_inserted": inserted_total,
        "lims_enriched": enriched,
        "recalculated": recalculated,
        "operations": results,
    }


# =============================================================================
# 4. SUGGEST CIRCUIT FROM LIMS
# =============================================================================

@router.post("/projects/{pid}/circuit-templates/{tid}/suggest")
def suggest_circuit(pid: str, tid: str, user=Depends(project_user)):
    """Analyze LIMS data and suggest circuit operations.

    Metallurgical decision logic based on:
      - BWi / A×b / DWi  → comminution circuit selection (SABC vs HPGR vs AG)
      - S_total / S_sulfide / C_organic → refractory treatment (POX, BIOX, UFG)
      - GRG / gravity recovery → gravity circuit inclusion
      - Flotation recovery → flotation + regrind path
      - Leach recovery / NaCN consumption → CIL vs CIP vs heap leach
      - Thickener unit area → dewatering equipment
    """
    # Validate template
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    # ── Fetch LIMS averages from real tables ────────────────────────────────
    lims_data: dict[str, dict] = {}

    # B1 — Comminution (BWi, A×b, Ai, DWi)
    b1 = qall(
        "SELECT AVG(COALESCE(bwi_kwh_t, mb_kwh_t)) AS bwi, "
        "       AVG(a_x_b) AS axb, AVG(abrasion_index_ai) AS ai, "
        "       AVG(dwi_kwh_m3) AS dwi, AVG(ucs_mpa) AS ucs "
        "FROM lims_b1 WHERE project_id=%s", (pid,),
    )
    if b1 and b1[0]:
        lims_data["b1"] = {k: float(v) for k, v in (b1[0] or {}).items() if v is not None}

    # A1 — Assays (S_total, S_sulfide, C_organic, As)
    a1 = qall(
        "SELECT AVG(s_total_pct) AS s_total, AVG(s_sulfide_pct) AS s_sulfide, "
        "       AVG(c_organic_pct) AS c_organic, AVG(as_ppm) AS arsenic, "
        "       AVG(au_g_t) AS au_grade "
        "FROM lims_a1 WHERE project_id=%s", (pid,),
    )
    if a1 and a1[0]:
        lims_data["a1"] = {k: float(v) for k, v in (a1[0] or {}).items() if v is not None}

    # C2/C3 — Gravity recovery (GRG)
    c2 = qone(
        "SELECT AVG(au_recovery_pct) AS grg_rec FROM lims_c2 WHERE project_id=%s", (pid,),
    )
    if c2 and c2.get("grg_rec") is not None:
        lims_data["c2"] = {"grg_rec": float(c2["grg_rec"])}

    # Flotation
    g1 = qone(
        "SELECT AVG(au_recovery_pct) AS flot_rec, AVG(mass_pull_pct) AS mass_pull, "
        "       AVG(concentrate_grade_g_t) AS conc_grade "
        "FROM lims_flotation WHERE project_id=%s", (pid,),
    )
    if g1 and g1.get("flot_rec") is not None:
        lims_data["g1"] = {k: float(v) for k, v in (g1 or {}).items() if v is not None}

    # D1 — Leaching
    d1 = qone(
        "SELECT AVG(au_recovery_pct) AS leach_rec, "
        "       AVG(nacn_consumption_kg_t) AS nacn_kg_t, "
        "       AVG(cao_consumption_kg_t) AS cao_kg_t "
        "FROM lims_d1 WHERE project_id=%s", (pid,),
    )
    if d1 and d1.get("leach_rec") is not None:
        lims_data["d1"] = {k: float(v) for k, v in (d1 or {}).items() if v is not None}

    # E1 — Thickening
    e1 = qone(
        "SELECT AVG(unit_area_m2_t_d) AS unit_area, "
        "       AVG(flocculant_dosage_g_t) AS floc_dosage "
        "FROM lims_e1 WHERE project_id=%s", (pid,),
    )
    if e1 and e1.get("unit_area") is not None:
        lims_data["e1"] = {k: float(v) for k, v in (e1 or {}).items() if v is not None}

    # ── Already selected operations ─────────────────────────────────────────
    existing = qall(
        "SELECT op_code FROM circuit_operations WHERE template_id=%s AND enabled=true",
        (tid,),
    )
    existing_codes = {r["op_code"] for r in (existing or [])}

    # ── Metallurgical decision rules ────────────────────────────────────────
    suggestions = []

    def _suggest(op_code, label, category, reason, confidence, lims_key=None, lims_val=None):
        if op_code not in existing_codes:
            suggestions.append({
                "op_code": op_code, "label": label, "category": category,
                "reason": reason, "confidence": confidence,
                "lims_parameter": lims_key, "lims_value": lims_val,
            })

    bwi = lims_data.get("b1", {}).get("bwi")
    axb = lims_data.get("b1", {}).get("axb")
    ai  = lims_data.get("b1", {}).get("ai")
    s_total = lims_data.get("a1", {}).get("s_total")
    s_sulfide = lims_data.get("a1", {}).get("s_sulfide")
    c_org = lims_data.get("a1", {}).get("c_organic")
    arsenic = lims_data.get("a1", {}).get("arsenic")
    grg = lims_data.get("c2", {}).get("grg_rec")
    flot_rec = lims_data.get("g1", {}).get("flot_rec")
    leach_rec = lims_data.get("d1", {}).get("leach_rec")
    nacn = lims_data.get("d1", {}).get("nacn_kg_t")

    # ── Comminution circuit ─────────────────────────────────────────────────
    if bwi is not None:
        if bwi > 18:
            _suggest("HPGR", "HPGR (broyage haute pression)", "COMMINUTION",
                     f"BWi = {bwi:.1f} kWh/t — minerai très dur, HPGR recommandé pour réduire "
                     f"l'énergie spécifique et améliorer la fragmentation (économie ~15-25% énergie).",
                     "high", "b1.bwi", bwi)
        if bwi > 22:
            _suggest("ISAMILL", "IsaMill (broyage ultra-fin)", "REGRIND",
                     f"BWi = {bwi:.1f} kWh/t — minerai extrêmement dur, rebroyage ultra-fin "
                     f"recommandé pour libérer l'or fin encapsulé.",
                     "medium", "b1.bwi", bwi)
    if axb is not None and axb < 30:
        _suggest("SABC", "Circuit SABC (SAG + Pebble Crusher + BM)", "COMMINUTION",
                 f"A×b = {axb:.1f} — minerai résistant à l'impact, circuit SABC avec "
                 f"recirculation galets recommandé (vs SAG/BM simple).",
                 "high", "b1.axb", axb)

    # ── Gravity recovery ────────────────────────────────────────────────────
    if grg is not None:
        if grg > 20:
            _suggest("GRAVITY", "Circuit gravimétrique (Knelson/Falcon)", "GRAVITY",
                     f"GRG = {grg:.1f}% — or grossier significatif, circuit Knelson/Falcon "
                     f"recommandé en amont du CIL pour récupération rapide sans réactifs.",
                     "high" if grg > 40 else "medium", "c2.grg_rec", grg)
        if grg > 60:
            _suggest("GRAV_TABLE", "Table à secousses (concentré gravité)", "GRAVITY",
                     f"GRG = {grg:.1f}% — or très grossier, table de concentration recommandée "
                     f"pour produire un concentré gravimétrique haute teneur.",
                     "medium", "c2.grg_rec", grg)

    # ── Refractory ore treatment ────────────────────────────────────────────
    if s_sulfide is not None and s_sulfide > 2:
        _suggest("FLOTATION", "Flottation (concentration sulfures)", "FLOTATION",
                 f"S sulfure = {s_sulfide:.1f}% — minerai sulfuré, flottation recommandée "
                 f"pour concentrer les sulfures porteurs d'or avant traitement.",
                 "high", "a1.s_sulfide", s_sulfide)
    if s_total is not None and s_total > 5:
        _suggest("POX", "Autoclave POX (oxydation sous pression)", "PRETREATMENT",
                 f"S total = {s_total:.1f}% — minerai hautement réfractaire, "
                 f"oxydation sous pression (POX) recommandée pour ouvrir la matrice sulfurée.",
                 "high", "a1.s_total", s_total)
    elif s_total is not None and s_total > 3:
        _suggest("BIOX", "Bio-oxydation BIOX®", "PRETREATMENT",
                 f"S total = {s_total:.1f}% — minerai modérément réfractaire, "
                 f"BIOX® recommandé comme alternative au POX (CAPEX inférieur).",
                 "medium", "a1.s_total", s_total)
    if c_org is not None and c_org > 0.3:
        _suggest("CIP", "CIP au lieu de CIL (anti preg-robbing)", "LEACHING",
                 f"C organique = {c_org:.2f}% — risque de preg-robbing par carbone naturel, "
                 f"circuit CIP recommandé (charbon frais en tête) vs CIL.",
                 "high" if c_org > 0.5 else "medium", "a1.c_organic", c_org)
    if arsenic is not None and arsenic > 1000:
        _suggest("DETOX", "Détoxification renforcée (SO₂/air ou H₂O₂)", "TAILINGS",
                 f"As = {arsenic:.0f} ppm — arsenic élevé, traitement de détoxification "
                 f"renforcé des résidus requis (conformité IFC < 1 mg/L As).",
                 "high", "a1.arsenic", arsenic)

    # ── Flotation path ──────────────────────────────────────────────────────
    if flot_rec is not None and flot_rec > 80:
        _suggest("REGRIND", "Rebroyage concentré flottation", "REGRIND",
                 f"Récupération flottation = {flot_rec:.1f}% — concentré flotté nécessite "
                 f"rebroyage (P80 < 25 µm) avant lixiviation pour libérer l'or piégé.",
                 "high", "g1.flot_rec", flot_rec)

    # ── Leaching circuit ────────────────────────────────────────────────────
    if leach_rec is not None:
        if leach_rec < 70:
            _suggest("HEAP_LEACH", "Lixiviation en tas (Heap Leach)", "LEACHING",
                     f"Récupération CIL = {leach_rec:.1f}% — faible récupération, "
                     f"lixiviation en tas peut être plus économique pour les minerais à faible teneur.",
                     "medium", "d1.leach_rec", leach_rec)
    if nacn is not None and nacn > 2.0:
        _suggest("LEACH_DETOX", "Détoxification cyanure INCO (SO₂/air)", "TAILINGS",
                 f"NaCN = {nacn:.1f} kg/t — consommation élevée, "
                 f"système de détox INCO SO₂/air dimensionné en conséquence.",
                 "medium", "d1.nacn_kg_t", nacn)

    # ── Abrasion / wear ─────────────────────────────────────────────────────
    if ai is not None and ai > 0.4:
        _suggest("WEAR_LINER", "Revêtements haute résistance (Cr-Mo)", "COMMINUTION",
                 f"Abrasion Index = {ai:.2f} — minerai très abrasif, "
                 f"revêtements Cr-Mo et boulets haute chrome recommandés.",
                 "medium", "b1.ai", ai)

    # ── Sort by confidence ──────────────────────────────────────────────────
    conf_order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: conf_order.get(s.get("confidence", "low"), 2))

    # ── Also try catalog trigger rules if available ─────────────────────────
    try:
        catalog = qall("SELECT * FROM unit_operations_catalog ORDER BY sort_order")
        if catalog:
            for cat_row in (catalog or []):
                op_code = cat_row["op_code"]
                if op_code in existing_codes or any(s["op_code"] == op_code for s in suggestions):
                    continue
                triggers = cat_row.get("lims_triggers") or {}
                if isinstance(triggers, str):
                    try:
                        triggers = json.loads(triggers)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not triggers:
                    continue
                for key, rule in triggers.items():
                    parts = key.split(".", 1)
                    if len(parts) != 2:
                        continue
                    test_code, param = parts
                    if test_code not in lims_data or param not in lims_data[test_code]:
                        continue
                    actual = lims_data[test_code][param]
                    threshold = rule.get("value", 0)
                    op_str = rule.get("op", ">")
                    triggered = (
                        (op_str == ">" and actual > threshold)
                        or (op_str == ">=" and actual >= threshold)
                        or (op_str == "<" and actual < threshold)
                        or (op_str == "<=" and actual <= threshold)
                    )
                    if triggered:
                        _suggest(
                            op_code, cat_row.get("label", op_code),
                            cat_row.get("category", "OTHER"),
                            rule.get("reason", f"{param} = {actual:.2f} {op_str} {threshold}"),
                            "medium", key, actual,
                        )
                        break
    except Exception:  # intentional: ignore optional lookup failure
        pass  # catalog table may not exist; hard-coded rules above are sufficient

    return {"suggestions": suggestions, "lims_data": lims_data}


@router.post("/projects/{pid}/circuit-templates/{tid}/validate")
def validate_circuit(pid: str, tid: str, user=Depends(project_user)):
    """Metallurgical coherence check: compare enabled operations against LIMS data.

    Returns warnings for operations that lack metallurgical justification,
    and missing operations that LIMS data suggests should be included.
    """
    tpl = qone("SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s", (tid, pid))
    if not tpl:
        raise HTTPException(404, "Template not found")

    existing = qall(
        "SELECT op_code FROM circuit_operations WHERE template_id=%s AND enabled=true", (tid,),
    )
    ops = {r["op_code"] for r in (existing or [])}

    # Fetch LIMS summaries
    b1 = qone("SELECT AVG(COALESCE(bwi_kwh_t, mb_kwh_t)) AS bwi, AVG(a_x_b) AS axb "
              "FROM lims_b1 WHERE project_id=%s", (pid,))
    a1 = qone("SELECT AVG(s_total_pct) AS s_total, AVG(s_sulfide_pct) AS s_sulfide, "
              "AVG(c_organic_pct) AS c_organic FROM lims_a1 WHERE project_id=%s", (pid,))
    c2 = qone("SELECT AVG(au_recovery_pct) AS grg FROM lims_c2 WHERE project_id=%s", (pid,))

    bwi = float(b1["bwi"]) if b1 and b1.get("bwi") else None
    _axb = float(b1["axb"]) if b1 and b1.get("axb") else None
    s_total = float(a1["s_total"]) if a1 and a1.get("s_total") else None
    s_sulfide = float(a1["s_sulfide"]) if a1 and a1.get("s_sulfide") else None
    c_org = float(a1["c_organic"]) if a1 and a1.get("c_organic") else None
    grg = float(c2["grg"]) if c2 and c2.get("grg") else None

    warnings = []
    missing = []

    # ── Unjustified operations ──────────────────────────────────────────────
    if "ISAMILL" in ops:
        if bwi is not None and bwi < 18 and (s_sulfide is None or s_sulfide < 3):
            warnings.append({
                "op_code": "ISAMILL", "severity": "high",
                "message": f"IsaMill activé mais BWi = {bwi:.1f} kWh/t (< 18) et S sulfure "
                           f"= {s_sulfide or 0:.1f}% (< 3%). Le rebroyage ultra-fin n'est "
                           f"justifié que pour minerais réfractaires ou très durs. "
                           f"Recommandation : désactiver pour réduire le CAPEX/OPEX.",
                "action": "disable",
            })

    if "HPGR" in ops and bwi is not None and bwi < 16:
        warnings.append({
            "op_code": "HPGR", "severity": "medium",
            "message": f"HPGR activé mais BWi = {bwi:.1f} kWh/t (< 16). Le HPGR "
                       f"n'est économiquement justifié que pour minerais durs (BWi > 16-18).",
            "action": "review",
        })

    if "POX" in ops and s_total is not None and s_total < 4:
        warnings.append({
            "op_code": "POX", "severity": "high",
            "message": f"Autoclave POX activé mais S total = {s_total:.1f}% (< 4%). "
                       f"Le POX n'est justifié que pour minerais hautement réfractaires.",
            "action": "disable",
        })

    if "BIOX" in ops and s_total is not None and s_total < 2:
        warnings.append({
            "op_code": "BIOX", "severity": "medium",
            "message": f"Bio-oxydation activée mais S total = {s_total:.1f}% (< 2%). "
                       f"Pas de justification métallurgique.",
            "action": "disable",
        })

    # ── Missing operations ──────────────────────────────────────────────────
    if grg is not None and grg > 30:
        gravity_ops = {"GRAVITE_KNELSON", "GRAVITE_FALCON", "GRAVITY"}
        if not ops & gravity_ops:
            missing.append({
                "op_code": "GRAVITE_KNELSON", "severity": "high",
                "message": f"GRG = {grg:.1f}% (> 30%) mais aucun circuit gravimétrique "
                           f"activé. Perte de récupération estimée : {grg * 0.6:.0f}% de "
                           f"l'or grossier non récupéré sans gravité.",
            })

    if c_org is not None and c_org > 0.5:
        if "CIL" in ops and "CIP" not in ops:
            missing.append({
                "op_code": "CIP", "severity": "high",
                "message": f"C organique = {c_org:.2f}% (> 0.5%) avec CIL activé. "
                           f"Risque de preg-robbing — remplacer CIL par CIP.",
            })

    return {
        "warnings": warnings,
        "missing": missing,
        "ops_count": len(ops),
        "is_coherent": len(warnings) == 0 and len(missing) == 0,
    }


# =============================================================================
# 5. CRITERIA CRUD
# =============================================================================

_CRITERIA_ALLOWED_FIELDS = frozenset([
    "design_value", "nominal_value", "min_value", "max_value",
    "source_code", "revision", "author", "comments",
])


@router.get("/projects/{pid}/circuit-templates/{tid}/criteria")
def list_criteria(pid: str, tid: str, user=Depends(project_user)):
    """List all enabled criteria for this template, grouped by section.

    Prepends standard PDC general sections (General Plant Design Criteria,
    General Project Information, Ore Characteristics) from the legacy
    design_criteria table before circuit-specific V2 criteria.
    """
    # Validate template belongs to project
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    # 1. Load general sections from legacy design_criteria table
    _GENERAL_SECTIONS = (
        "General Plant Design Criteria",
        "General Project Information",
        "Ore Characteristics",
    )
    legacy_rows = qall(
        "SELECT * FROM design_criteria WHERE project_id=%s ORDER BY sort_order",
        (pid,),
    )
    general_groups: list[dict] = []
    if legacy_rows:
        general_grouped: dict[str, dict] = {}
        for row in legacy_rows:
            section = row.get("section", "General")
            if section not in _GENERAL_SECTIONS:
                continue
            if section not in general_grouped:
                general_grouped[section] = {
                    "section_title": section,
                    "op_code": section,
                    "criteria": [],
                }
            general_grouped[section]["criteria"].append({
                "id": str(row["id"]) if row.get("id") else None,
                "ref_number": row.get("ref_code", ""),
                "item": row.get("item", ""),
                "unit": row.get("unit", ""),
                "design_value": row.get("design"),
                "nominal_value": row.get("nominal"),
                "min_value": row.get("min_val"),
                "max_value": row.get("max_val"),
                "source_code": row.get("source", "X"),
                "revision": row.get("revision", ""),
                "author": row.get("author", ""),
                "comments": row.get("comments", ""),
                "is_header": row.get("is_header", False),
                "version": 1,
            })
        # Maintain standard section order
        for sec_name in _GENERAL_SECTIONS:
            if sec_name in general_grouped:
                general_groups.append(general_grouped[sec_name])

    # 2. Load circuit-specific V2 criteria
    rows = qall(
        "SELECT * FROM design_criteria_v2 "
        "WHERE template_id=%s AND project_id=%s AND enabled=true "
        "ORDER BY sort_order, ref_number",
        (tid, pid),
    )

    # Group by section_title
    grouped: dict[str, dict] = {}
    for row in rows:
        section = row.get("section_title") or "General"
        if section not in grouped:
            grouped[section] = {
                "section_title": section,
                "op_code": row.get("op_code"),
                "criteria": [],
            }
        grouped[section]["criteria"].append(row)

    def _criteria_group_sort_key(group: dict):
        criteria = group.get("criteria") or []
        first_sort = min((int(c.get("sort_order") or 0) for c in criteria), default=0)
        op_code = group.get("op_code") or ""
        return (PROCESS_OP_ORDER.get(op_code, first_sort or 999_999), first_sort, group.get("section_title") or "")

    # 3. Return general sections first, then circuit sections in process order
    return general_groups + sorted(grouped.values(), key=_criteria_group_sort_key)


@router.patch("/projects/{pid}/circuit-templates/{tid}/criteria/bulk")
def bulk_update_criteria(pid: str, tid: str, body: BulkCriteriaUpdate, user=Depends(project_user)):
    """Bulk update criteria with optimistic locking per row.

    Body: {"updates": [{"id": ..., "version": ..., "design_value": ..., ...}, ...]}
    Returns: {"updated": int, "conflicts": [ids]}
    """
    # Validate template
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    updates = [u.model_dump() for u in body.updates] or []
    if not updates:
        raise HTTPException(400, "updates list is required")

    updated_count = 0
    conflicts = []

    c = conn()
    cur = None
    try:
        import psycopg2.extras
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for entry in updates:
            cid = entry.get("id")
            version = entry.get("version")
            if not cid or version is None:
                continue

            # Build SET clause from allowed fields
            sets, vals = build_update_sets(
                {k: entry[k] for k in _CRITERIA_ALLOWED_FIELDS if k in entry},
                allowed=_CRITERIA_ALLOWED_FIELDS,
            )

            if not sets:
                continue

            sets.append("version = version + 1")
            sets.append("updated_at = NOW()")
            sets.append("updated_by = %s")
            vals.append(user.get("id"))

            vals.extend([cid, tid, version])

            cur.execute(
                f"UPDATE design_criteria_v2 SET {', '.join(sets)} "
                f"WHERE id = %s AND template_id = %s AND version = %s "
                f"RETURNING *",
                vals,
            )
            row = cur.fetchone()
            if row:
                updated_count += 1
            else:
                conflicts.append(cid)

        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    resp = {"updated": updated_count, "conflicts": conflicts}
    if conflicts:
        raise HTTPException(409, detail=resp)
    return resp


@router.patch("/projects/{pid}/circuit-templates/{tid}/criteria/{cid}")
def patch_criterion(pid: str, tid: str, cid: str, body: dict, user=Depends(project_user)):
    """Update a single criterion with optimistic locking.

    Body must include "version" and at least one of the allowed fields.
    """
    # Validate template
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    version = body.get("version")  # Optional: if provided, enables optimistic locking

    # Build SET clause
    sets, vals = build_update_sets(
        {k: body[k] for k in _CRITERIA_ALLOWED_FIELDS if k in body},
        allowed=_CRITERIA_ALLOWED_FIELDS,
    )

    if not sets:
        raise HTTPException(400, "No valid fields to update")

    sets.append("version = version + 1")
    sets.append("updated_at = NOW()")
    sets.append("updated_by = %s")
    vals.append(user.get("id"))

    if version is not None:
        vals.extend([cid, tid, version])
        where = "WHERE id = %s AND template_id = %s AND version = %s"
    else:
        vals.extend([cid, tid])
        where = "WHERE id = %s AND template_id = %s"

    c = conn()
    cur = None
    try:
        import psycopg2.extras
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"UPDATE design_criteria_v2 SET {', '.join(sets)} "
            f"{where} "
            f"RETURNING *",
            vals,
        )
        row = cur.fetchone()
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    if not row:
        raise HTTPException(409, "Conflict: version mismatch or criterion not found")

    return dict(row)


@router.post("/projects/{pid}/circuit-templates/{tid}/criteria/enrich")
def enrich_from_lims(pid: str, tid: str, user=Depends(project_user)):
    """Re-run LIMS enrichment on all criteria for this template."""
    # Validate template
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    try:
        from ..engines.dc_generator import enrich_criteria_with_lims, get_lims_summary
    except ImportError:  # pragma: no cover
        from engines.dc_generator import enrich_criteria_with_lims, get_lims_summary

    c = conn()
    cur = None
    try:
        import psycopg2.extras
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        result = enrich_criteria_with_lims(pid, tid, cur)
        lims_summary = get_lims_summary(pid, cur)

        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    result["lims_summary"] = lims_summary
    return result


@router.post("/projects/{pid}/circuit-templates/{tid}/criteria/recalculate")
def recalculate_criteria(pid: str, tid: str, user=Depends(project_user)):
    """Recalculate ALL derived design criteria values from primary inputs.

    When a user changes a primary input (throughput, grade, recovery, BWi, etc.),
    this endpoint propagates the change through the entire calculation chain:
    Crushing → Comminution → Flotation → Regrind → Thickener → Leach/CIP →
    Reagents → Detox → Tailings → Gold production.
    """
    tpl = qone("SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s", (tid, pid))
    if not tpl:
        raise HTTPException(404, "Template not found")

    try:
        from ..engines.dc_calculator import recalculate_all
    except ImportError:
        from engines.dc_calculator import recalculate_all

    c = conn()
    try:
        cur = c.cursor()
        result = recalculate_all(pid, tid, cur)
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        release(c)

    return result


# =============================================================================
# 7. PROPAGATION — circuit → flowsheet blocks + equipment
# =============================================================================

def _load_circuit_block_mappings() -> tuple[dict[str, str], dict[str, str]]:
    path = Path(__file__).resolve().parent.parent / "config" / "circuit_block_mappings.json"
    if not path.exists():
        logger.warning("circuit block mapping file not found: %s", path)
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # intentional: log and continue on optional operation
        logger.exception("failed to load circuit block mappings from %s", path)
        return {}, {}

    cat = raw.get("category_block_type") if isinstance(raw.get("category_block_type"), dict) else {}
    op = raw.get("opcode_block_type") if isinstance(raw.get("opcode_block_type"), dict) else {}

    return ({str(k): str(v) for k, v in cat.items()}, {str(k): str(v) for k, v in op.items()})


_CATEGORY_BLOCK_TYPE, _OPCODE_BLOCK_TYPE = _load_circuit_block_mappings()


@router.post("/projects/{pid}/circuit-templates/{tid}/propagate")
def propagate_circuit(pid: str, tid: str, user=Depends(project_user)):
    """Propagate circuit operations to flowsheet blocks and equipment.

    For each enabled operation in the circuit:
    1. Check if a flowsheet block with matching label exists -- if not, add it
    2. Check if equipment with matching op_code tag prefix exists -- if not, add basic items

    This is a simplified propagation that adds blocks to the existing flowsheet
    and basic equipment entries. Full MER generation comes in Phase 3.

    Returns: {flowsheet_blocks_added: int, equipment_items_added: int}
    """
    # Validate template
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE id=%s AND project_id=%s",
        (tid, pid),
    )
    if not tpl:
        raise HTTPException(404, "Template not found")

    # 1. Read all enabled operations with catalog info
    ops = qall(
        "SELECT co.*, uoc.label, uoc.category, uoc.sort_order AS cat_sort "
        "FROM circuit_operations co "
        "JOIN unit_operations_catalog uoc ON uoc.op_code = co.op_code "
        "WHERE co.template_id=%s AND co.enabled=true "
        "ORDER BY co.sort_order",
        (tid,),
    )
    if not ops:
        return {"flowsheet_blocks_added": 0, "equipment_items_added": 0}

    # 2. Read existing flowsheet
    fs_rows = qall(
        "SELECT * FROM flowsheets WHERE project_id=%s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )

    existing_blocks = []
    fs_id = None
    if fs_rows:
        fs_id = fs_rows[0]["id"]
        blocks_raw = fs_rows[0].get("blocks") or []
        if isinstance(blocks_raw, str):
            blocks_raw = json.loads(blocks_raw)
        existing_blocks = blocks_raw

    existing_labels = {b.get("label", "").lower() for b in existing_blocks}

    # 3. Read existing equipment
    existing_equip = qall(
        "SELECT * FROM equipment WHERE project_id=%s",
        (pid,),
    )
    existing_tags = {(e.get("equipment_tag") or "").upper() for e in existing_equip}

    blocks_added = 0
    equip_added = 0
    new_blocks = list(existing_blocks)

    import psycopg2.extras

    c = conn()
    cur = None
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        for idx, op in enumerate(ops):
            op_code = op["op_code"]
            label = op["label"]
            category = op.get("category", "")

            # --- Flowsheet block ---
            if label.lower() not in existing_labels:
                block_type = _OPCODE_BLOCK_TYPE.get(
                    op_code, _CATEGORY_BLOCK_TYPE.get(category, "PROCESS")
                )
                # Auto-position: grid layout based on sort order
                x_start = int(os.getenv("FLOWSHEET_PROPAGATE_X_START", "100"))
                y_start = int(os.getenv("FLOWSHEET_PROPAGATE_Y_START", "100"))
                x_step = int(os.getenv("FLOWSHEET_PROPAGATE_X_STEP", "200"))
                y_step = int(os.getenv("FLOWSHEET_PROPAGATE_Y_STEP", "180"))
                col = idx % 6
                row = idx // 6
                new_block = {
                    "id": str(uuid.uuid4()),
                    "type": block_type,
                    "label": label,
                    "x": x_start + col * x_step,
                    "y": y_start + row * y_step,
                    "op_code": op_code,
                }
                new_blocks.append(new_block)
                existing_labels.add(label.lower())
                blocks_added += 1

            # --- Equipment ---
            # Generate a tag like "GIRATOIRE-001"
            tag_prefix = op_code.replace("_", "-")
            tag = f"{tag_prefix}-001"
            if tag.upper() not in existing_tags:
                cur.execute(
                    "INSERT INTO equipment "
                    "(project_id, equipment_tag, equipment_type, is_long_lead) "
                    "VALUES (%s, %s, %s, false) RETURNING *",
                    (pid, tag, label),
                )
                existing_tags.add(tag.upper())
                equip_added += 1

        # Save flowsheet blocks
        if blocks_added > 0:
            if fs_id:
                cur.execute(
                    "UPDATE flowsheets SET blocks=%s::jsonb WHERE id=%s AND project_id=%s",
                    (psycopg2.extras.Json(new_blocks), fs_id, pid),
                )
            else:
                cur.execute(
                    "INSERT INTO flowsheets (project_id, blocks, connections) "
                    "VALUES (%s, %s::jsonb, '[]'::jsonb)",
                    (pid, psycopg2.extras.Json(new_blocks)),
                )

        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    return {
        "flowsheet_blocks_added": blocks_added,
        "equipment_items_added": equip_added,
    }


# =============================================================================
# 6. CLONE CIRCUIT TEMPLATE
# =============================================================================

class CloneTemplateIn(BaseModel):
    """Body for cloning a circuit template to another (or same) project."""
    target_project_id: str
    new_name: str


@router.post("/projects/{pid}/circuit-templates/{tid}/clone", status_code=201)
def clone_template(pid: str, tid: str, body: CloneTemplateIn, user=Depends(project_user)):
    """Clone a circuit template — copies operations + design criteria — to target project.

    The user must have access to both source and target projects.
    """
    try:
        from ..auth import ensure_project_access as _ensure  # type: ignore
    except ImportError:
        from auth import ensure_project_access as _ensure  # type: ignore
    _ensure(body.target_project_id, user)

    # Validate source template
    src_tpl = qone(
        "SELECT * FROM circuit_templates WHERE id=%s AND project_id=%s AND is_active=true",
        (tid, pid),
    )
    if not src_tpl:
        raise HTTPException(404, "Template source introuvable")

    # Check name uniqueness in target project
    existing = qone(
        "SELECT id FROM circuit_templates WHERE project_id=%s AND name=%s",
        (body.target_project_id, body.new_name),
    )
    if existing:
        raise HTTPException(409, f"Un template '{body.new_name}' existe déjà dans le projet cible")

    c = conn()
    cur = None
    try:
        import psycopg2.extras
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        # Create new template
        cur.execute(
            "INSERT INTO circuit_templates (project_id, name, created_by) "
            "VALUES (%s, %s, %s) RETURNING *",
            (body.target_project_id, body.new_name, user.get("id")),
        )
        new_tpl = dict(cur.fetchone())
        new_tid = str(new_tpl["id"])

        # Copy operations
        src_ops = qall(
            "SELECT * FROM circuit_operations WHERE template_id=%s ORDER BY sort_order",
            (tid,),
        )
        ops_copied = 0
        for op in (src_ops or []):
            cur.execute(
                "INSERT INTO circuit_operations (template_id, op_code, sort_order, enabled, created_by) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (template_id, op_code) DO NOTHING",
                (new_tid, op["op_code"], op["sort_order"], op["enabled"], user.get("id")),
            )
            ops_copied += 1

        # Copy design criteria (reset author / updated_by to cloning user)
        src_criteria = qall(
            "SELECT * FROM design_criteria_v2 WHERE template_id=%s AND enabled=true ORDER BY sort_order",
            (tid,),
        )
        criteria_copied = 0
        for crit in (src_criteria or []):
            cur.execute(
                "INSERT INTO design_criteria_v2 "
                "(project_id, template_id, op_code, ref_number, dag_key, section_title, "
                "item, unit, design_value, nominal_value, min_value, max_value, "
                "source_code, revision, comments, lims_value, industry_default, "
                "enabled, sort_order, updated_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (template_id, ref_number) DO NOTHING",
                (
                    body.target_project_id, new_tid, crit["op_code"],
                    crit["ref_number"], crit.get("dag_key"), crit.get("section_title"),
                    crit["item"], crit.get("unit"), crit.get("design_value"),
                    crit.get("nominal_value"), crit.get("min_value"), crit.get("max_value"),
                    crit.get("source_code", "X"), crit.get("revision", "A"),
                    crit.get("comments"), crit.get("lims_value"), crit.get("industry_default"),
                    True, crit.get("sort_order", 0), user.get("id"),
                ),
            )
            criteria_copied += 1

        c.commit()
    except HTTPException:
        if c:
            c.rollback()
        raise
    except Exception:  # intentional broad catch for transaction cleanup
        if c:
            c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    log_user_action(
        "circuit_template.clone",
        user_id=str(user.get("id")),
        entity_type="circuit_template",
        entity_id=new_tid,
        details={
            "source_template_id": tid,
            "source_project_id": pid,
            "target_project_id": body.target_project_id,
            "new_name": body.new_name,
        },
    )

    new_tpl["id"] = new_tid
    new_tpl["operations_copied"] = ops_copied
    new_tpl["criteria_copied"] = criteria_copied
    return new_tpl
