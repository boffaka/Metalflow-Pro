from __future__ import annotations

import secrets
try:
    from .logging_config import (
        JsonFormatter,
        TextFormatter,
        configure_logging,
        get_logger,
        log_user_action,
    )
except ImportError:  # pragma: no cover - supports direct script imports
    from logging_config import (
        JsonFormatter,
        TextFormatter,
        configure_logging,
        get_logger,
        log_user_action,
    )

__all__ = [
    "JsonFormatter",
    "TextFormatter",
    "configure_logging",
    "get_logger",
    "log_user_action",
    "generate_csp_nonce",
    "security_headers",
    "startup_mode",
    "DEFAULT_SECURITY_HEADERS",
]


_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "interest-cohort=()",
    "X-XSS-Protection": "1; mode=block",
    "Cross-Origin-Opener-Policy": "same-origin",
}

# Strict CSP for API and React /app routes
_STRICT_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://cdn.plot.ly; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:;"
)

# Relaxed CSP for legacy HTML frontend — unsafe-inline required for 500+ inline
# event handlers (onclick, onchange, etc.) that cannot use nonces.  unsafe-eval
# is intentionally removed (no eval/new Function usage found).
_LEGACY_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://cdn.plot.ly; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self' ws: wss:; "
    "worker-src 'self' blob:;"
)


def generate_csp_nonce() -> str:
    """Return a cryptographically secure random nonce for use in CSP headers."""
    return secrets.token_urlsafe(32)

DEFAULT_SECURITY_HEADERS = {**_BASE_HEADERS, "Content-Security-Policy": _STRICT_CSP}


def startup_mode(auto_migrate: bool, bootstrap_schema: bool) -> str:
    if auto_migrate:
        return "alembic"
    if bootstrap_schema:
        return "bootstrap"
    return "seed-only"


def security_headers(
    request_id: str,
    *,
    enable_hsts: bool = False,
    path: str = "",
    request=None,
) -> dict[str, str]:
    # Use relaxed CSP for the legacy HTML frontend, strict for everything else
    is_legacy = path == "/" or path == ""
    csp = _LEGACY_CSP if is_legacy else _STRICT_CSP
    headers = {
        **_BASE_HEADERS,
        "Content-Security-Policy": csp,
        "X-Request-ID": request_id,
        "Cache-Control": "no-store",
    }
    if enable_hsts:
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers
