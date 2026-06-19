"""Tests for cascade staleness tracking."""
import pytest


def test_mark_stale_creates_row(client, admin_headers, test_project_id):
    """mark_stale should create a staleness row for a module."""
    from db import mark_stale, get_staleness

    mark_stale(test_project_id, ["mass_balance"], "Test reason")
    result = get_staleness(test_project_id)
    assert result["mass_balance"]["is_stale"] is True
    assert result["mass_balance"]["reason"] == "Test reason"
    assert result["flowsheet"]["is_stale"] is False


def test_clear_stale_resets_row(client, admin_headers, test_project_id):
    """clear_stale should reset a module's staleness."""
    from db import mark_stale, clear_stale, get_staleness

    mark_stale(test_project_id, ["flowsheet"], "Going stale")
    clear_stale(test_project_id, "flowsheet")
    result = get_staleness(test_project_id)
    assert result["flowsheet"]["is_stale"] is False


def test_mark_stale_updates_existing_row(client, admin_headers, test_project_id):
    """mark_stale called twice should update the reason."""
    from db import mark_stale, get_staleness

    mark_stale(test_project_id, ["costs"], "First reason")
    mark_stale(test_project_id, ["costs"], "Second reason")
    result = get_staleness(test_project_id)
    assert result["costs"]["reason"] == "Second reason"


def test_clear_stale_on_missing_row(client, admin_headers, test_project_id):
    """clear_stale on a module with no row should not raise."""
    from db import clear_stale, get_staleness

    clear_stale(test_project_id, "costs")
    result = get_staleness(test_project_id)
    assert result["costs"]["is_stale"] is False


def test_staleness_api_endpoint(client, admin_headers, test_project_id):
    """GET /staleness should return staleness for all modules."""
    from db import mark_stale

    mark_stale(test_project_id, ["mass_balance", "flowsheet"], "DC changed")
    resp = client.get(
        f"/api/v1/projects/{test_project_id}/staleness",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["mass_balance"]["is_stale"] is True
    assert data["flowsheet"]["is_stale"] is True
    assert data["costs"]["is_stale"] is False
