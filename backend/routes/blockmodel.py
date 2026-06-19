import json
import logging
import threading
import time
from typing import Any, Dict, List

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:
    from constants import TROY_OZ_PER_GRAM

logger = logging.getLogger("mpdpms.blockmodel")

MAX_BLOCKS_PER_REQUEST = 50_000

# In-memory TTL cache for blocks/stats — full-table aggregations on the
# blocks table are expensive (4 GROUP BY scans on 70k+ rows) and are hit
# repeatedly by the v3 frontend's render loop. Cache invalidates on
# upload_blocks/clear_blocks. 30s ceiling regardless.
_STATS_TTL_SEC = 30
_stats_cache: dict[str, tuple[float, dict]] = {}
_stats_lock = threading.Lock()


def _stats_cache_get(config_id: str) -> dict | None:
    with _stats_lock:
        entry = _stats_cache.get(config_id)
        if entry and entry[0] > time.monotonic():
            return entry[1]
        return None


def _stats_cache_put(config_id: str, payload: dict) -> None:
    with _stats_lock:
        _stats_cache[config_id] = (time.monotonic() + _STATS_TTL_SEC, payload)


def _stats_cache_invalidate(config_id: str) -> None:
    with _stats_lock:
        _stats_cache.pop(config_id, None)


try:
    from ..auth import project_user
    from ..db import conn, execute, qall, qone, release
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import conn, execute, qall, qone, release


router = APIRouter(prefix="/api/v1/projects/{pid}/blockmodels", tags=["blockmodels"])


class BlockModelConfigIn(BaseModel):
    name: str = "Modele de base"
    x_origin: float = 0
    y_origin: float = 0
    z_origin: float = 0
    x_block_size: float = Field(default=10, gt=0)
    y_block_size: float = Field(default=10, gt=0)
    z_block_size: float = Field(default=5, gt=0)
    rotation_angle: float = 0


class BlockIn(BaseModel):
    i_index: int = 0
    j_index: int = 0
    k_index: int = 0
    x_center: float = 0
    y_center: float = 0
    z_center: float = 0
    density: float = Field(default=2.7, gt=0)
    volume: float = Field(default=500, gt=0)
    grade_au: float = Field(default=0, ge=0)
    rock_type: str = "Unknown"
    attributes: Dict[str, Any] = Field(default_factory=dict)


