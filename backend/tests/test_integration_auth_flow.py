"""
Integration tests — Authentication & Authorization flow.

Critical interaction chain:
  HTTP request → JWT decode → DB user-lookup → token-version check
  → project_user dependency → ensure_project_access

Each test exercises the **full chain** between at least two modules
(auth.py ↔ db.py, auth.py ↔ routes, routes ↔ FastAPI dependency system).

Scenarios:
  Nominal  — valid token, user in DB, correct version, correct role
  State    — token version mismatch after revocation, cookie fallback
  Failure  — DB down, user deleted, expired/garbage token, missing creds,
             non-PM accessing another user's project
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch, MagicMock
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword1!")

try:
    from backend import auth as auth_mod
    from backend.main import app
except ImportError:
    import auth as auth_mod
    from main import app

from fastapi.testclient import TestClient


# ─── Test fixtures ────────────────────────────────────────────────────────────

_PM_ROW = {
    "id": "pm-uuid-1",
    "email": "pm@kokoya.test",
    "role": "Project Manager",
    "full_name": "PM User",
    "token_version": 2,
}
_MET_ROW = {
    "id": "met-uuid-1",
    "email": "met@kokoya.test",
    "role": "Metallurgist",
    "full_name": "Met User",
    "token_version": 1,
}

def _make_creds(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

def _make_token(user_id: str, role: str, version: int) -> str:
    return auth_mod.jwt.encode(
        {"sub": user_id, "role": role, "ver": version},
        auth_mod.JWT_SECRET,
        algorithm="HS256",
    )


# =============================================================================
# 1. resolve_current_user — full chain: JWT decode → DB lookup → version check
# =============================================================================

class TestResolveCurrentUser(unittest.TestCase):
    """Tests the complete auth.py ↔ db.py integration."""

    # ── Nominal ───────────────────────────────────────────────────────────────

    def test_valid_token_matching_version_returns_user(self) -> None:
        token = _make_token("pm-uuid-1", "Project Manager", 2)
        creds = _make_creds(token)
        with patch("backend.auth.qone", return_value=_PM_ROW):
            user = auth_mod.resolve_current_user(None, creds)
        self.assertEqual(user["id"], "pm-uuid-1")
        self.assertEqual(user["role"], "Project Manager")

    def test_user_id_coerced_to_string_in_return(self) -> None:
        token = _make_token("pm-uuid-1", "Project Manager", 2)
        creds = _make_creds(token)
        with patch("backend.auth.qone", return_value={**_PM_ROW, "id": "pm-uuid-1"}):
            user = auth_mod.resolve_current_user(None, creds)
        self.assertIsInstance(user["id"], str)

    # ── Token version mismatch → 401 (revoked session) ────────────────────────

    def test_mismatched_token_version_raises_401(self) -> None:
        token = _make_token("pm-uuid-1", "Project Manager", version=0)  # DB has ver=2
        creds = _make_creds(token)
        with patch("backend.auth.qone", return_value=_PM_ROW):
            with self.assertRaises(HTTPException) as cm:
                auth_mod.resolve_current_user(None, creds)
        self.assertEqual(cm.exception.status_code, 401)
        self.assertIn("Session", cm.exception.detail)

    def test_exact_version_match_succeeds(self) -> None:
        """Token ver=2, DB token_version=2 → success."""
        token = _make_token("pm-uuid-1", "Project Manager", 2)
        creds = _make_creds(token)
        with patch("backend.auth.qone", return_value={**_PM_ROW, "token_version": 2}):
            user = auth_mod.resolve_current_user(None, creds)
        self.assertIsNotNone(user)

    # ── User not in DB → 401 ──────────────────────────────────────────────────

    def test_user_not_found_in_db_raises_401(self) -> None:
        token = _make_token("ghost-user", "Metallurgist", 1)
        creds = _make_creds(token)
        with patch("backend.auth.qone", return_value=None):
            with self.assertRaises(HTTPException) as cm:
                auth_mod.resolve_current_user(None, creds)
        self.assertEqual(cm.exception.status_code, 401)

    # ── Expired token → 401 ───────────────────────────────────────────────────

    def test_expired_token_raises_401(self) -> None:
        import time
        expired = auth_mod.jwt.encode(
            {"sub": "pm-uuid-1", "role": "Project Manager", "ver": 2, "exp": int(time.time()) - 3600},
            auth_mod.JWT_SECRET,
            algorithm="HS256",
        )
        creds = _make_creds(expired)
        with self.assertRaises(HTTPException) as cm:
            auth_mod.resolve_current_user(None, creds)
        self.assertEqual(cm.exception.status_code, 401)

    # ── Garbage token → 401 ───────────────────────────────────────────────────

    def test_garbage_token_raises_401(self) -> None:
        creds = _make_creds("not.a.jwt")
        with self.assertRaises(HTTPException) as cm:
            auth_mod.resolve_current_user(None, creds)
        self.assertEqual(cm.exception.status_code, 401)

    def test_empty_token_raises_401(self) -> None:
        creds = _make_creds("")
        with self.assertRaises(HTTPException) as cm:
            auth_mod.resolve_current_user(None, creds)
        self.assertEqual(cm.exception.status_code, 401)

    # ── Missing credentials → 401 ─────────────────────────────────────────────

    def test_no_credentials_no_cookie_raises_401(self) -> None:
        mock_request = MagicMock()
        mock_request.cookies = {}
        with self.assertRaises(HTTPException) as cm:
            auth_mod.resolve_current_user(mock_request, None)
        self.assertEqual(cm.exception.status_code, 401)

    # ── Cookie fallback ───────────────────────────────────────────────────────

    def test_cookie_token_accepted_when_no_header(self) -> None:
        token = _make_token("pm-uuid-1", "Project Manager", 2)
        mock_request = MagicMock()
        mock_request.cookies = {"access_token": token}
        with patch("backend.auth.qone", return_value=_PM_ROW):
            user = auth_mod.resolve_current_user(mock_request, None)
        self.assertEqual(user["id"], "pm-uuid-1")

    # ── DB failure ────────────────────────────────────────────────────────────

    def test_db_error_during_user_lookup_propagates(self) -> None:
        token = _make_token("pm-uuid-1", "Project Manager", 2)
        creds = _make_creds(token)
        with patch("backend.auth.qone", side_effect=RuntimeError("DB down")):
            with self.assertRaises(Exception):
                auth_mod.resolve_current_user(None, creds)


# =============================================================================
# 2. ensure_project_access — auth.py ↔ db.py integration
# =============================================================================

class TestEnsureProjectAccess(unittest.TestCase):
    """Project Manager sees all; other roles see only their own projects."""

    def test_pm_can_access_any_project(self) -> None:
        with patch("backend.auth.qone", return_value={"id": "proj-1"}):
            auth_mod.ensure_project_access("proj-1", _PM_ROW)
        # No exception raised

    def test_pm_with_nonexistent_project_raises_404(self) -> None:
        with patch("backend.auth.qone", return_value=None):
            with self.assertRaises(HTTPException) as cm:
                auth_mod.ensure_project_access("ghost-proj", _PM_ROW)
        self.assertEqual(cm.exception.status_code, 404)

    def test_metallurgist_can_access_own_project(self) -> None:
        with patch("backend.auth.qone", return_value={"id": "proj-2"}):
            auth_mod.ensure_project_access("proj-2", _MET_ROW)

    def test_metallurgist_cannot_access_other_project_raises_404(self) -> None:
        with patch("backend.auth.qone", return_value=None):
            with self.assertRaises(HTTPException) as cm:
                auth_mod.ensure_project_access("proj-other", _MET_ROW)
        self.assertEqual(cm.exception.status_code, 404)

    def test_pm_query_does_not_filter_by_user_id(self) -> None:
        """PM check must NOT include user_id filter in SQL."""
        with patch("backend.auth.qone", return_value={"id": "p"}) as mock_qone:
            auth_mod.ensure_project_access("p", _PM_ROW)
        sql = mock_qone.call_args[0][0]
        self.assertNotIn("user_id", sql)

    def test_non_pm_query_filters_by_user_id(self) -> None:
        """Non-PM must filter by user_id to prevent enumeration."""
        with patch("backend.auth.qone", return_value={"id": "p"}) as mock_qone:
            auth_mod.ensure_project_access("p", _MET_ROW)
        sql = mock_qone.call_args[0][0]
        self.assertIn("user_id", sql)


# =============================================================================
# 3. HTTP-level: missing / invalid token on protected endpoints
# =============================================================================

class TestHttpAuthProtection(unittest.TestCase):
    """FastAPI middleware correctly rejects unauthenticated requests."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app, raise_server_exceptions=False)

    def test_missing_auth_header_returns_401_or_403(self) -> None:
        r = self.client.get("/api/v1/projects")
        self.assertIn(r.status_code, (401, 403, 422))

    def test_garbage_bearer_token_returns_401(self) -> None:
        r = self.client.get(
            "/api/v1/projects",
            headers={"Authorization": "Bearer garbage.token.here"},
        )
        self.assertIn(r.status_code, (401, 403))

    def test_valid_auth_without_db_returns_non_2xx(self) -> None:
        """With real DB unavailable a valid token still fails (no user row)."""
        token = _make_token("any-user", "Project Manager", 0)
        with patch("backend.auth.qone", return_value=None):
            r = self.client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.assertIn(r.status_code, (401, 404, 500))


