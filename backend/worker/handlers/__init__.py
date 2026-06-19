"""Importing this package registers all handlers with the runner registry.

Each submodule calls `register("<job_type>", fn)` at import time. The runner's
`_process_one_job` looks up handlers via `JOB_HANDLERS[job_type]`.
"""
from __future__ import annotations

# Side-effect imports — DO NOT remove. Registration happens on import.
from . import sensitivity  # noqa: F401
from . import simulate_optimize  # noqa: F401
from . import ni43101_export  # noqa: F401
