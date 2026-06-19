"""Auth helpers for the jobs subsystem.

Wraps existing project-scope auth so jobs/* routes can defer access
checks to the same logic as the rest of the API.
"""
from __future__ import annotations
from typing import Any

try:
    from auth import ensure_project_access
except ImportError:  # pragma: no cover
    from backend.auth import ensure_project_access


def assert_job_access(user: dict[str, Any], job_row: dict[str, Any]) -> None:
    """Raise HTTPException(404) if user cannot access the job's project.

    Uses the same 404-on-forbidden convention as the rest of the codebase
    (existence is not leaked).
    """
    ensure_project_access(str(job_row["project_id"]), user)
