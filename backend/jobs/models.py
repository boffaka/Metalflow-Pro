"""Plain TypedDicts for job records.

These describe the shape of dicts returned by repo.py — they are NOT
Pydantic models and do not perform validation. Used for type hints
and documentation.
"""
from __future__ import annotations
from typing import Any, Literal, TypedDict


JobKind = Literal[
    "sensitivity_spider",
    "sensitivity_tornado",
    "simulate_optimize",
    "ni43101_export",
]

JobStatus = Literal["queued", "running", "success", "failed", "cancelled"]


class ResultRef(TypedDict, total=False):
    kind: str  # "simulation_run_v2" | "job_artifact"
    id: str | int
    filename: str
    content_type: str


class JobRow(TypedDict):
    id: str
    kind: JobKind
    project_id: str
    created_by: str
    payload: dict[str, Any]
    status: JobStatus
    progress: int
    progress_message: str | None
    result_ref: ResultRef | None
    error: str | None
    cancel_requested: bool
    worker_id: str | None
    last_heartbeat_at: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
