"""Tests for the immutable audit trail module."""
from audit import _compute_checksum, build_audit_event


def test_checksum_deterministic():
    """Same inputs produce same checksum."""
    event = {"user_id": "u1", "entity_type": "lims_sample", "action": "create", "new_value": {"au_g_t": 5.2}}
    prev = "0000"
    c1 = _compute_checksum(event, prev)
    c2 = _compute_checksum(event, prev)
    assert c1 == c2
    assert len(c1) == 64


def test_checksum_chain_integrity():
    """Changing previous checksum changes current."""
    event = {"user_id": "u1", "entity_type": "lims_sample", "action": "create"}
    c1 = _compute_checksum(event, "aaa")
    c2 = _compute_checksum(event, "bbb")
    assert c1 != c2


def test_build_audit_event_has_required_fields():
    """build_audit_event returns a dict with all required fields."""
    evt = build_audit_event(
        user_id="u1",
        project_id="p1",
        entity_type="lims_sample",
        entity_id="e1",
        action="create",
        new_value={"au_g_t": 5.2},
        source="web",
        ip_address="127.0.0.1",
        previous_checksum="0" * 64,
    )
    assert evt["user_id"] == "u1"
    assert evt["action"] == "create"
    assert evt["source"] == "web"
    assert "checksum" in evt
    assert len(evt["checksum"]) == 64


def test_build_event_with_old_and_new_value():
    """build_audit_event includes old/new values."""
    evt = build_audit_event(
        user_id="u1",
        project_id="p1",
        entity_type="design_criteria",
        entity_id="dc1",
        action="update",
        field_name="target_tph",
        old_value=100.0,
        new_value=150.0,
        previous_checksum="0" * 64,
    )
    assert evt["old_value"] == 100.0
    assert evt["new_value"] == 150.0
    assert evt["field_name"] == "target_tph"
