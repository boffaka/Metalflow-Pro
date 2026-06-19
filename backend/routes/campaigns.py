"""Test campaign manager — group LIMS samples into named campaigns."""
from __future__ import annotations
import logging
import psycopg2
from typing import List
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel

logger = logging.getLogger("mpdpms.campaigns")

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, build_update_sets, paginated_qall
    from ..models import CampaignIn, CampaignPatch, GeometDomainIn, GeometDomainPatch, SampleDomainAssignIn, CompositeIn, CompositePatch, CompositeSampleLinkIn
except ImportError:
    from auth import project_user
    from db import qone, qall, execute, build_update_sets, paginated_qall
    from models import CampaignIn, CampaignPatch, GeometDomainIn, GeometDomainPatch, SampleDomainAssignIn, CompositeIn, CompositePatch, CompositeSampleLinkIn

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["campaigns"])

_WRITE_ROLES = ("Project Manager", "Metallurgist")
_VALID_STATUSES = {"planned", "active", "complete", "cancelled"}
_VALID_TEST_TYPES = {
    "comminution", "flotation", "leach", "gravity", "thickening",
    "filtration", "elution", "environmental", "mineralogy", "pilot_plant", "other"
}
_VALID_QA_STATUSES = {"draft", "reviewed", "approved", "rejected"}


class SampleLinkIn(BaseModel):
    sample_ids: List[str]


def _serialize(row: dict) -> dict:
    out = dict(row)
    for k in ("id", "project_id", "campaign_id", "domain_id", "sample_id"):
        if out.get(k):
            out[k] = str(out[k])
    if out.get("campaign_name") is not None and out.get("name") is None:
        out["name"] = out["campaign_name"]
    for k in ("created_at", "started_at", "completed_at", "start_date", "end_date", "assigned_at", "added_at"):
        if out.get(k):
            out[k] = str(out[k])
    return out


def _get_composite_or_404(pid: str, composite_id: str) -> dict:
    composite = qone("SELECT * FROM geomet_composites WHERE id=%s AND project_id=%s", (composite_id, pid))
    if not composite:
        raise HTTPException(404, "Composite introuvable")
    return composite


def _compute_composite_summary(pid: str, composite_id: str) -> dict:
    composite = _get_composite_or_404(pid, composite_id)
    rows = qall(
        "SELECT gcs.*, ls.sample_id_display, ls.mass_kg AS sample_mass_kg, sgd.domain_id "
        "FROM geomet_composite_samples gcs "
        "JOIN lims_samples ls ON ls.id = gcs.sample_id "
        "LEFT JOIN sample_geomet_domain sgd ON sgd.sample_id = gcs.sample_id "
        "WHERE gcs.composite_id=%s ORDER BY ls.sample_id_display",
        (composite_id,),
    ) or []
    total_mass = round(sum(float(r.get("mass_kg") or 0) for r in rows), 3)
    total_weight_pct = round(sum(float(r.get("weight_pct") or 0) for r in rows), 2)
    assigned_domains = sum(1 for r in rows if r.get("domain_id"))
    summary = {
        "composite": _serialize(composite),
        "member_count": len(rows),
        "total_mass_kg": total_mass,
        "total_weight_pct": total_weight_pct,
        "weight_balance_ok": abs(total_weight_pct - 100.0) <= 1.0 if rows else False,
        "domain_coverage_pct": round((assigned_domains / len(rows)) * 100, 1) if rows else 0.0,
        "members": [_serialize(r) for r in rows],
    }
    return summary


