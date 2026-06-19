"""GISTM tailings endpoints — design basis, violations, overrides."""
from __future__ import annotations

import html
import io
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

try:
    from auth import project_user, require_project_role
    from engines.gistm import ConsequenceInputs
    from services import gistm as svc
    from routes.gistm_schemas import (
        DesignBasisCreateIn,
        DesignBasisOut,
        DesignBasisHistoryOut,
        DesignCriteriaOut,
        OverrideCreateIn,
        OverrideOut,
        ConsequenceInputsIn,
    )
except ImportError:  # pragma: no cover
    from backend.auth import project_user, require_project_role
    from backend.engines.gistm import ConsequenceInputs
    from backend.services import gistm as svc
    from backend.routes.gistm_schemas import (
        DesignBasisCreateIn,
        DesignBasisOut,
        DesignBasisHistoryOut,
        DesignCriteriaOut,
        OverrideCreateIn,
        OverrideOut,
        ConsequenceInputsIn,
    )

logger = logging.getLogger("mpdpms.routes.gistm")
router = APIRouter(tags=["gistm"])


def _to_inputs(body: ConsequenceInputsIn) -> ConsequenceInputs:
    return ConsequenceInputs(
        par_count=body.par_count,
        env_damage_class=body.env_damage_class,
        economic_damage_usd_m=body.economic_damage_usd_m,
        critical_infra_downstream=body.critical_infra_downstream,
    )


@router.post(
    "/{pid}/gistm/design-basis/preview",
    response_model=DesignCriteriaOut,
)
async def preview_design_basis(
    pid: str,
    body: ConsequenceInputsIn,
    _user: Any = Depends(project_user),
) -> dict[str, Any]:
    """Live-preview the consequence class + derived criteria. No DB write."""
    return svc.preview_criteria(_to_inputs(body))


@router.post(
    "/{pid}/gistm/design-basis",
    status_code=201,
    response_model=DesignBasisOut,
)
async def create_design_basis(
    pid: str,
    body: DesignBasisCreateIn,
    user: Any = Depends(project_user),
) -> dict[str, Any]:
    """Create a draft basis (status='draft'). Activation is a separate call."""
    inputs = _to_inputs(body)
    return svc.create_design_basis(
        project_id=pid,
        inputs=inputs,
        notes=body.notes,
        created_by=str(user["id"]),
    )


@router.get(
    "/{pid}/gistm/design-basis",
    response_model=DesignBasisOut,
)
async def get_active_design_basis(
    pid: str,
    _user: Any = Depends(project_user),
) -> dict[str, Any]:
    """Return the currently active basis for the project, or 404."""
    row = svc.get_active_basis(pid)
    if row is None:
        raise HTTPException(status_code=404, detail="No active GISTM design basis for this project.")
    return row


@router.get(
    "/{pid}/gistm/design-basis/history",
    response_model=DesignBasisHistoryOut,
)
async def list_design_basis_history(
    pid: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _user: Any = Depends(project_user),
) -> dict[str, Any]:
    return svc.list_history(pid, limit=limit, offset=offset)


@router.get(
    "/{pid}/gistm/design-basis/{basis_id}",
    response_model=DesignBasisOut,
)
async def get_design_basis_by_id(
    pid: str,
    basis_id: str,
    _user: Any = Depends(project_user),
) -> dict[str, Any]:
    row = svc.get_basis(basis_id)
    if row is None or str(row["project_id"]) != pid:
        raise HTTPException(status_code=404, detail="Design basis not found in this project.")
    return row


