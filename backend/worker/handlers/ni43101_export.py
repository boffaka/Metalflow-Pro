"""Worker handler for NI 43-101 export. Reads sections + project from DB,
calls the pure build_export compute, stores the artifact in job_artifacts."""
from __future__ import annotations

from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor

try:
    from compute.ni43101_export import build_export
    from worker.registry import register
except ImportError:  # pragma: no cover
    from backend.compute.ni43101_export import build_export
    from backend.worker.registry import register


def _load_sections(ctx) -> list[dict]:
    with ctx.conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT section_number, subsection_key, sort_order, "
            "       title_fr, title_en, content_fr, content_en "
            "FROM ni43101_sections WHERE project_id = %s "
            "ORDER BY section_number, sort_order",
            (str(ctx.project_id),),
        )
        return [dict(r) for r in cur.fetchall()]


def _load_project(ctx) -> dict:
    with ctx.conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT project_name, project_code FROM projects WHERE id = %s",
            (str(ctx.project_id),),
        )
        row = cur.fetchone()
        return dict(row) if row else {}


def handle_ni43101_export(payload: dict[str, Any], ctx) -> dict:
    sections = _load_sections(ctx)
    if not sections:
        raise LookupError("no NI 43-101 sections for project — call /generate first")
    project = _load_project(ctx)

    filename, content_type, data = build_export(
        {"fmt": payload.get("fmt"), "lang": payload.get("lang"),
         "sections": sections, "project": project},
        ctx,
    )

    with ctx.conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO job_artifacts "
            "(job_id, filename, content_type, data, byte_size) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (str(ctx.job_id), filename, content_type,
             psycopg2.Binary(data), len(data)),
        )
        artifact_id = cur.fetchone()["id"]

    return {
        "kind": "job_artifact",
        "id": artifact_id,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": len(data),
    }


register("ni43101_export", handle_ni43101_export)
