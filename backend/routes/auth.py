"""
MPDPMS — Auth routes (login, me, logout, rotate, refresh).
"""
from __future__ import annotations

import logging
import psycopg2
import time

from fastapi import APIRouter, HTTPException, Depends, Request

logger = logging.getLogger("mpdpms.auth")
from fastapi.responses import JSONResponse

try:
    from ..auth import current_user, make_token, bump_token_version, create_refresh_session, get_refresh_session, rotate_refresh_session, revoke_refresh_session
    from ..db import is_database_unavailable, qone
    from ..models import LoginIn, LoginOut, RefreshTokenIn
    from ..security import audit_log
    from ..security_store import build_security_store
    from ..settings import get_settings
    from ..logging_config import log_user_action
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import current_user, make_token, bump_token_version, create_refresh_session, get_refresh_session, rotate_refresh_session, revoke_refresh_session
    from db import is_database_unavailable, qone
    from models import LoginIn, LoginOut, RefreshTokenIn
    from security import audit_log
    from security_store import build_security_store
    from settings import get_settings
    from logging_config import log_user_action


def _set_auth_cookies(response: JSONResponse, access_token: str, refresh_token: str) -> JSONResponse:
    """Set httpOnly, Secure, SameSite cookies for JWT tokens."""
    _s = get_settings()
    secure = _s.enable_hsts  # Use HSTS setting as proxy for HTTPS
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=_s.jwt_exp_h * 3600,
        path="/",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=_s.refresh_exp_days * 86400,
        path="/api/v1/auth",
    )
    return response


def _clear_auth_cookies(response: JSONResponse) -> JSONResponse:
    """Remove auth cookies on logout."""
    response.delete_cookie("access_token", path="/")
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    return response

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_DB_UNAVAILABLE_DETAIL = "Base de données temporairement indisponible. Réessayez dans quelques minutes."


def _raise_if_db_down(exc: BaseException) -> None:
    if is_database_unavailable(exc):
        raise HTTPException(status_code=503, detail=_DB_UNAVAILABLE_DETAIL) from exc


_settings = get_settings()
_LOGIN_WINDOW_S = _settings.login_window_s
_LOGIN_MAX_ATTEMPTS = _settings.login_max_attempts
SECURITY_STORE = build_security_store(_settings.security_store_backend, _settings.redis_url)


def _login_rate_key(ip: str, email: str | None = None) -> str:
    normalized_email = (email or "").strip().lower()
    return f"{ip}:{normalized_email}"


def _check_login_rate(ip: str, email: str | None = None) -> None:
    key = _login_rate_key(ip, email)
    now = time.time()
    attempts = SECURITY_STORE.record_login_attempt(key, now, _LOGIN_WINDOW_S)
    if attempts > _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Trop de tentatives. Réessayez dans 60 secondes.")


def _reset_login_rate(ip: str, email: str | None = None) -> None:
    SECURITY_STORE.reset_login_attempts(_login_rate_key(ip, email))


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request):
    try:
        client_ip = request.client.host if request.client else "unknown"
        _check_login_rate(client_ip, body.email)
        row = qone(
            "SELECT id, email, role, full_name FROM users "
            "WHERE email = %s AND password_hash = crypt(%s, password_hash)",
            (body.email, body.password),
        )
        if not row:
            audit_log(
                action="auth.login_failed",
                entity_type="auth",
                user_id=None,
                new_value={"email": body.email.lower(), "client_ip": client_ip},
            )
            log_user_action(
                "auth.login_failed",
                details={"email": body.email.lower(), "client_ip": client_ip},
            )
            raise HTTPException(status_code=401, detail="Identifiants incorrects")
        _reset_login_rate(client_ip, body.email)
        audit_log(
            action="auth.login_success",
            entity_type="auth",
            user_id=str(row["id"]),
            entity_id=str(row["id"]),
            new_value={"email": row["email"], "client_ip": client_ip, "role": row["role"]},
        )
        log_user_action(
            "auth.login_success",
            user_id=str(row["id"]),
            entity_type="user",
            entity_id=str(row["id"]),
            details={"email": row["email"], "role": row["role"], "client_ip": client_ip},
        )
        refresh_token, refresh_session_id = create_refresh_session(str(row["id"]))
        audit_log(
            action="auth.refresh_issued",
            entity_type="auth",
            user_id=str(row["id"]),
            entity_id=refresh_session_id,
            new_value={"email": row["email"], "client_ip": client_ip},
        )
        access_token = make_token(str(row["id"]), row["role"])
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {
                "id": str(row["id"]),
                "email": row["email"],
                "role": row["role"],
                "full_name": row["full_name"],
            },
        }
        response = JSONResponse(content=payload)
        _set_auth_cookies(response, access_token, refresh_token)
        return response
    except HTTPException:
        raise
    except psycopg2.OperationalError as e:
        _raise_if_db_down(e)
    except RuntimeError as e:
        _raise_if_db_down(e)


