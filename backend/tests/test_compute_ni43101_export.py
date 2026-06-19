"""Layer 2 — pure compute tests for NI 43-101 export."""
from __future__ import annotations

import pytest

from backend.compute.ni43101_export import build_export
from backend.jobs.context import JobCancelled


class _FakeCtx:
    def __init__(self, cancel_after: int | None = None):
        self.calls = 0
        self.cancel_after = cancel_after
    def check_cancelled(self) -> None:
        self.calls += 1
        if self.cancel_after is not None and self.calls > self.cancel_after:
            raise JobCancelled()
    def report_progress(self, *a, **k) -> None:
        pass


_SECTIONS = [
    {"section_number": 13, "subsection_key": "13.1",
     "title_fr": "Essais", "title_en": "Tests",
     "content_fr": "Contenu test\n- bullet", "content_en": "Test content\n- bullet"},
    {"section_number": 17, "subsection_key": "17.1",
     "title_fr": "Récupération", "title_en": "Recovery",
     "content_fr": "Procédé", "content_en": "Process"},
]
_PROJECT = {"project_name": "Côté Gold", "project_code": "CG-001"}


def test_build_export_docx_returns_bytes_and_metadata():
    ctx = _FakeCtx()
    filename, ctype, data = build_export(
        {"fmt": "docx", "lang": "fr", "sections": _SECTIONS, "project": _PROJECT},
        ctx,
    )
    assert filename.endswith(".docx") and "FR" in filename
    assert ctype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert isinstance(data, bytes) and len(data) > 1000  # docx files have a sane minimum size
    # docx is a zip - first bytes are PK
    assert data[:2] == b"PK"


def test_build_export_pdf_returns_bytes_and_metadata():
    ctx = _FakeCtx()
    filename, ctype, data = build_export(
        {"fmt": "pdf", "lang": "en", "sections": _SECTIONS, "project": _PROJECT},
        ctx,
    )
    assert filename.endswith(".pdf") and "EN" in filename
    assert ctype == "application/pdf"
    assert isinstance(data, bytes) and len(data) > 500
    assert data[:4] == b"%PDF"


def test_build_export_rejects_invalid_fmt():
    ctx = _FakeCtx()
    with pytest.raises(ValueError):
        build_export({"fmt": "xls", "lang": "fr", "sections": _SECTIONS, "project": _PROJECT}, ctx)


def test_build_export_rejects_invalid_lang():
    ctx = _FakeCtx()
    with pytest.raises(ValueError):
        build_export({"fmt": "pdf", "lang": "es", "sections": _SECTIONS, "project": _PROJECT}, ctx)


def test_build_export_rejects_empty_sections():
    ctx = _FakeCtx()
    with pytest.raises(ValueError):
        build_export({"fmt": "pdf", "lang": "fr", "sections": [], "project": _PROJECT}, ctx)


def test_build_export_checks_cancelled_at_least_once():
    ctx = _FakeCtx()
    build_export({"fmt": "docx", "lang": "fr", "sections": _SECTIONS, "project": _PROJECT}, ctx)
    assert ctx.calls >= 1
