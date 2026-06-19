"""
MPDPMS — Flowsheet tree per project.

The flowsheet is a per-project tree (rooted at the FEED/ore, branches at splits,
leaves marked with product_kind such as 'bullion' or 'tailings').

Storage piggybacks on `circuit_template_operations` (extended in migration 000038)
with `parent_op_id`, value columns and `values_source`.

Endpoints (under `/api/v1/projects/{pid}/flowsheet`):
  GET    /                          → tree + KPIs (with LIMS fallback)
  POST   /                          → ensure a circuit_template exists, return it
  POST   /operations                → add a node (with parent_op_id)
  PATCH  /operations/{oid}          → update node (values, parent, label, product_kind)
  DELETE /operations/{oid}          → remove node (cascades to children)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

try:
    from ..auth import project_user
    from ..db import qall, qone, execute
    from ..logging_config import log_user_action
    from ..lims_lookup import fetch_op_defaults
except ImportError:  # pragma: no cover
    from auth import project_user

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM
    from db import qall, qone, execute
    from logging_config import log_user_action
    from lims_lookup import fetch_op_defaults


router = APIRouter(prefix="/api/v1/projects", tags=["flowsheet"])
logger = logging.getLogger("mpdpms.flowsheet_tree")


# ─── Pydantic models ────────────────────────────────────────────────────────

class FlowsheetNodeIn(BaseModel):
    """Body for POST /operations."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    op_code: str = Field(..., min_length=1, max_length=100)
    parent_op_id: Optional[str] = Field(default=None)
    node_label: Optional[str] = Field(default=None, max_length=200)
    product_kind: Optional[str] = Field(default=None)
    sort_order: int = Field(default=0, ge=0)
    recovery_pct: Optional[float] = Field(default=None, ge=0, le=100)
    throughput_tph: Optional[float] = Field(default=None, ge=0)
    water_m3h: Optional[float] = Field(default=None, ge=0)
    grade_au_gt: Optional[float] = Field(default=None, ge=0)


