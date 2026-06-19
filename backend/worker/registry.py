"""Handler registry: kind → callable.

Populated in Chunk 3 as handlers are implemented. The loop reads this
mapping to dispatch jobs to the right handler. Keeping it as a plain
module-level dict (no class) makes test patching trivial.

Handler signature:
    handler(payload: dict, ctx: JobContext) -> ResultRef

ResultRef is a dict like {"kind": "simulation_run_v2", "id": "<uuid>"}
or {"kind": "job_artifact", "id": <int>, "filename": "<name>",
"content_type": "<mime>"}. The loop persists it via repo.set_terminal_success.
"""
from __future__ import annotations
from typing import Any, Callable

JOB_HANDLERS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {}


def register(kind: str, handler: Callable) -> None:
    """Register a handler. Called from each handler module on import."""
    JOB_HANDLERS[kind] = handler


def get(kind: str) -> Callable | None:
    return JOB_HANDLERS.get(kind)
