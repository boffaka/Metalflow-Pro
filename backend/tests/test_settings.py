from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.settings import get_settings, reset_settings_cache


class SettingsTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_settings_cache()

    def test_defaults_are_valid(self) -> None:
        # ADMIN_PASSWORD is mandatory; JWT_SECRET may be generated in dev.
        with patch.dict(os.environ, {"ADMIN_PASSWORD": "Str0ngPass!"}, clear=True):
            reset_settings_cache()
            settings = get_settings()
        self.assertTrue(settings.database_url.startswith("postgresql://"))
        self.assertTrue(settings.jwt_secret)
        self.assertEqual(settings.log_level, "INFO")

    def test_conflicting_schema_modes_are_rejected(self) -> None:
        with patch.dict(os.environ, {"AUTO_MIGRATE": "1", "BOOTSTRAP_SCHEMA": "1", "JWT_SECRET": "a" * 32, "ADMIN_PASSWORD": "12345678"}, clear=True):
            reset_settings_cache()
            with self.assertRaises(ValueError):
                get_settings()

    def test_short_jwt_secret_is_rejected(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET": "short", "ADMIN_PASSWORD": "12345678"}, clear=True):
            reset_settings_cache()
            with self.assertRaises(ValueError):
                get_settings()

    def test_short_admin_password_is_rejected(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET": "a" * 32, "ADMIN_PASSWORD": "short"}, clear=True):
            reset_settings_cache()
            with self.assertRaises(ValueError):
                get_settings()

    def test_redis_backend_requires_redis_url(self) -> None:
        with patch.dict(os.environ, {"JWT_SECRET": "a" * 32, "ADMIN_PASSWORD": "StrongPass1", "SECURITY_STORE_BACKEND": "redis"}, clear=True):
            reset_settings_cache()
            with self.assertRaises(ValueError):
                get_settings()


def test_short_jwt_secret_logs_warning(caplog):
    """JWT_SECRET shorter than 32 chars must emit a WARNING log."""
    import logging

    # 24 characters: satisfies minimum 16 but triggers sub-32 warning in validate().
    with patch.dict(
        os.environ,
        {"JWT_SECRET": "short_but_valid_12345678", "ADMIN_PASSWORD": "Str0ngPass!"},
        clear=True,
    ):
        reset_settings_cache()
        with caplog.at_level(logging.WARNING, logger="mpdpms.settings"):
            get_settings()
    assert any("JWT_SECRET" in r.message and "32" in r.message for r in caplog.records)
    reset_settings_cache()
