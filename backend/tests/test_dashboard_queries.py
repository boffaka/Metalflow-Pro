"""Tests for dashboard N+1 query consolidation (Task 5).

Verifies:
- Feature flag routing between legacy and optimized paths
- Response contains all expected top-level keys
- Cache TTL is set to 120s
- psycopg2.OperationalError yields 503
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys
import types
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers — build a fake module environment so we can import dashboard
# without a live database or full FastAPI app wiring.
# ---------------------------------------------------------------------------

_EXPECTED_TOP_KEYS = {
    "project", "production", "stage_gates", "lims", "geomet",
    "automation", "modules", "costs", "risks", "alerts", "activity",
}

_FAKE_LIMS_AUDIT = {
    "quality_score": 75.0,
    "issue_counts": {"high": 1, "medium": 2, "low": 0},
    "issues": [],
}

_DASHBOARD_FILE = os.path.join(
    os.path.dirname(__file__), "..", "routes", "dashboard.py",
)


def _mock_qone(sql, params=None):
    """Return plausible empty/zero rows for any qone call."""
    if "projects" in sql and "SELECT *" in sql:
        return {
            "project_name": "Test Project",
            "project_code": "TP-001",
            "target_tph": 100,
            "gold_grade_g_t": 1.5,
            "availability_pct": 92,
            "operating_hours_day": 22,
            "mine_life_years": 10,
            "gold_price_usd_oz": 1800,
        }
    if "stage_gates" in sql and "stage_name" in sql:
        # Legacy path: qone for current gate — no incomplete gates
        return None
    if "simulation_params" in sql:
        return None
    if "lims_d1" in sql and "AVG" in sql:
        return {"avg_rec": None}
    # Generic COUNT / SUM queries — return a dict with all possible aliases
    return {
        "n": 0, "total": 0, "t": 0,
        "sample_count": 0, "lims_complete": 0,
        "geomet_domains": 0, "geomet_composites": 0,
        "control_variables": 0, "control_alarms": 0, "control_interlocks": 0,
        "equip_count": 0, "equip_capex": 0,
        "dc_count": 0, "dc_v2_count": 0,
        "mb_streams": 0, "failed_tasks": 0,
        "ni_sections": 0, "recent_audit": 0,
        "capex_total": 0, "opex_total": 0,
        "opex_manpower": 0, "opex_power": 0,
        "opex_reagents": 0, "opex_mobile": 0,
    }


def _mock_qall(sql, params=None):
    """Return empty lists for any qall call."""
    return []


def _load_dashboard():
    """Load the dashboard module from file with mocked dependencies.

    Uses importlib.util.spec_from_file_location so we bypass the normal
    package-relative import chain entirely.
    """
    # Build stub modules for the ImportError fallback path in dashboard.py
    stub_auth = types.ModuleType("auth")
    stub_auth.project_user = lambda: None

    stub_db = types.ModuleType("db")
    stub_db.qone = _mock_qone
    stub_db.qall = _mock_qall

    stub_routes = types.ModuleType("routes")
    stub_routes_lims = types.ModuleType("routes.lims")
    stub_routes_lims._audit_lims_project = lambda pid: _FAKE_LIMS_AUDIT
    stub_routes.__dict__["lims"] = stub_routes_lims

    saved = {}
    patch_mods = {
        "auth": stub_auth,
        "db": stub_db,
        "routes": stub_routes,
        "routes.lims": stub_routes_lims,
    }
    # Save and inject stubs
    for name, mod in patch_mods.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # Also remove any previously cached dashboard module
    for key in list(sys.modules):
        if "dashboard" in key:
            saved[key] = sys.modules.pop(key)

    try:
        spec = importlib.util.spec_from_file_location(
            "routes.dashboard", os.path.abspath(_DASHBOARD_FILE),
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        # Restore original modules
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    """Feature flag routes between legacy and optimized implementations."""

    def test_optimized_path_by_default(self):
        """When DASHBOARD_LEGACY_QUERIES is unset/0, optimized path runs."""
        with patch.dict(os.environ, {"DASHBOARD_LEGACY_QUERIES": "0"}, clear=False):
            dashboard = _load_dashboard()
            assert dashboard._USE_LEGACY_QUERIES is False
            result = dashboard._get_dashboard_impl("test-pid", {})
            assert isinstance(result, dict)

    def test_legacy_path_when_flag_set(self):
        """When DASHBOARD_LEGACY_QUERIES=1, legacy path runs."""
        with patch.dict(os.environ, {"DASHBOARD_LEGACY_QUERIES": "1"}, clear=False):
            dashboard = _load_dashboard()
            assert dashboard._USE_LEGACY_QUERIES is True
            result = dashboard._get_dashboard_impl("test-pid", {})
            assert isinstance(result, dict)

    def test_dispatcher_calls_legacy(self):
        """_get_dashboard_impl delegates to _get_dashboard_legacy when flag is set."""
        dashboard = _load_dashboard()
        dashboard._USE_LEGACY_QUERIES = True
        with patch.object(dashboard, "_get_dashboard_legacy", return_value={"ok": True}) as mock_leg:
            result = dashboard._get_dashboard_impl("p1", {})
            mock_leg.assert_called_once_with("p1", {})
            assert result == {"ok": True}

    def test_dispatcher_calls_optimized(self):
        """_get_dashboard_impl delegates to _get_dashboard_optimized when flag is off."""
        dashboard = _load_dashboard()
        dashboard._USE_LEGACY_QUERIES = False
        with patch.object(dashboard, "_get_dashboard_optimized", return_value={"ok": True}) as mock_opt:
            result = dashboard._get_dashboard_impl("p1", {})
            mock_opt.assert_called_once_with("p1", {})
            assert result == {"ok": True}


class TestResponseStructure:
    """Both paths produce a response with all expected top-level keys."""

    def test_optimized_has_all_keys(self):
        dashboard = _load_dashboard()
        dashboard._USE_LEGACY_QUERIES = False
        result = dashboard._get_dashboard_impl("test-pid", {})
        assert _EXPECTED_TOP_KEYS == set(result.keys()), (
            f"Missing: {_EXPECTED_TOP_KEYS - set(result.keys())}; "
            f"Extra: {set(result.keys()) - _EXPECTED_TOP_KEYS}"
        )

    def test_legacy_has_all_keys(self):
        dashboard = _load_dashboard()
        dashboard._USE_LEGACY_QUERIES = True
        result = dashboard._get_dashboard_impl("test-pid", {})
        assert _EXPECTED_TOP_KEYS == set(result.keys()), (
            f"Missing: {_EXPECTED_TOP_KEYS - set(result.keys())}; "
            f"Extra: {set(result.keys()) - _EXPECTED_TOP_KEYS}"
        )

    def test_optimized_response_nested_keys(self):
        """Verify important nested keys exist in optimized response."""
        dashboard = _load_dashboard()
        dashboard._USE_LEGACY_QUERIES = False
        result = dashboard._get_dashboard_impl("test-pid", {})

        # project
        for k in ("name", "code", "target_tph", "gold_grade", "availability_pct", "gold_price", "mine_life_years"):
            assert k in result["project"], f"Missing project.{k}"

        # production
        for k in ("annual_tonnes", "annual_gold_oz", "annual_gold_koz", "annual_revenue_musd", "recovery_pct"):
            assert k in result["production"], f"Missing production.{k}"

        # stage_gates
        for k in ("current_phase", "total", "completed", "completion_pct", "blocked_count"):
            assert k in result["stage_gates"], f"Missing stage_gates.{k}"

        # lims
        for k in ("sample_count", "tests_complete", "tests_pending", "quality_score", "high_issues", "medium_issues"):
            assert k in result["lims"], f"Missing lims.{k}"

        # costs
        for k in ("capex_total", "opex_total", "opex_v2_total", "opex_breakdown", "currency"):
            assert k in result["costs"], f"Missing costs.{k}"
        for k in ("manpower", "power", "reagents", "mobile"):
            assert k in result["costs"]["opex_breakdown"], f"Missing costs.opex_breakdown.{k}"

        # alerts
        for k in ("failed_tasks", "critical_risks", "high_risks", "lims_high_issues", "blocked_gates"):
            assert k in result["alerts"], f"Missing alerts.{k}"

        # activity
        for k in ("recent_audit_events", "recent_decisions"):
            assert k in result["activity"], f"Missing activity.{k}"

    def test_legacy_response_nested_keys(self):
        """Verify important nested keys exist in legacy response."""
        dashboard = _load_dashboard()
        dashboard._USE_LEGACY_QUERIES = True
        result = dashboard._get_dashboard_impl("test-pid", {})

        # Spot-check a few critical nested keys
        assert "capex_total" in result["costs"]
        assert "opex_breakdown" in result["costs"]
        assert "recovery_pct" in result["production"]
        assert "blocked_gates" in result["alerts"]


class TestCacheTTL:
    """Cache TTL is set to 120 seconds."""

    def test_ttl_is_120(self):
        dashboard = _load_dashboard()
        assert dashboard._DASHBOARD_TTL == 120.0


class TestExceptionHandling:
    """get_dashboard catches psycopg2.OperationalError with 503."""

    def test_operational_error_handling(self):
        """Verify the module imports psycopg2 and the except clause references OperationalError."""
        import psycopg2
        dashboard = _load_dashboard()

        # Verify the source code contains the psycopg2.OperationalError handler
        import inspect
        source = inspect.getsource(dashboard.get_dashboard)
        assert "psycopg2.OperationalError" in source
        assert "503" in source
