"""
MPDPMS — Rapport Interne et Externe
Upload, list, download and delete project reports organised by phase.
Files are stored on disk in UPLOADS_DIR; metadata is stored in project_reports.
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import psycopg2
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

try:
    from ..auth import project_user
    from ..db import execute, qall, qone
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import execute, qall, qone

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["reports"])

# Resolved at import time via the app's UPLOADS_DIR (set in main.py)
_UPLOADS_DIR: Optional[Path] = None

ALLOWED_PHASES = {
    "scoping", "pea", "pfs", "bfs", "feed",
    "detailed", "construction", "commissioning", "operations", "other",
}

ALLOWED_TYPES = {"interne", "externe"}

MAX_SIZE = int(os.getenv("MAX_REPORT_SIZE_BYTES", str(25 * 1024 * 1024)))  # 25 MB default

# Maximum stored filename length (prevents DoS via very long names)
MAX_FILENAME_LEN = 100


def _uploads_dir() -> Path:
    global _UPLOADS_DIR
    if _UPLOADS_DIR is None:
        base = Path(__file__).resolve().parent.parent
        _UPLOADS_DIR = base / "storage" / "reports"
        _UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return _UPLOADS_DIR


def _project_dir(pid: str) -> Path:
    d = _uploads_dir() / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── List ───────────────────────────────────────────────────────────────────────
@router.get("/reports")
def list_reports(pid: str, phase: Optional[str] = None, user=Depends(project_user)):
    try:
        sql = """
            SELECT r.id, r.filename, r.title, r.report_type, r.phase,
                   r.description, r.file_size, r.author, r.created_at,
                   u.email AS uploaded_by_email
            FROM project_reports r
            LEFT JOIN users u ON r.uploaded_by = u.id
            WHERE r.project_id = %s
        """
        params: list = [pid]
        if phase:
            sql += " AND r.phase = %s"
            params.append(phase)
        sql += " ORDER BY r.created_at DESC"
        rows = qall(sql, tuple(params))
        return [dict(r) for r in (rows or [])]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        logger.error("DB error in list_reports pid=%s", pid)
        raise HTTPException(503, detail="Base de données temporairement indisponible")


# ── Upload ─────────────────────────────────────────────────────────────────────
@router.post("/reports", status_code=201)
async def upload_report(
    pid: str,
    file: UploadFile = File(...),
    title: str = Form(...),
    phase: str = Form(...),
    report_type: str = Form("interne"),
    description: str = Form(""),
    author: str = Form(""),
    user=Depends(project_user),
):
    try:
        if phase not in ALLOWED_PHASES:
            raise HTTPException(422, f"Phase invalide. Valeurs acceptées: {sorted(ALLOWED_PHASES)}")
        if report_type not in ALLOWED_TYPES:
            raise HTTPException(422, "report_type doit être 'interne' ou 'externe'")

        content = await file.read()
        if len(content) > MAX_SIZE:
            raise HTTPException(413, f"Fichier trop volumineux (max {MAX_SIZE // (1024*1024)} Mo)")

        # Sanitize filename (prevent path traversal, keep only safe chars)
        raw_name = Path(file.filename or "fichier").name
        import re as _re
        safe_name = _re.sub(r'[^\w.\-]', '_', raw_name)[:MAX_FILENAME_LEN]
        uid = str(uuid.uuid4())
        dest = _project_dir(pid) / f"{uid}_{safe_name}"

        # FIX: INSERT DB first, then write file — avoids orphaned files on DB failure
        execute(
            """
            INSERT INTO project_reports
                (id, project_id, filename, title, report_type, phase,
                 description, file_path, file_size, author, uploaded_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (uid, pid, safe_name, title, report_type, phase,
             description, str(dest), len(content), author, user["id"]),
        )
        dest.write_bytes(content)
        return {"id": uid, "filename": safe_name, "title": title, "phase": phase,
                "report_type": report_type, "file_size": len(content)}
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(404, "Fichier introuvable")
    except PermissionError:
        raise HTTPException(403, "Permission refusée")
    except psycopg2.OperationalError:
        logger.error("DB error in upload_report pid=%s", pid)
        raise HTTPException(503, detail="Base de données temporairement indisponible")


# ── Download ───────────────────────────────────────────────────────────────────
@router.get("/reports/{rid}/download")
def download_report(pid: str, rid: str, user=Depends(project_user)):
    try:
        row = qone(
            "SELECT filename, file_path FROM project_reports WHERE id=%s AND project_id=%s",
            (rid, pid),
        )
        if not row:
            raise HTTPException(404, "Rapport introuvable")
        fp = Path(row["file_path"]).resolve()
        allowed_dir = _project_dir(pid).resolve()
        if not fp.is_relative_to(allowed_dir):
            logger.warning("Path traversal blocked: %s outside %s", fp, allowed_dir)
            raise HTTPException(403, "Accès refusé")
        if not fp.exists():
            raise HTTPException(404, "Fichier introuvable sur le serveur")
        return FileResponse(str(fp), filename=row["filename"],
                            media_type="application/octet-stream")
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(404, "Fichier introuvable")
    except PermissionError:
        raise HTTPException(403, "Permission refusée")
    except psycopg2.OperationalError:
        logger.error("DB error in download_report pid=%s", pid)
        raise HTTPException(503, detail="Base de données temporairement indisponible")


# ── Delete ─────────────────────────────────────────────────────────────────────
@router.delete("/reports/{rid}", status_code=204)
def delete_report(pid: str, rid: str, user=Depends(project_user)):
    try:
        row = qone(
            "SELECT file_path FROM project_reports WHERE id=%s AND project_id=%s",
            (rid, pid),
        )
        if not row:
            raise HTTPException(404, "Rapport introuvable")
        fp = Path(row["file_path"]).resolve()
        allowed_dir = _project_dir(pid).resolve()
        if not fp.is_relative_to(allowed_dir):
            logger.warning("Path traversal blocked in delete: %s outside %s", fp, allowed_dir)
            raise HTTPException(403, "Accès refusé")
        try:
            fp.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete file %s", fp)
        execute("DELETE FROM project_reports WHERE id=%s AND project_id=%s", (rid, pid))
        return None
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(404, "Fichier introuvable")
    except PermissionError:
        raise HTTPException(403, "Permission refusée")
    except psycopg2.OperationalError:
        logger.error("DB error in delete_report pid=%s", pid)
        raise HTTPException(503, detail="Base de données temporairement indisponible")
