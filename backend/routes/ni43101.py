"""
NI 43-101 Report routes — CRUD for sections + generate + export (PDF/Word).
"""
from __future__ import annotations
import logging
import psycopg2


from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query
from fastapi.responses import Response
from pydantic import BaseModel

logger = logging.getLogger("mpdpms.ni43101")

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, build_update_sets, paginated_qall
    from .ni43101_generator import (
        ALLOWED_METALLURGY_SECTIONS,
        generate_report_section,
        generate_report_sections,
    )
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, build_update_sets, paginated_qall
    from routes.ni43101_generator import (
        ALLOWED_METALLURGY_SECTIONS,
        generate_report_section,
        generate_report_sections,
    )

import bleach

# Safe HTML tags allowed in PDF/DOCX exports
_ALLOWED_TAGS = [
    'h1', 'h2', 'h3', 'h4', 'p', 'br',
    'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'b', 'i', 'strong', 'em', 'u',
    'span', 'div',
]

def sanitize_html(content: str) -> str:
    """Strip unsafe HTML before passing to PDF/DOCX renderer."""
    return bleach.clean(content, tags=_ALLOWED_TAGS, attributes={}, strip=True)

router = APIRouter(prefix="/api/v1/projects/{pid}/ni43101", tags=["ni43101"])


# =============================================================================
# NI 43-101 Readiness Checker
# =============================================================================

_REQUIREMENTS = {
    "scoping": {
        "a1_min": 1, "b1_min": 0, "d1_min": 3,
        "dc_no_default": False, "mass_balance": False,
        "simulation": False, "environmental": False,
    },
    "pfs": {
        "a1_min": 15, "b1_min": 5, "d1_min": 10,
        "dc_no_default": True, "mass_balance": True,
        "simulation": False, "environmental": True,
    },
    "fs": {
        "a1_min": 30, "b1_min": 10, "d1_min": 15,
        "dc_no_default": True, "mass_balance": True,
        "simulation": True, "environmental": True,
    },
    "dfs": {
        "a1_min": 50, "b1_min": 15, "d1_min": 30,
        "dc_no_default": True, "mass_balance": True,
        "simulation": True, "environmental": True,
    },
}


def check_readiness(
    stage: str,
    test_counts: dict[str, int],
    dc_sources: dict[str, int],
    has_mass_balance: bool = False,
    has_simulation: bool = False,
    has_environmental: bool = False,
) -> dict:
    reqs = _REQUIREMENTS.get(stage)
    if not reqs:
        return {"ready": False, "score_pct": 0, "checklist": [{"item": f"Stage '{stage}' inconnu", "status": "fail"}]}

    checklist = []

    a1_count = test_counts.get("a1", 0)
    a1_ok = a1_count >= reqs["a1_min"]
    checklist.append({
        "item": f"A1 head assay tests >= {reqs['a1_min']}",
        "status": "pass" if a1_ok else "fail",
        "detail": f"{a1_count} tests",
        "action": None if a1_ok else f"Besoin de {reqs['a1_min'] - a1_count} tests A1 supplementaires",
    })

    b1_count = test_counts.get("b1", 0)
    b1_ok = b1_count >= reqs["b1_min"]
    checklist.append({
        "item": f"B1 comminution tests >= {reqs['b1_min']}",
        "status": "pass" if b1_ok else ("fail" if reqs["b1_min"] > 0 else "pass"),
        "detail": f"{b1_count} tests",
        "action": None if b1_ok else f"Besoin de {reqs['b1_min'] - b1_count} tests B1 supplementaires",
    })

    d1_count = test_counts.get("d1", 0)
    d1_ok = d1_count >= reqs["d1_min"]
    checklist.append({
        "item": f"D1 leach recovery tests >= {reqs['d1_min']}",
        "status": "pass" if d1_ok else "fail",
        "detail": f"{d1_count} tests",
        "action": None if d1_ok else f"Besoin de {reqs['d1_min'] - d1_count} tests D1 supplementaires",
    })

    dc_default_count = dc_sources.get("D", 0)
    dc_ok = not reqs["dc_no_default"] or dc_default_count == 0
    checklist.append({
        "item": "Design criteria sans valeurs par defaut",
        "status": "pass" if dc_ok else "warning",
        "detail": f"{dc_default_count} criteres utilisent encore les valeurs par defaut",
        "action": None if dc_ok else "Remplacer les valeurs par defaut par des donnees LIMS ou manuelles",
    })

    if reqs["mass_balance"]:
        mb_ok = has_mass_balance
        checklist.append({
            "item": "Mass balance calcule",
            "status": "pass" if mb_ok else "fail",
            "detail": "Present" if mb_ok else "Absent",
            "action": None if mb_ok else "Generer le mass balance via /mass-balance/auto-generate",
        })

    if reqs["simulation"]:
        sim_ok = has_simulation
        checklist.append({
            "item": "Simulation rigoureuse executee",
            "status": "pass" if sim_ok else "fail",
            "detail": "Present" if sim_ok else "Absent",
            "action": None if sim_ok else "Executer au moins 1 simulation rigoureuse",
        })

    total = len(checklist)
    passed = sum(1 for c in checklist if c["status"] == "pass")
    score = round(100 * passed / total) if total > 0 else 0

    return {
        "stage": stage,
        "ready": score == 100,
        "score_pct": score,
        "checklist": checklist,
    }