@router.post(
    "/{pid}/gistm/design-basis/{basis_id}/activate",
    response_model=DesignBasisOut,
)
async def activate_design_basis(
    pid: str,
    basis_id: str,
    user: Any = Depends(require_project_role("Project Manager")),
) -> dict[str, Any]:
    """Activate a draft basis; supersede any currently-active basis. Owner-only."""
    try:
        return svc.activate_basis(basis_id=basis_id, project_id=pid, activated_by=str(user["id"]))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post(
    "/{pid}/gistm/violations/{violation_id}/override",
    status_code=201,
    response_model=OverrideOut,
)
async def override_violation(
    pid: str,
    violation_id: str,
    body: OverrideCreateIn,
    user: Any = Depends(require_project_role("Project Manager")),
) -> dict[str, Any]:
    """Sign an owner-only override on a violation (GISTM Principle 6 deviation)."""
    try:
        return svc.record_override(
            violation_id=violation_id,
            justification=body.justification,
            signed_by=str(user["id"]),
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


# ─── Design Basis Memorandum (PDF export) ────────────────────────────────────


_PDF_CSS = """
@page { size: A4; margin: 2.5cm 2.5cm 3cm 3cm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 11pt;
       line-height: 1.5; color: #222; }
h1 { font-size: 18pt; color: #1a365d; border-bottom: 2px solid #d4a01a;
     padding-bottom: 6pt; margin-top: 24pt; }
h2 { font-size: 13pt; color: #2d3748; margin-top: 14pt; }
.title-page { text-align: center; padding-top: 100pt; }
.title-page h1 { border: none; color: #d4a01a; font-size: 24pt; }
.disclaimer { background: #fff8e1; border-left: 4px solid #d4a01a;
              padding: 8pt 12pt; margin: 12pt 0; font-size: 10pt; }
table { border-collapse: collapse; width: 100%; margin-top: 8pt; }
th, td { border: 1px solid #ccc; padding: 6pt; text-align: left; font-size: 10pt; }
th { background: #f0f0f0; }
.class-label { font-size: 16pt; font-weight: bold; color: #c2410c; }
"""


def _build_memorandum_html(basis: dict[str, Any], project_id: str) -> str:
    e = html.escape

    def fmt(v: Any) -> str:
        return e(str(v)) if v is not None else "—"

    methods = ", ".join(list(basis["allowed_construction_methods"]))
    pga = basis["pga_threshold_g"]
    pga_str = f"{float(pga):.2f}g" if pga is not None else "—"

    return f"""<!DOCTYPE html><html><head><meta charset='utf-8'>
<style>{_PDF_CSS}</style></head><body>
<div class='title-page'>
  <h1>GISTM Tailings Design Basis Memorandum</h1>
  <div style='font-size:14pt;margin-top:12pt'>Projet {fmt(project_id)} — Version {fmt(basis['version'])}</div>
  <div style='color:#888;margin-top:24pt'>{datetime.now().strftime('%Y-%m-%d')}</div>
</div>

<h1>1. Classification de conséquence</h1>
<table>
  <tr><th>Critère</th><th>Valeur saisie</th></tr>
  <tr><td>Population At Risk (PAR)</td><td>{fmt(basis['par_count'])}</td></tr>
  <tr><td>Dommages environnementaux</td><td>{fmt(basis['env_damage_class'])}</td></tr>
  <tr><td>Dommages économiques</td><td>{fmt(basis['economic_damage_usd_m'])} M USD</td></tr>
  <tr><td>Infrastructure critique aval</td><td>{'Oui' if basis['critical_infra_downstream'] else 'Non'}</td></tr>
</table>
<p>Classe résultante (Annex 2 GISTM, "highest classification wins"):</p>
<p class='class-label'>{e(str(basis['consequence_class']).upper())}</p>

<h1>2. Critères de design dérivés</h1>
<table>
  <tr><th>Critère</th><th>Valeur</th></tr>
  <tr><td>IDF (Inflow Design Flood) return period</td><td>{int(basis['idf_return_period_yr']):,} ans</td></tr>
  <tr><td>MDE (Maximum Design Earthquake) return period</td><td>{int(basis['mde_return_period_yr']):,} ans</td></tr>
  <tr><td>FoS statique minimum</td><td>{float(basis['fs_static_min']):.2f}</td></tr>
  <tr><td>FoS sismique minimum</td><td>{float(basis['fs_seismic_min']):.2f}</td></tr>
  <tr><td>FoS post-liquéfaction minimum</td><td>{float(basis['fs_post_liquefaction_min']):.2f}</td></tr>
  <tr><td>Méthodes de construction autorisées</td><td>{e(methods)}</td></tr>
  <tr><td>PGA threshold (upstream interdit au-dessus)</td><td>{e(pga_str)}</td></tr>
</table>

<h1>3. Statut et signature</h1>
<table>
  <tr><th>Champ</th><th>Valeur</th></tr>
  <tr><td>Version</td><td>{fmt(basis['version'])}</td></tr>
  <tr><td>Statut</td><td>{fmt(basis['status'])}</td></tr>
  <tr><td>Créé le</td><td>{fmt(basis['created_at'])}</td></tr>
  <tr><td>Activé le</td><td>{fmt(basis['activated_at'])}</td></tr>
</table>

<div class='disclaimer'>
<strong>Note de validité :</strong> les seuils de la matrice par défaut MetalFlow Pro sont
une synthèse défendable alignée GISTM (ICMM/UNEP/PRI 2020) + Canadian Dam Association (CDA 2019)
+ ANCOLD. GISTM lui-même ne fixe pas de FoS numériques explicites — les valeurs ci-dessus sont
des défauts qui <strong>doivent être validés par l'Engineer of Record (EOR) du projet</strong>
avant utilisation pour la conception finale.
</div>

{f"<h1>4. Notes</h1><p>{e(basis['notes'])}</p>" if basis.get('notes') else ''}

</body></html>"""


@router.get("/{pid}/gistm/design-basis/{basis_id}/export.pdf")
async def export_memorandum_pdf(
    pid: str,
    basis_id: str,
    _user: Any = Depends(project_user),
) -> StreamingResponse:
    """Export the Design Basis Memorandum as PDF (xhtml2pdf)."""
    basis = svc.get_basis(basis_id)
    if basis is None or str(basis["project_id"]) != pid:
        raise HTTPException(status_code=404, detail="Design basis not found in this project.")
    try:
        from xhtml2pdf import pisa
    except ImportError:  # pragma: no cover
        raise HTTPException(status_code=500, detail="xhtml2pdf is not installed.")

    html_str = _build_memorandum_html(basis, pid)
    buf = io.BytesIO()
    pisa.CreatePDF(io.StringIO(html_str), dest=buf)
    buf.seek(0)
    filename = f"gistm-design-basis-v{basis['version']}.pdf"
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