@router.get("")
@router.get("/")
def list_block_models(pid: str, user=Depends(project_user)):
    try:
        return qall("SELECT * FROM block_model_configs WHERE project_id = %s ORDER BY created_at DESC", (pid,))
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("")
@router.post("/")
def create_block_model(pid: str, body: BlockModelConfigIn, user=Depends(project_user)):
    try:
        row = execute(
            "INSERT INTO block_model_configs "
            "(project_id, name, x_origin, y_origin, z_origin, x_block_size, y_block_size, z_block_size, rotation_angle) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (project_id, name) DO UPDATE SET "
            "x_origin = EXCLUDED.x_origin, "
            "y_origin = EXCLUDED.y_origin, "
            "z_origin = EXCLUDED.z_origin, "
            "x_block_size = EXCLUDED.x_block_size, "
            "y_block_size = EXCLUDED.y_block_size, "
            "z_block_size = EXCLUDED.z_block_size, "
            "rotation_angle = EXCLUDED.rotation_angle, "
            "updated_at = NOW() "
            "RETURNING *",
            (
                pid,
                body.name,
                body.x_origin,
                body.y_origin,
                body.z_origin,
                body.x_block_size,
                body.y_block_size,
                body.z_block_size,
                body.rotation_angle,
            ),
        )
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/{config_id}")
def delete_block_model(pid: str, config_id: str, user=Depends(project_user)):
    cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
    execute("DELETE FROM blocks WHERE config_id = %s", (config_id,))
    execute("DELETE FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
    _stats_cache_invalidate(config_id)
    return {"ok": True}


@router.delete("/{config_id}/blocks")
def delete_all_blocks(pid: str, config_id: str, user=Depends(project_user)):
    cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
    execute("DELETE FROM blocks WHERE config_id = %s", (config_id,))
    _stats_cache_invalidate(config_id)
    return {"ok": True}



@router.get("/{config_id}/blocks/stats")
def block_stats(pid: str, config_id: str, user=Depends(project_user)):
    cached = _stats_cache_get(config_id)
    if cached is not None:
        return cached
    cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
    stats = qone(
        "SELECT COUNT(*) as total_blocks, "
        "COALESCE(SUM(tonnage), 0) as total_tonnage, "
        "COALESCE(AVG(grade_au), 0) as avg_grade, "
        "COALESCE(MIN(grade_au), 0) as min_grade, "
        "COALESCE(MAX(grade_au), 0) as max_grade, "
        "COALESCE(AVG(density), 0) as avg_density, "
        "COALESCE(SUM(tonnage * grade_au) / NULLIF(SUM(tonnage), 0), 0) as weighted_grade "
        "FROM blocks WHERE config_id = %s",
        (config_id,),
    )
    rock_types = qall(
        "SELECT rock_type, COUNT(*) as block_count, "
        "COALESCE(SUM(tonnage), 0) as total_tonnage, "
        "COALESCE(AVG(grade_au), 0) as avg_grade, "
        "COALESCE(MAX(grade_au), 0) as max_grade, "
        "COALESCE(SUM(tonnage * grade_au) / NULLIF(SUM(tonnage), 0), 0) as weighted_grade "
        "FROM blocks WHERE config_id = %s GROUP BY rock_type ORDER BY total_tonnage DESC",
        (config_id,),
    )

    max_grade = float(stats["max_grade"]) if stats and stats["max_grade"] else 5.0
    bin_width = max(0.1, round(max_grade / 12, 2))
    histogram = qall(
        "SELECT FLOOR(grade_au / %s) * %s as grade_min, "
        "FLOOR(grade_au / %s) * %s + %s as grade_max, "
        "COUNT(*) as count "
        "FROM blocks WHERE config_id = %s "
        "GROUP BY FLOOR(grade_au / %s) "
        "ORDER BY grade_min",
        (bin_width, bin_width, bin_width, bin_width, bin_width, config_id, bin_width),
    )

    z_levels = qall(
        "SELECT z_center as z_level, COUNT(*) as block_count, "
        "COALESCE(AVG(grade_au), 0) as avg_grade "
        "FROM blocks WHERE config_id = %s "
        "GROUP BY z_center ORDER BY z_center DESC",
        (config_id,),
    )

    payload = {
        **(stats or {}),
        "by_rock_type": rock_types,
        "grade_histogram": histogram,
        "z_levels": z_levels,
    }
    _stats_cache_put(config_id, payload)
    return payload


@router.get("/{config_id}/blocks/cutoff")
def block_cutoff(pid: str, config_id: str, user=Depends(project_user)):
    cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
    rows = qall(
        "SELECT c.cutoff, "
        "  COUNT(b.config_id) as block_count, "
        "  COALESCE(SUM(b.tonnage), 0) as tonnage, "
        "  COALESCE(SUM(b.tonnage * b.grade_au) / NULLIF(SUM(b.tonnage), 0), 0) as avg_grade, "
        f"  COALESCE(SUM(b.tonnage * b.grade_au) * {TROY_OZ_PER_GRAM}, 0) as contained_oz "
        "FROM (VALUES (0.0),(0.3),(0.5),(0.7),(1.0),(1.5),(2.0),(3.0),(5.0)) AS c(cutoff) "
        "LEFT JOIN blocks b ON b.config_id = %s AND b.grade_au >= c.cutoff "
        "GROUP BY c.cutoff ORDER BY c.cutoff",
        (config_id,),
    )
    return [dict(r) for r in rows]


@router.get("/{config_id}/summary")
def get_block_model_summary(pid: str, config_id: str, user=Depends(project_user)):
    try:
        cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
        if not cfg:
            raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
        summary = qone(
            "SELECT COUNT(*) as total_blocks, "
            "       COALESCE(SUM(COALESCE(tonnage, volume * density, 0)), 0) as total_tonnage, "
            "       COALESCE("
            "         SUM(COALESCE(tonnage, volume * density, 0) * COALESCE(grade_au, 0))"
            "         / NULLIF(SUM(COALESCE(tonnage, volume * density, 0)), 0),"
            "         0"
            "       ) as avg_grade "
            "FROM blocks WHERE config_id = %s",
            (config_id,),
        )
        return summary
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{config_id}/blocks/cutoff")
def get_cutoff_sensitivity(pid: str, config_id: str, user=Depends(project_user)):
    """Resource sensitivity table at multiple cut-off grades."""
    try:
        cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
        if not cfg:
            raise HTTPException(status_code=404, detail="Configuration introuvable")
        rows = qall(
            "WITH cutoffs(co) AS ("
            "  SELECT unnest(ARRAY[0,0.25,0.5,0.75,1.0,1.5,2.0,2.5,3.0,4.0,5.0]::numeric[])"
            "), bdata AS ("
            "  SELECT COALESCE(tonnage, volume * density, 0) AS eff_tonnage, grade_au "
            "  FROM blocks WHERE config_id = %s"
            ")"
            "SELECT c.co::float AS cutoff,"
            "  COUNT(*) FILTER (WHERE b.grade_au >= c.co) AS block_count,"
            "  COALESCE(SUM(b.eff_tonnage) FILTER (WHERE b.grade_au >= c.co), 0)::float AS tonnage,"
            "  COALESCE("
            "    SUM(b.eff_tonnage * b.grade_au) FILTER (WHERE b.grade_au >= c.co)"
            "    / NULLIF(SUM(b.eff_tonnage) FILTER (WHERE b.grade_au >= c.co), 0)"
            "  , 0)::float AS avg_grade"
            " FROM cutoffs c LEFT JOIN bdata b ON TRUE"
            " GROUP BY c.co ORDER BY c.co",
            (config_id,),
        )
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{config_id}/blocks")
def list_blocks(
    pid: str,
    config_id: str,
    limit: int = 100,
    offset: int = 0,
    rock_type: str = None,
    grade_min: float = None,
    grade_max: float = None,
    z_min: float = None,
    z_max: float = None,
    user=Depends(project_user),
):
    """Paginated block retrieval with optional filters. Returns aggregate stats."""
    try:
        cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
        if not cfg:
            raise HTTPException(status_code=404, detail="Configuration introuvable")
        limit = min(limit, 500)

        conditions = ["config_id = %s"]
        params: list = [config_id]
        if rock_type:
            conditions.append("rock_type = %s")
            params.append(rock_type)
        if grade_min is not None:
            conditions.append("grade_au >= %s")
            params.append(grade_min)
        if grade_max is not None:
            conditions.append("grade_au <= %s")
            params.append(grade_max)
        if z_min is not None:
            conditions.append("z_center >= %s")
            params.append(z_min)
        if z_max is not None:
            conditions.append("z_center <= %s")
            params.append(z_max)

        where = " AND ".join(conditions)
        rows = qall(
            f"SELECT id, i_index, j_index, k_index, x_center, y_center, z_center, "
            f"density, volume, COALESCE(tonnage, volume * density, 0) AS tonnage, grade_au, rock_type "
            f"FROM blocks WHERE {where} "
            f"ORDER BY z_center DESC, i_index, j_index LIMIT %s OFFSET %s",
            (*params, limit, offset),
        )
        agg = qone(
            f"SELECT COUNT(*) AS total, "
            f"COALESCE(SUM(COALESCE(tonnage, volume * density, 0)), 0) AS total_tonnage, "
            f"COALESCE("
            f"  SUM(COALESCE(tonnage, volume * density, 0) * COALESCE(grade_au, 0)) "
            f"  / NULLIF(SUM(COALESCE(tonnage, volume * density, 0)), 0), "
            f"  0"
            f") AS avg_grade "
            f"FROM blocks WHERE {where}",
            tuple(params),
        )
        return {
            "blocks": rows,
            "total": int(agg["total"]) if agg else 0,
            "total_tonnage": float(agg["total_tonnage"]) if agg else 0.0,
            "avg_grade": float(agg["avg_grade"]) if agg else 0.0,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/{config_id}/blocks/stats")
def get_blocks_stats(pid: str, config_id: str, user=Depends(project_user)):
    """Distribution stats: by rock type + grade histogram buckets."""
    try:
        cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
        if not cfg:
            raise HTTPException(status_code=404, detail="Configuration introuvable")
        by_rock = qall(
            "SELECT rock_type, COUNT(*) as block_count, "
            "COALESCE(SUM(COALESCE(tonnage, volume * density, 0)), 0) as total_tonnage, "
            "COALESCE("
            "  SUM(COALESCE(tonnage, volume * density, 0) * COALESCE(grade_au, 0))"
            "  / NULLIF(SUM(COALESCE(tonnage, volume * density, 0)), 0),"
            "  0"
            ") as avg_grade, "
            "COALESCE(MAX(grade_au), 0) as max_grade "
            "FROM blocks WHERE config_id = %s "
            "GROUP BY rock_type ORDER BY total_tonnage DESC",
            (config_id,),
        )
        grade_hist = qall(
            "WITH bounds AS (SELECT GREATEST(MAX(grade_au), 1) as max_grade FROM blocks WHERE config_id = %s) "
            "SELECT width_bucket(grade_au, 0, bounds.max_grade, 10) as bucket, "
            "MIN(grade_au) as grade_min, MAX(grade_au) as grade_max, COUNT(*) as count "
            "FROM blocks, bounds WHERE config_id = %s "
            "GROUP BY bucket ORDER BY bucket",
            (config_id, config_id),
        )
        z_levels = qall(
            "SELECT ROUND(z_center::numeric, 0) as z_level, COUNT(*) as block_count, "
            "COALESCE("
            "  SUM(COALESCE(tonnage, volume * density, 0) * COALESCE(grade_au, 0))"
            "  / NULLIF(SUM(COALESCE(tonnage, volume * density, 0)), 0),"
            "  0"
            ") as avg_grade "
            "FROM blocks WHERE config_id = %s "
            "GROUP BY z_level ORDER BY z_level DESC LIMIT 20",
            (config_id,),
        )
        return {"by_rock_type": by_rock, "grade_histogram": grade_hist, "z_levels": z_levels}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/{config_id}")
def delete_block_model(pid: str, config_id: str, user=Depends(project_user)):
    """Delete a block model configuration and all its blocks."""
    try:
        cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
        if not cfg:
            raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
        execute("DELETE FROM blocks WHERE config_id = %s", (config_id,))
        execute("DELETE FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
        _stats_cache_invalidate(config_id)
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/{config_id}/blocks")
def clear_blocks(pid: str, config_id: str, user=Depends(project_user)):
    try:
        cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
        if not cfg:
            raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
        execute("DELETE FROM blocks WHERE config_id = %s", (config_id,))
        _stats_cache_invalidate(config_id)
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/{config_id}/blocks", status_code=201)
def upload_blocks(pid: str, config_id: str, body: List[BlockIn], user=Depends(project_user)):
    cfg = qone("SELECT id FROM block_model_configs WHERE id = %s AND project_id = %s", (config_id, pid))
    if not cfg:
        raise HTTPException(status_code=404, detail="Configuration introuvable pour ce projet")
    if not body:
        raise HTTPException(status_code=400, detail="Aucun bloc a importer")
    if len(body) > MAX_BLOCKS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Trop de blocs: {len(body)} > {MAX_BLOCKS_PER_REQUEST}. Divisez l'import en plusieurs requêtes.",
        )
    c = conn()
    cur = None
    try:
        cur = c.cursor()
        rows = [
            (
                config_id,
                block.i_index,
                block.j_index,
                block.k_index,
                block.x_center,
                block.y_center,
                block.z_center,
                block.density,
                block.volume,
                block.grade_au,
                block.rock_type,
                json.dumps(block.attributes),
            )
            for block in body
        ]
        psycopg2.extras.execute_batch(
            cur,
            "INSERT INTO blocks (config_id, i_index, j_index, k_index, x_center, y_center, z_center, density, volume, grade_au, rock_type, attributes) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            rows,
            page_size=500,
        )
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        logger.exception("block import failed for config_id=%s project=%s", config_id, pid)
        raise HTTPException(status_code=500, detail="Echec de l'import des blocs")
    finally:
        if cur is not None:
            cur.close()
        release(c)
    _stats_cache_invalidate(config_id)
    return {"ok": True, "count": len(body)}
