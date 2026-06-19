"""CSRF protection middleware.

Origin/Referer-based check on state-changing requests. Cookie-based JWT auth
without this is vulnerable to forged cross-site requests.
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable, Iterable
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("mpdpms.csrf")

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

DEFAULT_EXEMPT_PATHS = frozenset({
    "/api/v1/health",
    "/api/v1/ready",
    "/metrics",
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
})

DEFAULT_EXEMPT_PREFIXES: tuple[str, ...] = ("/ws/",)


def _allowed_hosts(origins: Iterable[str]) -> set[str]:
    return {urlparse(o).netloc for o in origins if o}


def _origin_allowed(url: str | None, allowed: set[str]) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return bool(parsed.netloc) and parsed.netloc in allowed


def build_csrf_middleware(
    allowed_origins: Iterable[str],
    exempt_paths: Iterable[str] = DEFAULT_EXEMPT_PATHS,
    exempt_prefixes: Iterable[str] = DEFAULT_EXEMPT_PREFIXES,
) -> Callable[[Request, Callable[[Request], Awaitable]], Awaitable]:
    """Return an ASGI HTTP middleware enforcing CSRF on mutating requests."""
    allowed = _allowed_hosts(allowed_origins)
    exempt_paths_set = frozenset(exempt_paths)
    exempt_prefixes_tuple = tuple(exempt_prefixes)

    async def middleware(request: Request, call_next):
        if request.method in SAFE_METHODS:
            return await call_next(request)

        path = request.url.path
        if path in exempt_paths_set or any(path.startswith(p) for p in exempt_prefixes_tuple):
            return await call_next(request)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")

        if origin:
            if not _origin_allowed(origin, allowed):
                logger.warning("csrf reject", extra={"reason": "origin", "origin": origin, "path": path})
                return JSONResponse({"detail": "CSRF origin rejected"}, status_code=403)
        elif referer:
            if not _origin_allowed(referer, allowed):
                logger.warning("csrf reject", extra={"reason": "referer", "referer": referer, "path": path})
                return JSONResponse({"detail": "CSRF referer rejected"}, status_code=403)
        else:
            # No browser headers. Allow only if Bearer auth is used (server-to-server).
            # Cookie-only auth without Origin/Referer is a forgery indicator.
            auth = request.headers.get("authorization", "")
            has_session_cookie = bool(
                request.cookies.get("access_token") or request.cookies.get("session")
            )
            if has_session_cookie and not auth.lower().startswith("bearer "):
                logger.warning("csrf reject", extra={"reason": "missing-origin-with-cookie", "path": path})
                return JSONResponse(
                    {"detail": "CSRF: Origin/Referer required for cookie auth"}, status_code=403
                )

        return await call_next(request)

    return middleware
