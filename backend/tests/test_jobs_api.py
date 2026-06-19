"""Layer 3 - API tests using TestClient. Worker is NOT running. We drive jobs to
completion by calling _process_one_job directly with the same DB."""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")


def _submit_spider_job(client, auth_headers, pid):
    r = client.post(
        f"/api/v1/projects/{pid}/simulation/sensitivity/spider/async",
        json={
            "base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24},
            "params_to_vary": ["p80"],
            "delta_pcts": [-5.0, 5.0],
        },
        headers=auth_headers,
    )
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


def test_submit_returns_202_and_job_id_uuid(client, auth_headers, test_project_id):
    job_id = _submit_spider_job(client, auth_headers, test_project_id)
    uuid.UUID(job_id)  # raises if not a valid UUID


def test_get_status_for_queued_job(client, auth_headers, test_project_id):
    job_id = _submit_spider_job(client, auth_headers, test_project_id)
    r = client.get(f"/api/v1/projects/{test_project_id}/jobs/{job_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == job_id
    assert body["status"] == "queued"
    assert body["type"] == "sensitivity_spider"
    assert body["error"] is None


def test_get_status_404_for_other_project(client, auth_headers, test_project_id):
    """Job belongs to project A; querying it via project B's URL must 404."""
    job_id = _submit_spider_job(client, auth_headers, test_project_id)
    other_pid = "00000000-0000-0000-0000-000000000000"
    r = client.get(f"/api/v1/projects/{other_pid}/jobs/{job_id}", headers=auth_headers)
    assert r.status_code == 404


def test_list_jobs_returns_recent_first(client, auth_headers, test_project_id):
    a = _submit_spider_job(client, auth_headers, test_project_id)
    b = _submit_spider_job(client, auth_headers, test_project_id)
    r = client.get(f"/api/v1/projects/{test_project_id}/jobs", headers=auth_headers)
    assert r.status_code == 200
    ids = [j["job_id"] for j in r.json()["jobs"]]
    assert ids[0] == b and ids[1] == a


def test_cancel_queued_job_marks_cancelled(client, auth_headers, test_project_id):
    job_id = _submit_spider_job(client, auth_headers, test_project_id)
    r = client.post(f"/api/v1/projects/{test_project_id}/jobs/{job_id}/cancel",
                    headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"
    # Status endpoint reflects it
    r2 = client.get(f"/api/v1/projects/{test_project_id}/jobs/{job_id}", headers=auth_headers)
    assert r2.json()["status"] == "cancelled"


def test_cancel_already_finished_job_is_noop(client, auth_headers, test_project_id):
    """If the job is already done, cancel returns 200 but status stays done.
    DB stores 'success'; the API surface translates it to 'done' on read.
    """
    from backend.db import execute
    job_id = _submit_spider_job(client, auth_headers, test_project_id)
    execute("UPDATE jobs SET status='success', finished_at=NOW() WHERE id=%s", (job_id,))
    r = client.post(f"/api/v1/projects/{test_project_id}/jobs/{job_id}/cancel",
                    headers=auth_headers)
    assert r.status_code == 200
    r2 = client.get(f"/api/v1/projects/{test_project_id}/jobs/{job_id}", headers=auth_headers)
    assert r2.json()["status"] == "done"


def test_artifact_download_404_if_no_artifact(client, auth_headers, test_project_id):
    job_id = _submit_spider_job(client, auth_headers, test_project_id)
    r = client.get(f"/api/v1/projects/{test_project_id}/jobs/{job_id}/artifact",
                   headers=auth_headers)
    assert r.status_code == 404


def test_progress_endpoint_after_handler_runs(client, auth_headers, test_project_id):
    """Submit, pickup, drive to completion via _process_one_job, then fetch status."""
    from backend.db import execute
    from backend.jobs import repo
    from backend.worker.loop import _process_one_job
    import backend.worker.handlers  # noqa: F401  trigger registration

    # Clear queue to isolate this test from prior submissions in the same session.
    execute("UPDATE jobs SET status='cancelled', finished_at=now() WHERE status='queued'")
    job_id = _submit_spider_job(client, auth_headers, test_project_id)
    worker_id = "test-runner"
    picked = repo.pickup_one(worker_id=worker_id)
    assert picked is not None and str(picked["id"]) == str(job_id)
    _process_one_job(picked, worker_id)

    r = client.get(f"/api/v1/projects/{test_project_id}/jobs/{job_id}", headers=auth_headers)
    body = r.json()
    assert body["status"] == "done"
    assert body["result_ref"] is not None
    assert body["result_ref"]["kind"] == "simulation_run_v2"
    assert body["progress"]["current"] == body["progress"]["total"] > 0


def test_submit_tornado_returns_202(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/simulation/sensitivity/tornado/async",
        json={
            "base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24},
            "params_to_vary": ["p80"],
            "delta_pcts": [10.0],
        },
        headers=auth_headers,
    )
    assert r.status_code == 202
    job = client.get(
        f"/api/v1/projects/{test_project_id}/jobs/{r.json()['job_id']}",
        headers=auth_headers,
    ).json()
    assert job["type"] == "sensitivity_tornado"


def test_submit_optimize_returns_202(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/simulation/optimize/async",
        json={"base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24}},
        headers=auth_headers,
    )
    assert r.status_code == 202
    job = client.get(
        f"/api/v1/projects/{test_project_id}/jobs/{r.json()['job_id']}",
        headers=auth_headers,
    ).json()
    assert job["type"] == "simulate_optimize"


def test_submit_rejects_empty_params_to_vary(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/simulation/sensitivity/spider/async",
        json={
            "base_params": {"p80": 75},
            "params_to_vary": [],
            "delta_pcts": [-5.0, 5.0],
        },
        headers=auth_headers,
    )
    assert r.status_code == 400


#
# test_legacy_sync_endpoint_emits_deprecation_header — REMOVED 2026-05-06
# It verified that POST /simulation/optimize (deprecated=True) emitted the
# Deprecation/Sunset headers. The endpoint itself has been removed; the test
# is no longer applicable. The async replacement POST /simulation/optimize/async
# is exercised by `test_submit_optimize_returns_202` above.


def test_submit_ni43101_export_returns_202(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/ni43101/export/docx/fr/async",
        headers=auth_headers,
    )
    assert r.status_code == 202
    job = client.get(
        f"/api/v1/projects/{test_project_id}/jobs/{r.json()['job_id']}",
        headers=auth_headers,
    ).json()
    assert job["type"] == "ni43101_export"


def test_submit_ni43101_export_rejects_bad_fmt(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/ni43101/export/xls/fr/async",
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_submit_ni43101_export_rejects_bad_lang(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/ni43101/export/pdf/es/async",
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_artifact_download_after_export_handler_runs(client, auth_headers, test_project_id):
    """Submit export, seed sections, pickup, drive worker, download artifact."""
    from backend.db import execute
    from backend.jobs import repo
    from backend.worker.loop import _process_one_job
    import backend.worker.handlers  # noqa: F401
    import uuid as _u

    # Clear queue so pickup_one returns the test's job, not a leftover.
    execute("UPDATE jobs SET status='cancelled', finished_at=now() WHERE status='queued'")

    sid = str(_u.uuid4())
    execute(
        "INSERT INTO ni43101_sections "
        "(id, project_id, section_number, subsection_key, sort_order, "
        " title_fr, title_en, content_fr, content_en) "
        "VALUES (%s, %s, 13, '13.1', 1, 'T', 'T', 'C', 'C')",
        (sid, test_project_id),
    )
    try:
        r = client.post(
            f"/api/v1/projects/{test_project_id}/ni43101/export/pdf/en/async",
            headers=auth_headers,
        )
        job_id = r.json()["job_id"]

        worker_id = "test-runner"
        picked = repo.pickup_one(worker_id=worker_id)
        assert picked is not None and str(picked["id"]) == str(job_id)
        _process_one_job(picked, worker_id)

        status = client.get(
            f"/api/v1/projects/{test_project_id}/jobs/{job_id}",
            headers=auth_headers,
        ).json()
        assert status["status"] == "done"
        assert status["result_ref"]["kind"] == "job_artifact"

        d = client.get(
            f"/api/v1/projects/{test_project_id}/jobs/{job_id}/artifact",
            headers=auth_headers,
        )
        assert d.status_code == 200
        assert d.headers["content-type"] == "application/pdf"
        # RFC 6266: ascii fallback `filename="…"` plus `filename*=UTF-8''…` extension.
        assert ".pdf" in d.headers["content-disposition"]
        assert 'attachment; filename="' in d.headers["content-disposition"]
        assert d.content[:4] == b"%PDF"
    finally:
        execute("DELETE FROM ni43101_sections WHERE id = %s", (sid,))


def test_list_jobs_filtered_by_status(client, auth_headers, test_project_id):
    from backend.db import execute

    a = _submit_spider_job(client, auth_headers, test_project_id)
    b = _submit_spider_job(client, auth_headers, test_project_id)
    # DB stores 'success'; the API translates to 'done' on read.
    execute("UPDATE jobs SET status='success', finished_at=NOW() WHERE id=%s", (a,))

    r = client.get(
        f"/api/v1/projects/{test_project_id}/jobs?status=done",
        headers=auth_headers,
    )
    ids = {j["job_id"] for j in r.json()["jobs"]}
    assert a in ids and b not in ids

    r2 = client.get(
        f"/api/v1/projects/{test_project_id}/jobs?status=queued",
        headers=auth_headers,
    )
    ids2 = {j["job_id"] for j in r2.json()["jobs"]}
    assert b in ids2 and a not in ids2


def test_list_jobs_rejects_invalid_status(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/jobs?status=weird",
        headers=auth_headers,
    )
    assert r.status_code == 400


def test_legacy_ni43101_export_emits_deprecation_header(client, auth_headers, test_project_id):
    from backend.db import execute
    import uuid as _u
    sid = str(_u.uuid4())
    execute(
        "INSERT INTO ni43101_sections "
        "(id, project_id, section_number, subsection_key, sort_order, "
        " title_fr, title_en, content_fr, content_en) "
        "VALUES (%s, %s, 13, '13.1', 1, 'T', 'T', 'C', 'C')",
        (sid, test_project_id),
    )
    try:
        r = client.get(
            f"/api/v1/projects/{test_project_id}/ni43101/export/pdf/en",
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.headers.get("Deprecation") == "true"
    finally:
        execute("DELETE FROM ni43101_sections WHERE id = %s", (sid,))
