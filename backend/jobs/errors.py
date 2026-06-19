"""Custom exceptions for the jobs subsystem."""


class JobCancelledException(Exception):
    """Raised by JobContext.check_cancelled() when cancel_requested is true.

    The worker loop catches this and transitions the job to status='cancelled'.
    Handlers should NOT catch this — let it propagate.
    """


class JobNotFoundError(Exception):
    """Raised when a job_id has no corresponding row."""