# =============================================================================
# 4. State consistency after token version bump
# =============================================================================

class TestTokenVersionRevocationFlow(unittest.TestCase):
    """bump_token_version invalidates old tokens immediately."""

    def test_old_token_rejected_after_version_bump(self) -> None:
        old_token = _make_token("pm-uuid-1", "Project Manager", version=1)
        creds = _make_creds(old_token)
        # DB now shows version=2 after bump
        updated_row = {**_PM_ROW, "token_version": 2}
        with patch("backend.auth.qone", return_value=updated_row):
            with self.assertRaises(HTTPException) as cm:
                auth_mod.resolve_current_user(None, creds)
        self.assertEqual(cm.exception.status_code, 401)

    def test_new_token_accepted_after_version_bump(self) -> None:
        new_token = _make_token("pm-uuid-1", "Project Manager", version=2)
        creds = _make_creds(new_token)
        with patch("backend.auth.qone", return_value={**_PM_ROW, "token_version": 2}):
            user = auth_mod.resolve_current_user(None, creds)
        self.assertEqual(user["id"], "pm-uuid-1")

    def test_bump_token_version_calls_execute(self) -> None:
        with patch("backend.auth.execute", return_value={"token_version": 3}) as mock_exec:
            result = auth_mod.bump_token_version("pm-uuid-1")
        self.assertEqual(result, 3)
        mock_exec.assert_called_once()

    def test_bump_token_version_user_not_found_raises_404(self) -> None:
        with patch("backend.auth.execute", return_value=None):
            with self.assertRaises(HTTPException) as cm:
                auth_mod.bump_token_version("ghost")
        self.assertEqual(cm.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
