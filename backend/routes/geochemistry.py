# backend/routes/geochemistry.py
"""
Geochemistry endpoints:
  POST /api/v1/projects/{pid}/geochemistry/aba-nag   — submit ABA/NAG test
  GET  /api/v1/projects/{pid}/geochemistry/ard-risk  — compute ARD risk
"""
from __future__ import annotations
import uuid
import logging
from typing import Optional
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

try:
    from ..db import conn, release
    from ..auth import project_user
    from ..engines.geotech import compute_aba, classify_pag, classify_ard_risk
except ImportError:
    from db import conn, release
    from auth import project_user
    from engines.geotech import compute_aba, classify_pag, classify_ard_risk

router = APIRouter(tags=["geochemistry"])


class ABANAGIn(BaseModel):
    sample_id: Optional[str] = None
    total_s_pct: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Soufre total (%). Optionnel — si absent, calculé comme "
            "sulfide_s_pct + sulfate_s_pct (ou sulfide_s_pct seul si sulfate absent)."
        ),
    )
    sulfide_s_pct: float = Field(ge=0, description="Soufre sulfure (%) — base du potentiel acide Sobek-style")
    sulfate_s_pct: Optional[float] = Field(
        default=None,
        ge=0,
        description=(
            "Soufre sulfate (%). Optionnel — si absent et total_s_pct fourni, "
            "calculé comme total_s_pct - sulfide_s_pct (plancher 0)."
        ),
    )
    np_kg_caco3_t: float = Field(ge=0, description="Potentiel neutralisant NP, kg CaCO₃/t")
    ph_nag: Optional[float] = None
    test_date: Optional[date] = None
    laboratory: Optional[str] = None

    def resolved_sulfate_s_pct(self) -> float:
        """Return sulfate_s_pct, deriving it when absent."""
        if self.sulfate_s_pct is not None:
            return self.sulfate_s_pct
        if self.total_s_pct is not None:
            return round(max(0.0, self.total_s_pct - self.sulfide_s_pct), 4)
        return 0.0

    def resolved_total_s_pct(self) -> float:
        """Return total_s_pct, deriving it when absent."""
        if self.total_s_pct is not None:
            return self.total_s_pct
        return round(self.sulfide_s_pct + self.resolved_sulfate_s_pct(), 4)


def _sulfur_balance_warnings(total: float, sulfide: float, sulfate: float) -> list[str]:
    """Flags inconsistent S speciation vs total (laboratory QA)."""
    w: list[str] = []
    if total <= 0:
        return w
    summed = sulfide + sulfate
    if summed > total + 0.15:
        w.append(
            f"S sulfure + S sulfate ({summed:.2f} %) dépasse S total ({total:.2f} %) — vérifier la saisie."
        )
    elif summed < total * 0.85 and sulfide + sulfate > 0:
        w.append(
            "S sulfure + S sulfate nettement inférieurs au S total — soufre organique / autre forme possible."
        )
    return w


