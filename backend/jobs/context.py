"""Compatibility re-exports for compute-layer code.

Compute modules expect `JobContext` and `JobCancelled` to live here. The
real `JobContext` is in `backend.worker.context`; the real exception is
`JobCancelledException` in `backend.jobs.errors`. We alias here so the
compute and handler layers can keep a single import path.
"""
from __future__ import annotations

try:
    from backend.worker.context import JobContext  # noqa: F401
    from backend.jobs.errors import JobCancelledException as JobCancelled  # noqa: F401
except ImportError:  # pragma: no cover
    from worker.context import JobContext  # noqa: F401
    from jobs.errors import JobCancelledException as JobCancelled  # noqa: F401
