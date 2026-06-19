"""
MPDPMS — Database connection pool and query helpers.
Thread-safe pool with proper resource cleanup.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

logger = logging.getLogger("mpdpms.db")
try:
    from .settings import get_settings
except ImportError:  # pragma: no cover - supports direct script imports
    from settings import get_settings

_pool: ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _compute_pool_max(dsn: str, fallback: int = 15, num_services: int = 3) -> int:
    """Derive pool max from the server's max_connections setting.

    Returns max(2, 80% of max_connections / num_services) so co-located
    services share the connection budget fairly.  Falls back to *fallback*
    when the server is unreachable or the query fails.
    """
    try:
        import psycopg2 as _pg2
        c = _pg2.connect(dsn, connect_timeout=5)
        cur = c.cursor()
        cur.execute("SHOW max_connections")
        max_conn = int(cur.fetchone()[0])
        cur.close()
        c.close()
        return max(2, int(max_conn * 0.8 / num_services))
    except Exception as e:
        logger.warning("Could not query max_connections, fallback=%d: %s", fallback, e)
        return fallback


def get_pool() -> ThreadedConnectionPool:
    """Return the global connection pool, creating it on first call (thread-safe)."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                try:
                    dsn = get_settings().database_url
                    pool_min = int(os.getenv("DB_POOL_MIN", "2"))
                    env_max = os.getenv("DB_POOL_MAX")
                    pool_max = int(env_max) if env_max else _compute_pool_max(dsn)
                    _pool = ThreadedConnectionPool(
                        pool_min, pool_max, dsn,
                        connect_timeout=5,
                        options="-c statement_timeout=60000",  # 60s — simulations/optimisations peuvent durer >10s
                    )
                    logger.info("DB pool initialised min=%d max=%d", pool_min, pool_max)
                except Exception as e:
                    logger.error("Failed to create database connection pool: %s", e)
                    raise RuntimeError(f"Database connection pool initialization failed: {e}") from e
    return _pool


def conn() -> Any:
    """Get a connection from the pool."""
    try:
        pool = get_pool()
        # Warn when pool utilisation exceeds 80%
        try:
            used = len(pool._used)
            total = pool.maxconn
            if total and used / total > 0.8:
                logger.warning(
                    "DB pool utilisation high: %d/%d connections in use (%.0f%%)",
                    used, total, used / total * 100,
                )
        except Exception:
            pass  # _used is an implementation detail; never let monitoring break requests
        return pool.getconn()
    except Exception as e:
        logger.error("Failed to acquire database connection from pool: %s", e)
        raise RuntimeError(f"Could not acquire database connection: {e}") from e


def release(c: Any) -> None:
    """Return a connection to the pool."""
    try:
        get_pool().putconn(c)
    except Exception as e:
        logger.error("Failed to release database connection back to pool: %s", e)


@contextmanager
def get_cursor(*, commit: bool = False, dict_cursor: bool = True):
    """Context manager that properly handles connection + cursor lifecycle.

    Usage:
        with get_cursor() as cur:            # read-only
            cur.execute("SELECT ...")
        with get_cursor(commit=True) as cur:  # write with commit
            cur.execute("INSERT ...")
    """
    c = conn()
    cur = None
    try:
        factory = psycopg2.extras.RealDictCursor if dict_cursor else None
        cur = c.cursor(cursor_factory=factory)
        yield cur
        if commit:
            c.commit()
        else:
            # Rollback-for-reads: Postgres auto-opens an implicit transaction
            # on the first statement.  If we don't close it, the connection
            # returns to the pool stuck in "idle in transaction" state, which
            # holds shared locks, blocks VACUUM / DDL, and can eventually
            # starve the pool.  rollback() is the cheapest way to end the
            # transaction when no writes occurred.
            c.rollback()
    except Exception:
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)


@contextmanager
def get_conn():
    """Context manager that provides a raw connection with proper cleanup.

    Usage:
        with get_conn() as c:
            cur = c.cursor()
            ...
            c.commit()
    """
    c = conn()
    try:
        yield c
    except Exception:
        c.rollback()
        raise
    finally:
        release(c)


def is_database_unavailable(exc: BaseException) -> bool:
    """True when the pool cannot reach Postgres (timeouts, refused, etc.)."""
    if isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)):
        return True
    if isinstance(exc, RuntimeError) and "database connection" in str(exc).lower():
        return True
    return False


