"""
Analytics API — process tags, data ingestion, KPIs, anomaly events, forecasts.

Routes (all under /api/v1/projects/{pid}/analytics/):
  POST /tags              — Create process tag (201)
  GET  /tags              — List process tags
  POST /import            — CSV upload → bulk insert tag_readings
  GET  /kpi               — Latest KPI snapshot
  GET  /trends            — Tag readings time-series (tag_id, start, end, limit)
  GET  /forecast          — Forecast entries from kpi_snapshots
  POST /connectors        — Create data connector (201)
  GET  /connectors        — List data connectors
"""
from __future__ import annotations
import uuid, json, csv, io, logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, Body

logger = logging.getLogger(__name__)

try:
    from ..db import conn, release
    from ..auth import project_user
except ImportError:
    from db import conn, release
    from auth import project_user

router = APIRouter()


# ─── Process Tags ─────────────────────────────────────────────────────────────

@router.post("/tags", status_code=201)
async def create_tag(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """Create a new process tag."""
    if not payload.get("tag_name"):
        raise HTTPException(status_code=400, detail="tag_name is required")
    tag_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO process_tags
                   (id, project_id, tag_name, description, area, unit, data_type,
                    normal_min, normal_target, normal_max, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    tag_id, pid,
                    payload.get("tag_name"), payload.get("description"),
                    payload.get("area"), payload.get("unit", ""),
                    payload.get("data_type", "float"),
                    payload.get("normal_min"), payload.get("normal_target"),
                    payload.get("normal_max"), payload.get("source", "manual"),
                )
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
    return {"tag_id": tag_id}


@router.get("/tags")
async def list_tags(pid: str, _auth=Depends(project_user)):
    """List all process tags for a project."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, tag_name, description, area, unit, data_type,
                          normal_min, normal_target, normal_max, source
                   FROM process_tags
                   WHERE project_id = %s
                   ORDER BY tag_name""",
                (pid,)
            )
            rows = cur.fetchall()
    finally:
        if db is not None:
            release(db)
    return [
        {
            "tag_id": str(r[0]),
            "id": str(r[0]),
            "tag_name": r[1],
            "description": r[2],
            "area": r[3],
            "unit": r[4],
            "data_type": r[5],
            "normal_min": r[6],
            "normal_target": r[7],
            "normal_max": r[8],
            "source": r[9],
        }
        for r in rows
    ]


# ─── CSV Import ────────────────────────────────────────────────────────────────

@router.post("/import")
async def import_csv(pid: str, file: UploadFile = File(...), _auth=Depends(project_user)):
    """Upload a CSV file and bulk-insert readings into tag_readings.

    Expected CSV format:
        timestamp, TAG_NAME_1, TAG_NAME_2, ...
        2026-01-01T08:00:00Z, 620.5, ...
    """
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    if reader.fieldnames is None or "timestamp" not in reader.fieldnames:
        raise HTTPException(400, "CSV must contain a 'timestamp' column")

    tag_columns = [f for f in reader.fieldnames if f != "timestamp"]
    if not tag_columns:
        raise HTTPException(400, "CSV must contain at least one tag column")

    # Resolve tag names → ids
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT tag_name, id FROM process_tags WHERE project_id = %s AND tag_name = ANY(%s)",
                (pid, tag_columns)
            )
            tag_map = {row[0]: str(row[1]) for row in cur.fetchall()}

        rows_imported = 0
        with db.cursor() as cur:
            for row in reader:
                raw_ts = row.get("timestamp", "").strip()
                if not raw_ts:
                    continue
                try:
                    ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                except ValueError:
                    logger.warning("Skipping invalid timestamp: %s", raw_ts)
                    continue

                for col in tag_columns:
                    if col not in tag_map:
                        continue
                    raw_val = row.get(col, "").strip()
                    if raw_val == "":
                        continue
                    try:
                        value = float(raw_val)
                    except ValueError:
                        continue
                    cur.execute(
                        """INSERT INTO tag_readings (time, tag_id, value)
                           VALUES (%s, %s, %s)
                           ON CONFLICT DO NOTHING""",
                        (ts, tag_map[col], value)
                    )
                    rows_imported += 1

        db.commit()
    except HTTPException:
        raise
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

    unmatched = [c for c in (reader.fieldnames or []) if c not in ("timestamp", "time", "datetime") and c not in tag_map]
    return {"rows_imported": rows_imported, "filename": file.filename, "unmatched_columns": unmatched}


# ─── KPI Snapshot ─────────────────────────────────────────────────────────────

