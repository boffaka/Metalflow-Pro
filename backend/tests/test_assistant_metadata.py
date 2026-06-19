"""Unit tests for assistant response metadata (no DB)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

from engines.assistant import build_assistant_metadata, _assistant_result


def test_local_intent_includes_citations_and_actions():
    meta = build_assistant_metadata("proj-1", "lims", "local")
    assert len(meta["citations"]) >= 1
    assert meta["citations"][0]["path"] == "/projects/proj-1/lims"
    assert any(a["path"].endswith("/lims") for a in meta["suggested_actions"])
    assert "QP" in meta["limitations"] or "Qualified Person" in meta["limitations"]


def test_llm_source_adds_context_citations():
    meta = build_assistant_metadata("proj-2", None, "llm")
    assert len(meta["citations"]) == 3
    assert "IA" in meta["limitations"]


def test_fallback_notes_local_only():
    meta = build_assistant_metadata("proj-3", None, "fallback")
    assert "ANTHROPIC" in meta["limitations"] or "local" in meta["limitations"].lower()


def test_assistant_result_merges_fields():
    out = _assistant_result("p1", "Hello", "local", "production")
    assert out["response"] == "Hello"
    assert out["source"] == "local"
    assert out["intent"] == "production"
    assert "citations" in out and "suggested_actions" in out