# We need a second router for the readiness endpoint (different prefix)
readiness_router = APIRouter(prefix="/api/v1/projects", tags=["ni43101"])


@readiness_router.get("/{pid}/ni43101/readiness/{stage}")
def get_readiness(pid: str, stage: str, user=Depends(project_user)):
    try:
        return _get_readiness_impl(pid, stage)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _get_readiness_impl(pid: str, stage: str):
    if stage not in _REQUIREMENTS:
        raise HTTPException(400, f"Stage '{stage}' non supporte. Options: {list(_REQUIREMENTS.keys())}")

    test_counts = {}
    try:
        from .lims import LIMS_TABLES, safe_table_name
    except ImportError:
        from lims import LIMS_TABLES, safe_table_name
    for code, table in LIMS_TABLES.items():
        tbl = safe_table_name(table)
        row = qone(f"SELECT COUNT(*) as cnt FROM {tbl} WHERE project_id = %s", (pid,))
        test_counts[code] = row["cnt"] if row else 0

    dc_rows = qall("SELECT source FROM design_criteria WHERE project_id = %s", (pid,))
    dc_sources = {}
    for r in (dc_rows or []):
        s = (r.get("source") or "D").upper()[:1]
        dc_sources[s] = dc_sources.get(s, 0) + 1

    mb = qone("SELECT COUNT(*) as cnt FROM mass_balance_streams WHERE project_id = %s", (pid,))
    has_mb = (mb["cnt"] if mb else 0) > 0

    sim = qone("SELECT COUNT(*) as cnt FROM simulation_runs WHERE project_id = %s", (pid,))
    has_sim = (sim["cnt"] if sim else 0) > 0

    return check_readiness(stage, test_counts, dc_sources, has_mb, has_sim)


# ─── Pydantic models ──────────────────────────────────────────────────────────

class SectionIn(BaseModel):
    section_number: int = 13
    subsection_key: str
    title_fr: str = ""
    title_en: str = ""
    content_fr: str = ""
    content_en: str = ""
    sort_order: int = 99


