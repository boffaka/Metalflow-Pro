"""Pydantic models for the /jobs API surface."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]


class JobSubmitResponse(BaseModel):
    job_id: str
    status: JobStatus = "queued"


class ProgressFragment(BaseModel):
    current: int = 0
    total: int = 0
    message: Optional[str] = None


class JobStatusResponse(BaseModel):
    job_id: str
    type: str
    status: JobStatus
    progress: ProgressFragment = Field(default_factory=ProgressFragment)
    result_ref: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class JobListItem(BaseModel):
    job_id: str
    type: str
    status: JobStatus
    created_at: str
    finished_at: Optional[str] = None


class JobListResponse(BaseModel):
    jobs: list[JobListItem]
