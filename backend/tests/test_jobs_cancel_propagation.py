"""Layer 3 — verifies API cancel → ctx.check_cancelled() → JobCancelled → 'cancelled'."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")


def test_running_handler_observes_cancel_within_one_iteration(client, auth_headers, test_project_id):
    """Submit a long-running job, cancel it after 1 iter, verify it stops.

    We reuse the registered `sensitivity_spider` kind (so the CHECK constraint
    on `jobs.kind` accepts it) but monkey-patch its handler with a slow stand-in
    that polls `ctx.check_cancelled()`. The original handler is restored after
    the test.
    """
    import time
    import uuid as _uuid
    # The runner uses bare imports first ("from worker import registry"),
    # so monkey-patches must land on the same module the loop reads.
    import backend.worker.handlers  # noqa: F401  populate handler registry
    from backend.db import qone
    from worker.registry import register, JOB_HANDLERS
    from worker import loop as worker_loop
    from backend.jobs import repo

    iterations = []

    def slow_handler(payload, ctx):
        for i in range(20):
            ctx.check_cancelled()
            iterations.append(i)
            if i == 0:
                client.post(
                    f"/api/v1/projects/{test_project_id}/jobs/{ctx.job_id}/cancel",
                    headers=auth_headers,
                )
                # Force the next check_cancelled to actually hit the DB.
                ctx._cancel_cached_at_ms = 0.0
                time.sleep(0.05)
        return {"kind": "noop"}

    # Clear any queued leftovers from sibling tests so pickup_one returns ours.
    from backend.db import execute
    execute("UPDATE jobs SET status='cancelled', finished_at=now() WHERE status='queued'")

    original = JOB_HANDLERS.get("sensitivity_spider")
    register("sensitivity_spider", slow_handler)
    try:
        admin = qone("SELECT id FROM users WHERE email = %s",
                     (os.environ.get("ADMIN_EMAIL", "admin@mpdpms.dev"),))
        assert admin, "admin user must exist for this test"

        job_id = repo.insert_job(
            kind="sensitivity_spider",
            project_id=test_project_id,
            created_by=admin["id"],
            payload={},
        )

        worker_id = _uuid.uuid4().hex
        picked = repo.pickup_one(worker_id=worker_id)
        assert picked is not None and str(picked["id"]) == str(job_id)
        worker_loop._process_one_job(picked, worker_id)

        row = qone("SELECT status FROM jobs WHERE id=%s", (job_id,))
        assert row["status"] == "cancelled"
        assert len(iterations) <= 3, f"Handler did not stop quickly: {iterations}"
    finally:
        if original is not None:
            register("sensitivity_spider", original)
        else:
            JOB_HANDLERS.pop("sensitivity_spider", None)
