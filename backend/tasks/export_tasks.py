# backend/tasks/export_tasks.py
"""
Celery tasks for P&ID exports (SVG, PDF, DXF) and 3D layout exports.
DXF generation uses ezdxf (R2018 compatible).
"""
from __future__ import annotations
import io, logging
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

try:
    from celery_app import celery_app
except ImportError:
    from backend.celery_app import celery_app


def generate_pid_dxf(
    elements: List[Dict[str, Any]],
    title: str = "P&ID",
    sheet: str = "1",
) -> bytes:
    """
    Generate a DXF R2018 file from P&ID element data.

    Each element dict should have: {type, tag, x, y, symbol (optional)}
    Returns DXF file as UTF-8 encoded bytes.
    """
    try:
        import ezdxf

        doc = ezdxf.new(dxfversion="R2018")
        doc.header["$MEASUREMENT"] = 1  # metric
        msp = doc.modelspace()

        # Title block
        msp.add_text(
            f"P&ID Sheet {sheet}: {title}",
            dxfattribs={"height": 5.0, "insert": (10, -10)}
        )

        for elem in elements:
            x = float(elem.get("x", 0)) / 10.0
            y = float(elem.get("y", 0)) / -10.0
            tag = str(elem.get("tag", ""))
            etype = str(elem.get("type", ""))

            if etype == "equipment":
                msp.add_lwpolyline(
                    [(x-3, y-2), (x+3, y-2), (x+3, y+2), (x-3, y+2), (x-3, y-2)],
                    dxfattribs={"layer": "EQUIPMENT", "color": 3}
                )
            elif etype == "instrument":
                msp.add_circle(
                    center=(x, y), radius=1.5,
                    dxfattribs={"layer": "INSTRUMENTS", "color": 5}
                )
            elif etype == "valve":
                msp.add_lwpolyline(
                    [(x-1, y-1.5), (x+1, y-1.5), (x, y+1.5), (x-1, y-1.5)],
                    dxfattribs={"layer": "VALVES", "color": 1}
                )

            if tag:
                msp.add_text(tag, dxfattribs={"height": 1.2, "insert": (x+2, y+0.5)})

        buf = io.StringIO()
        doc.write(buf)
        return doc.encode(buf.getvalue())
    except Exception as e:
        logger.error("DXF generation failed for P&ID title=%s sheet=%s (n_elements=%d): %s", title, sheet, len(elements), e)
        raise RuntimeError(f"DXF export generation failed: {e}") from e


@celery_app.task(name="tasks.export_tasks.export_pid_dxf_task")
def export_pid_dxf_task(project_id: str, diagram_id: str) -> dict:
    """Fetch P&ID data from DB and generate DXF. Saves to uploads/exports/."""
    try:
        from db import conn, release
    except ImportError:
        from backend.db import conn, release
    import os, uuid

    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT elements, title, sheet_number FROM pid_diagrams WHERE id=%s",
                (diagram_id,)
            )
            row = cur.fetchone()
    finally:
        if db is not None:
            release(db)

    if not row:
        return {"error": "Diagram not found"}

    elements = row[0] if isinstance(row[0], list) else []
    title = row[1] or "P&ID"
    sheet = str(row[2] or "1")

    dxf_bytes = generate_pid_dxf(elements=elements, title=title, sheet=sheet)

    export_dir = "/app/uploads/exports"
    os.makedirs(export_dir, exist_ok=True)
    filename = f"pid_{diagram_id}_{uuid.uuid4().hex[:8]}.dxf"
    filepath = os.path.join(export_dir, filename)
    with open(filepath, "wb") as f:
        f.write(dxf_bytes)

    return {"status": "done", "filepath": filepath, "filename": filename}
