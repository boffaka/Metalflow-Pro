from __future__ import annotations

import re
from typing import Any

import psycopg2.extras

try:
    from .db import execute
except ImportError:  # pragma: no cover - supports direct script imports
    from db import execute


PASSWORD_PATTERN = re.compile(r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d).+$")


def validate_password_strength(value: str) -> str:
    if len(value) < 8:
        raise ValueError("Password must be at least 8 characters long")
    if not PASSWORD_PATTERN.match(value):
        raise ValueError("Password must include uppercase, lowercase, and a digit")
    return value


import logging as _sec_logging

_sec_logger = _sec_logging.getLogger("mpdpms.security")


def audit_log(
    *,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    user_id: str | None = None,
    old_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> None:
    try:
        execute(
            "INSERT INTO audit_log (user_id, action, entity_type, entity_id, old_value, new_value) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)",
            (
                user_id, action, entity_type, entity_id,
                psycopg2.extras.Json(old_value) if old_value is not None else None,
                psycopg2.extras.Json(new_value) if new_value is not None else None,
            ),
        )
    except Exception as e:
        _sec_logger.error(
            "Failed to write audit log entry action=%s entity_type=%s entity_id=%s: %s",
            action, entity_type, entity_id, e,
        )
