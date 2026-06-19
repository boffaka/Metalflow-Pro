"""Campaign routes — serialization and validation (no DB)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.routes import campaigns as camp
except ImportError:
    import routes.campaigns as camp


def test_valid_statuses():
    assert camp._VALID_STATUSES == {"planned", "active", "complete", "cancelled"}


def test_valid_test_types_includes_pilot():
    assert "pilot_plant" in camp._VALID_TEST_TYPES


def test_serialize_maps_uuids_and_dates():
    row = {
        "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "project_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "campaign_name": "Phase 1 Flotation",
        "name": None,
        "created_at": "2026-01-15T10:00:00+00:00",
        "status": "active",
    }
    out = camp._serialize(row)
    assert out["id"] == str(row["id"])
    assert out["name"] == "Phase 1 Flotation"
    assert out["created_at"] == str(row["created_at"])


def test_serialize_preserves_existing_name():
    row = {"id": "1", "name": "Custom", "campaign_name": "Legacy"}
    out = camp._serialize(row)
    assert out["name"] == "Custom"