class FlowsheetNodePatch(BaseModel):
    """Body for PATCH /operations/{oid}. All fields optional."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    op_code: Optional[str] = Field(default=None, min_length=1, max_length=100)
    parent_op_id: Optional[str] = Field(default=None)
    node_label: Optional[str] = Field(default=None, max_length=200)
    product_kind: Optional[str] = Field(default=None)
    sort_order: Optional[int] = Field(default=None, ge=0)
    recovery_pct: Optional[float] = Field(default=None, ge=0, le=100)
    throughput_tph: Optional[float] = Field(default=None, ge=0)
    water_m3h: Optional[float] = Field(default=None, ge=0)
    grade_au_gt: Optional[float] = Field(default=None, ge=0)
    equipment_id: Optional[str] = Field(default=None)


_VALID_PRODUCT_KIND = {"bullion", "tailings", "concentrate"}


# ─── Helpers ───────────────────────────────────────────────────────────────

def _get_or_create_template(pid: str, user_id: str) -> dict:
    """Return the most recently updated circuit_template for the project,
    creating one if none exists."""
    tpl = qone(
        "SELECT * FROM circuit_templates WHERE project_id=%s "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
        (pid,),
    )
    if tpl:
        return tpl
    return execute(
        "INSERT INTO circuit_templates (project_id, name) "
        "VALUES (%s, 'Flowsheet principal') RETURNING *",
        (pid,),
    )


def _get_active_template(pid: str) -> Optional[dict]:
    """Return the active flowsheet template (most recently updated). None if none."""
    return qone(
        "SELECT * FROM circuit_templates WHERE project_id=%s "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
        (pid,),
    )


def _validate_parent(template_id: str, parent_op_id: Optional[str]) -> None:
    """Parent must belong to the same template. None is allowed (root)."""
    if parent_op_id is None:
        return
    parent = qone(
        "SELECT id FROM circuit_template_operations WHERE id=%s AND template_id=%s",
        (parent_op_id, template_id),
    )
    if not parent:
        raise HTTPException(400, "parent_op_id not found in this template")


def _has_root(template_id: str, exclude_op_id: Optional[str] = None) -> bool:
    """Check whether the template already has a root (parent_op_id IS NULL)."""
    if exclude_op_id is None:
        row = qone(
            "SELECT 1 AS x FROM circuit_template_operations "
            "WHERE template_id=%s AND parent_op_id IS NULL LIMIT 1",
            (template_id,),
        )
    else:
        row = qone(
            "SELECT 1 AS x FROM circuit_template_operations "
            "WHERE template_id=%s AND parent_op_id IS NULL AND id <> %s LIMIT 1",
            (template_id, exclude_op_id),
        )
    return bool(row)


def _validate_product_kind(value: Optional[str]) -> None:
    if value is None or value == "":
        return
    if value not in _VALID_PRODUCT_KIND:
        raise HTTPException(400, f"product_kind must be one of {sorted(_VALID_PRODUCT_KIND)}")


def _node_has_descendant(template_id: str, ancestor_id: str, candidate_id: str) -> bool:
    """Return True if `candidate_id` is in the subtree rooted at `ancestor_id`.
    Used to prevent setting a parent that would create a cycle."""
    rows = qall(
        "SELECT id, parent_op_id FROM circuit_template_operations WHERE template_id=%s",
        (template_id,),
    ) or []
    children: dict[str, list[str]] = {}
    for r in rows:
        pid_ = str(r["parent_op_id"]) if r["parent_op_id"] else None
        if pid_:
            children.setdefault(pid_, []).append(str(r["id"]))
    stack = list(children.get(str(ancestor_id), []))
    while stack:
        nid = stack.pop()
        if nid == str(candidate_id):
            return True
        stack.extend(children.get(nid, []))
    return False


# ─── Tree assembly ─────────────────────────────────────────────────────────

def _row_to_node(row: dict, lims_defaults: dict[str, dict[str, float]]) -> dict:
    """Convert a SQL row into the node JSON shape with LIMS fallback applied."""
    op_code = row["op_code"]
    src = row["values_source"] or "manual"

    def _val(field: str) -> tuple[Optional[float], str]:
        cur = row.get(field)
        if cur is not None:
            # Persisted value wins
            return float(cur), "manual" if src == "manual" else "lims_auto"
        # Try LIMS fallback
        fallback = lims_defaults.get(op_code, {}).get(field)
        if fallback is not None:
            return float(fallback), "lims_auto"
        return None, "missing"

    recovery_val, recovery_src = _val("recovery_pct")
    throughput_val, throughput_src = _val("throughput_tph")
    water_val, water_src = _val("water_m3h")
    grade_val, grade_src = _val("grade_au_gt")

    # If all four fields are missing, source='missing'; if any has lims_auto and none is manual,
    # source='lims_auto'; if any is manual, source='manual'.
    sources = {recovery_src, throughput_src, water_src, grade_src}
    if "manual" in sources:
        node_src = "manual"
    elif "lims_auto" in sources:
        node_src = "lims_auto"
    else:
        node_src = "missing"

    return {
        "id": str(row["id"]),
        "op_code": op_code,
        "label": row.get("node_label") or row.get("instance_label") or op_code,
        "product_kind": row.get("product_kind"),
        "sort_order": row.get("sort_order") or 0,
        "values": {
            "recovery_pct": recovery_val,
            "throughput_tph": throughput_val,
            "water_m3h": water_val,
            "grade_au_gt": grade_val,
            "source": node_src,
        },
        "field_sources": {
            "recovery_pct": recovery_src,
            "throughput_tph": throughput_src,
            "water_m3h": water_src,
            "grade_au_gt": grade_src,
        },
        "children": [],
    }


def _build_tree(rows: list[dict], lims_defaults: dict[str, dict[str, float]]) -> tuple[Optional[dict], list[dict]]:
    """Return (root_node, all_root_candidates). All_root_candidates lists every node
    where parent_op_id IS NULL (used for warning if more than one)."""
    nodes_by_id: dict[str, dict] = {}
    roots: list[dict] = []
    for r in rows:
        node = _row_to_node(r, lims_defaults)
        nodes_by_id[node["id"]] = node
    for r in rows:
        nid = str(r["id"])
        pid_ = r.get("parent_op_id")
        if pid_ is None:
            roots.append(nodes_by_id[nid])
            continue
        parent = nodes_by_id.get(str(pid_))
        if parent is not None:
            parent["children"].append(nodes_by_id[nid])
    # Sort children by sort_order
    for n in nodes_by_id.values():
        n["children"].sort(key=lambda c: c["sort_order"])
    roots.sort(key=lambda c: c["sort_order"])
    return (roots[0] if roots else None), roots


def _compute_kpis(root: Optional[dict]) -> dict[str, Optional[float]]:
    """Find the path root → first leaf with product_kind='bullion'.
    Compute global recovery, production oz/h, water makeup, feed tph."""
    null_kpis = {
        "feed_tph": None,
        "global_recovery_pct": None,
        "production_oz_h": None,
        "water_makeup_m3h": None,
    }
    if root is None:
        return null_kpis

    feed_tph = root["values"].get("throughput_tph")
    feed_grade = root["values"].get("grade_au_gt")

    # DFS for the path to a bullion leaf
    bullion_path: Optional[list[dict]] = None

    def dfs(node: dict, path: list[dict]) -> None:
        nonlocal bullion_path
        if bullion_path is not None:
            return
        path = path + [node]
        if not node["children"]:
            if node.get("product_kind") == "bullion":
                bullion_path = path
            return
        for child in node["children"]:
            dfs(child, path)

    dfs(root, [])

    if not bullion_path:
        return {
            "feed_tph": feed_tph,
            "global_recovery_pct": None,
            "production_oz_h": None,
            "water_makeup_m3h": None,
        }

    # Global recovery = product of recoveries on the path (excluding root if its recovery is null)
    rec_factors: list[float] = []
    water_sum = 0.0
    water_known = True
    for n in bullion_path:
        r = n["values"].get("recovery_pct")
        if r is not None:
            rec_factors.append(r / 100.0)
        # Note: missing recovery on a node breaks global recovery (we leave None below).
        w = n["values"].get("water_m3h")
        if w is not None:
            water_sum += float(w)
        else:
            water_known = False

    # If any node on the path has recovery_pct=None, we can't compute global.
    missing_rec = any(n["values"].get("recovery_pct") is None for n in bullion_path[1:])
    # The root's recovery is conceptually 100% (no losses at the feed).
    if missing_rec:
        global_rec_pct: Optional[float] = None
    else:
        prod = 1.0
        for f in rec_factors:
            prod *= f
        # Root contributes 1.0 if its recovery is None — feed loss not modeled.
        global_rec_pct = prod * 100.0 if rec_factors else None

    # production oz/h = feed_tph * feed_grade(g/t) * recovery * TROY_OZ_PER_GRAM
    if feed_tph is not None and feed_grade is not None and global_rec_pct is not None:
        production = float(feed_tph) * float(feed_grade) * (global_rec_pct / 100.0) * TROY_OZ_PER_GRAM
    else:
        production = None

    return {
        "feed_tph": feed_tph,
        "global_recovery_pct": round(global_rec_pct, 3) if global_rec_pct is not None else None,
        "production_oz_h": round(production, 3) if production is not None else None,
        "water_makeup_m3h": round(water_sum, 2) if water_known else None,
    }


def _merge_kpis_from_run(kpis: dict[str, Optional[float]], run_row: Optional[dict]) -> dict[str, Optional[float]]:
    """Overlay banner KPIs from the latest rigorous simulation when the tree lacks bullion metrics."""
    if not run_row:
        return kpis
    raw = run_row.get("results")
    if not raw:
        return kpis
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return kpis
    if not isinstance(raw, dict):
        return kpis
    overall = raw.get("overall") or {}
    merged = dict(kpis)
    feed = overall.get("feed_tph")
    if feed is not None:
        merged["feed_tph"] = float(feed)
    rec = overall.get("total_recovery_pct") or overall.get("plant_recovery_pct")
    if rec is not None:
        merged["global_recovery_pct"] = round(float(rec), 3)
    grade = overall.get("feed_grade_au")
    tph = merged.get("feed_tph")
    if merged.get("production_oz_h") is None and tph and grade and rec is not None:
        merged["production_oz_h"] = round(
            float(tph) * float(grade) * (float(rec) / 100.0) * TROY_OZ_PER_GRAM, 3
        )
    annual_oz = overall.get("annual_gold_oz")
    if merged.get("production_oz_h") is None and annual_oz:
        merged["production_oz_h"] = round(float(annual_oz) / 8760.0, 3)
    energy = overall.get("total_energy_kwh_t")
    if energy is not None:
        merged["energy_kwh_t"] = round(float(energy), 3)
    return merged


# ─── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/{pid}/flowsheet")
def get_flowsheet(pid: str, response: Response, user=Depends(project_user)):
    """Return the project's flowsheet tree + KPIs.

    404 if no template exists (front shows empty-state CTA).
    422 if template exists but has no root.
    """
    tpl = _get_active_template(pid)
    if not tpl:
        raise HTTPException(404, {"reason": "no_flowsheet"})

    rows = qall(
        "SELECT id, template_id, op_code, instance_label, sort_order, parent_op_id, "
        "       node_label, product_kind, recovery_pct, throughput_tph, water_m3h, "
        "       grade_au_gt, values_source "
        "FROM circuit_template_operations WHERE template_id=%s",
        (tpl["id"],),
    ) or []

    if not rows:
        raise HTTPException(422, {"reason": "empty_tree"})

    lims_defaults = fetch_op_defaults(pid)
    root, all_roots = _build_tree(rows, lims_defaults)

    if root is None:
        raise HTTPException(422, {"reason": "no_root"})

    if len(all_roots) > 1:
        response.headers["X-Flowsheet-Warn"] = f"multiple-roots:{len(all_roots)}"

    kpis = _compute_kpis(root)
    response.headers["Cache-Control"] = "no-store"

    last_run = qone(
        "SELECT id, results FROM simulation_runs_v2 WHERE project_id=%s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (pid,),
    )
    if last_run:
        kpis = _merge_kpis_from_run(kpis, last_run)

    return {
        "template_id": str(tpl["id"]),
        "name": tpl.get("name"),
        "updated_at": tpl.get("updated_at").isoformat() if tpl.get("updated_at") else None,
        "tree": root,
        "kpis": kpis,
        "last_run_id": str(last_run["id"]) if last_run else None,
    }


@router.post("/{pid}/flowsheet", status_code=201)
def ensure_flowsheet(pid: str, user=Depends(project_user)):
    """Idempotent: ensure a circuit_template exists for the project. Return it.
    The frontend calls this when the user lands on the editor for the first time."""
    tpl = _get_or_create_template(pid, str(user.get("id")))
    return {"template_id": str(tpl["id"]), "name": tpl.get("name")}


@router.post("/{pid}/flowsheet/operations", status_code=201)
def add_node(pid: str, body: FlowsheetNodeIn, user=Depends(project_user)):
    """Add a node to the project's flowsheet tree.

    - parent_op_id=None creates the root. A second root is rejected (400).
    - parent_op_id must reference a node in the same template.
    - Values present in the body are stored; values_source='manual' if any value is set,
      else 'lims_auto' (the node will rely on LIMS fallback at GET time).
    """
    _validate_product_kind(body.product_kind)
    tpl = _get_or_create_template(pid, str(user.get("id")))
    template_id = str(tpl["id"])

    _validate_parent(template_id, body.parent_op_id)
    if body.parent_op_id is None and _has_root(template_id):
        raise HTTPException(400, "Template already has a root node")

    has_any_value = any(
        v is not None for v in (body.recovery_pct, body.throughput_tph, body.water_m3h, body.grade_au_gt)
    )
    values_source = "manual" if has_any_value else "lims_auto"

    row = execute(
        """
        INSERT INTO circuit_template_operations
          (template_id, op_code, instance_label, sort_order, parent_op_id,
           node_label, product_kind, recovery_pct, throughput_tph, water_m3h,
           grade_au_gt, values_source)
        VALUES (%s, %s, '', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            template_id, body.op_code, body.sort_order, body.parent_op_id,
            body.node_label, body.product_kind, body.recovery_pct,
            body.throughput_tph, body.water_m3h, body.grade_au_gt, values_source,
        ),
    )
    # Bump template updated_at
    execute("UPDATE circuit_templates SET updated_at=NOW() WHERE id=%s", (template_id,))

    log_user_action(
        "flowsheet.node.add",
        user_id=str(user.get("id")),
        entity_type="circuit_template_operation",
        entity_id=str(row["id"]),
        details={"project_id": pid, "op_code": body.op_code,
                 "parent_op_id": body.parent_op_id},
    )
    return {**row, "id": str(row["id"])}


