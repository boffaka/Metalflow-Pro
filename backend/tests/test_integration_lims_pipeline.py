"""
Integration tests — LIMS write → Pipeline cascade state machine.

Critical interaction chain:
  LIMS route write → _signal_lims_change()
    → set_status(lims, "complete")
    → mark_stale_cascade("lims")
      → get_status(downstream_module)   [DB read]
      → set_status(downstream, "stale") [DB write if was complete/error]

Also tests the full STALE_CASCADE graph coverage and correct
status-transition guards (only complete/error → stale, not pending).

Scenarios:
  Nominal  — LIMS write cascades stale to all 7 downstream modules
  State    — only "complete"/"error" modules get marked stale; "pending" untouched
  Cascade  — each source module correctly marks exactly its declared descendants
  Failure  — DB failure in pipeline signal does NOT block the LIMS write
  Edge     — economics has no downstream; get_status returns "pending" when absent
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch, call, MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword1!")

try:
    from backend.routes.pipeline import (
        get_status,
        set_status,
        mark_stale_cascade,
        STALE_CASCADE,
        _ensure_status_row,
    )
    from backend.routes.lims import _signal_lims_change
except ImportError:
    from routes.pipeline import (
        get_status,
        set_status,
        mark_stale_cascade,
        STALE_CASCADE,
        _ensure_status_row,
    )
    from routes.lims import _signal_lims_change


# =============================================================================
# 1. STALE_CASCADE graph completeness
# =============================================================================

class TestStaleCascadeGraph(unittest.TestCase):
    """Verify the declared dependency graph is complete and consistent."""

    def test_lims_cascades_to_seven_modules(self) -> None:
        downstream = STALE_CASCADE["lims"]
        self.assertEqual(len(downstream), 7)
        for mod in ("design_criteria", "mass_balance", "flowsheet",
                    "equipment", "simulation", "opex", "economics"):
            self.assertIn(mod, downstream)

    def test_blockmodel_cascades_only_to_lims(self) -> None:
        self.assertEqual(STALE_CASCADE["blockmodel"], ["lims"])

    def test_design_criteria_cascades_to_six(self) -> None:
        downstream = STALE_CASCADE["design_criteria"]
        self.assertEqual(len(downstream), 6)
        for mod in ("mass_balance", "flowsheet", "equipment", "opex", "economics", "risks"):
            self.assertIn(mod, downstream)

    def test_mass_balance_cascades_to_four(self) -> None:
        self.assertEqual(STALE_CASCADE["mass_balance"], ["flowsheet", "equipment", "opex", "risks"])

    def test_flowsheet_cascades_to_three(self) -> None:
        self.assertEqual(STALE_CASCADE["flowsheet"], ["equipment", "opex", "risks"])

    def test_equipment_cascades_to_three(self) -> None:
        for mod in ("opex", "economics", "risks"):
            self.assertIn(mod, STALE_CASCADE["equipment"])

    def test_opex_cascades_to_economics_and_risks(self) -> None:
        self.assertEqual(STALE_CASCADE["opex"], ["economics", "risks"])

    def test_economics_cascades_only_to_risks(self) -> None:
        self.assertEqual(STALE_CASCADE["economics"], ["risks"])

    def test_risks_has_no_downstream(self) -> None:
        # "risks" is a leaf module — nothing depends on it.
        self.assertEqual(STALE_CASCADE["risks"], [])

    def test_all_module_keys_present(self) -> None:
        required = {"lims", "blockmodel", "design_criteria", "mass_balance",
                    "flowsheet", "equipment", "simulation", "opex", "economics", "risks"}
        self.assertTrue(required.issubset(set(STALE_CASCADE.keys())))


# =============================================================================
# 2. get_status — DB interaction
# =============================================================================

class TestGetStatus(unittest.TestCase):

    def test_returns_pending_when_no_db_row(self) -> None:
        with patch("backend.routes.pipeline.qone", return_value=None):
            status = get_status("proj-1", "mass_balance")
        self.assertEqual(status, "pending")

    def test_returns_stored_status(self) -> None:
        with patch("backend.routes.pipeline.qone", return_value={"status": "complete"}):
            status = get_status("proj-1", "mass_balance")
        self.assertEqual(status, "complete")

    def test_returns_stale_correctly(self) -> None:
        with patch("backend.routes.pipeline.qone", return_value={"status": "stale"}):
            status = get_status("proj-1", "lims")
        self.assertEqual(status, "stale")

    def test_null_db_status_falls_back_to_pending(self) -> None:
        with patch("backend.routes.pipeline.qone", return_value={"status": None}):
            status = get_status("proj-1", "opex")
        self.assertEqual(status, "pending")

    def test_queries_correct_project_and_module(self) -> None:
        with patch("backend.routes.pipeline.qone", return_value=None) as mock_qone:
            get_status("proj-abc", "flowsheet")
        args = mock_qone.call_args[0]
        self.assertIn("proj-abc", args[1])
        self.assertIn("flowsheet", args[1])


# =============================================================================
# 3. mark_stale_cascade — core state transition logic
# =============================================================================

class TestMarkStaleCascade(unittest.TestCase):
    """mark_stale_cascade must only transition complete/error → stale."""

    # ── Complete modules get marked stale ─────────────────────────────────────

    def test_lims_cascade_marks_complete_downstream_stale(self) -> None:
        statuses = {mod: "complete" for mod in STALE_CASCADE["lims"]}

        def _get_status_side_effect(pid, mod):
            return statuses.get(mod, "pending")

        with (
            patch("backend.routes.pipeline.get_status", side_effect=_get_status_side_effect),
            patch("backend.routes.pipeline.set_status") as mock_set,
        ):
            marked = mark_stale_cascade("proj-1", "lims")
        self.assertEqual(len(marked), 7)
        stale_calls = [c for c in mock_set.call_args_list
                       if c[0][2] == "stale"]
        self.assertEqual(len(stale_calls), 7)

    def test_error_modules_also_marked_stale(self) -> None:
        statuses = {mod: "error" for mod in STALE_CASCADE["lims"]}

        def _eff(pid, mod):
            return statuses.get(mod, "pending")

        with (
            patch("backend.routes.pipeline.get_status", side_effect=_eff),
            patch("backend.routes.pipeline.set_status") as mock_set,
        ):
            marked = mark_stale_cascade("proj-1", "lims")
        self.assertEqual(len(marked), 7)

    # ── Pending modules are left untouched ────────────────────────────────────

    def test_pending_modules_not_marked_stale(self) -> None:
        with (
            patch("backend.routes.pipeline.get_status", return_value="pending"),
            patch("backend.routes.pipeline.set_status") as mock_set,
        ):
            marked = mark_stale_cascade("proj-1", "lims")
        self.assertEqual(marked, [])
        mock_set.assert_not_called()

    def test_already_stale_modules_not_touched(self) -> None:
        with (
            patch("backend.routes.pipeline.get_status", return_value="stale"),
            patch("backend.routes.pipeline.set_status") as mock_set,
        ):
            marked = mark_stale_cascade("proj-1", "lims")
        self.assertEqual(marked, [])
        mock_set.assert_not_called()

    # ── Per-source-module cascade scope ───────────────────────────────────────

    def test_design_criteria_cascade_scope(self) -> None:
        with (
            patch("backend.routes.pipeline.get_status", return_value="complete"),
            patch("backend.routes.pipeline.set_status"),
        ):
            marked = mark_stale_cascade("proj-1", "design_criteria")
        expected = set(STALE_CASCADE["design_criteria"])
        self.assertEqual(set(marked), expected)

    def test_mass_balance_cascade_scope(self) -> None:
        with (
            patch("backend.routes.pipeline.get_status", return_value="complete"),
            patch("backend.routes.pipeline.set_status"),
        ):
            marked = mark_stale_cascade("proj-1", "mass_balance")
        self.assertEqual(set(marked), {"flowsheet", "equipment", "opex", "risks"})

    def test_opex_cascade_scope(self) -> None:
        with (
            patch("backend.routes.pipeline.get_status", return_value="complete"),
            patch("backend.routes.pipeline.set_status"),
        ):
            marked = mark_stale_cascade("proj-1", "opex")
        self.assertEqual(marked, ["economics", "risks"])

    def test_economics_cascade_marks_only_risks(self) -> None:
        with (
            patch("backend.routes.pipeline.get_status", return_value="complete"),
            patch("backend.routes.pipeline.set_status"),
        ):
            marked = mark_stale_cascade("proj-1", "economics")
        self.assertEqual(marked, ["risks"])

    def test_risks_cascade_returns_empty(self) -> None:
        # "risks" is a leaf — its cascade marks nothing.
        with (
            patch("backend.routes.pipeline.get_status", return_value="complete"),
            patch("backend.routes.pipeline.set_status"),
        ):
            marked = mark_stale_cascade("proj-1", "risks")
        self.assertEqual(marked, [])

    # ── set_status called with correct args ───────────────────────────────────

    def test_set_status_called_with_stale_and_triggered_by(self) -> None:
        with (
            patch("backend.routes.pipeline.get_status", return_value="complete"),
            patch("backend.routes.pipeline.set_status") as mock_set,
        ):
            mark_stale_cascade("proj-1", "opex", user_id="u-1")
        # "opex" cascades to both "economics" and "risks" — both must be set stale
        # with the same triggered_by="opex".
        self.assertEqual(mock_set.call_count, 2)
        mock_set.assert_any_call(
            "proj-1", "economics", "stale",
            user_id="u-1", triggered_by="opex",
        )
        mock_set.assert_any_call(
            "proj-1", "risks", "stale",
            user_id="u-1", triggered_by="opex",
        )

    # ── Mixed statuses ────────────────────────────────────────────────────────

    def test_mixed_statuses_only_complete_and_error_marked(self) -> None:
        """3 complete, 2 pending, 1 stale, 1 error among LIMS 7 downstream."""
        downstream = list(STALE_CASCADE["lims"])
        status_map = {
            downstream[0]: "complete",
            downstream[1]: "complete",
            downstream[2]: "complete",
            downstream[3]: "pending",
            downstream[4]: "pending",
            downstream[5]: "stale",
            downstream[6]: "error",
        }

        def _eff(pid, mod):
            return status_map.get(mod, "pending")

        with (
            patch("backend.routes.pipeline.get_status", side_effect=_eff),
            patch("backend.routes.pipeline.set_status") as mock_set,
        ):
            marked = mark_stale_cascade("proj-1", "lims")
        self.assertEqual(len(marked), 4)  # 3 complete + 1 error


# =============================================================================
# 4. _signal_lims_change — LIMS write → pipeline integration
# =============================================================================

class TestSignalLimsChange(unittest.TestCase):
    """LIMS signal must call set_status(complete) then mark_stale_cascade."""

    def test_signal_calls_set_status_complete(self) -> None:
        with (
            patch("backend.routes.pipeline.set_status") as mock_set,
            patch("backend.routes.pipeline.mark_stale_cascade"),
        ):
            _signal_lims_change("proj-1", user_id="u-1")
        mock_set.assert_called_once_with(
            "proj-1", "lims", "complete",
            user_id="u-1", triggered_by="lims_write",
        )

    def test_signal_calls_mark_stale_cascade(self) -> None:
        with (
            patch("backend.routes.pipeline.set_status"),
            patch("backend.routes.pipeline.mark_stale_cascade") as mock_cascade,
        ):
            _signal_lims_change("proj-1", user_id="u-1")
        mock_cascade.assert_called_once_with("proj-1", "lims", user_id="u-1")

    def test_signal_without_user_id_still_calls_both(self) -> None:
        with (
            patch("backend.routes.pipeline.set_status") as mock_set,
            patch("backend.routes.pipeline.mark_stale_cascade") as mock_cascade,
        ):
            _signal_lims_change("proj-2")
        mock_set.assert_called_once()
        mock_cascade.assert_called_once()

    # ── DB failure must NOT block LIMS write ──────────────────────────────────

    def test_set_status_failure_does_not_raise(self) -> None:
        with (
            patch("backend.routes.pipeline.set_status", side_effect=RuntimeError("DB down")),
            patch("backend.routes.pipeline.mark_stale_cascade"),
        ):
            try:
                _signal_lims_change("proj-1", user_id="u-1")
            except Exception:
                self.fail("_signal_lims_change must swallow DB errors")

    def test_mark_stale_failure_does_not_raise(self) -> None:
        with (
            patch("backend.routes.pipeline.set_status"),
            patch("backend.routes.pipeline.mark_stale_cascade",
                  side_effect=RuntimeError("cascade failed")),
        ):
            try:
                _signal_lims_change("proj-1", user_id="u-1")
            except Exception:
                self.fail("_signal_lims_change must swallow cascade errors")

    def test_both_failures_do_not_raise(self) -> None:
        with (
            patch("backend.routes.pipeline.set_status",
                  side_effect=RuntimeError("DB down")),
            patch("backend.routes.pipeline.mark_stale_cascade",
                  side_effect=RuntimeError("DB down")),
        ):
            try:
                _signal_lims_change("proj-1")
            except Exception:
                self.fail("_signal_lims_change must always be safe to call")


# =============================================================================
# 5. End-to-end state flow: lims → everything stale
# =============================================================================

class TestEndToEndLimsToPipelineState(unittest.TestCase):
    """Full state machine: all modules complete → LIMS write → all stale."""

    def test_all_complete_modules_become_stale_after_lims_write(self) -> None:
        all_modules = list(STALE_CASCADE["lims"])
        marked_stale = []

        def _get_status(pid, mod):
            return "complete" if mod in all_modules else "pending"

        def _set_status(pid, mod, status, **kwargs):
            if status == "stale":
                marked_stale.append(mod)

        with (
            patch("backend.routes.pipeline.get_status", side_effect=_get_status),
            patch("backend.routes.pipeline.set_status", side_effect=_set_status),
        ):
            result = mark_stale_cascade("proj-e2e", "lims")

        self.assertEqual(set(result), set(all_modules))
        self.assertEqual(set(marked_stale), set(all_modules))

    def test_partial_completion_correct_subset_stale(self) -> None:
        """Only mass_balance and flowsheet are complete; only those become stale."""
        complete_modules = {"mass_balance", "flowsheet"}

        def _get_status(pid, mod):
            return "complete" if mod in complete_modules else "pending"

        marked = []

        def _set_status(pid, mod, status, **kwargs):
            if status == "stale":
                marked.append(mod)

        with (
            patch("backend.routes.pipeline.get_status", side_effect=_get_status),
            patch("backend.routes.pipeline.set_status", side_effect=_set_status),
        ):
            mark_stale_cascade("proj-e2e", "lims")

        self.assertEqual(set(marked), complete_modules)


if __name__ == "__main__":
    unittest.main()
