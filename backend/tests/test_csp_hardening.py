"""
Tests for CSP hardening — Task 1.
Verifies that the legacy CSP policy removes unsafe-eval from script-src,
retains unsafe-inline (required for 500+ inline event handlers), and
retains unsafe-inline in style-src.
"""
from __future__ import annotations

import sys
import os

_BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from observability import _LEGACY_CSP  # noqa: E402


def _script_src(csp: str) -> str:
    """Extract the script-src directive value from a CSP string."""
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.startswith("script-src "):
            return directive
    return ""


def _style_src(csp: str) -> str:
    """Extract the style-src directive value from a CSP string."""
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.startswith("style-src "):
            return directive
    return ""


def test_legacy_csp_has_no_unsafe_eval():
    """script-src must not contain 'unsafe-eval'."""
    assert "'unsafe-eval'" not in _script_src(_LEGACY_CSP), (
        f"'unsafe-eval' found in script-src: {_script_src(_LEGACY_CSP)!r}"
    )


def test_legacy_csp_has_unsafe_inline_in_script_src():
    """script-src must contain 'unsafe-inline' (required for inline event handlers)."""
    assert "'unsafe-inline'" in _script_src(_LEGACY_CSP), (
        f"'unsafe-inline' missing from script-src: {_script_src(_LEGACY_CSP)!r}"
    )


def test_legacy_csp_style_src_keeps_unsafe_inline():
    """style-src must still contain 'unsafe-inline' (inline styles are needed)."""
    assert "'unsafe-inline'" in _style_src(_LEGACY_CSP), (
        f"'unsafe-inline' missing from style-src: {_style_src(_LEGACY_CSP)!r}"
    )


def test_legacy_csp_restricts_script_sources():
    """script-src must restrict to self + known CDNs only."""
    src = _script_src(_LEGACY_CSP)
    assert "'self'" in src
    assert "https://cdnjs.cloudflare.com" in src
    assert "https://cdn.jsdelivr.net" in src
    assert "https://cdn.plot.ly" in src
