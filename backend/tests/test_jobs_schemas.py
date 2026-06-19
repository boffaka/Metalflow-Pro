"""Layer 2 — schema validation tests (no FastAPI, no DB)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.routes.jobs_schemas import (
    JobSubmitResponse, JobStatusResponse, JobListItem, ProgressFragment,
)


def test_job_submit_response_round_trip():
    r = JobSubmitResponse(job_id="11111111-1111-1111-1111-111111111111", status="queued")
    assert r.model_dump() == {
        "job_id": "11111111-1111-1111-1111-111111111111", "status": "queued",
    }


def test_job_status_minimum_fields():
    r = JobStatusResponse(
        job_id="11111111-1111-1111-1111-111111111111",
        type="sensitivity_spider",
        status="running",
        progress=ProgressFragment(current=3, total=10, message="p80 +5%"),
    )
    assert r.status == "running"
    assert r.progress.current == 3
    assert r.error is None
    assert r.result_ref is None


def test_job_status_rejects_bad_status():
    with pytest.raises(ValidationError):
        JobStatusResponse(
            job_id="x", type="sensitivity_spider", status="weird",
        )


def test_job_list_item_omits_internal_fields():
    """Listing should not leak heartbeat_at, worker_id, attempts."""
    item = JobListItem(
        job_id="11111111-1111-1111-1111-111111111111",
        type="sensitivity_spider", status="done",
        created_at="2026-04-26T10:00:00Z",
    )
    payload = item.model_dump()
    assert "worker_id" not in payload
    assert "heartbeat_at" not in payload
    assert "attempts" not in payload
