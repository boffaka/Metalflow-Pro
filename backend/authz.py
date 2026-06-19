"""
Project authorization decisions — Lot C Phase 1 (membership model).

Pure decision logic, separated from the DB/HTTP layer in auth.py so it is
unit-testable in isolation. auth.ensure_project_access fetches the facts
(does the project exist? is the user a member?) and delegates the verdict here.

Membership model (F2): access is granted to project members (project_members
table) plus any Project Manager (org-wide role). This replaces the previous
owner-only check (projects.user_id), enabling multi-user collaboration.
"""

from __future__ import annotations


def project_access_allowed(*, role: str, project_exists: bool, is_member: bool) -> bool:
    """Decide whether a user may access a project.

    Args:
        role: The user's org-wide role (e.g. "Project Manager", "Metallurgist").
        project_exists: Whether the project row exists.
        is_member: Whether the user is a member of the project (project_members),
            owner included (owners are backfilled as members).

    Returns:
        True if access is allowed. A non-existent project is always denied
        (callers surface 404 for both not-found and forbidden, to avoid leaking
        project existence).
    """
    if not project_exists:
        return False
    if role == "Project Manager":
        return True
    return is_member
