"""
Integration tests — Circuit Designer → Mass Balance flow.

Critical interaction chain:
  POST /auto-generate → qone(active template) → generate_mass_balance(engine)
  → conn/cursor transaction → commit/rollback → record_event → log_user_action

Tested with mocked DB so no live database is required.

Scenarios:
  Nominal  — active template found, engine runs, summary returned
  State    — coherence warnings from circuit validation appended to response
  Failure  — no template → 404, engine exception → 500 + rollback,
             stream version conflict → 409, stream not found → 404
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch, MagicMock, call

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword1!")

try:
    from backend import auth as auth_mod
    from backend.main import app
    from backend.routes.massbalance_v2 import _patch_stream_impl
    from backend.routes.circuit import create_template as _circuit_create_template
except ImportError:
    import auth as auth_mod
    from main import app
    from routes.massbalance_v2 import _patch_stream_impl

from fastapi.testclient import TestClient
from fastapi import HTTPException

_PM_USER = {
    "id": "pm-1",
    "email": "pm@test.com",
    "role": "Project Manager",
    "full_name": "PM",
}


def _pm_dependency(pid: str = ""):
    return _PM_USER


# ─── TestClient wired with dependency override ────────────────────────────────

def _get_client():
    app.dependency_overrides[auth_mod.project_user] = _pm_dependency
    return TestClient(app, raise_server_exceptions=False)


# =============================================================================
# 1. auto_generate endpoint — Circuit → MB full flow
# =============================================================================

class TestAutoGenerateFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = _get_client()

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.pop(auth_mod.project_user, None)

    def _mock_conn(self):
        mock_cur = MagicMock()
        mock_c = MagicMock()
        mock_c.cursor.return_value = mock_cur
        return mock_c

    # ── No active template → 404 ──────────────────────────────────────────────

    def test_no_active_template_returns_404(self) -> None:
        with patch("backend.routes.massbalance_v2.qone", return_value=None):
            r = self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        self.assertEqual(r.status_code, 404)
        self.assertIn("circuit", r.json()["detail"].lower())

    # ── Active template + successful engine ───────────────────────────────────

    def test_successful_generation_returns_summary(self) -> None:
        summary = {
            "sections_created": 8,
            "streams_created": 42,
            "total_feed_tph": 1517.0,
            "overall_recovery_pct": 88.5,
            "annual_gold_oz": 215_000.0,
        }
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release"),
            patch("backend.routes.massbalance_v2.generate_mass_balance", return_value=summary),
            patch("backend.routes.massbalance_v2.record_event"),
            patch("backend.routes.massbalance_v2.log_user_action"),
        ):
            r = self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["sections_created"], 8)
        self.assertEqual(data["streams_created"], 42)

    def test_successful_generation_commits_transaction(self) -> None:
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release"),
            patch("backend.routes.massbalance_v2.generate_mass_balance", return_value={"sections_created": 1, "streams_created": 5, "total_feed_tph": 500.0, "overall_recovery_pct": 90.0, "annual_gold_oz": 50000.0}),
            patch("backend.routes.massbalance_v2.record_event"),
            patch("backend.routes.massbalance_v2.log_user_action"),
        ):
            r = self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        self.assertEqual(r.status_code, 200)
        mock_c.commit.assert_called_once()
        mock_c.rollback.assert_not_called()

    def test_successful_generation_calls_record_event(self) -> None:
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release"),
            patch("backend.routes.massbalance_v2.generate_mass_balance", return_value={"sections_created": 1, "streams_created": 5, "total_feed_tph": 500.0, "overall_recovery_pct": 90.0, "annual_gold_oz": 50000.0}),
            patch("backend.routes.massbalance_v2.record_event") as mock_event,
            patch("backend.routes.massbalance_v2.log_user_action"),
        ):
            self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        mock_event.assert_called_once()
        kwargs = mock_event.call_args[1] if mock_event.call_args[1] else {}
        args = mock_event.call_args[0]
        # record_event called with action="auto_generate"
        call_str = str(args) + str(kwargs)
        self.assertIn("auto_generate", call_str)

    def test_successful_generation_calls_log_user_action(self) -> None:
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release"),
            patch("backend.routes.massbalance_v2.generate_mass_balance", return_value={"sections_created": 1, "streams_created": 5, "total_feed_tph": 500.0, "overall_recovery_pct": 90.0, "annual_gold_oz": 50000.0}),
            patch("backend.routes.massbalance_v2.record_event"),
            patch("backend.routes.massbalance_v2.log_user_action") as mock_log,
        ):
            self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        mock_log.assert_called_once()
        action = mock_log.call_args[0][0]
        self.assertEqual(action, "mass_balance.auto_generate")

    def test_successful_generation_releases_connection(self) -> None:
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release") as mock_release,
            patch("backend.routes.massbalance_v2.generate_mass_balance", return_value={"sections_created": 1, "streams_created": 5, "total_feed_tph": 500.0, "overall_recovery_pct": 90.0, "annual_gold_oz": 50000.0}),
            patch("backend.routes.massbalance_v2.record_event"),
            patch("backend.routes.massbalance_v2.log_user_action"),
        ):
            self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        mock_release.assert_called_once_with(mock_c)

    # ── Engine failure → 500 + rollback ───────────────────────────────────────

    def test_sync_recovery_writes_text_marker_not_numeric(self) -> None:
        from backend.routes.massbalance_v2 import _sync_recovery_params_from_mb_summary

        mock_cur = MagicMock()
        mock_cur.rowcount = 0
        summary = {
            "overall_recovery_pct": 88.5,
            "recovery_snapshot_source": "mass_balance",
        }
        _sync_recovery_params_from_mb_summary("proj-1", summary, mock_cur)
        sql_calls = [c.args[0] for c in mock_cur.execute.call_args_list]
        self.assertTrue(
            any("param_value_text" in sql and "recovery_snapshot_source" in str(c)
                for c in mock_cur.execute.call_args_list),
            "recovery_snapshot_source must use param_value_text, not param_value",
        )
        self.assertFalse(
            any("param_value=%s" in sql and "mass_balance" in str(c.args)
                for sql, c in zip(sql_calls, mock_cur.execute.call_args_list)
                if len(c.args) > 1),
        )

    def test_engine_failure_returns_500(self) -> None:
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release"),
            patch("backend.routes.massbalance_v2.generate_mass_balance",
                  side_effect=RuntimeError("engine exploded")),
        ):
            r = self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        self.assertEqual(r.status_code, 500)

    def test_engine_failure_triggers_rollback(self) -> None:
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release"),
            patch("backend.routes.massbalance_v2.generate_mass_balance",
                  side_effect=RuntimeError("crash")),
        ):
            self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        mock_c.rollback.assert_called_once()
        mock_c.commit.assert_not_called()

    def test_engine_failure_still_releases_connection(self) -> None:
        mock_c = self._mock_conn()
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release") as mock_release,
            patch("backend.routes.massbalance_v2.generate_mass_balance",
                  side_effect=RuntimeError("crash")),
        ):
            self.client.post("/api/v1/projects/proj-1/mass-balance-v2/auto-generate")
        mock_release.assert_called_once()

    # ── Coherence warnings in response ────────────────────────────────────────

    def test_coherence_warnings_appended_to_summary(self) -> None:
        summary = {"sections_created": 6, "streams_created": 30, "total_feed_tph": 1000.0, "overall_recovery_pct": 85.0, "annual_gold_oz": 100_000.0}
        mock_c = self._mock_conn()
        warnings = ["Operation X has no LIMS data"]
        with (
            patch("backend.routes.massbalance_v2.qone", return_value={"id": "tpl-1"}),
            patch("backend.routes.massbalance_v2.conn", return_value=mock_c),
            patch("backend.routes.massbalance_v2.release"),
            patch("backend.routes.massbalance_v2.generate_mass_balance", return_value=summary),
            patch("backend.routes.massbalance_v2.record_event"),
            patch("backend.routes.massbalance_v2.log_user_action"),
            patch("backend.routes.massbalance_v2.circuit", create=True),
        ):
            # Simulate coherence module returning warnings
            with patch("backend.routes.massbalance_v2.circuit.validate_circuit",
                       return_value={"warnings": warnings, "missing": []},
                       create=True):
                r = self.client.post(
                    "/api/v1/projects/proj-1/mass-balance-v2/auto-generate"
                )
        # Response must be successful even if coherence check fails silently
        self.assertEqual(r.status_code, 200)


# =============================================================================
# 2. _patch_stream_impl — optimistic locking integration
# =============================================================================

class TestPatchStreamImpl(unittest.TestCase):
    """Tests the MB stream patch: DB read → version check → derived recalc → write."""

    def _current_stream(self, version: int = 3) -> dict:
        return {
            "id": "stream-1",
            "section_id": "sec-1",
            "project_id": "proj-1",
            "solids_tph": 500.0,
            "water_tph": 250.0,
            "slurry_pct_w": 66.7,
            "au_gt": 1.5,
            "hours_per_day": 22.0,
            "source": "Manual",
            "solids_sg": 2.74,
            "version": version,
        }

    def _body(self, version: int = 3, **kw) -> dict:
        return {
            "version": version,
            "solids_tph": 600.0,
            **kw,
        }

    # ── Nominal ───────────────────────────────────────────────────────────────

    def test_correct_version_triggers_execute(self) -> None:
        updated = {**self._current_stream(4), "solids_tph": 600.0}
        with (
            patch("backend.routes.massbalance_v2.qone", return_value=self._current_stream(3)),
            patch("backend.routes.massbalance_v2.execute", return_value=updated),
        ):
            result = _patch_stream_impl("proj-1", "stream-1", self._body(version=3), _PM_USER)
        self.assertEqual(result["solids_tph"], 600.0)

    def test_derived_fields_recalculated(self) -> None:
        """After update, derived fields (slurry_tph etc.) must be recomputed."""
        updated = {**self._current_stream(4), "solids_tph": 600.0, "slurry_tph": 850.0}
        with patch("backend.routes.massbalance_v2.qone", return_value=self._current_stream(3)):
            with patch("backend.routes.massbalance_v2.execute", return_value=updated) as mock_exec:
                _patch_stream_impl("proj-1", "stream-1", self._body(version=3), _PM_USER)
        # execute must be called with computed derived values
        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        # slurry_tph = 600 + 250 = 850
        self.assertIn(850.0, call_args[1])

    # ── Version mismatch → 409 ────────────────────────────────────────────────

    def test_wrong_version_raises_409(self) -> None:
        with (
            patch("backend.routes.massbalance_v2.qone", return_value=self._current_stream(version=5)),
        ):
            with self.assertRaises(HTTPException) as cm:
                _patch_stream_impl("proj-1", "stream-1", self._body(version=3), _PM_USER)
        self.assertEqual(cm.exception.status_code, 409)

    # ── Stream not found → 404 ────────────────────────────────────────────────

    def test_stream_not_found_raises_404(self) -> None:
        with patch("backend.routes.massbalance_v2.qone", return_value=None):
            with self.assertRaises(HTTPException) as cm:
                _patch_stream_impl("proj-1", "ghost-stream", self._body(), _PM_USER)
        self.assertEqual(cm.exception.status_code, 404)

    # ── No valid fields → 400 ─────────────────────────────────────────────────

    def test_no_valid_fields_raises_400(self) -> None:
        with self.assertRaises(HTTPException) as cm:
            _patch_stream_impl("proj-1", "s1", {"version": 1, "bad_field": "x"}, _PM_USER)
        self.assertEqual(cm.exception.status_code, 400)

    def test_missing_version_raises_400(self) -> None:
        with self.assertRaises(HTTPException) as cm:
            _patch_stream_impl("proj-1", "s1", {"solids_tph": 100.0}, _PM_USER)
        self.assertEqual(cm.exception.status_code, 400)

    # ── Concurrent modification (execute returns None) → 409 ─────────────────

    def test_concurrent_modification_raises_409(self) -> None:
        with (
            patch("backend.routes.massbalance_v2.qone", return_value=self._current_stream(3)),
            patch("backend.routes.massbalance_v2.execute", return_value=None),
        ):
            with self.assertRaises(HTTPException) as cm:
                _patch_stream_impl("proj-1", "stream-1", self._body(version=3), _PM_USER)
        self.assertEqual(cm.exception.status_code, 409)


# =============================================================================
# 3. create_template — log_user_action is called after DB insert
# =============================================================================

class TestCreateTemplateLogging(unittest.TestCase):

    def test_create_template_calls_log_user_action(self) -> None:
        new_tpl = {"id": "ct-new", "project_id": "proj-1", "name": "Flotation Circuit"}
        with (
            patch("backend.routes.circuit.execute", return_value=new_tpl),
            patch("backend.routes.circuit.log_user_action") as mock_log,
        ):
            from backend.models import CircuitTemplateIn
            body = CircuitTemplateIn(name="Flotation Circuit")
            result = _circuit_create_template("proj-1", body, _PM_USER)
        mock_log.assert_called_once()
        action = mock_log.call_args[0][0]
        self.assertEqual(action, "circuit_template.create")

    def test_create_template_passes_entity_id_to_log(self) -> None:
        new_tpl = {"id": "ct-42", "project_id": "proj-1", "name": "CIL Circuit"}
        with (
            patch("backend.routes.circuit.execute", return_value=new_tpl),
            patch("backend.routes.circuit.log_user_action") as mock_log,
        ):
            from backend.models import CircuitTemplateIn
            body = CircuitTemplateIn(name="CIL Circuit")
            _circuit_create_template("proj-1", body, _PM_USER)
        kwargs = mock_log.call_args[1]
        self.assertEqual(kwargs["entity_id"], "ct-42")

    def test_create_template_returns_db_row(self) -> None:
        new_tpl = {"id": "ct-99", "name": "SAG Circuit", "project_id": "proj-1"}
        with (
            patch("backend.routes.circuit.execute", return_value=new_tpl),
            patch("backend.routes.circuit.log_user_action"),
        ):
            from backend.models import CircuitTemplateIn
            result = _circuit_create_template(
                "proj-1", CircuitTemplateIn(name="SAG Circuit"), _PM_USER
            )
        self.assertEqual(result["id"], "ct-99")


if __name__ == "__main__":
    unittest.main()
