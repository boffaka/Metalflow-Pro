# backend/routes/geotech.py
"""
Geotechnical endpoints:
  POST /api/v1/projects/{pid}/lims/tests/geotech  — G1-G6 LIMS test entry
  POST /api/v1/projects/{pid}/geotech/slope-stability
  GET  /api/v1/projects/{pid}/geotech/slope-stability
  POST /api/v1/projects/{pid}/geotech/tsf-design
"""
from __future__ import annotations
import json
import uuid
import logging
from typing import Any, Dict, Optional
from datetime import date

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from ..db import conn, release
    from ..auth import project_user
    from ..engines.geotech import (
        bishop_factor_of_safety,
        tsf_volume_capacity,
        tsf_raise_height,
        TSF_MIN_FS,
    )
    from ..engines.gistm import TSFDesignSnapshot
    from ..services import gistm as gistm_svc
except ImportError:
    from db import conn, release
    from auth import project_user
    from engines.geotech import (
        bishop_factor_of_safety,
        tsf_volume_capacity,
        tsf_raise_height,
        TSF_MIN_FS,
    )
    from engines.gistm import TSFDesignSnapshot
    from services import gistm as gistm_svc

router = APIRouter(tags=["geotech"])

VALID_GEOTECH_CODES = {"G1", "G2", "G3", "G4", "G5", "G6"}


class GeotechTestIn(BaseModel):
    sample_id: Optional[str] = None
    test_code: str
    results: Dict[str, Any] = {}
    laboratory: str
    test_date: Optional[date] = None
    notes: Optional[str] = None


class SlopeStabilityIn(BaseModel):
    location: str
    slope_angle_deg: float = Field(gt=0, lt=90)
    slope_height_m: float = Field(gt=0)
    cohesion_kpa: float = Field(ge=0)
    friction_angle_deg: float = Field(gt=0, lt=90)
    gamma_kn_m3: float = Field(gt=0)
    pore_pressure_ratio: float = Field(ge=0, le=1)


class TSFDesignIn(BaseModel):
    construction_method: str
    total_capacity_m3: float = Field(gt=0)
    annual_deposition_t: float = Field(gt=0)
    deposition_density_t_m3: float = Field(gt=0)
    embankment_area_ha: float = Field(gt=0)
    water_balance: Optional[Dict[str, Any]] = None
    notes: Optional[str] = None
    fs_post_liquefaction: Optional[float] = Field(default=None, gt=0)
    design_flood_return_yr: Optional[int] = Field(default=None, gt=0)
    site_pga_g: Optional[float] = Field(default=None, ge=0)


@router.post("/{pid}/lims/tests/geotech", status_code=201)
async def submit_geotech_test(pid: str, body: GeotechTestIn, _auth=Depends(project_user)):
    if body.test_code not in VALID_GEOTECH_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"test_code must be one of {sorted(VALID_GEOTECH_CODES)}"
        )
    record_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO geotech_tests
                   (id, project_id, sample_id, test_code, results,
                    laboratory, test_date, notes)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s)""",
                (record_id, pid,
                 body.sample_id, body.test_code,
                 json.dumps(body.results),
                 body.laboratory, body.test_date, body.notes)
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    return {"id": record_id, "test_code": body.test_code, "results": body.results}


@router.get("/{pid}/lims/tests/geotech")
async def list_geotech_tests(pid: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT gt.id, gt.test_code, gt.results, gt.laboratory,
                          gt.test_date, gt.notes, gt.sample_id,
                          ls.sample_id_display
                   FROM geotech_tests gt
                   LEFT JOIN lims_samples ls ON ls.id = gt.sample_id
                   WHERE gt.project_id = %s
                   ORDER BY gt.test_code, gt.id""",
                (pid,)
            )
            rows = cur.fetchall()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    result = []
    for r in rows:
        res = r[2] if isinstance(r[2], dict) else json.loads(r[2]) if r[2] else {}
        entry = {
            "id": str(r[0]),
            "test_code": r[1],
            "results": res,
            "laboratory": r[3],
            "test_date": str(r[4]) if r[4] else None,
            "notes": r[5],
            "sample_id": str(r[6]) if r[6] else None,
            "sample_id_display": r[7],
            # Flatten common fields for frontend compatibility
            "cohesion_kpa": res.get("cohesion_kpa"),
            "friction_angle_deg": res.get("friction_angle_deg"),
            "ucs_mpa": res.get("ucs_mpa"),
        }
        result.append(entry)
    return result


