"""Unit tests for the equipment lifecycle helper.

Scope: verify that mutations to equipment items (PATCH, POST, DELETE,
auto-generate, purge) trigger the existing `mark_stale_cascade("equipment", ...)`
pipeline so downstream modules (opex, economics, risks per `STALE_CASCADE`) are
flagged as stale.

Why: NI 43-101 §5.5 conformance — derived computations must auto-flag when
their sources change. The cascade infrastructure already exists in
`routes.pipeline` for design_criteria / lims / mass_balance / flowsheet, but
equipment_v2 endpoints currently bypass it.

These tests run without a live database — they mock the cascade helper at
the module boundary. The module-level `pytestmark = pytest.mark.no_db` opts
out of the global skip applied by `conftest.py` when TEST_DATABASE_URL is
unset, so these tests execute in the default dev environment as well as in
CI.

NOTE: this is a coarse wiring check. For behavioural coverage of the full
cascade (PATCH equipment → module_generation_status updated → audit_events
row written), see the planned `test_integration_equipment_cascade_e2e`
suite tracked in `docs/operations/staleness-policy.md` (section "Itérations
prévues").
"""
from __future__ import annotations

import os

# Ensure the staleness module imports cleanly even outside the integration
# test environment (settings refuses to load without DATABASE_URL/JWT_SECRET).
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production-32chars-min")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.dev")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword123!")

from unittest.mock import patch  # noqa: E402

import pytest  # noqa: E402

# Opt out of the global TEST_DATABASE_URL skip — these are pure unit tests
# (all DB and audit boundaries are mocked).
pytestmark = pytest.mark.no_db


PROJECT_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"


def test_trigger_equipment_cascade_invokes_mark_stale_cascade():
    """The helper must delegate to routes.pipeline.mark_stale_cascade with
    the canonical source_module='equipment' so downstream modules are flagged."""
    with patch("routes.pipeline.mark_stale_cascade") as mock_cascade, \
         patch("audit.record_event"):
        mock_cascade.return_value = ["opex", "economics", "risks"]

        from services.equipment_lifecycle import trigger_equipment_cascade
        cascaded = trigger_equipment_cascade(
            project_id=PROJECT_ID,
            user_id=USER_ID,
            change_summary="motor_kw_changed",
        )

        mock_cascade.assert_called_once_with(
            PROJECT_ID, "equipment", user_id=USER_ID,
        )
        assert cascaded == ["opex", "economics", "risks"]


def test_trigger_equipment_cascade_records_audit_event_when_modules_marked():
    """A successful cascade must emit one NI 43-101 audit event listing the
    impacted downstream modules so a QP can audit the chain later."""
    with patch("routes.pipeline.mark_stale_cascade") as mock_cascade, \
         patch("audit.record_event") as mock_audit:
        mock_cascade.return_value = ["opex", "economics", "risks"]

        from services.equipment_lifecycle import trigger_equipment_cascade
        trigger_equipment_cascade(
            project_id=PROJECT_ID,
            user_id=USER_ID,
            change_summary="motor_kw_changed",
        )

        assert mock_audit.call_count == 1
        kwargs = mock_audit.call_args.kwargs
        assert kwargs["project_id"] == PROJECT_ID
        assert kwargs["user_id"] == USER_ID
        assert kwargs["entity_type"] == "staleness"
        assert kwargs["action"] == "mark_stale_cascade"
        assert kwargs["field_name"] == "equipment"
        assert kwargs["new_value"]["cascaded_to"] == ["opex", "economics", "risks"]
        assert kwargs["new_value"]["change_summary"] == "motor_kw_changed"


def test_trigger_equipment_cascade_does_not_audit_when_nothing_marked():
    """If the cascade marks nothing (e.g. all downstream modules were already
    stale or don't exist), no audit event should be recorded — avoid noise."""
    with patch("routes.pipeline.mark_stale_cascade") as mock_cascade, \
         patch("audit.record_event") as mock_audit:
        mock_cascade.return_value = []

        from services.equipment_lifecycle import trigger_equipment_cascade
        trigger_equipment_cascade(
            project_id=PROJECT_ID,
            user_id=USER_ID,
            change_summary="cosmetic_only",
        )

        mock_audit.assert_not_called()


