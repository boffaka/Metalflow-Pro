"""
MPDPMS — Immutable audit trail for NI 43-101 compliance.

Records every data mutation as an append-only event with SHA-256
checksum chaining for tamper detection.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger("mpdpms.audit")

try:
    from .db import execute, qone
except ImportError:
    from db import execute, qone


def _compute_checksum(event: dict, previous_checksum: str) -> str:
    """SHA-256 of the event payload chained with the previous checksum."""
    canonical = json.dumps(
        {k: v for k, v in sorted(event.items()) if k != "checksum"},
        sort_keys=True,
        default=str,
    )
    raw = f"{previous_checksum}:{canonical}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_audit_event(
    *,
    user_id: str | None,
    project_id: str | None,
    entity_type: str,
    entity_id: str | None,
    action: str,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    source: str = "web",
    ip_address: str | None = None,
    previous_checksum: str = "0" * 64,
) -> dict:
    """Build an audit event dict with computed checksum."""
    event = {
        "user_id": user_id,
        "project_id": project_id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action": action,
        "field_name": field_name,
        "old_value": old_value,
        "new_value": new_value,
        "source": source,
        "ip_address": ip_address,
    }
    event["checksum"] = _compute_checksum(event, previous_checksum)
    return event


def _get_last_checksum(project_id: str | None) -> str:
    """Fetch the last checksum in the chain for a project."""
    try:
        row = qone(
            "SELECT checksum FROM audit_events WHERE project_id = %s "
            "ORDER BY timestamp DESC LIMIT 1",
            (project_id,),
        )
        return row["checksum"] if row else "0" * 64
    except Exception as e:
        logger.error("Failed to fetch last audit checksum for project_id=%s: %s — using zero checksum", project_id, e)
        return "0" * 64


def record_event(
    *,
    user_id: str | None,
    project_id: str | None,
    entity_type: str,
    entity_id: str | None,
    action: str,
    field_name: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    source: str = "web",
    ip_address: str | None = None,
) -> dict:
    """Record an immutable audit event. Returns the inserted row."""
    try:
        import psycopg2.extras

        previous = _get_last_checksum(project_id)
        event = build_audit_event(
            user_id=user_id,
            project_id=project_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            source=source,
            ip_address=ip_address,
            previous_checksum=previous,
        )
        row = execute(
            "INSERT INTO audit_events "
            "(user_id, project_id, entity_type, entity_id, action, "
            "field_name, old_value, new_value, source, ip_address, checksum) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::inet,%s) RETURNING *",
            (
                event["user_id"], event["project_id"], event["entity_type"],
                event["entity_id"], event["action"], event["field_name"],
                psycopg2.extras.Json(event["old_value"]) if event["old_value"] is not None else None,
                psycopg2.extras.Json(event["new_value"]) if event["new_value"] is not None else None,
                event["source"], event["ip_address"], event["checksum"],
            ),
        )
        logger.info(
            "audit event recorded",
            extra={"action": action, "entity_type": entity_type, "entity_id": entity_id, "project_id": project_id},
        )
        return row
    except Exception as e:
        logger.error(
            "Failed to record audit event action=%s entity_type=%s entity_id=%s project_id=%s: %s",
            action, entity_type, entity_id, project_id, e,
        )
        return {}
