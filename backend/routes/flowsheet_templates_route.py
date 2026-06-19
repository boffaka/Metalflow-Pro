"""
GET  /api/v1/flowsheet-templates                       → list 28 templates
POST /api/v1/projects/{pid}/flowsheet/from-template    → apply a template to a project
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

try:
    from ..auth import current_user, project_user
    from ..db import execute
    from ..flowsheet_templates import TEMPLATES, get_template_by_code, get_templates_grouped
    from ..logging_config import log_user_action
except ImportError:  # pragma: no cover
    from auth import current_user, project_user
    from db import execute
    from flowsheet_templates import TEMPLATES, get_template_by_code, get_templates_grouped
    from logging_config import log_user_action


router = APIRouter(prefix="/api/v1", tags=["flowsheet-templates"])
logger = logging.getLogger("mpdpms.flowsheet_templates")


@router.get("/flowsheet-templates")
def list_templates(user=Depends(current_user)):
    return {
        "total": len(TEMPLATES),
        "groups": get_templates_grouped(),
    }


@router.post("/projects/{pid}/flowsheet/from-template", status_code=201)
def apply_template(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Body: {"template_code": "AU_CIL_OXIDE", "template_name": "Optional name"}.

    Creates a fresh circuit_template + cascades all operations from the template.
    Returns the created template_id. If the project already has a flowsheet, a
    new circuit_template is created (the existing one is not deleted — the user
    can keep it as history).
    """
    code = body.get("template_code")
    if not code:
        raise HTTPException(400, "template_code required")
    tpl = get_template_by_code(code)
    if not tpl:
        raise HTTPException(404, f"Unknown template_code: {code}")

    name = body.get("template_name") or tpl["name"]
    template_row = execute(
        "INSERT INTO circuit_templates (project_id, name) VALUES (%s, %s) RETURNING *",
        (pid, name),
    )
    tid = str(template_row["id"])

    # Insert nodes in tree order so parent_op_id can reference already-inserted rows.
    # We resolve local 'parent' string IDs to real DB UUIDs as we go.
    local_to_uuid: dict[str, str] = {}
    # Sort: roots first, then BFS by parent
    pending = list(tpl["nodes"])
    inserted = 0
    safety = 0
    while pending and safety < 10000:
        safety += 1
        progress = False
        for node in list(pending):
            if node["parent"] is None or node["parent"] in local_to_uuid:
                parent_uuid = local_to_uuid.get(node["parent"]) if node["parent"] else None
                row = execute(
                    """
                    INSERT INTO circuit_template_operations
                      (template_id, op_code, instance_label, sort_order, parent_op_id,
                       node_label, product_kind, values_source)
                    VALUES (%s, %s, '', %s, %s, %s, %s, 'lims_auto')
                    RETURNING id
                    """,
                    (tid, node["op_code"], node.get("sort", 0),
                     parent_uuid, node.get("label"), node.get("product_kind")),
                )
                local_to_uuid[node["id"]] = str(row["id"])
                pending.remove(node)
                inserted += 1
                progress = True
        if not progress:
            raise HTTPException(500, "Template tree has unresolvable parent references")

    log_user_action(
        "flowsheet.template.apply",
        user_id=str(user.get("id")),
        entity_type="circuit_template",
        entity_id=tid,
        details={"project_id": pid, "template_code": code, "node_count": inserted},
    )

    return {
        "template_id": tid,
        "template_code": code,
        "node_count": inserted,
    }