@router.post("/register", status_code=201)
def register(body: dict, request: Request, user=Depends(current_user)):
    """Project-Manager-only registration. Creates a new user with 'Metallurgist'
    role by default. Public self-signup is disabled — accounts must be
    provisioned by an authenticated Project Manager.
    """
    if user.get("role") != "Project Manager":
        raise HTTPException(403, "Only Project Managers can register new users")
    try:
        client_ip = request.client.host if request.client else "unknown"
        _check_login_rate(client_ip, body.get("email"))  # Rate limiting

        try:
            from ..security import validate_password_strength
        except ImportError:
            from security import validate_password_strength

        import re
        email = (body.get("email") or "").strip().lower()
        password = body.get("password") or ""
        full_name = (body.get("full_name") or "").strip()
        if not email or not password:
            raise HTTPException(400, "Email et mot de passe requis")
        # RFC-5322-lite email validation
        if not re.match(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$", email):
            raise HTTPException(400, "Adresse courriel invalide")
        if len(email) > 254:
            raise HTTPException(400, "Adresse courriel trop longue")
        try:
            validate_password_strength(password)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        try:
            from ..db import execute
        except ImportError:
            from db import execute

        existing = qone("SELECT id FROM users WHERE email = %s", (email,))
        if existing:
            raise HTTPException(409, "Un compte avec cet email existe déjà")

        row = execute(
            "INSERT INTO users (email, password_hash, full_name, role) "
            "VALUES (%s, crypt(%s, gen_salt('bf')), %s, %s) RETURNING id, email, role, full_name, created_at",
            (email, password, full_name or email.split("@")[0], "Metallurgist"),
        )
        client_ip = request.client.host if request.client else "unknown"
        audit_log(
            action="auth.register",
            entity_type="user",
            entity_id=str(row["id"]),
            user_id=str(row["id"]),
            new_value={"email": row["email"], "role": row["role"], "client_ip": client_ip},
        )
        return {
            "ok": True,
            "user": {"id": str(row["id"]), "email": row["email"], "role": row["role"], "full_name": row["full_name"]},
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")


@router.get("/me")
def auth_me(user=Depends(current_user)):
    """Return current authenticated user info."""
    return user


@router.post("/logout")
def auth_logout(body: RefreshTokenIn, request: Request, user=Depends(current_user)):
    try:
        client_ip = request.client.host if request.client else "unknown"
        bump_token_version(user["id"])
        # Accept refresh_token from body or cookie
        rt = body.refresh_token or request.cookies.get("refresh_token", "")
        if rt:
            revoke_refresh_session(rt, user["id"])
        audit_log(
            action="auth.logout",
            entity_type="auth",
            user_id=str(user["id"]),
            entity_id=str(user["id"]),
            new_value={"email": user["email"], "client_ip": client_ip},
        )
        response = JSONResponse(content={"ok": True})
        _clear_auth_cookies(response)
        return response
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/rotate", response_model=LoginOut)
def auth_rotate_token(body: RefreshTokenIn, request: Request, user=Depends(current_user)):
    try:
        client_ip = request.client.host if request.client else "unknown"
        bump_token_version(user["id"])
        token = make_token(str(user["id"]), user["role"])
        refresh_token, refresh_session_id = rotate_refresh_session(body.refresh_token, user["id"])
        audit_log(
            action="auth.rotate_token",
            entity_type="auth",
            user_id=str(user["id"]),
            entity_id=refresh_session_id,
            new_value={"email": user["email"], "client_ip": client_ip},
        )
        payload = {"access_token": token, "refresh_token": refresh_token, "token_type": "bearer", "user": user}
        response = JSONResponse(content=payload)
        _set_auth_cookies(response, token, refresh_token)
        return response
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.post("/refresh", response_model=LoginOut)
def auth_refresh(body: RefreshTokenIn, request: Request):
    try:
        client_ip = request.client.host if request.client else "unknown"
        # Accept refresh_token from body or cookie
        rt = body.refresh_token or request.cookies.get("refresh_token", "")
        session = get_refresh_session(rt)
        if not session:
            audit_log(
                action="auth.refresh_failed",
                entity_type="auth",
                new_value={"client_ip": client_ip},
            )
            raise HTTPException(status_code=401, detail="Refresh token invalide ou expire")

        user = qone(
            "SELECT id, email, role, full_name FROM users WHERE id = %s",
            (session["user_id"],),
        )
        if not user:
            raise HTTPException(status_code=401, detail="Utilisateur introuvable")

        bump_token_version(str(user["id"]))
        access_token = make_token(str(user["id"]), user["role"])
        refresh_token, refresh_session_id = rotate_refresh_session(rt, str(user["id"]))
        audit_log(
            action="auth.refresh_success",
            entity_type="auth",
            user_id=str(user["id"]),
            entity_id=refresh_session_id,
            new_value={"email": user["email"], "client_ip": client_ip},
        )
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {
                "id": str(user["id"]),
                "email": user["email"],
                "role": user["role"],
                "full_name": user["full_name"],
            },
        }
        response = JSONResponse(content=payload)
        _set_auth_cookies(response, access_token, refresh_token)
        return response
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