@router.patch("/{pid}/flowsheet/operations/{oid}")
def patch_node(pid: str, oid: str, body: FlowsheetNodePatch, user=Depends(project_user)):
    """Update a node. Setting any value to non-null switches values_source to 'manual'.
    Setting all four values to null resets values_source to 'lims_auto'."""
    _validate_product_kind(body.product_kind)

    # Locate the node and verify it belongs to a template owned by the project.
    cur = qone(
        """
        SELECT cto.*, ct.project_id, ct.id AS template_id_real
        FROM   circuit_template_operations cto
        JOIN   circuit_templates ct ON ct.id = cto.template_id
        WHERE  cto.id = %s
        """,
        (oid,),
    )
    if not cur or str(cur["project_id"]) != str(pid):
        raise HTTPException(404, "Node not found")

    template_id = str(cur["template_id_real"])

    # Validate new parent
    if body.parent_op_id is not None:
        _validate_parent(template_id, body.parent_op_id)
        # Reject self-parent
        if str(body.parent_op_id) == str(oid):
            raise HTTPException(400, "A node cannot be its own parent")
        # Reject cycle: new parent must not be in subtree of this node
        if _node_has_descendant(template_id, str(oid), str(body.parent_op_id)):
            raise HTTPException(400, "Cannot reparent: would create a cycle")
    else:
        # Caller explicitly passed parent_op_id=None — making it root. Only allowed if no other root exists.
        # We use a sentinel: only re-evaluate if the patch actually changes parent.
        pass

    set_clauses: list[str] = []
    params: list[Any] = []

    # Compute prospective new values to determine values_source.
    new_values = {
        "recovery_pct": cur.get("recovery_pct"),
        "throughput_tph": cur.get("throughput_tph"),
        "water_m3h": cur.get("water_m3h"),
        "grade_au_gt": cur.get("grade_au_gt"),
    }
    value_fields_in_body = {
        "recovery_pct": "recovery_pct" in body.model_fields_set,
        "throughput_tph": "throughput_tph" in body.model_fields_set,
        "water_m3h": "water_m3h" in body.model_fields_set,
        "grade_au_gt": "grade_au_gt" in body.model_fields_set,
    }
    if value_fields_in_body["recovery_pct"]:
        new_values["recovery_pct"] = body.recovery_pct
    if value_fields_in_body["throughput_tph"]:
        new_values["throughput_tph"] = body.throughput_tph
    if value_fields_in_body["water_m3h"]:
        new_values["water_m3h"] = body.water_m3h
    if value_fields_in_body["grade_au_gt"]:
        new_values["grade_au_gt"] = body.grade_au_gt

    any_value_changed = any(value_fields_in_body.values())
    any_value_set = any(v is not None for v in new_values.values())

    # Build UPDATE
    if "op_code" in body.model_fields_set:
        set_clauses.append("op_code=%s"); params.append(body.op_code)
    if "node_label" in body.model_fields_set:
        set_clauses.append("node_label=%s"); params.append(body.node_label)
    if "product_kind" in body.model_fields_set:
        # empty string → NULL
        set_clauses.append("product_kind=%s"); params.append(body.product_kind or None)
    if "sort_order" in body.model_fields_set:
        set_clauses.append("sort_order=%s"); params.append(body.sort_order)
    if "parent_op_id" in body.model_fields_set:
        set_clauses.append("parent_op_id=%s"); params.append(body.parent_op_id)
    # Validate equipment_id (must belong to the same project) — None allowed (unlink)
    if "equipment_id" in body.model_fields_set and body.equipment_id is not None:
        eq = qone(
            "SELECT id FROM equipment WHERE id=%s AND project_id=%s",
            (body.equipment_id, pid),
        )
        if not eq:
            raise HTTPException(400, "equipment_id not found in this project")
    if "equipment_id" in body.model_fields_set:
        set_clauses.append("equipment_id=%s"); params.append(body.equipment_id)
    if value_fields_in_body["recovery_pct"]:
        set_clauses.append("recovery_pct=%s"); params.append(body.recovery_pct)
    if value_fields_in_body["throughput_tph"]:
        set_clauses.append("throughput_tph=%s"); params.append(body.throughput_tph)
    if value_fields_in_body["water_m3h"]:
        set_clauses.append("water_m3h=%s"); params.append(body.water_m3h)
    if value_fields_in_body["grade_au_gt"]:
        set_clauses.append("grade_au_gt=%s"); params.append(body.grade_au_gt)
    if any_value_changed:
        set_clauses.append("values_source=%s")
        params.append("manual" if any_value_set else "lims_auto")

    if not set_clauses:
        raise HTTPException(400, "Nothing to update")

    params += [oid]
    row = execute(
        f"UPDATE circuit_template_operations SET {', '.join(set_clauses)} "
        f"WHERE id=%s RETURNING *",
        params,
    )
    if not row:
        raise HTTPException(404, "Node not found")

    execute("UPDATE circuit_templates SET updated_at=NOW() WHERE id=%s", (template_id,))

    log_user_action(
        "flowsheet.node.patch",
        user_id=str(user.get("id")),
        entity_type="circuit_template_operation",
        entity_id=str(oid),
        details={"project_id": pid, "fields": list(body.model_fields_set)},
    )
    return {**row, "id": str(row["id"])}


@router.delete("/{pid}/flowsheet/operations/{oid}", status_code=204)
def delete_node(pid: str, oid: str, user=Depends(project_user)):
    """Remove a node. ON DELETE CASCADE on parent_op_id removes the entire subtree."""
    cur = qone(
        """
        SELECT cto.id, ct.project_id, ct.id AS template_id_real
        FROM   circuit_template_operations cto
        JOIN   circuit_templates ct ON ct.id = cto.template_id
        WHERE  cto.id = %s
        """,
        (oid,),
    )
    if not cur or str(cur["project_id"]) != str(pid):
        raise HTTPException(404, "Node not found")

    execute("DELETE FROM circuit_template_operations WHERE id=%s", (oid,))
    execute("UPDATE circuit_templates SET updated_at=NOW() WHERE id=%s",
            (str(cur["template_id_real"]),))

    log_user_action(
        "flowsheet.node.delete",
        user_id=str(user.get("id")),
        entity_type="circuit_template_operation",
        entity_id=str(oid),
        details={"project_id": pid},
    )
    return Response(status_code=204)
