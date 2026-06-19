from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")

from backend.db import get_conn, execute, qone
from backend.worker.handlers.ni43101_export import handle_ni43101_export


class _Ctx:
    def __init__(self, conn, job_id, project_id, user_id):
        self.conn = conn
        self.job_id = job_id
        self.project_id = project_id
        self.user_id = user_id
    def check_cancelled(self) -> None: pass
    def report_progress(self, *a, **k) -> None: pass


@pytest.fixture
def seeded_sections(test_project_id):
    """Insert two NI 43-101 sections so the handler has something to render."""
    ids = []
    for sec_num, key, sort in [(13, "13.1", 1), (17, "17.1", 1)]:
        sid = str(uuid.uuid4())
        execute(
            "INSERT INTO ni43101_sections "
            "(id, project_id, section_number, subsection_key, sort_order, "
            " title_fr, title_en, content_fr, content_en) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, test_project_id, sec_num, key, sort,
             f"Titre {key}", f"Title {key}",
             f"Contenu {key}\n- bullet", f"Content {key}\n- bullet"),
        )
        ids.append(sid)
    yield
    for sid in ids:
        execute("DELETE FROM ni43101_sections WHERE id = %s", (sid,))


def _seed_job(conn, job_id, project_id, user_id, kind):
    """Insert a jobs row so artifact FK is satisfied."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (id, kind, status, project_id, created_by, payload) "
            "VALUES (%s, %s, 'running', %s, %s, '{}'::jsonb)",
            (str(job_id), kind, str(project_id), str(user_id)),
        )


def test_handle_ni43101_export_writes_docx_artifact(test_project_id, seeded_sections):
    user_id = qone("SELECT id FROM users WHERE email = %s",
                   (os.environ["ADMIN_EMAIL"],))["id"]
    job_id = uuid.uuid4()
    with get_conn() as conn:
        _seed_job(conn, job_id, test_project_id, user_id, "ni43101_export")
        ctx = _Ctx(conn, job_id, test_project_id, user_id)
        ref = handle_ni43101_export({"fmt": "docx", "lang": "fr"}, ctx)
        conn.commit()
    assert ref["kind"] == "job_artifact"
    assert ref["filename"].endswith(".docx")
    row = qone(
        "SELECT content_type, filename, byte_size, octet_length(data) AS n "
        "FROM job_artifacts WHERE id = %s", (ref["id"],),
    )
    assert row["content_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert row["byte_size"] == row["n"] > 1000


def test_handle_ni43101_export_writes_pdf_artifact(test_project_id, seeded_sections):
    user_id = qone("SELECT id FROM users WHERE email = %s",
                   (os.environ["ADMIN_EMAIL"],))["id"]
    job_id = uuid.uuid4()
    with get_conn() as conn:
        _seed_job(conn, job_id, test_project_id, user_id, "ni43101_export")
        ctx = _Ctx(conn, job_id, test_project_id, user_id)
        ref = handle_ni43101_export({"fmt": "pdf", "lang": "en"}, ctx)
        conn.commit()
    row = qone(
        "SELECT content_type, octet_length(data) AS n FROM job_artifacts WHERE id = %s",
        (ref["id"],),
    )
    assert row["content_type"] == "application/pdf"
    assert row["n"] > 500


def test_handle_ni43101_export_404_when_no_sections(test_project_id):
    user_id = qone("SELECT id FROM users WHERE email = %s",
                   (os.environ["ADMIN_EMAIL"],))["id"]
    job_id = uuid.uuid4()
    with get_conn() as conn:
        _seed_job(conn, job_id, test_project_id, user_id, "ni43101_export")
        ctx = _Ctx(conn, job_id, test_project_id, user_id)
        with pytest.raises(LookupError):
            handle_ni43101_export({"fmt": "pdf", "lang": "en"}, ctx)
