"""
MPDPMS — Equipment v2 (Mechanical Equipment Register) API routes.

Full MER CRUD with auto-generation from circuit template,
WBS-based summaries, and long-lead item tracking.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Depends
import psycopg2.extras

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release, build_update_sets
    from ..engines.mer_generator import generate_mer
    from ..audit import record_event
    from ..models import EquipmentV2In, EquipmentV2Patch
    from ..services.equipment_lifecycle import trigger_equipment_cascade
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, conn, release, build_update_sets
    from engines.mer_generator import generate_mer
    from audit import record_event
    from models import EquipmentV2In, EquipmentV2Patch
    from services.equipment_lifecycle import trigger_equipment_cascade

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["equipment-v2"])
logger = logging.getLogger("mpdpms.equipment_v2")

# ---------------------------------------------------------------------------
# Allowed fields for PATCH (prevents arbitrary column injection)
# ---------------------------------------------------------------------------
PATCH_FIELDS = {
    "equipment_name", "quantity", "description", "comments", "specifications",
    "has_vfd", "duty_status", "installed_kw", "emergency_power", "vendor",
    "price_cad", "installation_hours", "reference_doc", "is_long_lead",
    "lead_time_weeks", "weight_kg", "material",
}

# Required fields for manual item creation
REQUIRED_ADD_FIELDS = {"wbs_code", "eq_type", "equipment_name"}


# ============================================================================
# 1. GET /equipment-v2 — Full MER
# ============================================================================

@router.get("/equipment-v2")
def get_mer(pid: str, wbs: str = None, user=Depends(project_user)):
    """Return full MER, optionally filtered by WBS code.

    Returns: {items: [...], summary: {total_items, total_kw, total_capex, long_lead_count}}
    """
    if wbs:
        items = qall(
            "SELECT * FROM equipment_v2 "
            "WHERE project_id = %s AND enabled = TRUE AND wbs_code = %s "
            "ORDER BY wbs_code, sort_order",
            (pid, wbs),
        )
    else:
        items = qall(
            "SELECT * FROM equipment_v2 "
            "WHERE project_id = %s AND enabled = TRUE "
            "ORDER BY wbs_code, sort_order",
            (pid,),
        )

    total_kw = sum(float(it.get("installed_kw") or 0) for it in items)
    total_capex = sum(float(it.get("price_cad") or 0) for it in items)
    long_lead_count = sum(1 for it in items if it.get("is_long_lead"))

    return {
        "items": items,
        "summary": {
            "total_items": len(items),
            "total_kw": round(total_kw, 2),
            "total_capex": round(total_capex, 2),
            "long_lead_count": long_lead_count,
        },
    }


# ============================================================================
# 2. POST /equipment-v2/auto-generate — Generate from circuit
# ============================================================================

@router.post("/equipment-v2/auto-generate")
def auto_generate_mer(
    pid: str,
    dry_run: bool = False,
    template_id: str | None = None,
    user=Depends(project_user),
):
    """Generate full MER from the selected circuit template.

    If dry_run=True, return preview without inserting.
    Calls generate_mer() from engine.
    """
    if template_id:
        tpl = qone(
            "SELECT id FROM circuit_templates "
            "WHERE id = %s AND project_id = %s AND is_active = TRUE",
            (template_id, pid),
        )
    else:
        tpl = qone(
            "SELECT id FROM circuit_templates "
            "WHERE project_id = %s AND is_active = TRUE "
            "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
            (pid,),
        )
    if not tpl:
        raise HTTPException(
            404,
            "No matching active circuit template found — create or select a circuit first",
        )

    template_id = str(tpl["id"])

    if dry_run:
        # Run generation in a transaction that we roll back
        c = conn()
        try:
            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            summary = generate_mer(pid, template_id, cur)
            c.rollback()  # dry run — do not persist
            summary["dry_run"] = True
            return summary
        except Exception:  # intentional broad catch for transaction cleanup
            c.rollback()
            logger.exception("MER dry-run failed for project %s", pid)
            raise HTTPException(500, "MER generation (dry run) failed")
        finally:
            release(c)

    # Real generation — delete existing items first, then regenerate
    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "DELETE FROM equipment_v2 WHERE project_id = %s",
            (pid,),
        )
        summary = generate_mer(pid, template_id, cur)
        c.commit()

        record_event(
            user_id=user["id"], project_id=pid,
            entity_type="equipment", entity_id=None,
            action="auto_generate",
            new_value={"template_id": template_id},
            source="web",
        )

        trigger_equipment_cascade(
            project_id=pid,
            user_id=user["id"],
            change_summary=f"auto_generate(template={template_id})",
        )

        return summary
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        logger.exception("MER auto-generate failed for project %s", pid)
        raise HTTPException(500, "MER generation failed")
    finally:
        release(c)


# ============================================================================
# 3. PATCH /equipment-v2/{eid} — Update item
# ============================================================================

@router.patch("/equipment-v2/{eid}")
def patch_equipment(pid: str, eid: str, body: EquipmentV2Patch, user=Depends(project_user)):
    """Update equipment item fields. Optimistic locking with version.

    Allowed fields: equipment_name, quantity, description, comments, specifications,
    has_vfd, duty_status, installed_kw, emergency_power, vendor, price_cad,
    installation_hours, reference_doc, is_long_lead, lead_time_weeks, weight_kg, material
    """
    version = body.version
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in PATCH_FIELDS}
    if not updates:
        raise HTTPException(400, "No valid fields to update")

    # Build SET clause
    set_parts, vals = build_update_sets(updates, allowed=frozenset(PATCH_FIELDS))
    set_parts.append("version = version + 1")
    set_parts.append("updated_at = NOW()")
    vals.extend([eid, pid, version])

    sql = (
        f"UPDATE equipment_v2 SET {', '.join(set_parts)} "
        "WHERE id = %s AND project_id = %s AND version = %s "
        "RETURNING *"
    )
    row = execute(sql, vals)

    if not row:
        # Check if item exists at all
        existing = qone(
            "SELECT version FROM equipment_v2 WHERE id = %s AND project_id = %s",
            (eid, pid),
        )
        if not existing:
            raise HTTPException(404, "Equipment item not found")
        raise HTTPException(
            409,
            f"Version conflict — expected {version}, current is {existing['version']}",
        )

    record_event(
        user_id=user["id"], project_id=pid,
        entity_type="equipment", entity_id=eid,
        action="update", new_value=updates,
        source="web",
    )

    trigger_equipment_cascade(
        project_id=pid,
        user_id=user["id"],
        change_summary=f"patch({','.join(sorted(updates.keys()))})",
    )

    return row


# ============================================================================
# 4. POST /equipment-v2 — Add manual item
# ============================================================================

@router.post("/equipment-v2", status_code=201)
def add_equipment(pid: str, body: EquipmentV2In, user=Depends(project_user)):
    """Add a manual equipment item.

    Body must include: wbs_code, eq_type, equipment_name.
    Auto-generates: seq_no, equipment_tag, item_number.
    """
    wbs_code = body.wbs_code
    eq_type = body.eq_type
    equipment_name = body.equipment_name

    # Validate WBS code exists
    wbs = qone("SELECT description FROM wbs_codes WHERE code = %s", (wbs_code,))
    if not wbs:
        raise HTTPException(400, f"Invalid WBS code: {wbs_code}")

    # Get next item_number for this project
    max_item = qone(
        "SELECT COALESCE(MAX(item_number), 0) AS max_num "
        "FROM equipment_v2 WHERE project_id = %s",
        (pid,),
    )
    next_item = (max_item["max_num"] or 0) + 1

    # Get next seq_no within this WBS + eq_type
    max_seq = qone(
        "SELECT COALESCE(MAX(CAST(seq_no AS INTEGER)), 0) AS max_seq "
        "FROM equipment_v2 WHERE project_id = %s AND wbs_code = %s AND eq_type = %s",
        (pid, wbs_code, eq_type),
    )
    next_seq = str((max_seq["max_seq"] or 0) + 1).zfill(3)

    # Generate equipment tag: WBS-TYPE-SEQ (e.g. 562-PP-001)
    type_abbr = eq_type[:2].upper()
    equipment_tag = f"{wbs_code}-{type_abbr}-{next_seq}"

    # Get max sort_order for this WBS
    max_sort = qone(
        "SELECT COALESCE(MAX(sort_order), 0) AS max_sort "
        "FROM equipment_v2 WHERE project_id = %s AND wbs_code = %s",
        (pid, wbs_code),
    )
    next_sort = (max_sort["max_sort"] or 0) + 10

    row = execute(
        "INSERT INTO equipment_v2 "
        "(project_id, wbs_code, wbs_description, eq_type, seq_no, "
        " equipment_tag, equipment_name, item_number, sort_order, "
        " quantity, description, comments, specifications, "
        " has_vfd, duty_status, installed_kw, emergency_power, "
        " vendor, price_cad, is_long_lead, lead_time_weeks, "
        " weight_kg, material) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "RETURNING *",
        (
            pid, wbs_code, wbs["description"], eq_type, next_seq,
            equipment_tag, equipment_name, next_item, next_sort,
            body.get("quantity", 1),
            body.get("description"),
            body.get("comments"),
            body.get("specifications"),
            body.get("has_vfd", False),
            body.get("duty_status", "Duty"),
            body.get("installed_kw", 0),
            body.get("emergency_power", False),
            body.get("vendor"),
            body.get("price_cad"),
            body.get("is_long_lead", False),
            body.get("lead_time_weeks"),
            body.get("weight_kg"),
            body.get("material"),
        ),
    )

    record_event(
        user_id=user["id"], project_id=pid,
        entity_type="equipment", entity_id=str(row["id"]),
        action="create",
        new_value={"equipment_tag": equipment_tag, "equipment_name": equipment_name},
        source="web",
    )

    trigger_equipment_cascade(
        project_id=pid,
        user_id=user["id"],
        change_summary=f"add({equipment_tag})",
    )

    return row


# ============================================================================
# 5. DELETE /equipment-v2/all — Purge all items
# ============================================================================

@router.delete("/equipment-v2/all")
def purge_all_equipment(pid: str, user=Depends(project_user)):
    """Delete all equipment items for this project (hard delete)."""
    count_row = qone(
        "SELECT COUNT(*) AS n FROM equipment_v2 WHERE project_id = %s",
        (pid,),
    )
    count = (count_row or {}).get("n", 0)
    execute("DELETE FROM equipment_v2 WHERE project_id = %s", (pid,))

    if count > 0:
        # Skip audit + cascade when the purge was a no-op so we don't pollute
        # the audit chain or fire spurious downstream invalidations on a
        # project that had no equipment to begin with.
        record_event(
            user_id=user["id"], project_id=pid,
            entity_type="equipment", entity_id=None,
            action="purge_all",
            new_value={"deleted_count": count},
            source="web",
        )

        trigger_equipment_cascade(
            project_id=pid,
            user_id=user["id"],
            change_summary=f"purge_all(deleted={count})",
        )

    return {"ok": True, "deleted": count}


# ============================================================================
# 6. DELETE /equipment-v2/{eid} — Remove item
# ============================================================================

@router.delete("/equipment-v2/{eid}")
def delete_equipment(pid: str, eid: str, user=Depends(project_user)):
    """Soft-delete an equipment item (set enabled=false)."""
    row = execute(
        "UPDATE equipment_v2 SET enabled = FALSE, updated_at = NOW() "
        "WHERE id = %s AND project_id = %s RETURNING id",
        (eid, pid),
    )
    if not row:
        raise HTTPException(404, "Equipment item not found")

    record_event(
        user_id=user["id"], project_id=pid,
        entity_type="equipment", entity_id=eid,
        action="delete",
        source="web",
    )

    trigger_equipment_cascade(
        project_id=pid,
        user_id=user["id"],
        change_summary=f"delete({eid})",
    )

    return {"ok": True}


# ============================================================================
# 7. GET /equipment-v2/summary — Summary by WBS
# ============================================================================

@router.get("/equipment-v2/summary")
def get_summary(pid: str, user=Depends(project_user)):
    """Summary grouped by WBS code.

    Returns: [{wbs_code, wbs_description, item_count, total_kw, total_capex, long_lead_count}]
    """
    rows = qall(
        "SELECT wbs_code, wbs_description, "
        "  COUNT(*) AS item_count, "
        "  COALESCE(SUM(installed_kw), 0) AS total_kw, "
        "  COALESCE(SUM(price_cad), 0) AS total_capex, "
        "  SUM(CASE WHEN is_long_lead THEN 1 ELSE 0 END) AS long_lead_count "
        "FROM equipment_v2 "
        "WHERE project_id = %s AND enabled = TRUE "
        "GROUP BY wbs_code, wbs_description "
        "ORDER BY wbs_code",
        (pid,),
    )
    return rows


# ============================================================================
# 8. GET /equipment-v2/long-lead — Long-lead items
# ============================================================================

@router.get("/equipment-v2/long-lead")
def get_long_lead(pid: str, user=Depends(project_user)):
    """Return only long-lead items sorted by lead_time_weeks desc."""
    return qall(
        "SELECT * FROM equipment_v2 "
        "WHERE project_id = %s AND enabled = TRUE AND is_long_lead = TRUE "
        "ORDER BY lead_time_weeks DESC NULLS LAST",
        (pid,),
    )


# ============================================================================
# 9. GET /wbs-codes — WBS reference
# ============================================================================

@router.get("/wbs-codes")
def list_wbs_codes(pid: str, user=Depends(project_user)):
    """Return all WBS codes."""
    return qall("SELECT * FROM wbs_codes ORDER BY sort_order")
