"""NI 43-101 export compute: builds the DOCX or PDF artifact from pre-loaded
sections and project metadata. No DB access, no FastAPI dependencies."""
from __future__ import annotations

from typing import Any

try:
    from backend.routes.ni43101_export import generate_docx, generate_pdf
except ImportError:  # pragma: no cover
    from routes.ni43101_export import generate_docx, generate_pdf

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME = "application/pdf"


def build_export(payload: dict[str, Any], ctx) -> tuple[str, str, bytes]:
    """Returns (filename, content_type, raw_bytes)."""
    fmt = (payload.get("fmt") or "").lower()
    lang = (payload.get("lang") or "").lower()
    sections = payload.get("sections") or []
    project = payload.get("project") or {}

    if fmt not in ("pdf", "docx"):
        raise ValueError("fmt must be 'pdf' or 'docx'")
    if lang not in ("fr", "en"):
        raise ValueError("lang must be 'fr' or 'en'")
    if not sections:
        raise ValueError("sections must be a non-empty list")

    ctx.check_cancelled()
    if fmt == "docx":
        data = generate_docx(sections, lang, project)
        ctx.check_cancelled()
        return f"NI43-101_{lang.upper()}.docx", _DOCX_MIME, data
    data = generate_pdf(sections, lang, project)
    ctx.check_cancelled()
    return f"NI43-101_{lang.upper()}.pdf", _PDF_MIME, data
