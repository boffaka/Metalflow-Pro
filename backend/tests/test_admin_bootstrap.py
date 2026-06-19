"""Tests for admin user bootstrap security hardening.

In production (RAILWAY_ENVIRONMENT=production), weak/default passwords must
cause a RuntimeError at startup to prevent insecure deployments.

The password validation happens BEFORE any DB access, so these tests work
without a running database.
"""
import pytest
import os
from unittest.mock import patch, MagicMock


def _mock_db():
    """Return patch context managers that stub out DB calls in seed_admin_user."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value = mock_cursor
    return patch("main.conn", return_value=mock_conn), patch("main.release")


def test_weak_admin_password_blocked_in_production():
    """In production, admin123 password must raise RuntimeError at startup."""
    from main import seed_admin_user
    with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}), \
         patch("main._ADMIN_PASSWORD", "admin123"):
        with pytest.raises(RuntimeError, match="[Pp]assword|[Ii]nsecure"):
            seed_admin_user()


def test_short_admin_password_blocked_in_production():
    """Passwords shorter than 8 chars must be rejected in production."""
    from main import seed_admin_user
    with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}), \
         patch("main._ADMIN_PASSWORD", "short"):
        with pytest.raises(RuntimeError, match="at least 8 characters"):
            seed_admin_user()


def test_strong_password_allowed_in_production():
    """A sufficiently strong password must NOT raise in production."""
    from main import seed_admin_user
    p_conn, p_release = _mock_db()
    with patch.dict(os.environ, {"RAILWAY_ENVIRONMENT": "production"}), \
         patch("main._ADMIN_PASSWORD", "StrongP@ss99!"), \
         p_conn, p_release:
        try:
            seed_admin_user()
        except RuntimeError as exc:
            if "password" in str(exc).lower() or "insecure" in str(exc).lower():
                pytest.fail("Strong password should not trigger RuntimeError in production")


def test_weak_password_allowed_in_dev():
    """In non-production, admin123 should be allowed (just logged as warning)."""
    from main import seed_admin_user
    p_conn, p_release = _mock_db()
    # Ensure RAILWAY_ENVIRONMENT is not set (dev mode)
    env = os.environ.copy()
    env.pop("RAILWAY_ENVIRONMENT", None)
    with patch.dict(os.environ, env, clear=True), \
         patch("main._ADMIN_PASSWORD", "admin123"), \
         p_conn, p_release:
        try:
            seed_admin_user()
        except RuntimeError as exc:
            if "password" in str(exc).lower() or "insecure" in str(exc).lower():
                pytest.fail("admin123 should be allowed in dev/non-production")