def qone(sql: str, params: Any = None) -> dict | None:
    """Execute SQL and return a single row as dict, or None."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


def qall(sql: str, params: Any = None) -> list[dict]:
    """Execute SQL and return all rows as list of dicts."""
    with get_cursor() as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def execute(sql: str, params: Any = None) -> dict:
    """Execute a write SQL (INSERT/UPDATE/DELETE) with commit.
    Returns first row if RETURNING used, else empty dict.
    """
    with get_cursor(commit=True) as cur:
        cur.execute(sql, params)
        try:
            row = cur.fetchone()
            return dict(row) if row else {}
        except psycopg2.ProgrammingError:
            # No results to fetch (e.g. DELETE without RETURNING)
            return {}


import re as _re

_VALID_COLUMN_RE = _re.compile(r"^[a-z_][a-z0-9_]*$")


def safe_column(name: str) -> str:
    """Validate that a column name is a safe SQL identifier (lowercase snake_case only)."""
    if not _VALID_COLUMN_RE.match(name):
        raise ValueError(f"Invalid column name: {name!r}")
    return name


def build_update_sets(data: dict, *, allowed: frozenset[str]) -> tuple[list[str], list]:
    """
    Build SET clause fragments from a dict of column->value pairs.

    ``allowed`` is **required** — callers must explicitly declare which
    columns are acceptable for update.  Keys not in ``allowed`` are
    silently skipped, and every surviving column name is validated
    against the safe-identifier regex ``^[a-z_][a-z0-9_]*$``.

    Returns (["col1=%s", "col2=%s"], [val1, val2]).
    """
    fields, vals = [], []
    for k, v in data.items():
        if k not in allowed:
            continue
        safe_column(k)
        fields.append(f"{k}=%s")
        vals.append(v)
    return fields, vals


def paginated_qall(sql: str, params=None, *, limit: int = 100, offset: int = 0, max_limit: int = 1000) -> list[dict]:
    """Execute SQL with LIMIT/OFFSET appended, clamping values to safe bounds.

    *limit* is clamped to [1, max_limit] and *offset* to [0, +inf).
    """
    limit = min(max(1, limit), max_limit)
    offset = max(0, offset)
    paginated_sql = f"{sql} LIMIT %s OFFSET %s"
    merged_params = (*params, limit, offset) if params else (limit, offset)
    return qall(paginated_sql, merged_params)


def execute_script(sql: str) -> None:
    """Execute a multi-statement SQL script (e.g. schema). No return value."""
    with get_cursor(commit=True, dict_cursor=False) as cur:
        cur.execute(sql)


def close_pool() -> None:
    """Close all pool connections. Call on shutdown."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.closeall()
            _pool = None


# ─── Staleness helpers ────────────────────────────────────────────────────────

def mark_stale(project_id: str, modules: list[str], reason: str):
    for mod in modules:
        execute(
            "INSERT INTO project_staleness (project_id, module, is_stale, stale_since, reason) "
            "VALUES (%s, %s, TRUE, NOW(), %s) "
            "ON CONFLICT (project_id, module) DO UPDATE "
            "SET is_stale=TRUE, stale_since=NOW(), reason=%s",
            (project_id, mod, reason, reason),
        )


def clear_stale(project_id: str, module: str):
    execute(
        "INSERT INTO project_staleness (project_id, module, is_stale, stale_since, reason) "
        "VALUES (%s, %s, FALSE, NULL, NULL) "
        "ON CONFLICT (project_id, module) DO UPDATE "
        "SET is_stale=FALSE, stale_since=NULL, reason=NULL",
        (project_id, module),
    )


def get_staleness(project_id: str) -> dict:
    rows = qall(
        "SELECT module, is_stale, stale_since, reason "
        "FROM project_staleness WHERE project_id=%s",
        (project_id,),
    )
    result = {}
    for mod in ("mass_balance", "flowsheet", "costs"):
        match = next((r for r in rows if r["module"] == mod), None)
        if match and match["is_stale"]:
            result[mod] = {
                "is_stale": True,
                "stale_since": str(match["stale_since"]),
                "reason": match["reason"],
            }
        else:
            result[mod] = {"is_stale": False}
    return result
