"""Transaction context manager with after-commit callbacks.

Why: cross-module side effects (pipeline cascade signals, websocket broadcasts,
audit logs) must NOT roll back the main DB write if they fail, and must NOT
fire if the main write rolls back. This module gives services a clean way to
defer those side effects until after the commit succeeds.

Usage
-----
```python
from services.transaction import transaction, register_after_commit

def create_sample(pid, body, user_id):
    with transaction() as cur:
        cur.execute("INSERT INTO lims_samples ...", params)
        row = cur.fetchone()
        register_after_commit(lambda: signal_pipeline(pid, "lims", "complete"))
        return row
# pipeline signal fires here, only if the INSERT committed.
```

If the body raises, the transaction rolls back AND the registered callbacks
are dropped without firing.
"""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator

try:
    from db import conn, release
except ImportError:  # pragma: no cover - supports `from backend.services...`
    from backend.db import conn, release

logger = logging.getLogger("mpdpms.services.transaction")

_AfterCommit = Callable[[], Any]
_local = threading.local()


def _stack() -> list[list[_AfterCommit]]:
    if not hasattr(_local, "stack"):
        _local.stack = []
    return _local.stack


def register_after_commit(callback: _AfterCommit) -> None:
    """Schedule a callback to run after the current transaction commits.

    Outside any transaction, the callback runs immediately. Inside a
    transaction, it is queued and only fires if the transaction succeeds.
    """
    stack = _stack()
    if not stack:
        try:
            callback()
        except Exception:
            logger.exception("after-commit callback raised (no active tx)")
        return
    stack[-1].append(callback)


def run_after_commit(callbacks: list[_AfterCommit]) -> None:
    """Best-effort fire-and-log for queued callbacks. Public for testing."""
    for cb in callbacks:
        try:
            cb()
        except Exception:
            logger.exception("after-commit callback raised")


@contextmanager
def transaction(*, dict_cursor: bool = False) -> Iterator[Any]:
    """Yield a psycopg2 cursor inside an explicit transaction.

    Commits on clean exit, rolls back on exception. After-commit callbacks
    registered during the body fire only on a clean commit.

    By default returns tuple rows. Pass dict_cursor=True for RealDictCursor
    (rows accessed as dicts), matching the default of db.get_cursor().
    """
    import psycopg2.extras
    c = conn()
    factory = psycopg2.extras.RealDictCursor if dict_cursor else None
    cur = c.cursor(cursor_factory=factory)
    queued: list[_AfterCommit] = []
    _stack().append(queued)
    try:
        yield cur
        c.commit()
    except Exception:
        c.rollback()
        queued.clear()
        raise
    finally:
        cur.close()
        _stack().pop()
        release(c)
    run_after_commit(queued)
