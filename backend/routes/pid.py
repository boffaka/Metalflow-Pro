# backend/routes/pid.py
"""
P&ID diagram CRUD API.

KEY INVARIANT: When an instrument with a control function type
(FIC, TIC, LIC, AIC, PIC, FRC, TRC) is saved to pid_instruments, this
endpoint automatically inserts a row in pid_loops with
instrument_id FK and default Kp/Ti/Td = NULL (untuned).
"""
from __future__ import annotations
import psycopg2
import uuid, json, logging
from fastapi import APIRouter, Depends, HTTPException, Body

logger = logging.getLogger(__name__)

CONTROL_FUNCTION_TYPES = frozenset(["FIC", "TIC", "LIC", "AIC", "PIC", "FRC", "TRC"])

try:
    from ..db import conn, release
    from ..auth import project_user
except ImportError:
    from db import conn, release
    from auth import project_user

router = APIRouter()


def _auto_create_pid_loop(c, project_id: str, instrument_id: str, tag: str):
    """Insert a pid_loops row for a new control instrument. Idempotent."""
    loop_id = str(uuid.uuid4())
    with c.cursor() as cur:
        cur.execute(
            "SELECT id FROM pid_loops WHERE project_id=%s AND loop_tag=%s",
            (project_id, tag)
        )
        if cur.fetchone():
            return
        cur.execute(
            "INSERT INTO pid_loops (id, project_id, instrument_id, loop_tag) VALUES (%s, %s, %s, %s)",
            (loop_id, project_id, instrument_id, tag)
        )
    logger.debug("Auto-created pid_loop %s for tag %s", loop_id, tag)


