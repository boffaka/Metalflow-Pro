from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.routes.auth import _check_login_rate, _reset_login_rate
from backend.security import validate_password_strength, audit_log
from backend.security_store import MemorySecurityStore, build_security_store
from backend import auth
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials


class SecurityTests(unittest.TestCase):
    def tearDown(self) -> None:
        pass

    def test_password_policy_accepts_strong_password(self) -> None:
        self.assertEqual(validate_password_strength("StrongPass1"), "StrongPass1")

    def test_password_policy_rejects_missing_uppercase(self) -> None:
        with self.assertRaises(ValueError):
            validate_password_strength("weakpass1")

    def test_password_policy_rejects_missing_digit(self) -> None:
        with self.assertRaises(ValueError):
            validate_password_strength("Weakpass")

    def test_login_rate_limit_is_scoped_by_ip_and_email(self) -> None:
        store = MemorySecurityStore()
        with patch("backend.routes.auth.SECURITY_STORE", store):
            for _ in range(5):
                _check_login_rate("127.0.0.1", "user@example.com")
            with self.assertRaises(Exception):
                _check_login_rate("127.0.0.1", "user@example.com")
            _check_login_rate("127.0.0.1", "other@example.com")

    def test_login_rate_reset_clears_bucket(self) -> None:
        store = MemorySecurityStore()
        with patch("backend.routes.auth.SECURITY_STORE", store):
            _check_login_rate("127.0.0.1", "user@example.com")
            _reset_login_rate("127.0.0.1", "user@example.com")
            self.assertEqual(store.snapshot(), {})

    def test_audit_log_writes_expected_record(self) -> None:
        with patch("backend.security.execute") as execute_mock:
            audit_log(
                action="admin.delete_user",
                entity_type="user",
                entity_id="user-1",
                user_id="admin-1",
                old_value={"email": "old@example.com"},
            )
        execute_mock.assert_called_once()

    def test_make_token_embeds_token_version(self) -> None:
        with patch("backend.auth.qone", return_value={"token_version": 3}):
            token = auth.make_token("user-1", "Project Manager")
        payload = auth.jwt.decode(token, auth.JWT_SECRET, algorithms=["HS256"])
        self.assertEqual(payload["ver"], 3)
        self.assertIn("jti", payload)

    def test_current_user_rejects_revoked_token_version(self) -> None:
        token = auth.jwt.encode(
            {"sub": "user-1", "role": "Project Manager", "ver": 0},
            auth.JWT_SECRET,
            algorithm="HS256",
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with patch("backend.auth.qone", return_value={"id": "user-1", "email": "u@example.com", "role": "Project Manager", "full_name": None, "token_version": 1}):
            with self.assertRaises(Exception):
                auth.resolve_current_user(None, creds)

    def test_current_user_accepts_matching_token_version(self) -> None:
        token = auth.jwt.encode(
            {"sub": "user-1", "role": "Project Manager", "ver": 2},
            auth.JWT_SECRET,
            algorithm="HS256",
        )
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
        with patch("backend.auth.qone", return_value={"id": "user-1", "email": "u@example.com", "role": "Project Manager", "full_name": None, "token_version": 2}):
            user = auth.resolve_current_user(None, creds)
        self.assertEqual(user["id"], "user-1")

    def test_create_refresh_session_returns_token_and_id(self) -> None:
        with patch("backend.auth.execute", return_value={"id": "session-1"}):
            token, session_id = auth.create_refresh_session("user-1")
        self.assertTrue(token)
        self.assertEqual(session_id, "session-1")

    def test_get_refresh_session_hashes_token(self) -> None:
        with patch("backend.auth.qone", return_value={"id": "session-1"}) as qone_mock:
            row = auth.get_refresh_session("refresh-token")
        self.assertEqual(row["id"], "session-1")
        qone_mock.assert_called_once()

    def test_rotate_refresh_session_rejects_unknown_token(self) -> None:
        with patch("backend.auth.qone", return_value=None):
            with self.assertRaises(HTTPException):
                auth.rotate_refresh_session("bad-token", "user-1")

    def test_rotate_refresh_session_rotates_when_valid(self) -> None:
        with patch("backend.auth.qone", return_value={"id": "session-1"}), patch("backend.auth.create_refresh_session", return_value=("new-refresh", "session-2")), patch("backend.auth.execute") as execute_mock:
            token, session_id = auth.rotate_refresh_session("old-refresh", "user-1")
        self.assertEqual(token, "new-refresh")
        self.assertEqual(session_id, "session-2")
        execute_mock.assert_called_once()

    def test_build_security_store_returns_memory_store(self) -> None:
        store = build_security_store("memory", None)
        self.assertIsInstance(store, MemorySecurityStore)

    def test_build_security_store_rejects_unknown_backend(self) -> None:
        with self.assertRaises(ValueError):
            build_security_store("unknown", None)


if __name__ == "__main__":
    unittest.main()


def test_bleach_strips_script_tags():
    """Sanitization must strip <script> and event handlers from HTML content."""
    try:
        from routes.ni43101 import sanitize_html
    except ImportError:
        from backend.routes.ni43101 import sanitize_html
    dirty = '<p>Hello</p><script>alert("xss")</script><p onclick="evil()">click</p>'
    clean = sanitize_html(dirty)
    assert "<script>" not in clean
    assert "onclick" not in clean
    assert "Hello" in clean