@router.get("/campaigns")
def list_campaigns(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM test_campaigns WHERE project_id=%s ORDER BY created_at DESC", (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/campaigns", status_code=201)
def create_campaign(pid: str, body: CampaignIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant pour créer une campagne")
        if body.status not in _VALID_STATUSES:
            raise HTTPException(422, f"Statut invalide: {_VALID_STATUSES}")
        if body.test_type and body.test_type not in _VALID_TEST_TYPES:
            raise HTTPException(422, f"Type d'essai invalide: {_VALID_TEST_TYPES}")
        row = execute(
            "INSERT INTO test_campaigns (project_id, campaign_name, description, status, test_type, ore_types, protocol, laboratory, start_date, end_date, cost_usd, results_summary) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, body.name, body.description, body.status, body.test_type, body.ore_types, body.protocol, body.laboratory, body.start_date, body.end_date, body.cost_usd, body.results_summary)
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/campaigns/{cid}")
def patch_campaign(pid: str, cid: str, body: CampaignPatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant pour modifier une campagne")
        existing = qone("SELECT * FROM test_campaigns WHERE id=%s AND project_id=%s", (cid, pid))
        if not existing:
            raise HTTPException(404, "Campagne introuvable")
        if body.status and body.status not in _VALID_STATUSES:
            raise HTTPException(422, f"Statut invalide: {_VALID_STATUSES}")
        if body.test_type and body.test_type not in _VALID_TEST_TYPES:
            raise HTTPException(422, f"Type d'essai invalide: {_VALID_TEST_TYPES}")
        column_map = {
            "name": "campaign_name",
            "description": "description",
            "status": "status",
            "test_type": "test_type",
            "ore_types": "ore_types",
            "protocol": "protocol",
            "laboratory": "laboratory",
            "start_date": "start_date",
            "end_date": "end_date",
            "cost_usd": "cost_usd",
            "results_summary": "results_summary",
        }
        mapped = {column_map[k]: v for k, v in body.model_dump(exclude_none=True).items()}
        fields, vals = build_update_sets(mapped, allowed=frozenset(column_map.values()))
        if not fields:
            return _serialize(existing)
        vals += [cid, pid]
        row = execute(
            f"UPDATE test_campaigns SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *",
            vals
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


@router.delete("/campaigns/{cid}")
def delete_campaign(pid: str, cid: str, user=Depends(project_user)):
    try:
        if user["role"] != "Project Manager":
            raise HTTPException(403, "Seul un Project Manager peut supprimer une campagne")
        existing = qone("SELECT id FROM test_campaigns WHERE id=%s AND project_id=%s", (cid, pid))
        if not existing:
            raise HTTPException(404, "Campagne introuvable")
        execute("DELETE FROM test_campaigns WHERE id=%s", (cid,))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/campaigns/{cid}/samples")
def add_samples_to_campaign(pid: str, cid: str, body: SampleLinkIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant pour modifier une campagne")
        existing = qone("SELECT id FROM test_campaigns WHERE id=%s AND project_id=%s", (cid, pid))
        if not existing:
            raise HTTPException(404, "Campagne introuvable")
        added = 0
        for sid in body.sample_ids:
            sample = qone("SELECT id FROM lims_samples WHERE id=%s AND project_id=%s", (sid, pid))
            if sample:
                execute(
                    "INSERT INTO campaign_samples (campaign_id, sample_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                    (cid, sid)
                )
                added += 1
        return {"ok": True, "added": added}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/campaigns/{cid}/samples/{sid}")
def remove_sample_from_campaign(pid: str, cid: str, sid: str, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        execute("DELETE FROM campaign_samples WHERE campaign_id=%s AND sample_id=%s", (cid, sid))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/geomet/domains")
def list_geomet_domains(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM geomet_domains WHERE project_id=%s ORDER BY domain_code", (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/geomet/domains", status_code=201)
def create_geomet_domain(pid: str, body: GeometDomainIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        row = execute(
            "INSERT INTO geomet_domains (project_id, domain_code, domain_name, lithology, alteration, mineralization_style, oxidation_state, hardness_class, variability_index, representative, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, body.domain_code, body.domain_name, body.lithology, body.alteration, body.mineralization_style, body.oxidation_state, body.hardness_class, body.variability_index, body.representative, body.notes)
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/geomet/domains/{domain_id}")
def patch_geomet_domain(pid: str, domain_id: str, body: GeometDomainPatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        existing = qone("SELECT * FROM geomet_domains WHERE id=%s AND project_id=%s", (domain_id, pid))
        if not existing:
            raise HTTPException(404, "Domaine introuvable")
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            return _serialize(existing)
        vals += [domain_id, pid]
        row = execute(f"UPDATE geomet_domains SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.post("/samples/{sid}/geomet-domain")
def assign_sample_domain(pid: str, sid: str, body: SampleDomainAssignIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        sample = qone("SELECT id FROM lims_samples WHERE id=%s AND project_id=%s", (sid, pid))
        if not sample:
            raise HTTPException(404, "Échantillon introuvable")
        domain = qone("SELECT id FROM geomet_domains WHERE id=%s AND project_id=%s", (body.domain_id, pid))
        if not domain:
            raise HTTPException(404, "Domaine introuvable")
        execute(
            "INSERT INTO sample_geomet_domain (sample_id, domain_id, confidence_pct, assigned_by, notes) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (sample_id) DO UPDATE SET domain_id=EXCLUDED.domain_id, confidence_pct=EXCLUDED.confidence_pct, assigned_by=EXCLUDED.assigned_by, assigned_at=NOW(), notes=EXCLUDED.notes",
            (sid, body.domain_id, body.confidence_pct, user["id"], body.notes)
        )
        row = qone("SELECT * FROM sample_geomet_domain WHERE sample_id=%s", (sid,))
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.get("/geomet/composites")
def list_composites(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM geomet_composites WHERE project_id=%s ORDER BY composite_code", (pid,), limit=limit, offset=offset) or []
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/geomet/composites", status_code=201)
def create_composite(pid: str, body: CompositeIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        if body.qa_status and body.qa_status not in _VALID_QA_STATUSES:
            raise HTTPException(422, f"QA status invalide: {_VALID_QA_STATUSES}")
        row = execute(
            "INSERT INTO geomet_composites (project_id, campaign_id, composite_code, composite_name, purpose, domain_id, target_mass_kg, actual_mass_kg, blend_method, representativity_score, qa_status, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, body.campaign_id, body.composite_code, body.composite_name, body.purpose, body.domain_id, body.target_mass_kg, body.actual_mass_kg, body.blend_method, body.representativity_score, body.qa_status, body.notes)
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/geomet/composites/{composite_id}")
def patch_composite(pid: str, composite_id: str, body: CompositePatch, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        existing = qone("SELECT * FROM geomet_composites WHERE id=%s AND project_id=%s", (composite_id, pid))
        if not existing:
            raise HTTPException(404, "Composite introuvable")
        if body.qa_status and body.qa_status not in _VALID_QA_STATUSES:
            raise HTTPException(422, f"QA status invalide: {_VALID_QA_STATUSES}")
        fields, vals = build_update_sets(body.model_dump(exclude_none=True), allowed=frozenset(type(body).model_fields.keys()))
        if not fields:
            return _serialize(existing)
        vals += [composite_id, pid]
        row = execute(f"UPDATE geomet_composites SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


@router.get("/geomet/composites/{composite_id}/summary")
def composite_summary(pid: str, composite_id: str, user=Depends(project_user)):
    try:
        return _compute_composite_summary(pid, composite_id)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/geomet/composites/{composite_id}/samples", status_code=201)
def add_sample_to_composite(pid: str, composite_id: str, body: CompositeSampleLinkIn, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _get_composite_or_404(pid, composite_id)
        sample = qone("SELECT id FROM lims_samples WHERE id=%s AND project_id=%s", (body.sample_id, pid))
        if not sample:
            raise HTTPException(404, "Échantillon introuvable")
        execute(
            "INSERT INTO geomet_composite_samples (composite_id, sample_id, mass_kg, weight_pct, role_in_composite) VALUES (%s,%s,%s,%s,%s) "
            "ON CONFLICT (composite_id, sample_id) DO UPDATE SET mass_kg=EXCLUDED.mass_kg, weight_pct=EXCLUDED.weight_pct, role_in_composite=EXCLUDED.role_in_composite",
            (composite_id, body.sample_id, body.mass_kg, body.weight_pct, body.role_in_composite)
        )
        return _compute_composite_summary(pid, composite_id)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/geomet/composites/{composite_id}/samples/{sample_id}")
def remove_sample_from_composite(pid: str, composite_id: str, sample_id: str, user=Depends(project_user)):
    try:
        if user["role"] not in _WRITE_ROLES:
            raise HTTPException(403, "Rôle insuffisant")
        _get_composite_or_404(pid, composite_id)
        execute("DELETE FROM geomet_composite_samples WHERE composite_id=%s AND sample_id=%s", (composite_id, sample_id))
        return _compute_composite_summary(pid, composite_id)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/geomet/confidence")
def geomet_confidence(pid: str, user=Depends(project_user)):
    try:
        sample_total = int((qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        domain_total = int((qone("SELECT COUNT(*) AS n FROM geomet_domains WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        assigned_total = int((qone(
            "SELECT COUNT(*) AS n FROM sample_geomet_domain sgd JOIN lims_samples ls ON ls.id = sgd.sample_id WHERE ls.project_id=%s",
            (pid,),
        ) or {}).get("n", 0))
        composite_total = int((qone("SELECT COUNT(*) AS n FROM geomet_composites WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        composite_members = int((qone(
            "SELECT COUNT(*) AS n FROM geomet_composite_samples gcs JOIN geomet_composites gc ON gc.id = gcs.composite_id WHERE gc.project_id=%s",
            (pid,),
        ) or {}).get("n", 0))
        approved_composites = int((qone(
            "SELECT COUNT(*) AS n FROM geomet_composites WHERE project_id=%s AND qa_status='approved'",
            (pid,),
        ) or {}).get("n", 0))
        campaign_total = int((qone("SELECT COUNT(*) AS n FROM test_campaigns WHERE project_id=%s", (pid,)) or {}).get("n", 0))

        sample_assignment_score = min(100.0, (assigned_total / sample_total) * 100.0) if sample_total else 0.0
        domain_coverage_score = min(100.0, domain_total * 20.0)
        composite_readiness_score = min(100.0, (composite_members / max(composite_total, 1)) * 25.0) if composite_total else 0.0
        qa_score = (approved_composites / composite_total) * 100.0 if composite_total else 0.0
        campaign_score = min(100.0, campaign_total * 20.0)

        overall = round(
            (sample_assignment_score * 0.30)
            + (domain_coverage_score * 0.20)
            + (composite_readiness_score * 0.20)
            + (qa_score * 0.20)
            + (campaign_score * 0.10),
            1,
        )

        return {
            "project_id": pid,
            "confidence_score": overall,
            "components": {
                "sample_assignment_score": round(sample_assignment_score, 1),
                "domain_coverage_score": round(domain_coverage_score, 1),
                "composite_readiness_score": round(composite_readiness_score, 1),
                "qa_score": round(qa_score, 1),
                "campaign_score": round(campaign_score, 1),
            },
            "counts": {
                "samples": sample_total,
                "domains": domain_total,
                "assigned_samples": assigned_total,
                "composites": composite_total,
                "composite_members": composite_members,
                "approved_composites": approved_composites,
                "campaigns": campaign_total,
            },
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