class SectionPatch(BaseModel):
    title_fr: str | None = None
    title_en: str | None = None
    content_fr: str | None = None
    content_en: str | None = None
    sort_order: int | None = None


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _serialize(row: dict) -> dict:
    """Ensure UUIDs and timestamps are serializable."""
    out = dict(row)
    for k in ("id", "project_id"):
        if out.get(k):
            out[k] = str(out[k])
    for k in ("created_at", "updated_at"):
        if out.get(k):
            out[k] = str(out[k])
    return out


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/sections")
def list_sections(pid: str, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    """List all NI 43-101 subsections for the project."""
    try:
        rows = paginated_qall(
            "SELECT * FROM ni43101_sections WHERE project_id = %s "
            "ORDER BY section_number, sort_order",
            (pid,), limit=limit, offset=offset)
        return [_serialize(r) for r in rows]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/generate")
def generate_report(pid: str, user=Depends(project_user)):
    """Generate or regenerate the NI 43-101 report from current project data.
    Replaces all auto-generated sections. Preserves manually edited/added sections.
    """
    try:
        return _generate_report_impl(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _generate_report_impl(pid: str):
    allowed = tuple(sorted(ALLOWED_METALLURGY_SECTIONS))
    # Remove legacy sections outside metallurgical TR scope
    execute(
        "DELETE FROM ni43101_sections WHERE project_id = %s AND section_number NOT IN %s",
        (pid, allowed),
    )
    execute(
        "DELETE FROM ni43101_sections WHERE project_id = %s AND is_auto_generated = TRUE",
        (pid,),
    )

    new_sections = generate_report_sections(pid)

    # Check for manually added sections to adjust sort_order
    manual = qall(
        "SELECT * FROM ni43101_sections WHERE project_id = %s AND is_auto_generated = FALSE "
        "ORDER BY section_number, sort_order",
        (pid,),
    )

    saved = []
    for s in new_sections:
        row = execute(
            "INSERT INTO ni43101_sections "
            "(project_id, section_number, subsection_key, title_fr, title_en, "
            "content_fr, content_en, sort_order, is_auto_generated, source_data) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, '{}') RETURNING *",
            (pid, s["section_number"], s["key"], s["title_fr"], s["title_en"],
             s["content_fr"], s["content_en"], s["sort_order"]),
        )
        saved.append(_serialize(row))

    # Append manual sections
    for m in manual:
        saved.append(_serialize(m))

    # Sort by section_number then sort_order
    saved.sort(key=lambda x: (x.get("section_number", 0), x.get("sort_order", 0)))
    return {"ok": True, "sections": saved, "count": len(saved)}


@router.post("/generate/{section_number}")
def generate_one_section(pid: str, section_number: int, user=Depends(project_user)):
    """Regenerate a single NI 43-101 section from current project data."""
    try:
        return _generate_one_section_impl(pid, section_number)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _generate_one_section_impl(pid: str, section_number: int):
    if section_number not in ALLOWED_METALLURGY_SECTIONS:
        raise ValueError(
            f"Section {section_number} non supportee. Sections autorisees: "
            f"{sorted(ALLOWED_METALLURGY_SECTIONS)}"
        )

    execute(
        "DELETE FROM ni43101_sections "
        "WHERE project_id = %s AND section_number = %s AND is_auto_generated = TRUE",
        (pid, section_number),
    )

    new_sections = generate_report_section(pid, section_number)
    saved = []
    for s in new_sections:
        row = execute(
            "INSERT INTO ni43101_sections "
            "(project_id, section_number, subsection_key, title_fr, title_en, "
            "content_fr, content_en, sort_order, is_auto_generated, source_data) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, '{}') RETURNING *",
            (pid, s["section_number"], s["key"], s["title_fr"], s["title_en"],
             s["content_fr"], s["content_en"], s["sort_order"]),
        )
        saved.append(_serialize(row))

    manual = qall(
        "SELECT * FROM ni43101_sections WHERE project_id = %s AND section_number = %s "
        "AND is_auto_generated = FALSE ORDER BY sort_order",
        (pid, section_number),
    )
    for m in manual:
        saved.append(_serialize(m))
    saved.sort(key=lambda x: (x.get("sort_order", 0)))

    all_rows = qall(
        "SELECT * FROM ni43101_sections WHERE project_id = %s "
        "ORDER BY section_number, sort_order",
        (pid,),
    )
    return {
        "ok": True,
        "section_number": section_number,
        "sections": [_serialize(r) for r in all_rows],
        "generated": saved,
        "count": len(saved),
    }


@router.post("/sections")
def add_section(pid: str, body: SectionIn, user=Depends(project_user)):
    """Add a custom (manual) subsection."""
    try:
        if body.section_number not in ALLOWED_METALLURGY_SECTIONS:
            raise HTTPException(
                400,
                f"section_number doit etre l'un de: {sorted(ALLOWED_METALLURGY_SECTIONS)}",
            )
        row = execute(
            "INSERT INTO ni43101_sections "
            "(project_id, section_number, subsection_key, title_fr, title_en, "
            "content_fr, content_en, sort_order, is_auto_generated) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE) RETURNING *",
            (pid, body.section_number, body.subsection_key, body.title_fr,
             body.title_en, body.content_fr, body.content_en, body.sort_order),
        )
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.patch("/sections/{sid}")
def patch_section(pid: str, sid: str, body: SectionPatch, user=Depends(project_user)):
    """Update a subsection (title/content). Marks it as manually edited."""
    try:
        existing = qone("SELECT * FROM ni43101_sections WHERE id = %s AND project_id = %s", (sid, pid))
        if not existing:
            raise HTTPException(404, "Section introuvable")

        _NI43101_SECTION_ALLOWED = frozenset(["title_fr", "title_en", "content_fr", "content_en", "sort_order"])
        fields, vals = build_update_sets(
            {attr: v for attr in _NI43101_SECTION_ALLOWED if (v := getattr(body, attr, None)) is not None},
            allowed=_NI43101_SECTION_ALLOWED,
        )

        if not fields:
            raise HTTPException(400, "Aucun champ a mettre a jour")

        # Mark as no longer auto-generated once manually edited
        fields.append("is_auto_generated = FALSE")
        fields.append("updated_at = NOW()")
        vals.append(sid)
        vals.append(pid)

        execute(
            f"UPDATE ni43101_sections SET {', '.join(fields)} WHERE id = %s AND project_id = %s",
            tuple(vals),
        )
        row = qone("SELECT * FROM ni43101_sections WHERE id = %s", (sid,))
        return _serialize(row)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.delete("/sections/{sid}")
def delete_section(pid: str, sid: str, user=Depends(project_user)):
    """Delete a subsection."""
    try:
        existing = qone("SELECT * FROM ni43101_sections WHERE id = %s AND project_id = %s", (sid, pid))
        if not existing:
            raise HTTPException(404, "Section introuvable")
        execute("DELETE FROM ni43101_sections WHERE id = %s", (sid,))
        return {"ok": True}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/sections/import")
async def import_section(
    pid: str,
    file: UploadFile = File(...),
    section_number: int = Form(13),
    subsection_key: str = Form("imported"),
    user=Depends(project_user),
):
    """Import a document (.txt, .docx, .pdf) as a NI 43-101 section.

    Extracts text content from the uploaded file and creates a new section.
    Supported formats: .txt, .docx, .pdf
    """
    try:
        return await _import_section_impl(pid, file, section_number, subsection_key, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


async def _import_section_impl(pid, file, section_number, subsection_key, user):
    filename = (file.filename or "").lower()
    content_bytes = await file.read()

    if not content_bytes:
        raise HTTPException(400, "Fichier vide")

    # Extract text based on file type
    text = ""
    if filename.endswith(".txt") or filename.endswith(".md"):
        text = content_bytes.decode("utf-8", errors="replace")

    elif filename.endswith(".docx"):
        try:
            import docx
            import io
            doc = docx.Document(io.BytesIO(content_bytes))
            paragraphs = []
            for para in doc.paragraphs:
                if para.text.strip():
                    paragraphs.append(para.text)
            text = "\n\n".join(paragraphs)
        except HTTPException:
            raise
        except psycopg2.OperationalError:
            raise HTTPException(503, detail="Database temporarily unavailable")

    elif filename.endswith(".pdf"):
        try:
            import io
            # Try pdfplumber first (better extraction), fall back to basic
            try:
                import pdfplumber
                with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
                    pages = []
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            pages.append(page_text)
                    text = "\n\n".join(pages)
            except ImportError:
                # Fallback: use xhtml2pdf's basic text extraction
                text = content_bytes.decode("utf-8", errors="replace")
        except HTTPException:
            raise
        except psycopg2.OperationalError:
            raise HTTPException(503, detail="Database temporarily unavailable")

    else:
        raise HTTPException(
            400,
            "Format non supporte. Utilisez .txt, .docx ou .pdf",
        )

    if not text.strip():
        raise HTTPException(400, "Aucun contenu textuel extrait du fichier")

    # Derive title from filename
    import os
    title = os.path.splitext(file.filename or "Import")[0]

    # Create the section
    row = execute(
        "INSERT INTO ni43101_sections "
        "(project_id, section_number, subsection_key, title_fr, title_en, "
        "content_fr, content_en, sort_order, is_auto_generated) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE) RETURNING *",
        (pid, section_number, subsection_key, title, title,
         text.strip(), "", 50),
    )
    return _serialize(row)


@router.get("/export/{fmt}/{lang}", deprecated=True)
def export_report(pid: str, fmt: str, lang: str, user=Depends(project_user)):
    """Export the report as PDF or DOCX in FR or EN.

    Deprecated synchronous endpoint — prefer POST /export/{fmt}/{lang}/async.
    """
    try:
        result = _export_report_impl(pid, fmt, lang, user)
        # _export_report_impl returns a Response with body+headers built in;
        # stamp deprecation markers on the returned object directly.
        if hasattr(result, "headers"):
            result.headers["Deprecation"] = "true"
            result.headers["Sunset"] = "Wed, 30 Sep 2026 00:00:00 GMT"
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ─── Async export endpoint (Chunk 4) ─────────────────────────────────────────
try:
    from .jobs import submit_job
except ImportError:  # pragma: no cover
    from routes.jobs import submit_job


@router.post("/export/{fmt}/{lang}/async", status_code=202)
def submit_export_async(pid: str, fmt: str, lang: str, user=Depends(project_user)):
    if fmt not in ("pdf", "docx"):
        raise HTTPException(400, "Format doit etre pdf ou docx")
    if lang not in ("fr", "en"):
        raise HTTPException(400, "Langue doit etre fr ou en")
    return submit_job(
        project_id=pid, user_id=user["id"],
        job_type="ni43101_export", payload={"fmt": fmt, "lang": lang},
    )


def _export_report_impl(pid: str, fmt: str, lang: str, user):
    if fmt not in ("pdf", "docx"):
        raise HTTPException(400, "Format doit etre pdf ou docx")
    if lang not in ("fr", "en"):
        raise HTTPException(400, "Langue doit etre fr ou en")

    sections = qall(
        "SELECT * FROM ni43101_sections WHERE project_id = %s "
        "ORDER BY section_number, sort_order",
        (pid,),
    )
    if not sections:
        raise HTTPException(404, "Aucune section generee. Utilisez /generate d'abord.")

    # Sanitize all section HTML content before export
    for s in sections:
        for field in ("content_fr", "content_en"):
            if s.get(field):
                s[field] = sanitize_html(s[field])

    project = qone("SELECT * FROM projects WHERE id = %s", (pid,))

    if fmt == "docx":
        try:
            from .ni43101_export import generate_docx
        except ImportError:  # pragma: no cover
            from routes.ni43101_export import generate_docx
        data = generate_docx(sections, lang, project)
        filename = f"NI43-101_{lang.upper()}.docx"
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    else:
        try:
            from .ni43101_export import generate_pdf
        except ImportError:  # pragma: no cover
            from routes.ni43101_export import generate_pdf
        data = generate_pdf(sections, lang, project)
        filename = f"NI43-101_{lang.upper()}.pdf"
        return Response(
            content=data,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
