from __future__ import annotations

import logging
from typing import Any, Optional

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, build_update_sets, paginated_qall
    from ..audit import record_event
except ImportError:
    from auth import project_user
    from db import qone, qall, execute, build_update_sets, paginated_qall
    from audit import record_event

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["scenarios"])

_WRITE_ROLES = ("Project Manager", "Process Engineer", "Metallurgist", "Cost Engineer")
_VALID_SCENARIO_TYPES = {"process", "economic", "flowsheet", "integrated"}
_VALID_SCENARIO_STATUSES = {"draft", "candidate", "selected", "archived"}

# Multi-criteria scenario score (0–100 inputs × weights). Includes explicit economic pillar.
_SCENARIO_EVAL_WEIGHTS: dict[str, float] = {
    "recovery": 0.28,
    "energy": 0.18,
    "opex": 0.18,
    "geomet": 0.10,
    "automation": 0.10,
    "environment": 0.05,
    "safety": 0.05,
    "economic": 0.06,
}


def _merge_compare_weights(raw: dict[str, float] | None) -> dict[str, float]:
    """Merge client weights with defaults and L1-normalize to sum to 1."""
    merged = {**_SCENARIO_EVAL_WEIGHTS, **(raw or {})}
    s = sum(max(0.0, float(v)) for v in merged.values())
    if s <= 0:
        return dict(_SCENARIO_EVAL_WEIGHTS)
    return {k: max(0.0, float(v)) / s for k, v in merged.items()}


def _evaluation_insights(
    *,
    recovery_score: float,
    energy_score: float,
    opex_score: float,
    geomet_confidence: float,
    automation_readiness: float,
    environmental_score: float,
    safety_score: float,
    economic_score: float,
    recovery_raw: float,
    energy_raw: float,
    opex_raw: float,
) -> list[str]:
    """Rule-based coaching strings (FR) — auditable, no black-box scoring."""
    out: list[str] = []
    if geomet_confidence < 40:
        out.append(
            "Couverture géométallurgique faible : renforcer composites et affectation "
            "aux domaines avant de figer le scénario."
        )
    if automation_readiness < 35:
        out.append(
            "Automatisation / contrôle sous-dimensionnés : cartographier variables "
            "critiques et interlocks pour réduire la variabilité opérationnelle."
        )
    if energy_raw and energy_score < 55:
        out.append(
            "Intensité énergétique élevée : cibles de broyage et cyclonage sont des "
            "leviers rapides face aux benchmarks sans CAPEX massif."
        )
    if opex_raw and opex_score < 55:
        out.append(
            "OPEX au tonne élevé : valider réactifs (CN, chaux) et recyclage d'eau — "
            "le modèle reste sensible à ces postes."
        )
    if economic_score < 50:
        out.append(
            "Score économique modéré : croiser sensibilités prix de l'or et taux "
            "d'actualisation dans les hypothèses du scénario."
        )
    if safety_score < 60:
        out.append(
            "Sécurité process : documenter zones ATEX / cyanuration et plans de "
            "décontamination — critère croissant chez les auditeurs ESG."
        )
    if environmental_score < 55:
        out.append(
            "Empreinte environnementale perfectible : intensité CO₂ et gestion TSF "
            "sont des leviers de différenciation vs. outils génériques."
        )
    if recovery_score < 65 and recovery_raw > 0:
        out.append(
            "Récupération sous pression : itérer sur résidence CIL/CIP et gravité "
            "avant arbitrage final."
        )
    return out[:6]


class ScenarioIn(BaseModel):
    scenario_name: str = Field(..., max_length=200)
    scenario_type: str = Field(default="process", max_length=50)
    status: str = Field(default="draft", max_length=50)
    base_scenario_id: Optional[str] = None
    description: Optional[str] = Field(default=None, max_length=4000)
    assumptions: dict[str, Any] = Field(default_factory=dict)
    evaluation_notes: Optional[str] = Field(default=None, max_length=4000)


class ScenarioPatch(BaseModel):
    scenario_name: Optional[str] = Field(default=None, max_length=200)
    scenario_type: Optional[str] = Field(default=None, max_length=50)
    status: Optional[str] = Field(default=None, max_length=50)
    description: Optional[str] = Field(default=None, max_length=4000)
    assumptions: Optional[dict[str, Any]] = None
    evaluation_notes: Optional[str] = Field(default=None, max_length=4000)


