"""
MPDPMS — Centralized Logging Configuration.

Single source of truth for all logging in the application.

Formatters
----------
* TextFormatter  — human-readable, for development / console.
  Format: ``2026-04-16T12:00:00.123Z | INFO     | mpdpms.circuit | message``

* JsonFormatter  — structured JSON, for production / log aggregators
  (Datadog, ELK, CloudWatch, Loki…).

Levels: DEBUG < INFO < WARNING < ERROR < CRITICAL  (Python standard)

Usage
-----
    from logging_config import get_logger, log_user_action

    logger = get_logger(__name__)
    logger.info("project loaded", extra={"project_id": pid})

    log_user_action(
        action="project.create",
        user_id=user["id"],
        entity_type="project",
        entity_id=str(row["id"]),
        details={"project_name": body.project_name},
    )

Environment variables
---------------------
LOG_LEVEL        DEBUG | INFO | WARNING | ERROR | CRITICAL   (default: INFO)
LOG_JSON         1 | 0                                        (default: 1 / True)
LOG_FILE         /path/to/mpdpms.log                         (default: stdout only)
LOG_FILE_MAX_MB  max file size before rotation (MB)          (default: 50)
LOG_FILE_BACKUPS number of rotated backup files              (default: 10)
LOG_MPDPMS_LEVEL override level for mpdpms.* namespace only  (default: LOG_LEVEL)
LOG_UVICORN      WARNING | INFO | DEBUG                       (default: WARNING)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import traceback
from datetime import datetime, timezone
from typing import Any

# ─── Module-level logger (bootstrapped before configure_logging is called) ──
_early_logger = logging.getLogger("mpdpms.logging_config")

# ─── Field list extracted from LogRecord into JSON / text extra fields ──────
_HTTP_EXTRA_FIELDS = (
    "request_id", "method", "path", "status_code",
    "duration_ms", "client_ip",
)
_APP_EXTRA_FIELDS = (
    "project_id", "user_id", "entity_type", "entity_id",
    "action", "startup_mode",
)
_ALL_EXTRA_FIELDS = _HTTP_EXTRA_FIELDS + _APP_EXTRA_FIELDS


# =============================================================================
# Formatters
# =============================================================================

class JsonFormatter(logging.Formatter):
    """Structured JSON log line — one JSON object per line.

    Includes timestamp, level, logger name, source location, message,
    all extra context fields, and full stack trace on exceptions.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "module": record.name,
            "source": f"{record.pathname}:{record.lineno}",
            "function": record.funcName,
            "message": record.getMessage(),
        }

        for field in _ALL_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": traceback.format_exception(*record.exc_info),
            }

        if record.stack_info:
            payload["stack_info"] = record.stack_info

        return json.dumps(payload, ensure_ascii=True, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable log line for development / console.

    Format::

        2026-04-16T12:00:00.123Z | INFO     | mpdpms.circuit          | message

    Extra context fields are appended as ``key=value`` pairs.
    Stack traces are printed on the following lines.
    """

    LEVEL_COLORS: dict[str, str] = {
        "DEBUG":    "\033[36m",   # cyan
        "INFO":     "\033[32m",   # green
        "WARNING":  "\033[33m",   # yellow
        "ERROR":    "\033[31m",   # red
        "CRITICAL": "\033[1;31m", # bold red
    }
    RESET = "\033[0m"

    def __init__(self, colorize: bool = True) -> None:
        super().__init__()
        self._colorize = colorize and _is_tty()

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(
            record.created, tz=timezone.utc
        ).isoformat(timespec="milliseconds")

        level = record.levelname
        if self._colorize:
            color = self.LEVEL_COLORS.get(level, "")
            level_str = f"{color}{level:<8}{self.RESET}"
        else:
            level_str = f"{level:<8}"

        module = record.name[:40]
        message = record.getMessage()

        extras = []
        for field in _ALL_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                extras.append(f"{field}={value}")

        line = f"{ts} | {level_str} | {module:<40} | {message}"
        if extras:
            line += "  [" + "  ".join(extras) + "]"

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        if record.stack_info:
            line += "\n" + record.stack_info

        return line


def _is_tty() -> bool:
    import sys
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# =============================================================================
# Configuration builder
# =============================================================================

def _make_handlers(
    formatter: logging.Formatter,
    level: int,
    log_file: str | None,
    file_max_mb: int,
    file_backups: int,
) -> list[logging.Handler]:
    """Build the list of active handlers from env config."""
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(level)
    handlers: list[logging.Handler] = [console]

    if log_file:
        import pathlib
        pathlib.Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=file_max_mb * 1024 * 1024,
            backupCount=file_backups,
            encoding="utf-8",
        )
        fh.setFormatter(JsonFormatter())
        fh.setLevel(level)
        handlers.append(fh)

    return handlers


def configure_logging() -> None:
    """Read env vars and configure Python's logging system.

    Must be called once at application startup (``main.py`` lifespan).
    Safe to call multiple times — idempotent (clears existing handlers first).

    Environment variables
    ---------------------
    LOG_LEVEL           Root log level (default: INFO)
    LOG_JSON            1 = JSON lines, 0 = human-readable text (default: 1)
    LOG_FILE            Path to rotating log file (default: stdout only)
    LOG_FILE_MAX_MB     Max size per file in MB (default: 50)
    LOG_FILE_BACKUPS    Number of backup files (default: 10)
    LOG_MPDPMS_LEVEL    Level override for mpdpms.* namespace (default: LOG_LEVEL)
    LOG_UVICORN         Level for uvicorn loggers (default: WARNING)
    """
    valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    if level_name not in valid:
        level_name = "INFO"
    level = getattr(logging, level_name)

    log_json = os.getenv("LOG_JSON", "1").lower() not in {"0", "false", "no", "off"}
    log_file = os.getenv("LOG_FILE") or None
    file_max_mb = int(os.getenv("LOG_FILE_MAX_MB", "50"))
    file_backups = int(os.getenv("LOG_FILE_BACKUPS", "10"))

    mpdpms_level_name = os.getenv("LOG_MPDPMS_LEVEL", level_name).upper()
    if mpdpms_level_name not in valid:
        mpdpms_level_name = level_name
    mpdpms_level = getattr(logging, mpdpms_level_name)

    uvicorn_level_name = os.getenv("LOG_UVICORN", "WARNING").upper()
    uvicorn_level = getattr(logging, uvicorn_level_name, logging.WARNING)

    formatter: logging.Formatter = JsonFormatter() if log_json else TextFormatter()
    handlers = _make_handlers(formatter, level, log_file, file_max_mb, file_backups)

    # ── Root logger ──────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.handlers.clear()
    for h in handlers:
        root.addHandler(h)
    root.setLevel(logging.WARNING)

    # ── mpdpms namespace (all application code) ──────────────────────────────
    mpdpms = logging.getLogger("mpdpms")
    mpdpms.handlers.clear()
    for h in handlers:
        mpdpms.addHandler(h)
    mpdpms.setLevel(mpdpms_level)
    mpdpms.propagate = False

    # ── Web server ───────────────────────────────────────────────────────────
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        for h in handlers:
            lg.addHandler(h)
        lg.setLevel(uvicorn_level)
        lg.propagate = False

    # ── Background workers ───────────────────────────────────────────────────
    for name in ("celery", "celery.task"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        for h in handlers:
            lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False

    # ── Mute noisy third-party libraries ─────────────────────────────────────
    for name in ("sqlalchemy.engine", "sqlalchemy.pool", "alembic", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    _early_logger.info(
        "logging configured",
        extra={"action": "logging.configure", "startup_mode": "logging_config"},
    )
    _early_logger.debug(
        "logging details",
        extra={
            "action": "logging.configure",
            "level": level_name,
            "mpdpms_level": mpdpms_level_name,
            "log_json": log_json,
            "log_file": log_file or "stdout only",
        },
    )


# =============================================================================
# Public helpers
# =============================================================================

def get_logger(name: str) -> logging.Logger:
    """Return a ``logging.Logger`` under the ``mpdpms`` hierarchy.

    If *name* already starts with ``mpdpms``, it is used as-is.
    Otherwise ``mpdpms.`` is prepended so all application logs share
    the same namespace and are governed by a single handler.

    Example::

        logger = get_logger(__name__)
        logger.info("action done", extra={"project_id": pid})
    """
    if name.startswith("mpdpms"):
        return logging.getLogger(name)
    return logging.getLogger(f"mpdpms.{name}")


# ─── Dedicated logger for user-action audit trail ────────────────────────────
_ACTION_LOGGER = logging.getLogger("mpdpms.user_actions")


def log_user_action(
    action: str,
    *,
    user_id: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
    level: int = logging.INFO,
) -> None:
    """Log a critical user action at INFO level (or *level* if overridden).

    All user-facing write operations (create, update, delete, generate,
    approve…) should call this helper so they appear in a consistent,
    searchable audit stream.

    Parameters
    ----------
    action:      Dot-namespaced action code, e.g. ``"project.create"``
    user_id:     UUID of the acting user (None for system actions)
    entity_type: e.g. ``"project"``, ``"circuit_template"``, ``"lims_sample"``
    entity_id:   UUID of the affected entity
    details:     Free-form dict of relevant values (sanitise before passing)
    level:       Override log level (default: INFO)

    Example::

        log_user_action(
            "circuit_template.create",
            user_id=user["id"],
            entity_type="circuit_template",
            entity_id=str(template_id),
            details={"name": body.name, "project_id": pid},
        )
    """
    extra: dict[str, Any] = {"action": action}
    if user_id is not None:
        extra["user_id"] = str(user_id)
    if entity_type is not None:
        extra["entity_type"] = entity_type
    if entity_id is not None:
        extra["entity_id"] = str(entity_id)

    msg = action
    if details:
        detail_str = "  ".join(f"{k}={v}" for k, v in details.items())
        msg = f"{action}  {detail_str}"

    _ACTION_LOGGER.log(level, msg, extra=extra)
