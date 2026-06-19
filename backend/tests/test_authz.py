"""Unit tests for project-access decision logic — Lot C Phase 1 (no DB)."""

import pytest

pytestmark = pytest.mark.no_db


def get_fn():
    try:
        from backend.authz import project_access_allowed
    except ImportError:
        from authz import project_access_allowed
    return project_access_allowed


def test_nonexistent_project_denied_even_for_pm():
    fn = get_fn()
    assert fn(role="Project Manager", project_exists=False, is_member=False) is False


def test_project_manager_sees_any_existing_project():
    fn = get_fn()
    assert fn(role="Project Manager", project_exists=True, is_member=False) is True


def test_member_is_allowed():
    fn = get_fn()
    assert fn(role="Metallurgist", project_exists=True, is_member=True) is True


def test_non_member_non_pm_denied():
    fn = get_fn()
    assert fn(role="Metallurgist", project_exists=True, is_member=False) is False


def test_readonly_member_allowed():
    fn = get_fn()
    assert fn(role="Read-only", project_exists=True, is_member=True) is True
