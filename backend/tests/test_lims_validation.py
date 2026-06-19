"""Tests for LIMS data validation rules."""
import os
import pytest

# Set required env vars before importing modules that trigger settings validation
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

try:
    from routes.lims import validate_lims_data
except ImportError:
    from backend.routes.lims import validate_lims_data


def test_reject_negative_gold_grade():
    errors = validate_lims_data("a1", {"au_g_t": -0.5})
    assert len(errors) > 0
    assert any("au_g_t" in e for e in errors)


def test_accept_valid_gold_grade():
    errors = validate_lims_data("a1", {"au_g_t": 2.5})
    assert errors == []


def test_reject_recovery_over_100():
    errors = validate_lims_data("d1", {"au_recovery_pct": 105.0})
    assert len(errors) > 0


def test_reject_recovery_negative():
    errors = validate_lims_data("d1", {"au_recovery_pct": -5.0})
    assert len(errors) > 0


def test_accept_valid_recovery():
    errors = validate_lims_data("d1", {"au_recovery_pct": 92.5})
    assert errors == []


def test_reject_bwi_too_low():
    errors = validate_lims_data("b1", {"bwi_kwh_t": 2.0})
    assert len(errors) > 0


def test_reject_bwi_too_high():
    errors = validate_lims_data("b1", {"bwi_kwh_t": 35.0})
    assert len(errors) > 0


def test_accept_valid_bwi():
    errors = validate_lims_data("b1", {"bwi_kwh_t": 14.5})
    assert errors == []


def test_reject_s_total_over_100():
    errors = validate_lims_data("a1", {"s_total_pct": 110.0})
    assert len(errors) > 0


def test_reject_mass_pull_too_high():
    errors = validate_lims_data("c2", {"mass_pull_pct": 55.0})
    assert len(errors) > 0


def test_reject_nacn_too_high():
    errors = validate_lims_data("d1", {"nacn_consumption_kg_t": 12.0})
    assert len(errors) > 0


def test_unknown_field_ignored():
    errors = validate_lims_data("a1", {"unknown_field": -999})
    assert errors == []