@router.post("/{pid}/geochemistry/aba-nag", status_code=201)
async def submit_aba_nag(pid: str, body: ABANAGIn, _auth=Depends(project_user)):
    ap, nnp, npr = compute_aba(body.sulfide_s_pct, body.np_kg_caco3_t)
    pag_class = classify_pag(nnp, npr)
    total_s = body.resolved_total_s_pct()
    sulfate_s = body.resolved_sulfate_s_pct()
    balance_warnings = _sulfur_balance_warnings(
        total_s, body.sulfide_s_pct, sulfate_s
    )
    # NAG test override: pH_NAG < 4.5 confirms PAG regardless of ABA
    if body.ph_nag is not None and body.ph_nag < 4.5:
        pag_class = "PAG"
    record_id = str(uuid.uuid4())
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            # Resolve sample_id: accept UUID or sample_id_display string
            resolved_sample_id = None
            if body.sample_id:
                # Try as UUID first
                try:
                    uuid.UUID(body.sample_id)
                    resolved_sample_id = body.sample_id
                except ValueError:
                    # It's a display name — look up the UUID
                    cur.execute(
                        "SELECT id FROM lims_samples "
                        "WHERE project_id = %s AND sample_id_display = %s LIMIT 1",
                        (pid, body.sample_id),
                    )
                    row = cur.fetchone()
                    if row:
                        resolved_sample_id = str(row[0] if not isinstance(row, dict) else row["id"])
                    # If not found, store NULL (sample may not be in LIMS yet)

            cur.execute(
                """INSERT INTO aba_nag_results
                   (id, project_id, sample_id,
                    total_s_pct, sulfide_s_pct, sulfate_s_pct,
                    ap_kg_caco3_t, np_kg_caco3_t, nnp, npr,
                    ph_nag, pag_classification, test_date, laboratory)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (record_id, pid, resolved_sample_id,
                 total_s, body.sulfide_s_pct, sulfate_s,
                 ap, body.np_kg_caco3_t, nnp, npr,
                 body.ph_nag, pag_class, body.test_date, body.laboratory)
            )
        db.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        if db is not None:
            db.rollback()
        raise
    finally:
        if db is not None:
            release(db)
    out = {
        "id": record_id,
        "ap_kg_caco3_t": ap,
        "nnp": nnp,
        "npr": npr,
        "pag_classification": pag_class,
    }
    if balance_warnings:
        out["qa_warnings"] = balance_warnings
    return out


@router.get("/{pid}/geochemistry/aba-nag")
async def list_aba_nag(pid: str, _auth=Depends(project_user)):
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                """SELECT a.id, a.sample_id, a.total_s_pct, a.sulfide_s_pct,
                          a.sulfate_s_pct, a.ap_kg_caco3_t, a.np_kg_caco3_t,
                          a.nnp, a.npr, a.ph_nag, a.pag_classification,
                          a.test_date, a.laboratory,
                          ls.sample_id_display
                   FROM aba_nag_results a
                   LEFT JOIN lims_samples ls ON ls.id = a.sample_id
                   WHERE a.project_id = %s
                   ORDER BY a.id""",
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
    # SELECT order: id, sample_id, total_s, sulfide, sulfate, ap, np, nnp, npr,
    # ph_nag, pag_class, test_date, laboratory, sample_id_display
    return [
        {
            "id": str(r[0]),
            "sample_id": str(r[1]) if r[1] is not None else None,
            "sample_id_display": r[13],
            "total_s_pct": r[2],
            "sulfide_s_pct": r[3],
            "sulfate_s_pct": r[4],
            "ap_kg_caco3_t": r[5],
            "np_kg_caco3_t": r[6],
            "nnp": r[7],
            "npr": r[8],
            "ph_nag": r[9],
            "pag_classification": r[10],
            "test_date": str(r[11]) if r[11] else None,
            "laboratory": r[12],
        }
        for r in rows
    ]


@router.get("/{pid}/geochemistry/ard-risk")
async def get_ard_risk(pid: str, _auth=Depends(project_user)):
    """Aggregate all ABA/NAG results, compute PAG%, upsert ard_classifications."""
    db = None
    try:
        db = conn()
        with db.cursor() as cur:
            cur.execute(
                "SELECT pag_classification FROM aba_nag_results WHERE project_id = %s",
                (pid,)
            )
            rows = cur.fetchall()

        if not rows:
            return {
                "pag_count": 0, "non_pag_count": 0, "uncertain_count": 0,
                "pag_pct": 0.0, "ard_risk_level": "Low",
                "message": "No ABA/NAG results found",
            }

        total = len(rows)
        pag_count = sum(1 for r in rows if r[0] == "PAG")
        non_pag_count = sum(1 for r in rows if r[0] == "Non-PAG")
        uncertain_count = total - pag_count - non_pag_count
        pag_pct = round(pag_count / total * 100, 1)
        risk_level = classify_ard_risk(pag_pct)

        with db.cursor() as cur:
            cur.execute(
                """INSERT INTO ard_classifications
                   (id, project_id, domain_code,
                    pag_count, non_pag_count, uncertain_count,
                    pag_pct, ard_risk_level)
                   VALUES (%s,%s,'site',%s,%s,%s,%s,%s)
                   ON CONFLICT (project_id, domain_code)
                   DO UPDATE SET
                     pag_count=EXCLUDED.pag_count,
                     non_pag_count=EXCLUDED.non_pag_count,
                     uncertain_count=EXCLUDED.uncertain_count,
                     pag_pct=EXCLUDED.pag_pct,
                     ard_risk_level=EXCLUDED.ard_risk_level,
                     created_at=NOW()""",
                (str(uuid.uuid4()), pid,
                 pag_count, non_pag_count, uncertain_count,
                 pag_pct, risk_level)
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
        "pag_count": pag_count,
        "non_pag_count": non_pag_count,
        "uncertain_count": uncertain_count,
        "pag_pct": pag_pct,
        "ard_risk_level": risk_level,
        "ard_risk_class": risk_level,
        "mitigation_strategy": _mitigation(risk_level),
    }


def _mitigation(risk: str) -> str:
    return {
        "Low": "Standard monitoring — quarterly water quality sampling",
        "Medium": "Enhanced monitoring + cover design assessment",
        "High": "Engineered cover + active water treatment required",
        "Critical": "Immediate containment + regulatory notification required",
    }.get(risk, "Unknown")
