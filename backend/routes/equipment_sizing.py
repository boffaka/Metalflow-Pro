# backend/routes/equipment_sizing.py
"""
Equipment sizing API endpoints.

Routes under /api/v1/projects/{pid}/equipment/:
  POST /size                  — Size a single piece of equipment
  POST /size-all              — Auto-size all equipment from simulation params
  GET  /catalog               — Query vendor catalog
  POST /{equip_id}/select-vendor — Assign vendor model
  GET  /capex-estimate        — Total CAPEX from all sizings
"""
from __future__ import annotations
import psycopg2
import uuid, json, logging
from fastapi import APIRouter, Depends, HTTPException, Query, Body

logger = logging.getLogger(__name__)

try:
    from ..db import conn, release
    from ..auth import project_user
    from .. import config as _app_config
    from ..engines.equipment_sizing import (
        size_ball_mill, size_sag_mill, size_flotation,
        size_thickener, size_cil_tanks, size_ew_cells, apply_lang_factors
    )
except ImportError:
    from db import conn, release
    from auth import project_user
    import config as _app_config
    from engines.equipment_sizing import (
        size_ball_mill, size_sag_mill, size_flotation,
        size_thickener, size_cil_tanks, size_ew_cells, apply_lang_factors
    )

router = APIRouter()

SIZERS = {
    "ball_mill": lambda p: size_ball_mill(**p),
    "sag_mill": lambda p: size_sag_mill(**p),
    "flotation": lambda p: size_flotation(**p),
    "thickener": lambda p: size_thickener(**p),
    "cil_tanks": lambda p: size_cil_tanks(**p),
    "ew_cells": lambda p: size_ew_cells(**p),
}


def _rough_capex_usd(outputs: dict) -> float | None:
    """Analytical CAPEX proxy from sizing outputs (tunable via ``backend/config.py``)."""
    if "power_kw" in outputs and outputs["power_kw"] > 0:
        return _app_config.SIZING_CAPEX_MILL_USD_COEFF * (
            outputs["power_kw"] ** _app_config.SIZING_CAPEX_MILL_POWER_EXP
        )
    if "v_total_m3" in outputs:
        return _app_config.SIZING_CAPEX_TANK_USD_PER_M3 * outputs["v_total_m3"]
    if "n_cells" in outputs:
        return _app_config.SIZING_CAPEX_EW_USD_PER_CELL * outputs["n_cells"]
    if "area_total_m2" in outputs:
        return _app_config.SIZING_CAPEX_THICKENER_USD_PER_M2 * outputs["area_total_m2"]
    return None


@router.post("/{pid}/equipment/size")
async def size_equipment(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """Size a single equipment item and store in equipment_sizing table."""
    equip_type = payload.get("equipment_type", "").lower()
    params = payload.get("params", {})

    sizer = SIZERS.get(equip_type)
    if not sizer:
        raise HTTPException(400, f"Unknown equipment_type: {equip_type}. Valid: {list(SIZERS.keys())}")

    try:
        outputs = sizer(params)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, f"Sizing error: {e}")

    capex = _rough_capex_usd(outputs)

    sizing_id = str(uuid.uuid4())
    equipment_id = payload.get("equipment_id") or str(uuid.uuid4())

    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO equipment_sizing
                   (id, equipment_id, project_id, method, inputs, outputs, capex_estimate_usd)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (sizing_id, equipment_id, pid, equip_type,
                 json.dumps(params), json.dumps(outputs), capex)
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

    return {"sizing_id": sizing_id, "equipment_type": equip_type,
            "outputs": outputs, "capex_estimate_usd": capex}


