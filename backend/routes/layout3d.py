# backend/routes/layout3d.py
"""3D plant layout CRUD — positions (plant_layout_3d) and zones (layout_zones)."""
from __future__ import annotations
import psycopg2
import uuid, json, logging
from fastapi import APIRouter, Depends, HTTPException, Body

logger = logging.getLogger(__name__)

try:
    from ..db import conn, release
    from ..auth import project_user
except ImportError:
    from db import conn, release
    from auth import project_user

router = APIRouter()


@router.post("/zones", status_code=201)
async def create_zone(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    zone_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO layout_zones (id, project_id, zone_code, zone_name, color_hex, bbox) VALUES (%s, %s, %s, %s, %s, %s)",
                (zone_id, pid, payload.get("zone_code"), payload.get("zone_name"),
                 payload.get("color_hex", "#64748b"), json.dumps(payload.get("bbox", {})))
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
    return {"zone_id": zone_id}


@router.get("/zones")
async def list_zones(pid: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, zone_code, zone_name, color_hex, bbox FROM layout_zones WHERE project_id=%s",
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
    return [{"id": str(r[0]), "zone_code": r[1], "zone_name": r[2], "color_hex": r[3], "bbox": r[4]} for r in rows]


@router.post("/positions", status_code=201)
async def set_position(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    equipment_id = payload.get("equipment_id")
    if not equipment_id:
        raise HTTPException(400, "equipment_id required")
    position_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id FROM plant_layout_3d WHERE project_id=%s AND equipment_id=%s",
                (pid, equipment_id)
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "UPDATE plant_layout_3d SET x=%s, y=%s, z=%s, rotation_deg=%s, zone=%s, geometry_overrides=%s WHERE project_id=%s AND equipment_id=%s",
                    (payload.get("x", 0.0), payload.get("y", 0.0), payload.get("z", 0.0),
                     payload.get("rotation_deg", 0.0), payload.get("zone"),
                     json.dumps(payload.get("geometry_overrides", {})), pid, equipment_id)
                )
                position_id = str(existing[0])
            else:
                cur.execute(
                    "INSERT INTO plant_layout_3d (id, project_id, equipment_id, x, y, z, rotation_deg, zone, geometry_overrides) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (position_id, pid, equipment_id, payload.get("x", 0.0), payload.get("y", 0.0),
                     payload.get("z", 0.0), payload.get("rotation_deg", 0.0), payload.get("zone"),
                     json.dumps(payload.get("geometry_overrides", {})))
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
    return {"position_id": position_id}


@router.get("/positions")
async def get_positions(pid: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, equipment_id, x, y, z, rotation_deg, zone FROM plant_layout_3d WHERE project_id=%s",
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
    return [{"id": str(r[0]), "equipment_id": str(r[1]), "x": r[2], "y": r[3],
             "z": r[4], "rotation_deg": r[5], "zone": r[6]} for r in rows]


@router.post("/auto-arrange")
async def auto_arrange(pid: str, payload: dict = Body(default={}), _auth=Depends(project_user)):
    """Auto-arrange equipment using a zone-based grid layout."""
    ZONE_OFFSETS = {"100": (0, 0), "200": (60, 0), "300": (120, 0),
                    "400": (180, 0), "500": (240, 0), "600": (180, 80)}
    db = None
    arranged = 0
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute("SELECT id, name FROM equipment WHERE project_id=%s", (pid,))
            equipment = cur.fetchall()

        for i, (equip_id, equip_name) in enumerate(equipment):
            zone = "400"
            for z in ["100", "200", "300", "400", "500", "600"]:
                if z in str(equip_name or ""):
                    zone = z
                    break
            ox, oy = ZONE_OFFSETS.get(zone, (0, 0))
            x = ox + (i % 5) * 20.0
            y = oy + (i // 5) * 20.0
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO plant_layout_3d (id, project_id, equipment_id, x, y, z, rotation_deg, zone)
                       VALUES (%s, %s, %s, %s, %s, 0, 0, %s)
                       ON CONFLICT (project_id, equipment_id) DO UPDATE SET x=%s, y=%s, zone=%s""",
                    (str(uuid.uuid4()), pid, str(equip_id), x, y, zone, x, y, zone)
                )
            arranged += 1
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
    return {"arranged": arranged, "message": "Equipment positioned using zone-based grid layout"}
