"""
MPDPMS — Data traceability routes.
Traces the provenance chain from any result back to its LIMS source data.
"""
from __future__ import annotations

import logging
import psycopg2
from fastapi import APIRouter, HTTPException, Depends

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user
    from ..db import qone, qall
except ImportError:
    from auth import project_user
    from db import qone, qall

router = APIRouter(prefix="/api/v1/projects", tags=["traceability"])


@router.get("/{pid}/traceability/{entity_type}/{entity_id}")
def trace_provenance(pid: str, entity_type: str, entity_id: str, user=Depends(project_user)):
    try:
        table_map = {
            "scenario_evaluation": "scenario_evaluations",
            "mass_balance_stream": "mass_balance_streams",
        }
        table = table_map.get(entity_type)
        if not table:
            raise HTTPException(400, f"Entity type '{entity_type}' non supporte pour tracabilite")

        entity = qone(
            f"SELECT id, param_sources FROM {table} WHERE id = %s AND project_id = %s",
            (entity_id, pid),
        )
        if not entity:
            raise HTTPException(404, "Entite non trouvee")

        audit_events = qall(
            "SELECT action, field_name, old_value, new_value, source, timestamp "
            "FROM audit_events WHERE project_id = %s AND entity_type = %s AND entity_id = %s "
            "ORDER BY timestamp DESC LIMIT 50",
            (pid, entity_type, entity_id),
        )

        import_logs = qall(
            "SELECT id, test_type, import_type, samples_count, accepted_count, "
            "rejected_count, created_at FROM lims_import_log "
            "WHERE project_id = %s ORDER BY created_at DESC LIMIT 20",
            (pid,),
        )

        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "param_sources": entity.get("param_sources") or {},
            "audit_events": audit_events or [],
            "lims_imports": import_logs or [],
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
