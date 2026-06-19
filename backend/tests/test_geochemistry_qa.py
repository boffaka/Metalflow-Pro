"""Unit tests for geochemistry QA helpers (no database)."""
from __future__ import annotations

try:
    from backend.routes.geochemistry import _sulfur_balance_warnings
except ImportError:
    from routes.geochemistry import _sulfur_balance_warnings


def test_sulfur_balance_warns_when_sulfide_plus_sulfate_exceeds_total() -> None:
    w = _sulfur_balance_warnings(3.0, 2.0, 2.0)
    assert w and "dépasse" in w[0]


def test_sulfur_balance_empty_when_total_zero() -> None:
    assert _sulfur_balance_warnings(0.0, 1.0, 1.0) == []
