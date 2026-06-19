"""Soft-delete behavior on circuit_templates."""
import uuid
import pytest
from fastapi.testclient import TestClient

from db import execute, qone


@pytest.fixture
def inline_project_with_template(auth_headers):
    """Minimal inline setup: project + one active circuit_template.
    Cleaned up via ON DELETE CASCADE (projects FK)."""
    pid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    try:
        execute(
            "INSERT INTO projects (id, project_name, project_code) VALUES (%s, %s, %s)",
            (pid, f"TstSD-{pid[:6]}", f"TSD-{pid[:6]}"),
        )
        execute(
            "INSERT INTO circuit_templates (id, project_id, name, is_active) VALUES (%s, %s, %s, TRUE)",
            (tid, pid, "main-template"),
        )
        yield {"project_id": pid, "template_id": tid}
    finally:
        execute("DELETE FROM projects WHERE id = %s", (pid,))


def test_delete_template_marks_inactive_instead_of_removing(
    client: TestClient, auth_headers, inline_project_with_template
):
    pid = inline_project_with_template["project_id"]
    tid = inline_project_with_template["template_id"]

    r = client.delete(f"/api/v1/projects/{pid}/circuit-templates/{tid}", headers=auth_headers)
    assert r.status_code in (200, 204)

    # Row is still there, with is_active=FALSE
    row = qone("SELECT is_active FROM circuit_templates WHERE id = %s", (tid,))
    assert row is not None, "Template row was hard-deleted; expected soft-delete"
    # Accommodate both dict and tuple row styles
    val = row.get("is_active") if isinstance(row, dict) else row[0]
    assert val is False
