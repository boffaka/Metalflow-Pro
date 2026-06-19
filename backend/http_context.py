"""
HTTP request context for **sync** route handlers and services.

Amélioration observabilité : le middleware FastAPI attache ``X-Request-ID`` à
``request.state`` ; ce module expose le même identifiant via *contextvars* pour
le code synchrone (``qone``/``qall``/engines) qui n'a pas accès à ``Request``.
"""
from __future__ import annotations

import contextvars
from typing import Any

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)


def attach_request_id(request_id: str) -> contextvars.Token[Any]:
    return _request_id.set(request_id)


def reset_request_id(token: contextvars.Token[Any]) -> None:
    _request_id.reset(token)


def get_request_id() -> str | None:
    return _request_id.get()
