"""Public interface of the jobs subsystem.

Re-exports below are populated as repo.py functions land in subsequent tasks.
"""
from .auth import assert_job_access
from .errors import JobCancelledException, JobNotFoundError
from .repo import (
    insert_job,
    pickup_one,
    report_progress,
    set_terminal_success,
    mark_failed,
    mark_cancelled,
    request_cancel,
    is_cancel_requested,
    get_job,
    get_payload,
    list_jobs,
    heartbeat,
    reap_zombies,
    insert_artifact,
    get_artifact,
    purge_old,
    get_job_by_id,
    list_jobs_by_project,
    cancel_job_if_pending,
)

__all__ = [
    "assert_job_access",
    "JobCancelledException",
    "JobNotFoundError",
    "insert_job",
    "pickup_one",
    "report_progress",
    "set_terminal_success",
    "mark_failed",
    "mark_cancelled",
    "request_cancel",
    "is_cancel_requested",
    "get_job",
    "get_payload",
    "list_jobs",
    "heartbeat",
    "reap_zombies",
    "insert_artifact",
    "get_artifact",
    "purge_old",
    "get_job_by_id",
    "list_jobs_by_project",
    "cancel_job_if_pending",
]