@router.post("", status_code=201)
async def create_diagram(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    diagram_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO pid_diagrams
                   (id, project_id, sheet_number, title, area_code, elements, connections, revision)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (diagram_id, pid, payload.get("sheet_number", 1), payload.get("title", "Untitled"),
                 payload.get("area_code", ""), json.dumps(payload.get("elements", [])),
                 json.dumps(payload.get("connections", [])), payload.get("revision", "A"))
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            try:
                db.rollback()
            except Exception:  # intentional: ignore optional lookup failure
                pass
        raise
    finally:
        if db is not None:
            release(db)
    return {"diagram_id": diagram_id}


@router.get("")
async def list_diagrams(pid: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, sheet_number, title, area_code, revision, updated_at FROM pid_diagrams WHERE project_id=%s ORDER BY sheet_number",
                (pid,)
            )
            rows = cur.fetchall()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        if db is not None:
            release(db)
    return [{"id": str(r[0]), "sheet_number": r[1], "title": r[2],
             "area_code": r[3], "revision": r[4], "updated_at": str(r[5])} for r in rows]


@router.get("/{diagram_id}")
async def get_diagram(pid: str, diagram_id: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, sheet_number, title, area_code, elements, connections, revision FROM pid_diagrams WHERE id=%s AND project_id=%s",
                (diagram_id, pid)
            )
            row = cur.fetchone()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        if db is not None:
            release(db)
    if not row:
        raise HTTPException(404, "Diagram not found")
    return {"id": str(row[0]), "sheet_number": row[1], "title": row[2],
            "area_code": row[3], "elements": row[4], "connections": row[5], "revision": row[6]}


@router.put("/{diagram_id}")
async def update_diagram(pid: str, diagram_id: str, payload: dict = Body(...), _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "UPDATE pid_diagrams SET elements=%s, connections=%s, title=%s, revision=%s, updated_at=NOW() WHERE id=%s AND project_id=%s",
                (json.dumps(payload.get("elements", [])), json.dumps(payload.get("connections", [])),
                 payload.get("title"), payload.get("revision", "A"), diagram_id, pid)
            )
            if cur.rowcount == 0:
                raise HTTPException(404, "Diagram not found")
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            try:
                db.rollback()
            except Exception:  # intentional: ignore optional lookup failure
                pass
        raise
    finally:
        if db is not None:
            release(db)
    return {"status": "updated"}


@router.post("/{diagram_id}/instruments", status_code=201)
async def save_instrument(pid: str, diagram_id: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """Save a P&ID instrument. Auto-creates pid_loops row for control instrument types."""
    instrument_id = str(uuid.uuid4())
    tag = payload.get("tag", "")
    instrument_type = payload.get("instrument_type", "").upper()

    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO pid_instruments
                   (id, diagram_id, equipment_id, tag, service, instrument_type,
                    loop_number, area, p_rating, t_rating, fluid, line_size, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (instrument_id, diagram_id, payload.get("equipment_id"), tag,
                 payload.get("service", ""), instrument_type, payload.get("loop_number", ""),
                 payload.get("area", ""), payload.get("p_rating", ""), payload.get("t_rating", ""),
                 payload.get("fluid", ""), payload.get("line_size", ""), payload.get("notes", ""))
            )
        if instrument_type in CONTROL_FUNCTION_TYPES:
            _auto_create_pid_loop(db, pid, instrument_id, tag)
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            try:
                db.rollback()
            except Exception:  # intentional: ignore optional lookup failure
                pass
        raise
    finally:
        if db is not None:
            release(db)
    return {"instrument_id": instrument_id, "tag": tag,
            "pid_loop_created": instrument_type in CONTROL_FUNCTION_TYPES}


@router.get("/{diagram_id}/instruments")
async def list_instruments(pid: str, diagram_id: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, tag, service, instrument_type, loop_number, area FROM pid_instruments WHERE diagram_id=%s",
                (diagram_id,)
            )
            rows = cur.fetchall()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        if db is not None:
            release(db)
    return [{"id": str(r[0]), "tag": r[1], "service": r[2], "instrument_type": r[3],
             "loop_number": r[4], "area": r[5]} for r in rows]


@router.post("/auto-generate")
async def auto_generate_from_flowsheet(pid: str, payload: dict = Body(default={}), _auth=Depends(project_user)):
    """Auto-generate P&ID sheets from existing flowsheet blocks. One sheet per area."""
    areas = [
        ("100", "Crushing"), ("200", "Grinding"), ("300", "Flotation"),
        ("400", "CIL Leaching"), ("500", "ADR & Smelting"), ("600", "Tailings & Environment"),
    ]
    AREA_INSTRUMENTS = {
        "400": [("FIC-400-001", "CIL Feed Flow", "FIC"), ("AIC-400-002", "CIL pH Control", "AIC"),
                ("AIC-400-003", "DO Control", "AIC"), ("AIC-400-004", "NaCN Concentration", "AIC"),
                ("LIC-400-005", "CIL Tank 1 Level", "LIC")],
        "500": [("TIC-500-001", "Elution Temperature", "TIC")],
        "200": [("FIC-200-001", "Mill Feed Rate", "FIC")],
    }

    db = None
    diagrams_created = 0
    try:
        db = conn()
        for area_code, area_name in areas:
            diagram_id = str(uuid.uuid4())
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO pid_diagrams
                       (id, project_id, sheet_number, title, area_code, elements, connections)
                       VALUES (%s, %s, %s, %s, %s, '[]', '[]')
                       ON CONFLICT DO NOTHING""",
                    (diagram_id, pid, int(area_code) // 100, f"{area_name} P&ID", area_code)
                )
                if cur.rowcount > 0:
                    diagrams_created += 1
            for tag, service, itype in AREA_INSTRUMENTS.get(area_code, []):
                instrument_id = str(uuid.uuid4())
                with db.cursor() as cur:
                    cur.execute(
                        """INSERT INTO pid_instruments
                           (id, diagram_id, tag, service, instrument_type, area)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (tag) DO NOTHING""",
                        (instrument_id, diagram_id, tag, service, itype, area_code)
                    )
                if itype in CONTROL_FUNCTION_TYPES:
                    _auto_create_pid_loop(db, pid, instrument_id, tag)
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            try:
                db.rollback()
            except Exception:  # intentional: ignore optional lookup failure
                pass
        raise
    finally:
        if db is not None:
            release(db)
    return {"diagrams_created": diagrams_created, "areas": [a[0] for a in areas]}