@router.get("/kpis/latest")
async def get_kpi_latest_alias(pid: str, _auth=Depends(project_user)):
    """Alias for /kpi — used by HTML frontend."""
    return await get_kpi_snapshot(pid, _auth)


@router.get("/kpi")
async def get_kpi_snapshot(pid: str, _auth=Depends(project_user)):
    """Return the latest KPI snapshot for the project."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, period, kpi_data, snapshot_time
                   FROM kpi_snapshots
                   WHERE project_id = %s AND period != 'forecast'
                   ORDER BY snapshot_time DESC
                   LIMIT 1""",
                (pid,)
            )
            row = cur.fetchone()
    finally:
        if db is not None:
            release(db)

    if not row:
        return {"message": "No KPI snapshot available yet", "kpi_data": {}}

    kpi_data = row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}")
    # Flatten kpi_data fields to top level for frontend compatibility
    result = {
        "snapshot_id": str(row[0]),
        "period": row[1],
        "kpi_data": kpi_data,
        "snapshot_time": str(row[3]) if row[3] else None,
    }
    result.update(kpi_data)
    return result


# ─── Trends ───────────────────────────────────────────────────────────────────

@router.get("/trends")
async def get_trends(
    pid: str,
    tag_id: str = Query(...),
    start: str = Query(None),
    end: str = Query(None),
    limit: int = Query(1000, ge=1, le=10000),
    _auth=Depends(project_user),
):
    """Return time-series readings for a tag."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            # Verify tag belongs to this project
            cur.execute(
                "SELECT id FROM process_tags WHERE id = %s AND project_id = %s",
                (tag_id, pid)
            )
            if not cur.fetchone():
                raise HTTPException(404, "Tag not found for this project")

            params: list = [tag_id]

            if start:
                try:
                    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    params.append(start_dt)
                except ValueError:
                    raise HTTPException(400, "Invalid start datetime format")

            if end:
                try:
                    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
                    params.append(end_dt)
                except ValueError:
                    raise HTTPException(400, "Invalid end datetime format")

            params.append(limit)
            query = (
                "SELECT time, value FROM tag_readings WHERE tag_id = %s"
                + (" AND time >= %s" if start else "")
                + (" AND time <= %s" if end else "")
                + " ORDER BY time DESC LIMIT %s"
            )
            cur.execute(query, params)
            rows = cur.fetchall()
    except HTTPException:
        raise
    finally:
        if db is not None:
            release(db)

    return [{"time": str(r[0]), "value": r[1]} for r in rows]


# ─── Forecast ─────────────────────────────────────────────────────────────────

@router.get("/forecast")
async def get_forecast(pid: str, _auth=Depends(project_user)):
    """Return forecast entries from kpi_snapshots (period='forecast')."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, period, kpi_data, snapshot_time
                   FROM kpi_snapshots
                   WHERE project_id = %s AND period = 'forecast'
                   ORDER BY snapshot_time DESC""",
                (pid,)
            )
            rows = cur.fetchall()
    finally:
        if db is not None:
            release(db)

    return [
        {
            "snapshot_id": str(r[0]),
            "period": r[1],
            "kpi_data": r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}"),
            "snapshot_time": str(r[3]) if r[3] else None,
        }
        for r in rows
    ]


# ─── Data Connectors ──────────────────────────────────────────────────────────

@router.post("/connectors", status_code=201)
async def create_connector(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """Register a new data connector (OPC-UA, Modbus, MQTT, manual, etc.)."""
    if not payload.get("name") or not payload.get("protocol"):
        raise HTTPException(status_code=400, detail="name and protocol are required")
    connector_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO data_connectors
                   (id, project_id, name, protocol, config, poll_interval_s)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    connector_id, pid,
                    payload.get("name"),
                    payload.get("protocol"),
                    json.dumps(payload.get("config", {})),
                    payload.get("poll_interval_s", 60),
                )
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
    return {"connector_id": connector_id}


@router.get("/connectors")
async def list_connectors(pid: str, _auth=Depends(project_user)):
    """List all data connectors for a project."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, name, protocol, config, poll_interval_s, enabled, created_at
                   FROM data_connectors
                   WHERE project_id = %s
                   ORDER BY name""",
                (pid,)
            )
            rows = cur.fetchall()
    finally:
        if db is not None:
            release(db)
    return [
        {
            "connector_id": str(r[0]),
            "name": r[1],
            "protocol": r[2],
            "config": r[3] if isinstance(r[3], dict) else json.loads(r[3] or "{}"),
            "poll_interval_s": r[4],
            "enabled": r[5],
            "created_at": str(r[6]) if r[6] else None,
        }
        for r in rows
    ]
