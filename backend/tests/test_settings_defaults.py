"""Tests for new settings defaults."""
import os


def _make_settings():
    """Create settings with minimal required env vars."""
    os.environ.setdefault("DATABASE_URL", "sqlite:///test.db")
    os.environ.setdefault("ADMIN_EMAIL", "test@example.com")
    os.environ.setdefault("ADMIN_PASSWORD", "TestPass123!")
    os.environ.setdefault("JWT_SECRET", "test_secret_that_is_definitely_long_enough_32chars")
    from settings import get_settings, reset_settings_cache
    reset_settings_cache()
    return get_settings()


def test_settings_has_grid_co2():
    s = _make_settings()
    assert hasattr(s, "grid_co2_kg_kwh")
    assert s.grid_co2_kg_kwh == 0.50


def test_settings_has_wgc_benchmark():
    s = _make_settings()
    assert hasattr(s, "wgc_co2_benchmark")
    assert s.wgc_co2_benchmark == 800
