"""JobContext — the surface a handler uses to talk to the queue.

Provides:
- report_progress(pct, message) with throttling
- check_cancelled() with caching, raises JobCancelledException
- get_db_conn() for handlers that need a fresh connection (sized inserts)

Throttling is purely client-side; check_cancelled caches the result of
is_cancel_requested for cancel_cache_ms milliseconds.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

try:
    from db import get_conn
    from jobs import repo
    from jobs.errors import JobCancelledException
except ImportError:  # pragma: no cover
    from backend.db import get_conn
    from backend.jobs import repo
    from backend.jobs.errors import JobCancelledException


@dataclass
class JobContext:
    job_id: str
    project_id: str
    user_id: str
    worker_id: str
    conn: Any = None  # Set by the runner before calling the handler. Runner commits/rollbacks.
    progress_throttle_ms: int = 500
    cancel_cache_ms: int = 500

    _last_progress_at_ms: float = field(default=0.0, init=False)
    _cancel_cached_at_ms: float = field(default=0.0, init=False)
    _cancel_cached_value: bool = field(default=False, init=False)

    def report_progress(self, current: int, total: int | str | None = None,
                        message: str | None = None) -> None:
        """Update progress, throttled to progress_throttle_ms.

        Accepts two call shapes:
        - report_progress(pct, message)        — pct is 0..100
        - report_progress(current, total, msg) — pct = round(100 * current / total)

        The terminal write (pct == 100) is NEVER throttled — it must always land.
        """
        # Detect 2-arg (pct, message) form: total is None or a string.
        if total is None or isinstance(total, str):
            pct = int(current)
            if isinstance(total, str):
                message = total
        else:
            pct = int(round(100 * current / total)) if total else 0
        pct = max(0, min(100, pct))
        now_ms = time.monotonic() * 1000
        if pct < 100:
            if now_ms - self._last_progress_at_ms < self.progress_throttle_ms:
                return
        repo.report_progress(self.job_id, self.worker_id, pct, message)
        self._last_progress_at_ms = now_ms

    def check_cancelled(self) -> None:
        """Raise JobCancelledException if the cancel flag is set.

        Caches the lookup for cancel_cache_ms (default 500). Handlers may
        call this in tight inner loops; the cache bounds DB load.
        """
        now_ms = time.monotonic() * 1000
        if now_ms - self._cancel_cached_at_ms >= self.cancel_cache_ms:
            self._cancel_cached_value = repo.is_cancel_requested(self.job_id)
            self._cancel_cached_at_ms = now_ms
        if self._cancel_cached_value:
            raise JobCancelledException(self.job_id)

    @contextmanager
    def get_db_conn(self):
        """Yield a fresh DB connection from the pool for handlers that need it.

        Distinct from the worker loop's LISTEN connection — handlers MUST NOT
        reuse the LISTEN connection, since LISTEN holds it open in autocommit.
        """
        with get_conn() as c:
            yield c
