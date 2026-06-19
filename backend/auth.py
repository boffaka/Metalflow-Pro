"""
MPDPMS — Authentication & RBAC middleware.
JWT tokens carry user role. Provides current_user dependency and require_role decorator.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any
import logging
import uuid as uuid_lib
import hashlib
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

try:
    from .db import qone, execute
    from .settings import get_settings
    from .authz import project_access_allowed
except ImportError:  # pragma: no cover - supports direct script imports
    from db import qone, execute
    from settings import get_settings
    from authz import project_access_allowed

import os

logger = logging.getLogger("mpdpms.auth")

# ─── Config ──────────────────────────────────────────────────────────────────
JWT_SECRET = get_settings().jwt_secret
JWT_EXP_H = get_settings().jwt_exp_h
REFRESH_EXP_DAYS = get_settings().refresh_exp_days
if get_settings().jwt_secret_generated:
    logger.warning("JWT_SECRET missing - generated ephemeral secret for current process only")
# Operator guard: warn if the deprecated JWT_EXP_HOURS var is set in env —
# it has no effect (auth.py reads JWT_EXP_H from settings.py instead).
if os.getenv("JWT_EXP_HOURS"):
    logger.warning(
        "Env var JWT_EXP_HOURS is set but ignored. "
        "Use JWT_EXP_H to control token expiry (currently %sh). "
        "Remove JWT_EXP_HOURS from your environment.",
        JWT_EXP_H,
    )

bearer = HTTPBearer(auto_error=False)

# ─── Valid roles ─────────────────────────────────────────────────────────────
VALID_ROLES = (
    "Process Engineer",
    "Metallurgist",
    "Project Manager",
    "Cost Engineer",
    "Reviewer",
    "Read-only",
)


# ─── Token creation ─────────────────────────────────────────────────────────
def make_token(user_id: str, role: str) -> str:
    """Create a JWT containing user id and role."""
    try:
        row = qone("SELECT token_version FROM users WHERE id = %s", (user_id,))
        token_version = int(row["token_version"]) if row and row.get("token_version") is not None else 0
        payload = {
            "sub": user_id,
            "role": role,
            "ver": token_version,
            "jti": str(uuid_lib.uuid4()),
            "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXP_H),
        }
        return jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    except Exception as e:
        logger.error("Failed to create JWT for user_id=%s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Erreur lors de la creation du token d'authentification")


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_session(user_id: str) -> tuple[str, str]:
    try:
        refresh_token = secrets.token_urlsafe(48)
        row = execute(
            "INSERT INTO refresh_sessions (user_id, token_hash, expires_at) "
            "VALUES (%s, %s, NOW() + (%s || ' days')::interval) RETURNING id",
            (user_id, _hash_refresh_token(refresh_token), REFRESH_EXP_DAYS),
        )
        return refresh_token, str(row["id"])
    except Exception as e:
        logger.error("Failed to create refresh session for user_id=%s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Erreur lors de la creation de la session de rafraichissement")


def get_refresh_session(refresh_token: str) -> dict[str, Any] | None:
    try:
        return qone(
            "SELECT * FROM refresh_sessions WHERE token_hash = %s AND revoked_at IS NULL AND expires_at > NOW()",
            (_hash_refresh_token(refresh_token),),
        )
    except Exception as e:
        logger.error("Failed to retrieve refresh session: %s", e)
        raise HTTPException(status_code=500, detail="Erreur lors de la verification de la session de rafraichissement")


def rotate_refresh_session(refresh_token: str, user_id: str) -> tuple[str, str]:
    existing = qone(
        "SELECT id FROM refresh_sessions "
        "WHERE user_id = %s AND token_hash = %s AND revoked_at IS NULL AND expires_at > NOW()",
        (user_id, _hash_refresh_token(refresh_token)),
    )
    if not existing:
        raise HTTPException(status_code=401, detail="Refresh token invalide ou expire")
    new_token, new_session_id = create_refresh_session(user_id)
    execute(
        "UPDATE refresh_sessions SET revoked_at = NOW(), replaced_by_id = %s WHERE id = %s",
        (new_session_id, existing["id"]),
    )
    return new_token, new_session_id


def revoke_refresh_session(refresh_token: str, user_id: str) -> None:
    try:
        execute(
            "UPDATE refresh_sessions SET revoked_at = NOW() "
            "WHERE user_id = %s AND token_hash = %s AND revoked_at IS NULL",
            (user_id, _hash_refresh_token(refresh_token)),
        )
    except Exception as e:
        logger.error("Failed to revoke refresh session for user_id=%s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Erreur lors de la revocation de la session")


def revoke_all_refresh_sessions(user_id: str) -> None:
    try:
        execute(
            "UPDATE refresh_sessions SET revoked_at = NOW() WHERE user_id = %s AND revoked_at IS NULL",
            (user_id,),
        )
    except Exception as e:
        logger.error("Failed to revoke all refresh sessions for user_id=%s: %s", user_id, e)
        raise HTTPException(status_code=500, detail="Erreur lors de la revocation des sessions")


def bump_token_version(user_id: str) -> int:
    row = execute(
        "UPDATE users SET token_version = token_version + 1 WHERE id = %s RETURNING token_version",
        (user_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    return int(row["token_version"])


# ─── Current user dependency ────────────────────────────────────────────────
def _extract_token(creds: HTTPAuthorizationCredentials | None, request: Request | None) -> str:
    """Extract JWT from Authorization header or httpOnly cookie (fallback)."""
    if creds:
        return creds.credentials
    if request:
        token = request.cookies.get("access_token")
        if token:
            return token
    raise HTTPException(status_code=401, detail="Token manquant")


def resolve_current_user(
    request: Request | None,
    creds: HTTPAuthorizationCredentials | None,
) -> dict[str, Any]:
    """
    Validate JWT and return the current user row.

    ``request`` may be None when the token is supplied only via ``creds``
    (e.g. unit tests). Production routes use ``current_user`` below.
    """
    raw_token = _extract_token(creds, request)
    try:
        payload = jwt.decode(raw_token, JWT_SECRET, algorithms=["HS256"])
        user_id = payload["sub"]
        token_version = int(payload.get("ver", 0))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expire")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token invalide ou expire")
    except KeyError:
        raise HTTPException(status_code=401, detail="Token invalide")
    except Exception:
        # Catch-all for unexpected errors (e.g. DB down) — re-raise as 503
        raise HTTPException(status_code=503, detail="Service temporairement indisponible")

    row = qone(
        "SELECT id, email, role, full_name, token_version FROM users WHERE id = %s",
        (user_id,),
    )
    if not row:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable")
    if int(row.get("token_version", 0)) != token_version:
        raise HTTPException(status_code=401, detail="Session invalidee, reconnectez-vous")
    row["id"] = str(row["id"])
    return row


def current_user(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> dict[str, Any]:
    """
    FastAPI dependency. Returns a dict with keys: id, email, role, full_name.
    Accepts JWT via Authorization header OR httpOnly cookie.
    Decodes JWT then fetches fresh user record from DB so role changes take
    effect without requiring a new token.
    """
    return resolve_current_user(request, creds)


def ensure_project_access(pid: str, user: dict[str, Any]) -> None:
    """
    Enforce project-level access (Lot C Phase 1 — membership model):
    - Project Manager can access any existing project.
    - Other roles need to be a member of the project (project_members).
    Returns 404 for both not-found and forbidden to avoid leaking existence.

    Defensive during rollout: if project_members is not migrated yet, or the
    user is not listed there, fall back to the legacy owner column
    (projects.user_id) so behaviour is preserved until the backfill has run.
    """
    project = qone("SELECT id FROM projects WHERE id = %s", (pid,))
    project_exists = bool(project)

    is_member = False
    if project_exists and user["role"] != "Project Manager":
        try:
            row = qone(
                "SELECT 1 FROM project_members WHERE project_id = %s AND user_id = %s",
                (pid, user["id"]),
            )
            is_member = bool(row)
        except Exception:  # project_members not migrated yet — fall back below
            is_member = False
        if not is_member:
            legacy = qone(
                "SELECT 1 FROM projects WHERE id = %s AND user_id = %s",
                (pid, user["id"]),
            )
            is_member = bool(legacy)

    if not project_access_allowed(role=user["role"], project_exists=project_exists, is_member=is_member):
        raise HTTPException(status_code=404, detail="Projet introuvable ou acces refuse")


def project_user(pid: str, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Dependency: authenticated user with project-level access."""
    ensure_project_access(pid, user)
    return user


# ─── Role enforcement ───────────────────────────────────────────────────────
def require_role(*allowed_roles: str):
    """
    Returns a FastAPI dependency that checks the current user's role.

    Usage:
        @router.post("/something")
        def endpoint(user=Depends(require_role("Project Manager", "Process Engineer"))):
            ...
    """

    def dependency(user: dict = Depends(current_user)) -> dict:
        if user["role"] == "Project Manager":
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Acces refuse. Role requis: {', '.join(allowed_roles)}",
            )
        return user

    return dependency


def require_project_role(*allowed_roles: str):
    """Role + project ownership check in one dependency."""

    def dependency(pid: str, user: dict = Depends(current_user)) -> dict:
        ensure_project_access(pid, user)
        if user["role"] == "Project Manager":
            return user
        if user["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Acces refuse. Role requis: {', '.join(allowed_roles)}",
            )
        return user

    return dependency