def test_trigger_equipment_cascade_swallows_cascade_exceptions():
    """A cascade failure must NEVER block the originating request. The DB
    cascade is a best-effort traceability mechanism; if it fails we log and
    return [] so the API call (PATCH equipment, etc.) still returns 200."""
    with patch("routes.pipeline.mark_stale_cascade") as mock_cascade, \
         patch("audit.record_event"):
        mock_cascade.side_effect = RuntimeError("DB pool exhausted")

        from services.equipment_lifecycle import trigger_equipment_cascade
        cascaded = trigger_equipment_cascade(
            project_id=PROJECT_ID,
            user_id=USER_ID,
            change_summary="x",
        )
        assert cascaded == []


def test_trigger_equipment_cascade_swallows_audit_exceptions():
    """An audit-record failure must not bubble up either."""
    with patch("routes.pipeline.mark_stale_cascade") as mock_cascade, \
         patch("audit.record_event") as mock_audit:
        mock_cascade.return_value = ["opex"]
        mock_audit.side_effect = RuntimeError("audit table missing")

        from services.equipment_lifecycle import trigger_equipment_cascade
        cascaded = trigger_equipment_cascade(
            project_id=PROJECT_ID,
            user_id=USER_ID,
            change_summary="x",
        )
        # The cascade still happened; audit failure is logged but swallowed.
        assert cascaded == ["opex"]


def test_trigger_equipment_cascade_accepts_none_user_id():
    """System-driven changes (auto-generation triggered by a Celery task) may
    have no user_id. The helper must accept None gracefully."""
    with patch("routes.pipeline.mark_stale_cascade") as mock_cascade, \
         patch("audit.record_event"):
        mock_cascade.return_value = ["opex"]

        from services.equipment_lifecycle import trigger_equipment_cascade
        cascaded = trigger_equipment_cascade(
            project_id=PROJECT_ID,
            user_id=None,
            change_summary="auto_generated",
        )
        mock_cascade.assert_called_once_with(
            PROJECT_ID, "equipment", user_id=None,
        )
        assert cascaded == ["opex"]


# ---------------------------------------------------------------------------
# Wiring tests: equipment_v2 endpoints must call the lifecycle helper
# ---------------------------------------------------------------------------

def test_equipment_v2_module_imports_lifecycle_helper():
    """The equipment_v2 router must import trigger_equipment_cascade so the
    PATCH/POST/DELETE/auto-generate handlers can fire the cascade."""
    import routes.equipment_v2 as eq2
    assert hasattr(eq2, "trigger_equipment_cascade"), (
        "routes.equipment_v2 must import trigger_equipment_cascade from "
        "services.equipment_lifecycle"
    )


def test_equipment_v2_write_handlers_invoke_cascade():
    """Coarse wiring check: every write handler must reference
    `trigger_equipment_cascade` in its source.

    This is a defensive substring grep — it will pass on a literal mention
    even inside a comment, and will fail on a renamed-import alias. For
    behavioural coverage of the full cascade (PATCH equipment →
    module_generation_status updated → audit_events row written), see the
    planned `test_integration_equipment_cascade_e2e` suite tracked in
    `docs/operations/staleness-policy.md` (section "Itérations prévues").
    """
    import inspect
    import routes.equipment_v2 as eq2

    write_handlers = [
        eq2.patch_equipment,
        eq2.add_equipment,
        eq2.delete_equipment,
        eq2.purge_all_equipment,
        eq2.auto_generate_mer,
    ]
    for handler in write_handlers:
        src = inspect.getsource(handler)
        assert "trigger_equipment_cascade" in src, (
            f"Wiring missing: handler {handler.__name__} does not reference "
            f"`trigger_equipment_cascade`. NI 43-101 §5.5: equipment mutations "
            f"must propagate stale flags to downstream modules. If you renamed "
            f"the helper or wrapped it, update this assertion accordingly."
        )


def test_equipment_v2_read_handlers_do_not_invoke_cascade():
    """Read endpoints must NOT trigger the cascade — they don't mutate state."""
    import inspect
    import routes.equipment_v2 as eq2

    read_handlers = [
        eq2.get_mer,
        eq2.get_summary,
        eq2.get_long_lead,
        eq2.list_wbs_codes,
    ]
    for handler in read_handlers:
        src = inspect.getsource(handler)
        assert "trigger_equipment_cascade" not in src, (
            f"Handler {handler.__name__} unnecessarily triggers cascade — read endpoint "
            f"should be free of staleness side effects."
        )
