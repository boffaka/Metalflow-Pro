from __future__ import annotations

import logging
import psycopg2
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Query

logger = logging.getLogger("mpdpms.automation")
from pydantic import BaseModel, Field

import uuid
import json as _json

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release, build_update_sets, paginated_qall
except ImportError:
    from auth import project_user
    from db import qone, qall, execute, conn, release, build_update_sets, paginated_qall

try:
    from ..engines.pid_tuning import tune_ziegler_nichols, tune_lambda
except ImportError:
    from engines.pid_tuning import (
        tune_ziegler_nichols, tune_lambda
    )

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["automation"])

_WRITE_ROLES = ("Project Manager", "Process Engineer", "Metallurgist")
_VALID_ROLES = {"controlled", "manipulated", "disturbance", "measured", "constraint"}
_VALID_PRIORITIES = {"low", "medium", "high", "critical"}
_VALID_CRITICALITIES = {"medium", "high", "critical"}


class ControlVariableIn(BaseModel):
    tag: str = Field(..., max_length=100)
    area: Optional[str] = Field(default=None, max_length=100)
    variable_name: str = Field(..., max_length=200)
    variable_role: str = Field(..., max_length=20)
    unit: Optional[str] = Field(default=None, max_length=50)
    normal_min: Optional[float] = None
    normal_target: Optional[float] = None
    normal_max: Optional[float] = None
    critical_low: Optional[float] = None
    critical_high: Optional[float] = None
    measurement_source: Optional[str] = Field(default=None, max_length=200)
    control_strategy: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ControlVariablePatch(BaseModel):
    area: Optional[str] = Field(default=None, max_length=100)
    variable_name: Optional[str] = Field(default=None, max_length=200)
    variable_role: Optional[str] = Field(default=None, max_length=20)
    unit: Optional[str] = Field(default=None, max_length=50)
    normal_min: Optional[float] = None
    normal_target: Optional[float] = None
    normal_max: Optional[float] = None
    critical_low: Optional[float] = None
    critical_high: Optional[float] = None
    measurement_source: Optional[str] = Field(default=None, max_length=200)
    control_strategy: Optional[str] = Field(default=None, max_length=500)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ControlAlarmIn(BaseModel):
    variable_id: str
    alarm_code: str = Field(..., max_length=100)
    priority: str = Field(default="medium", max_length=20)
    trigger_condition: str = Field(..., max_length=500)
    consequence: Optional[str] = Field(default=None, max_length=1000)
    operator_action: Optional[str] = Field(default=None, max_length=1000)
    shutdown_required: bool = False


class ControlAlarmPatch(BaseModel):
    priority: Optional[str] = Field(default=None, max_length=20)
    trigger_condition: Optional[str] = Field(default=None, max_length=500)
    consequence: Optional[str] = Field(default=None, max_length=1000)
    operator_action: Optional[str] = Field(default=None, max_length=1000)
    shutdown_required: Optional[bool] = None


class ControlInterlockIn(BaseModel):
    interlock_code: str = Field(..., max_length=100)
    equipment_tag: Optional[str] = Field(default=None, max_length=100)
    cause_condition: str = Field(..., max_length=500)
    protective_action: str = Field(..., max_length=500)
    reset_requirement: Optional[str] = Field(default=None, max_length=500)
    criticality: str = Field(default="high", max_length=20)


class ControlInterlockPatch(BaseModel):
    equipment_tag: Optional[str] = Field(default=None, max_length=100)
    cause_condition: Optional[str] = Field(default=None, max_length=500)
    protective_action: Optional[str] = Field(default=None, max_length=500)
    reset_requirement: Optional[str] = Field(default=None, max_length=500)
    criticality: Optional[str] = Field(default=None, max_length=20)


def _serialize(row: dict) -> dict:
    out = dict(row)
    for k in ("id", "project_id", "variable_id"):
        if out.get(k):
            out[k] = str(out[k])
    if out.get("created_at"):
        out["created_at"] = str(out["created_at"])
    return out