@router.post("/{pid}/equipment/size-all")
async def size_all_equipment(pid: str, payload: dict = Body(...), _auth=Depends(project_user)):
    """Auto-size all major equipment items from simulation parameters."""
    sp = payload.get("simulation_params", {})

    sizing_map = [
        ("sag_mill",   {"spi_kwh_t": sp.get("spi_kwh_t", 10.0), "tph": sp.get("tph", float(_app_config.DEFAULT_TARGET_TPH_FALLBACK))}),
        ("ball_mill",  {"wi": sp.get("wi", 14.0), "tph": sp.get("tph", float(_app_config.DEFAULT_TARGET_TPH_FALLBACK)),
                        "p80_um": sp.get("p80_um", float(_app_config.DEFAULT_P80_UM)), "f80_um": sp.get("f80_um", float(_app_config.DEFAULT_BM_F80_UM))}),
        ("cil_tanks",  {"q_m3h": sp.get("q_cil_m3h", 600.0), "srt_h": sp.get("srt_cil_h", 24.0)}),
        ("thickener",  {"tpd": sp.get("tpd_solids", 3600.0), "ua_m2_t_d": sp.get("ua_m2_t_d", 0.08)}),
        ("ew_cells",   {"oz_per_day": sp.get("oz_per_day", 500.0)}),
    ]

    # Compute all sizings first (no DB)
    sized = []
    db_rows = []
    for equip_type, params in sizing_map:
        sizer = SIZERS.get(equip_type)
        if not sizer:
            continue
        try:
            outputs = sizer(params)
            # Compute CAPEX
            capex = _rough_capex_usd(outputs)
            sizing_id = str(uuid.uuid4())
            sized.append({"equipment_type": equip_type, "sizing_id": sizing_id, "outputs": outputs})
            db_rows.append((sizing_id, str(uuid.uuid4()), pid, equip_type,
                            json.dumps(params), json.dumps(outputs), capex))
        except Exception as e:  # intentional: collect error and continue
            sized.append({"equipment_type": equip_type, "error": str(e)})

    # Single transaction for all inserts
    if db_rows:
        db = None
        try:
            db = conn()
            with db.cursor() as cur:
                for row in db_rows:
                    cur.execute(
                        """INSERT INTO equipment_sizing
                           (id, equipment_id, project_id, method, inputs, outputs, capex_estimate_usd)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                        row
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

    return {"sized_equipment": sized, "total": len(sized)}


@router.get("/{pid}/equipment/catalog")
async def get_vendor_catalog(
    pid: str,
    family: str = Query(None, description="Filter by equipment family"),
    _auth=Depends(project_user),
):
    """Query vendor catalog — optionally filter by equipment family."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            if family:
                cur.execute(
                    "SELECT * FROM vendor_catalog WHERE equipment_family=%s ORDER BY manufacturer",
                    (family,)
                )
            else:
                cur.execute("SELECT * FROM vendor_catalog ORDER BY equipment_family, manufacturer")
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    finally:
        if db is not None:
            release(db)
    return [dict(zip(cols, [str(v) if hasattr(v, 'hex') else v for v in row])) for row in rows]


@router.post("/{pid}/equipment/{equipment_id}/select-vendor")
async def select_vendor(pid: str, equipment_id: str, payload: dict = Body(...),
                        _auth=Depends(project_user)):
    """Assign a vendor catalog entry to an equipment item."""
    catalog_id = payload.get("catalog_id")
    if not catalog_id:
        raise HTTPException(400, "catalog_id required")

    selection_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute("SELECT reference_capex_usd FROM vendor_catalog WHERE id=%s", (catalog_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Catalog entry not found")
            capex = float(row[0]) if row[0] else None
            cur.execute(
                """INSERT INTO equipment_selections
                   (id, equipment_id, catalog_id, quantity, is_spare, capex_usd, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (selection_id, equipment_id, catalog_id,
                 payload.get("quantity", 1), payload.get("is_spare", False),
                 capex, payload.get("notes", ""))
            )
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
    return {"selection_id": selection_id, "equipment_id": equipment_id, "catalog_id": catalog_id}


@router.get("/{pid}/equipment/capex-estimate")
async def get_capex_estimate(pid: str, _auth=Depends(project_user)):
    """Aggregate CAPEX from all equipment sizings + Lang assembly factors."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(capex_estimate_usd), 0) FROM equipment_sizing WHERE project_id=%s",
                (pid,)
            )
            total_equipment = float(cur.fetchone()[0])
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    finally:
        if db is not None:
            release(db)

    lang = apply_lang_factors(total_equipment)
    return {
        "equipment_cost_usd": total_equipment,
        **lang,
    }