@router.post("/{pid}/geotech/slope-stability", status_code=201)
async def analyze_slope(pid: str, body: SlopeStabilityIn, _auth=Depends(project_user)):
    fs_static, fs_seismic = bishop_factor_of_safety(
        slope_angle_deg=body.slope_angle_deg,
        slope_height_m=body.slope_height_m,
        cohesion_kpa=body.cohesion_kpa,
        friction_angle_deg=body.friction_angle_deg,
        gamma_kn_m3=body.gamma_kn_m3,
        pore_pressure_ratio=body.pore_pressure_ratio,
    )
    is_compliant = fs_static >= 1.3 and fs_seismic >= 1.1
    record_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO slope_analyses
                   (id, project_id, location,
                    slope_angle_deg, slope_height_m,
                    cohesion_kpa, friction_angle_deg, gamma_kn_m3,
                    pore_pressure_ratio, method,
                    fs_static, fs_seismic, is_compliant,
                    failure_surface)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'Bishop',%s,%s,%s,%s::jsonb)""",
                (record_id, pid, body.location,
                 body.slope_angle_deg, body.slope_height_m,
                 body.cohesion_kpa, body.friction_angle_deg, body.gamma_kn_m3,
                 body.pore_pressure_ratio,
                 fs_static, fs_seismic, is_compliant,
                 "{}")
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    return {
        "id": record_id,
        "location": body.location,
        "fs_static": fs_static,
        "fs_seismic": fs_seismic,
        "is_compliant": is_compliant,
        # Seuils indicatifs type ouvrages / talus — vérifier normes locales (CNRC, Eurocode, mine régionale).
        "requirements": {"static": "≥ 1.3", "seismic": "≥ 1.1 (pseudo-statique)"},
    }


@router.get("/{pid}/geotech/slope-stability")
async def list_slope_analyses(pid: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT id, location, slope_angle_deg, slope_height_m,
                          fs_static, fs_seismic, is_compliant
                   FROM slope_analyses
                   WHERE project_id = %s
                   ORDER BY id DESC""",
                (pid,)
            )
            rows = cur.fetchall()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    return [
        {
            "id": str(r[0]), "location": r[1],
            "slope_name": r[1],
            "slope_angle_deg": r[2], "slope_height_m": r[3],
            "fs_static": r[4], "fs_seismic": r[5],
            "factor_of_safety": r[4], "seismic_fs": r[5],
            "is_compliant": r[6]
        }
        for r in rows
    ]


@router.post("/{pid}/geotech/tsf-design", status_code=201)
async def design_tsf(pid: str, body: TSFDesignIn, _auth=Depends(project_user)):
    if body.construction_method not in TSF_MIN_FS:
        raise HTTPException(
            status_code=422,
            detail=f"construction_method must be one of {sorted(TSF_MIN_FS.keys())}"
        )
    annual_vol = tsf_volume_capacity(
        total_tailings_t=body.annual_deposition_t,
        deposition_density_t_m3=body.deposition_density_t_m3,
    )
    raise_h = tsf_raise_height(
        annual_volume_m3=annual_vol,
        embankment_area_ha=body.embankment_area_ha,
    )
    # Slope stability with defaults for MAC compliance check
    fs_static, fs_seismic = bishop_factor_of_safety(
        slope_angle_deg=18.0,
        slope_height_m=max(raise_h * 5, 1.0),
        cohesion_kpa=0.0,
        friction_angle_deg=28.0,
        gamma_kn_m3=18.0,
        pore_pressure_ratio=0.15,
    )
    min_fs = TSF_MIN_FS[body.construction_method]
    is_compliant = fs_static >= min_fs["static"]
    record_id = str(uuid.uuid4())
    # Snapshot the active basis (if any) on the tsf_design row for traceability.
    active_basis = gistm_svc.get_active_basis(pid)
    basis_id = str(active_basis["id"]) if active_basis else None
    consequence_class = active_basis["consequence_class"] if active_basis else None

    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO tsf_design
                   (id, project_id, version,
                    construction_method, total_capacity_m3, annual_deposition_t,
                    raise_height_m, embankment_area_ha,
                    fs_static, fs_seismic, is_mac_compliant,
                    water_balance, notes,
                    gistm_basis_id, consequence_class_at_design)
                   VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)""",
                (record_id, pid,
                 body.construction_method,
                 body.total_capacity_m3, body.annual_deposition_t,
                 raise_h, body.embankment_area_ha,
                 fs_static, fs_seismic, is_compliant,
                 json.dumps(body.water_balance or {}),
                 body.notes,
                 basis_id, consequence_class)
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)

    # Evaluate against the active GISTM basis and persist any violations.
    tsf_snapshot = TSFDesignSnapshot(
        construction_method=body.construction_method,
        fs_static=fs_static,
        fs_seismic=fs_seismic,
        fs_post_liquefaction=body.fs_post_liquefaction,
        design_flood_return_yr=body.design_flood_return_yr,
        site_pga_g=body.site_pga_g,
    )
    _, violations = gistm_svc.evaluate_tsf_design(pid, record_id, tsf_snapshot)

    return {
        "id": record_id,
        "raise_height_m": raise_h,
        "annual_volume_m3": annual_vol,
        "fs_static": fs_static,
        "fs_seismic": fs_seismic,
        "is_mac_compliant": is_compliant,
        "gistm_basis_id": basis_id,
        "consequence_class_at_design": consequence_class,
        "gistm_violations": violations,
    }