class ScenarioParamIn(BaseModel):
    category: str = Field(..., max_length=100)
    param_key: str = Field(..., max_length=100)
    param_value: Optional[float] = None
    param_value_text: Optional[str] = Field(default=None, max_length=500)
    source: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=1000)


class ScenarioEvaluationIn(BaseModel):
    recovery_pct: Optional[float] = None
    energy_kwh_t: Optional[float] = None
    capex_usd: Optional[float] = None
    opex_usd_t: Optional[float] = None
    environmental_score: Optional[float] = Field(default=None, ge=0, le=100)
    safety_score: Optional[float] = Field(default=None, ge=0, le=100)
    economic_score: Optional[float] = Field(default=None, ge=0, le=100)
    results_json: dict[str, Any] = Field(default_factory=dict)


class ScenarioCompareIn(BaseModel):
    scenario_ids: list[str] = Field(..., min_length=2)
    weights: dict[str, float] = Field(default_factory=lambda: dict(_SCENARIO_EVAL_WEIGHTS))


def _serialize(row: dict) -> dict:
    out = dict(row)
    for k in ("id", "project_id", "base_scenario_id", "scenario_id", "created_by", "source_flowsheet_id"):
        if out.get(k):
            out[k] = str(out[k])
    for k in ("created_at", "updated_at"):
        if out.get(k):
            out[k] = str(out[k])
    return out


def _validate_scenario_type(value: str) -> None:
    if value not in _VALID_SCENARIO_TYPES:
        raise HTTPException(422, f"scenario_type invalide: {_VALID_SCENARIO_TYPES}")


def _validate_scenario_status(value: str) -> None:
    if value not in _VALID_SCENARIO_STATUSES:
        raise HTTPException(422, f"status invalide: {_VALID_SCENARIO_STATUSES}")


def _get_scenario_or_404(pid: str, scenario_id: str) -> dict:
    row = qone("SELECT * FROM project_scenarios WHERE id=%s AND project_id=%s", (scenario_id, pid))
    if not row:
        raise HTTPException(404, "Scénario introuvable")
    return row


