"""Unit tests for the endpoint-deprecation shim — Lot B (no DB needed)."""

import pytest

pytestmark = pytest.mark.no_db


def get_fn():
    try:
        from backend.deprecation import build_deprecation_headers
    except ImportError:
        from deprecation import build_deprecation_headers
    return build_deprecation_headers


def test_sets_deprecation_true():
    fn = get_fn()
    h = fn(successor="/api/v1/projects/{pid}/mass-balance-v2")
    assert h["Deprecation"] == "true"


def test_link_points_to_successor_as_successor_version():
    fn = get_fn()
    h = fn(successor="/api/v1/projects/{pid}/mass-balance-v2")
    # RFC 8288 successor-version link relation
    assert "/api/v1/projects/{pid}/mass-balance-v2" in h["Link"]
    assert 'rel="successor-version"' in h["Link"]


def test_no_successor_omits_link():
    fn = get_fn()
    h = fn(successor=None)
    assert h["Deprecation"] == "true"
    assert "Link" not in h


def test_headers_are_str_str():
    fn = get_fn()
    h = fn(successor="/x")
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in h.items())
