"""Tests for LIMS data validation rules."""
from validation_rules import validate_lims_record, RULES


def test_au_grade_within_bounds():
    """Valid Au grade passes."""
    flags = validate_lims_record("a1", {"au_g_t": 5.2}, project_id="p1")
    assert len(flags) == 0


def test_au_grade_negative():
    """Negative Au grade is flagged."""
    flags = validate_lims_record("a1", {"au_g_t": -1.0}, project_id="p1")
    assert len(flags) == 1
    assert flags[0]["rule_code"] == "BOUNDS_au_g_t"
    assert flags[0]["severity"] == "error"


def test_au_grade_over_max():
    """Extreme Au grade is flagged."""
    flags = validate_lims_record("a1", {"au_g_t": 99999.0}, project_id="p1")
    assert len(flags) == 1
    assert flags[0]["rule_code"] == "BOUNDS_au_g_t"


def test_recovery_over_100():
    """Recovery > 100% is flagged."""
    flags = validate_lims_record("d1", {"au_recovery_pct": 105.0}, project_id="p1")
    assert len(flags) == 1
    assert flags[0]["rule_code"] == "BOUNDS_au_recovery_pct"


def test_recovery_valid():
    """Valid recovery passes."""
    flags = validate_lims_record("d1", {"au_recovery_pct": 88.5}, project_id="p1")
    assert len(flags) == 0


def test_unknown_table_no_rules():
    """Unknown table type returns empty flags."""
    flags = validate_lims_record("unknown", {"foo": 1}, project_id="p1")
    assert flags == []


def test_none_value_skipped():
    """None values are silently skipped."""
    flags = validate_lims_record("a1", {"au_g_t": None}, project_id="p1")
    assert flags == []


def test_multiple_fields_multiple_flags():
    """Multiple bad fields produce multiple flags."""
    flags = validate_lims_record("a1", {"au_g_t": -1, "cu_pct": 150}, project_id="p1")
    assert len(flags) == 2


def test_rules_registry_has_entries():
    """Rules dict is not empty."""
    assert len(RULES) > 0
    assert "a1" in RULES
    assert "d1" in RULES