def _scenario_geomet_confidence(pid: str) -> float:
    sample_total = int((qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    domain_total = int((qone("SELECT COUNT(*) AS n FROM geomet_domains WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    composite_total = int((qone("SELECT COUNT(*) AS n FROM geomet_composites WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    assigned_total = int((qone(
        "SELECT COUNT(*) AS n FROM sample_geomet_domain sgd JOIN lims_samples ls ON ls.id = sgd.sample_id WHERE ls.project_id=%s",
        (pid,),
    ) or {}).get("n", 0))
    return round(
        (min(100.0, (assigned_total / sample_total) * 100.0) * 0.4 if sample_total else 0.0)
        + (min(100.0, domain_total * 20.0) * 0.3)
        + (min(100.0, composite_total * 25.0) * 0.3),
        1,
    )


def _scenario_automation_readiness(pid: str) -> float:
    variable_count = int((qone("SELECT COUNT(*) AS n FROM control_variables WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    alarm_count = int((qone("SELECT COUNT(*) AS n FROM control_alarms WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    interlock_count = int((qone("SELECT COUNT(*) AS n FROM control_interlocks WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    return round(min(100.0, (variable_count * 10.0) + (alarm_count * 7.5) + (interlock_count * 12.5)), 1)


def _snapshot_flowsheet(pid: str, scenario_id: str) -> dict | None:
    flowsheet = qone("SELECT * FROM flowsheets WHERE project_id=%s ORDER BY created_at DESC LIMIT 1", (pid,))
    if not flowsheet:
        return None
    return execute(
        "INSERT INTO scenario_flowsheets (scenario_id, blocks, connections, source_flowsheet_id) VALUES (%s,%s::jsonb,%s::jsonb,%s) "
        "ON CONFLICT (scenario_id) DO UPDATE SET blocks=EXCLUDED.blocks, connections=EXCLUDED.connections, source_flowsheet_id=EXCLUDED.source_flowsheet_id RETURNING *",
        (scenario_id, psycopg2.extras.Json(flowsheet.get("blocks") or []), psycopg2.extras.Json(flowsheet.get("connections") or []), flowsheet.get("id")),
    )


@router.get("/scenarios")
def list_scenarios(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM project_scenarios WHERE project_id=%s ORDER BY scenario_name, version", (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/scenarios", status_code=201)
def create_scenario(pid: str, body: ScenarioIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _validate_scenario_type(body.scenario_type)
        _validate_scenario_status(body.status)
        row = execute(
            "INSERT INTO project_scenarios (project_id, scenario_name, scenario_type, status, base_scenario_id, description, assumptions, evaluation_notes, created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) RETURNING *",
            (pid, body.scenario_name, body.scenario_type, body.status, body.base_scenario_id, body.description, psycopg2.extras.Json(body.assumptions), body.evaluation_notes, user["id"]),
        )
        _snapshot_flowsheet(pid, row["id"])

        record_event(
            user_id=user["id"], project_id=pid,
            entity_type="scenario", entity_id=str(row["id"]),
            action="create",
            new_value={"scenario_name": body.scenario_name, "scenario_type": body.scenario_type},
            source="web",
        )

        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/scenarios/{scenario_id}")
def patch_scenario(pid: str, scenario_id: str, body: ScenarioPatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        existing = _get_scenario_or_404(pid, scenario_id)
        if body.scenario_type:
            _validate_scenario_type(body.scenario_type)
        if body.status:
            _validate_scenario_status(body.status)
        data = body.model_dump(exclude_none=True)
        assumptions = data.pop("assumptions", None)
        fields, vals = build_update_sets(data, allowed=frozenset(type(body).model_fields.keys()))
        if assumptions is not None:
            fields.append("assumptions=%s::jsonb")
            vals.append(psycopg2.extras.Json(assumptions))
        if not fields:
            return _serialize(existing)
        fields.append("updated_at=NOW()")
        vals += [scenario_id, pid]
        row = execute(f"UPDATE project_scenarios SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(pid: str, scenario_id: str, user=Depends(project_user)):
    try:
        if user["role"] != "Project Manager":
            raise HTTPException(403, "Seul un Project Manager peut supprimer un scénario")
        _get_scenario_or_404(pid, scenario_id)
        execute("DELETE FROM project_scenarios WHERE id=%s AND project_id=%s", (scenario_id, pid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _evaluation_brief_markdown(pid: str, rows: list[dict]) -> str:
    """Build a short, audit-friendly Markdown brief from scenario + evaluation rows."""
    lines = [
        f"# Brief scénarios — projet `{pid}`",
        "",
        "Document généré automatiquement (hypothèses et scores tels qu'enregistrés).",
        "",
    ]
    if not rows:
        lines.append("_Aucun scénario ou aucune évaluation enregistrée._")
        return "\n".join(lines)

    for r in rows:
        name = (r.get("scenario_name") or "Sans nom").replace("\n", " ")
        sid = r.get("scenario_id") or r.get("id")
        lines.append(f"## {name}")
        lines.append("")
        lines.append(f"- **ID scénario** : `{sid}`")
        if r.get("overall_score") is not None:
            lines.append(f"- **Score global** : {r['overall_score']}")
        if r.get("recovery_pct") is not None:
            lines.append(f"- **Récupération %** : {r['recovery_pct']}")
        if r.get("economic_score") is not None:
            lines.append(f"- **Score économique** : {r['economic_score']}")
        rj = r.get("results_json")
        if isinstance(rj, dict):
            bd = rj.get("score_breakdown")
            if isinstance(bd, dict):
                wp = bd.get("weighted_points")
                if isinstance(wp, dict) and wp:
                    lines.append("- **Points pondérés** :")
                    for k, v in wp.items():
                        lines.append(f"  - {k}: {v}")
            ins = rj.get("evaluation_insights")
            if isinstance(ins, list) and ins:
                lines.append("- **Recommandations** :")
                for item in ins:
                    lines.append(f"  - {item}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


@router.get("/scenarios/evaluation-brief", response_class=PlainTextResponse)
def get_scenario_evaluation_brief(pid: str, user=Depends(project_user)):
    """Export Markdown des évaluations de scénarios (contrat d'hypothèses léger)."""
    try:
        rows = qall(
            """
            SELECT s.id, s.scenario_name, e.scenario_id, e.overall_score, e.recovery_pct,
                   e.economic_score, e.results_json
            FROM project_scenarios s
            LEFT JOIN scenario_evaluations e ON e.scenario_id = s.id
            WHERE s.project_id = %s
            ORDER BY s.scenario_name, s.version NULLS LAST
            """,
            (pid,),
        ) or []
        body = _evaluation_brief_markdown(pid, rows)
        return PlainTextResponse(
            content=body,
            media_type="text/markdown; charset=utf-8",
        )
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/scenarios/{scenario_id}/params")
def list_scenario_params(pid: str, scenario_id: str, user=Depends(project_user)):
    try:
        _get_scenario_or_404(pid, scenario_id)
        rows = qall("SELECT * FROM scenario_simulation_params WHERE scenario_id=%s ORDER BY category, param_key", (scenario_id,)) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/scenarios/{scenario_id}/params", status_code=201)
def upsert_scenario_param(pid: str, scenario_id: str, body: ScenarioParamIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _get_scenario_or_404(pid, scenario_id)
        row = execute(
            "INSERT INTO scenario_simulation_params (scenario_id, category, param_key, param_value, param_value_text, source, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (scenario_id, category, param_key) DO UPDATE SET param_value=EXCLUDED.param_value, param_value_text=EXCLUDED.param_value_text, source=EXCLUDED.source, notes=EXCLUDED.notes RETURNING *",
            (scenario_id, body.category, body.param_key, body.param_value, body.param_value_text, body.source, body.notes),
        )

        record_event(
            user_id=user["id"], project_id=pid,
            entity_type="scenario_param", entity_id=str(row["id"]),
            action="upsert",
            new_value={"category": body.category, "param_key": body.param_key, "param_value": body.param_value},
            source="web",
        )

        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/scenarios/{scenario_id}/params/{param_id}")
def delete_scenario_param(pid: str, scenario_id: str, param_id: str, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _get_scenario_or_404(pid, scenario_id)
        execute("DELETE FROM scenario_simulation_params WHERE id=%s AND scenario_id=%s", (param_id, scenario_id))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/scenarios/{scenario_id}/snapshot-flowsheet")
def snapshot_scenario_flowsheet(pid: str, scenario_id: str, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _get_scenario_or_404(pid, scenario_id)
        row = _snapshot_flowsheet(pid, scenario_id)
        return _serialize(row) if row else {"ok": False, "message": "Aucun flowsheet projet à snapshotter"}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/scenarios/{scenario_id}/snapshot-flowsheet")
def get_scenario_flowsheet(pid: str, scenario_id: str, user=Depends(project_user)):
    try:
        _get_scenario_or_404(pid, scenario_id)
        row = qone("SELECT * FROM scenario_flowsheets WHERE scenario_id=%s", (scenario_id,))
        return _serialize(row) if row else None
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/scenarios/{scenario_id}/evaluate")
def evaluate_scenario(pid: str, scenario_id: str, body: ScenarioEvaluationIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _get_scenario_or_404(pid, scenario_id)
        geomet_confidence = _scenario_geomet_confidence(pid)
        automation_readiness = _scenario_automation_readiness(pid)
        recovery = float(body.recovery_pct or 0)
        energy = float(body.energy_kwh_t or 0)
        opex = float(body.opex_usd_t or 0)
        environmental_score = min(100.0, max(0.0, float(body.environmental_score or 0)))
        safety_score = min(100.0, max(0.0, float(body.safety_score or 0)))
        economic_score = min(100.0, max(0.0, float(body.economic_score or 0)))

        recovery_score = min(100.0, max(0.0, recovery))
        energy_score = max(0.0, min(100.0, 100.0 - (energy * 4.0))) if energy else 0.0
        opex_score = max(0.0, min(100.0, 100.0 - (opex * 5.0))) if opex else 0.0
        wmap = _SCENARIO_EVAL_WEIGHTS
        overall_score = round(
            (recovery_score * wmap["recovery"])
            + (energy_score * wmap["energy"])
            + (opex_score * wmap["opex"])
            + (geomet_confidence * wmap["geomet"])
            + (automation_readiness * wmap["automation"])
            + (environmental_score * wmap["environment"])
            + (safety_score * wmap["safety"])
            + (economic_score * wmap["economic"]),
            1,
        )

        results_merged = dict(body.results_json or {})
        results_merged["score_breakdown"] = {
            "weights": dict(wmap),
            "weighted_points": {
                "recovery": round(recovery_score * wmap["recovery"], 2),
                "energy": round(energy_score * wmap["energy"], 2),
                "opex": round(opex_score * wmap["opex"], 2),
                "geomet": round(geomet_confidence * wmap["geomet"], 2),
                "automation": round(automation_readiness * wmap["automation"], 2),
                "environment": round(environmental_score * wmap["environment"], 2),
                "safety": round(safety_score * wmap["safety"], 2),
                "economic": round(economic_score * wmap["economic"], 2),
            },
            "raw_inputs": {
                "recovery_pct": body.recovery_pct,
                "energy_kwh_t": body.energy_kwh_t,
                "opex_usd_t": body.opex_usd_t,
                "environmental_score": body.environmental_score,
                "safety_score": body.safety_score,
                "economic_score": body.economic_score,
            },
            "overall_score": overall_score,
        }
        results_merged["evaluation_insights"] = _evaluation_insights(
            recovery_score=recovery_score,
            energy_score=energy_score,
            opex_score=opex_score,
            geomet_confidence=geomet_confidence,
            automation_readiness=automation_readiness,
            environmental_score=environmental_score,
            safety_score=safety_score,
            economic_score=economic_score,
            recovery_raw=recovery,
            energy_raw=energy,
            opex_raw=opex,
        )

        row = execute(
            "INSERT INTO scenario_evaluations (scenario_id, recovery_pct, energy_kwh_t, capex_usd, opex_usd_t, geomet_confidence, automation_readiness, environmental_score, safety_score, economic_score, overall_score, results_json) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) "
            "ON CONFLICT (scenario_id) DO UPDATE SET recovery_pct=EXCLUDED.recovery_pct, energy_kwh_t=EXCLUDED.energy_kwh_t, capex_usd=EXCLUDED.capex_usd, opex_usd_t=EXCLUDED.opex_usd_t, geomet_confidence=EXCLUDED.geomet_confidence, automation_readiness=EXCLUDED.automation_readiness, environmental_score=EXCLUDED.environmental_score, safety_score=EXCLUDED.safety_score, economic_score=EXCLUDED.economic_score, overall_score=EXCLUDED.overall_score, results_json=EXCLUDED.results_json RETURNING *",
            (scenario_id, body.recovery_pct, body.energy_kwh_t, body.capex_usd, body.opex_usd_t, geomet_confidence, automation_readiness, body.environmental_score, body.safety_score, body.economic_score, overall_score, psycopg2.extras.Json(results_merged)),
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/scenarios/{scenario_id}/evaluation")
def get_scenario_evaluation(pid: str, scenario_id: str, user=Depends(project_user)):
    try:
        _get_scenario_or_404(pid, scenario_id)
        row = qone("SELECT * FROM scenario_evaluations WHERE scenario_id=%s", (scenario_id,))
        return _serialize(row) if row else None
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/scenarios/compare")
def compare_scenarios(pid: str, body: ScenarioCompareIn, user=Depends(project_user)):
    try:
        scenario_ids = body.scenario_ids
        scenarios = qall(
            "SELECT s.*, e.recovery_pct, e.energy_kwh_t, e.capex_usd, e.opex_usd_t, e.geomet_confidence, e.automation_readiness, e.environmental_score, e.safety_score, e.economic_score, e.overall_score "
            "FROM project_scenarios s LEFT JOIN scenario_evaluations e ON e.scenario_id = s.id "
            "WHERE s.project_id=%s AND s.id = ANY(%s)",
            (pid, scenario_ids),
        ) or []
        if len(scenarios) < 2:
            raise HTTPException(404, "Au moins deux scénarios évalués sont requis")

        weights_eff = _merge_compare_weights(body.weights)
        ranked = []
        for row in scenarios:
            recovery = float(row.get("recovery_pct") or 0)
            energy = float(row.get("energy_kwh_t") or 0)
            opex = float(row.get("opex_usd_t") or 0)
            geomet = float(row.get("geomet_confidence") or 0)
            automation = float(row.get("automation_readiness") or 0)
            environment = float(row.get("environmental_score") or 0)
            safety = float(row.get("safety_score") or 0)
            economic = float(row.get("economic_score") or 0)
            recovery_score = min(100.0, max(0.0, recovery))
            energy_score = max(0.0, min(100.0, 100.0 - (energy * 4.0))) if energy else 0.0
            opex_score = max(0.0, min(100.0, 100.0 - (opex * 5.0))) if opex else 0.0
            economic_score = min(100.0, max(0.0, economic))
            computed_score = round(
                recovery_score * weights_eff["recovery"]
                + energy_score * weights_eff["energy"]
                + opex_score * weights_eff["opex"]
                + geomet * weights_eff["geomet"]
                + automation * weights_eff["automation"]
                + environment * weights_eff["environment"]
                + safety * weights_eff["safety"]
                + economic_score * weights_eff["economic"],
                1,
            )
            item = _serialize(row)
            item["comparison_score"] = computed_score
            ranked.append(item)

        ranked.sort(key=lambda x: x.get("comparison_score", 0), reverse=True)
        return {
            "weights": weights_eff,
            "weights_requested": body.weights,
            "weights_effective": weights_eff,
            "best_scenario_id": ranked[0]["id"] if ranked else None,
            "ranked": ranked,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
