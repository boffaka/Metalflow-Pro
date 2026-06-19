"""Tests for NI 43-101 readiness checker."""
import os

# Set required env vars before importing modules that trigger settings validation
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

try:
    from routes.ni43101 import check_readiness
except ImportError:
    from backend.routes.ni43101 import check_readiness


def test_scoping_minimal_data():
    counts = {"a1": 5, "b1": 0, "c2": 0, "d1": 3, "e1": 0, "g1": 0}
    dc_sources = {"L": 2, "M": 1, "D": 5}
    result = check_readiness("scoping", counts, dc_sources, has_mass_balance=False, has_simulation=False)
    assert result["score_pct"] >= 50


def test_pfs_insufficient_d1():
    counts = {"a1": 20, "b1": 5, "c2": 3, "d1": 5, "e1": 2, "g1": 0}
    dc_sources = {"L": 10, "M": 5, "D": 0}
    result = check_readiness("pfs", counts, dc_sources, has_mass_balance=True, has_simulation=False)
    assert result["ready"] is False
    assert any("D1" in item["item"] or "d1" in item["item"].lower() for item in result["checklist"] if item["status"] == "fail")


def test_fs_all_requirements_met():
    counts = {"a1": 40, "b1": 12, "c2": 10, "d1": 35, "e1": 5, "g1": 8}
    dc_sources = {"L": 50, "M": 10, "D": 0}
    result = check_readiness("fs", counts, dc_sources, has_mass_balance=True, has_simulation=True)
    assert result["ready"] is True
    assert result["score_pct"] == 100


def test_unknown_stage():
    result = check_readiness("unknown_stage", {}, {})
    assert result["ready"] is False


def test_dfs_requirements():
    counts = {"a1": 60, "b1": 20, "c2": 15, "d1": 40, "e1": 10, "g1": 10}
    dc_sources = {"L": 70, "M": 5, "D": 0}
    result = check_readiness("dfs", counts, dc_sources, has_mass_balance=True, has_simulation=True)
    assert result["ready"] is True