def _validate_variable_ranges(body: ControlVariableIn | ControlVariablePatch) -> None:
    data = body.model_dump(exclude_none=True)
    if data.get("variable_role") and data["variable_role"] not in _VALID_ROLES:
        raise HTTPException(422, f"variable_role invalide: {_VALID_ROLES}")
    if "normal_min" in data and "normal_max" in data and data["normal_min"] > data["normal_max"]:
        raise HTTPException(422, "normal_min ne peut pas être supérieur à normal_max")
    if "critical_low" in data and "critical_high" in data and data["critical_low"] > data["critical_high"]:
        raise HTTPException(422, "critical_low ne peut pas être supérieur à critical_high")


@router.get("/automation/variables")
def list_control_variables(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM control_variables WHERE project_id=%s ORDER BY area NULLS LAST, tag", (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/automation/variables", status_code=201)
def create_control_variable(pid: str, body: ControlVariableIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _validate_variable_ranges(body)
        row = execute(
            "INSERT INTO control_variables (project_id, tag, area, variable_name, variable_role, unit, normal_min, normal_target, normal_max, critical_low, critical_high, measurement_source, control_strategy, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, body.tag, body.area, body.variable_name, body.variable_role, body.unit, body.normal_min, body.normal_target, body.normal_max, body.critical_low, body.critical_high, body.measurement_source, body.control_strategy, body.notes),
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/automation/variables/{variable_id}")
def patch_control_variable(pid: str, variable_id: str, body: ControlVariablePatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        existing = qone("SELECT * FROM control_variables WHERE id=%s AND project_id=%s", (variable_id, pid))
        if not existing:
            raise HTTPException(404, "Variable introuvable")
        _validate_variable_ranges(body)
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            return _serialize(existing)
        vals += [variable_id, pid]
        row = execute(f"UPDATE control_variables SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/automation/alarms")
def list_control_alarms(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall(
            "SELECT a.*, v.tag, v.variable_name FROM control_alarms a LEFT JOIN control_variables v ON v.id = a.variable_id WHERE a.project_id=%s ORDER BY a.priority DESC, a.alarm_code",
            (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/automation/alarms", status_code=201)
def create_control_alarm(pid: str, body: ControlAlarmIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        if body.priority not in _VALID_PRIORITIES:
            raise HTTPException(422, f"priority invalide: {_VALID_PRIORITIES}")
        variable = qone("SELECT id FROM control_variables WHERE id=%s AND project_id=%s", (body.variable_id, pid))
        if not variable:
            raise HTTPException(404, "Variable de contrôle introuvable")
        row = execute(
            "INSERT INTO control_alarms (project_id, variable_id, alarm_code, priority, trigger_condition, consequence, operator_action, shutdown_required) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, body.variable_id, body.alarm_code, body.priority, body.trigger_condition, body.consequence, body.operator_action, body.shutdown_required),
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/automation/alarms/{alarm_id}")
def patch_control_alarm(pid: str, alarm_id: str, body: ControlAlarmPatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        existing = qone("SELECT * FROM control_alarms WHERE id=%s AND project_id=%s", (alarm_id, pid))
        if not existing:
            raise HTTPException(404, "Alarme introuvable")
        if body.priority and body.priority not in _VALID_PRIORITIES:
            raise HTTPException(422, f"priority invalide: {_VALID_PRIORITIES}")
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            return _serialize(existing)
        vals += [alarm_id, pid]
        row = execute(f"UPDATE control_alarms SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/automation/interlocks")
def list_control_interlocks(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM control_interlocks WHERE project_id=%s ORDER BY criticality DESC, interlock_code", (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/automation/interlocks", status_code=201)
def create_control_interlock(pid: str, body: ControlInterlockIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        if body.criticality not in _VALID_CRITICALITIES:
            raise HTTPException(422, f"criticality invalide: {_VALID_CRITICALITIES}")
        row = execute(
            "INSERT INTO control_interlocks (project_id, interlock_code, equipment_tag, cause_condition, protective_action, reset_requirement, criticality) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, body.interlock_code, body.equipment_tag, body.cause_condition, body.protective_action, body.reset_requirement, body.criticality),
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/automation/interlocks/{interlock_id}")
def patch_control_interlock(pid: str, interlock_id: str, body: ControlInterlockPatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        existing = qone("SELECT * FROM control_interlocks WHERE id=%s AND project_id=%s", (interlock_id, pid))
        if not existing:
            raise HTTPException(404, "Interlock introuvable")
        if body.criticality and body.criticality not in _VALID_CRITICALITIES:
            raise HTTPException(422, f"criticality invalide: {_VALID_CRITICALITIES}")
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            return _serialize(existing)
        vals += [interlock_id, pid]
        row = execute(f"UPDATE control_interlocks SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/automation/readiness")
def automation_readiness(pid: str, user=Depends(project_user)):
    try:
        variable_count = int((qone("SELECT COUNT(*) AS n FROM control_variables WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        alarm_count = int((qone("SELECT COUNT(*) AS n FROM control_alarms WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        interlock_count = int((qone("SELECT COUNT(*) AS n FROM control_interlocks WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        critical_alarms = int((qone("SELECT COUNT(*) AS n FROM control_alarms WHERE project_id=%s AND priority='critical'", (pid,)) or {}).get("n", 0))
        critical_interlocks = int((qone("SELECT COUNT(*) AS n FROM control_interlocks WHERE project_id=%s AND criticality='critical'", (pid,)) or {}).get("n", 0))
        readiness_score = round(min(100.0, (variable_count * 10.0) + (alarm_count * 7.5) + (interlock_count * 12.5)), 1)
        return {
            "project_id": pid,
            "readiness_score": readiness_score,
            "counts": {
                "variables": variable_count,
                "alarms": alarm_count,
                "interlocks": interlock_count,
                "critical_alarms": critical_alarms,
                "critical_interlocks": critical_interlocks,
            },
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/automation/variables/{variable_id}")
def delete_control_variable(pid: str, variable_id: str, user=Depends(project_user)):
    try:
        if user["role"] != "Project Manager":
            raise HTTPException(403, "Seul un Project Manager peut supprimer une variable")
        execute("DELETE FROM control_variables WHERE id=%s AND project_id=%s", (variable_id, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/automation/alarms/{alarm_id}")
def delete_control_alarm(pid: str, alarm_id: str, user=Depends(project_user)):
    try:
        if user["role"] != "Project Manager":
            raise HTTPException(403, "Seul un Project Manager peut supprimer une alarme")
        execute("DELETE FROM control_alarms WHERE id=%s AND project_id=%s", (alarm_id, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.delete("/automation/interlocks/{interlock_id}")
def delete_control_interlock(pid: str, interlock_id: str, user=Depends(project_user)):
    try:
        if user["role"] != "Project Manager":
            raise HTTPException(403, "Seul un Project Manager peut supprimer un interlock")
        execute("DELETE FROM control_interlocks WHERE id=%s AND project_id=%s", (interlock_id, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/automation/seed-defaults")
def seed_default_control_register(pid: str, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        defaults = [
            ("CIL-PH-101", "CIL", "pH lixiviation", "controlled", "pH", 10.2, 10.7, 11.0, 10.0, 11.2, "Analyses en ligne", "Ajout lait de chaux"),
            ("CIL-CN-101", "CIL", "NaCN libre", "controlled", "mg/L", 200.0, 350.0, 500.0, 150.0, 550.0, "Analyseur CN / labo", "Ajout NaCN"),
            ("CIL-DO-101", "CIL", "Oxygène dissous", "controlled", "mg/L", 5.0, 7.5, 10.0, 4.0, 12.0, "Sonde DO", "Aération / O2"),
            ("THK-UF-101", "Thickening", "Densité underflow épaississeur", "constraint", "%sol", 50.0, 58.0, 65.0, 45.0, 70.0, "Densimètre", "Dosage floculant / débit soutirage"),
        ]
        created = 0
        for item in defaults:
            row = execute(
                "INSERT INTO control_variables (project_id, tag, area, variable_name, variable_role, unit, normal_min, normal_target, normal_max, critical_low, critical_high, measurement_source, control_strategy) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id, tag) DO NOTHING RETURNING id",
                (pid, *item),
            )
            if row:
                created += 1
        return {"ok": True, "created": created}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.post("/automation/seed-safeguards")
def seed_default_safeguards(pid: str, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        variables = {r["tag"]: r["id"] for r in (qall("SELECT id, tag FROM control_variables WHERE project_id=%s", (pid,)) or [])}
        created_alarms = 0
        created_interlocks = 0
        alarm_defaults = [
            (variables.get("CIL-PH-101"), "ALM-CIL-PH-LOW", "high", "pH < 10.0", "Risque HCN et baisse récupération", "Augmenter la chaux et vérifier l'instrumentation", True),
            (variables.get("CIL-CN-101"), "ALM-CIL-CN-LOW", "high", "NaCN libre < 200 mg/L", "Cinétique de lixiviation insuffisante", "Ajuster dosage NaCN et vérifier préparation réactifs", False),
            (variables.get("CIL-DO-101"), "ALM-CIL-DO-LOW", "medium", "DO < 5 mg/L", "Cinétique Au réduite", "Vérifier aérateurs / injection oxygène", False),
        ]
        for variable_id, alarm_code, priority, trigger_condition, consequence, operator_action, shutdown_required in alarm_defaults:
            if not variable_id:
                continue
            row = execute(
                "INSERT INTO control_alarms (project_id, variable_id, alarm_code, priority, trigger_condition, consequence, operator_action, shutdown_required) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id, alarm_code) DO NOTHING RETURNING id",
                (pid, variable_id, alarm_code, priority, trigger_condition, consequence, operator_action, shutdown_required),
            )
            if row:
                created_alarms += 1
        interlock_defaults = [
            ("ILK-CIL-PH-TRIP", "CIL", "pH < 9.5 confirmé", "Arrêt alimentation minerai et maintien agitation", "Réarmement après retour pH > 10.2 et confirmation opérateur", "critical"),
            ("ILK-CN-DET-FAIL", "CN_DESTRUCT", "Détox CN indisponible", "Bloquer envoi vers résidus non traités", "Réarmement après rétablissement détox et autorisation supervision", "critical"),
        ]
        for interlock_code, equipment_tag, cause_condition, protective_action, reset_requirement, criticality in interlock_defaults:
            row = execute(
                "INSERT INTO control_interlocks (project_id, interlock_code, equipment_tag, cause_condition, protective_action, reset_requirement, criticality) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (project_id, interlock_code) DO NOTHING RETURNING id",
                (pid, interlock_code, equipment_tag, cause_condition, protective_action, reset_requirement, criticality),
            )
            if row:
                created_interlocks += 1
        return {"ok": True, "alarms_created": created_alarms, "interlocks_created": created_interlocks}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


# ── PID Loops ──────────────────────────────────────────────────────────────

@router.get("/automation/pid-loops")
async def list_pid_loops(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall(
            "SELECT id, loop_tag, kp, ti_s, td_s, tuning_method, is_cascade FROM pid_loops WHERE project_id=%s ORDER BY loop_tag",
            (pid,), limit=limit, offset=offset) or []
        return [dict(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/automation/pid-loops", status_code=201)
async def create_pid_loop(pid: str, payload: dict, user=Depends(project_user)):
    loop_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO pid_loops (id, project_id, loop_tag, tuning_method)
                   VALUES (%s, %s, %s, %s)""",
                (loop_id, pid, payload.get("loop_tag"), payload.get("tuning_method"))
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    return {"loop_id": loop_id}


@router.post("/automation/pid-loops/{loop_id}/tune")
async def tune_loop(pid: str, loop_id: str, payload: dict, user=Depends(project_user)):
    """Compute Kp/Ti/Td from tuning method and store in pid_loops."""
    method = payload.get("method", "ziegler_nichols").lower()

    if method == "ziegler_nichols":
        result = tune_ziegler_nichols(
            ku=float(payload.get("ku", 2.0)),
            pu_s=float(payload.get("pu_s", 60.0)),
            controller_type=payload.get("controller_type", "PI"),
        )
    elif method == "lambda":
        result = tune_lambda(
            K=float(payload.get("K", 1.5)),
            tau_s=float(payload.get("tau_s", 120.0)),
            theta_s=float(payload.get("theta_s", 15.0)),
            lambda_s=float(payload.get("lambda_s", 60.0)),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown tuning method: {method}")

    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """UPDATE pid_loops
                   SET kp=%s, ti_s=%s, td_s=%s, tuning_method=%s
                   WHERE id=%s AND project_id=%s""",
                (result["kp"], result.get("ti_s"), result.get("td_s"), method, loop_id, pid)
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)

    return result


# ── Grafcet Sequences ──────────────────────────────────────────────────────

@router.post("/automation/grafcet", status_code=201)
async def create_grafcet(pid: str, payload: dict, user=Depends(project_user)):
    seq_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO grafcet_sequences
                   (id, project_id, sequence_name, area, steps, transitions, version)
                   VALUES (%s, %s, %s, %s, %s, %s, 1)""",
                (seq_id, pid, payload.get("sequence_name"),
                 payload.get("area"),
                 _json.dumps(payload.get("steps", [])),
                 _json.dumps(payload.get("transitions", [])))
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    return {"sequence_id": seq_id}


@router.get("/automation/grafcet")
async def list_grafcet(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall(
            "SELECT id, sequence_name, area, version, is_verified FROM grafcet_sequences WHERE project_id=%s",
            (pid,), limit=limit, offset=offset) or []
        return [dict(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ── FAT/SAT Checklists ─────────────────────────────────────────────────────

@router.post("/automation/fat-sat-checklists/generate")
async def generate_fat_checklists(pid: str, payload: dict, user=Depends(project_user)):
    """Auto-generate FAT or SAT checklists from equipment table."""
    checklist_type = payload.get("checklist_type", "FAT").upper()
    equipment = qall("SELECT id, name FROM equipment WHERE project_id=%s", (pid,)) or []

    db = None
    created = 0
    try:
        db = conn()
        for equip in equipment:
            equip_id = str(equip["id"])
            equip_name = equip["name"]
            item_id = str(uuid.uuid4())
            with db.cursor() as cur:
                cur.execute(
                    """INSERT INTO fat_sat_checklists
                       (id, project_id, checklist_type, equipment_id,
                        test_description, acceptance_criteria)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (item_id, pid, checklist_type, equip_id,
                     f"Functional test — {equip_name}",
                     "Equipment operates per design spec without alarms")
                )
            created += 1
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    return {"checklists_created": created, "checklist_type": checklist_type}


@router.get("/automation/fat-sat-checklists")
async def list_fat_checklists(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall(
            "SELECT id, checklist_type, test_description, acceptance_criteria, is_passed "
            "FROM fat_sat_checklists WHERE project_id=%s ORDER BY checklist_type",
            (pid,), limit=limit, offset=offset) or []
        return [dict(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ── Cause & Effect Matrix ──────────────────────────────────────────────────

@router.get("/automation/cause-effect")
async def get_cause_effect(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall(
            "SELECT id, cause_tag, cause_description, effects FROM cause_effect_matrix WHERE project_id=%s",
            (pid,), limit=limit, offset=offset) or []
        return [dict(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
