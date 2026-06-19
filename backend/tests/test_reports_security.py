"""
Security tests for backend/routes/reports.py
Covers:
  - Path traversal in download route (file_path from DB escapes project dir)
  - Symlink traversal in download route
  - Filename truncation in upload route
"""
from __future__ import annotations

import os
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure backend/ is on sys.path (conftest.py:41 already does this when run via pytest,
# but we guard here for direct invocation)
_backend_dir = Path(__file__).resolve().parent.parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

# ---------------------------------------------------------------------------
# App / router import — we need the FastAPI app (or at least the router) plus
# a test client that doesn't require a live DB.
# ---------------------------------------------------------------------------
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production")
os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/mpdpms_test")

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Import the router under test
try:
    from backend.routes.reports import router, _project_dir, _uploads_dir
    import backend.routes.reports as reports_mod
except ImportError:
    from routes.reports import router, _project_dir, _uploads_dir
    import routes.reports as reports_mod


def _make_app_with_fake_auth() -> FastAPI:
    """Create a minimal FastAPI app with the reports router and a fake auth dependency."""
    app = FastAPI()

    # Override project_user dependency so we don't need a real DB/JWT
    fake_user = {"id": "user-1", "email": "test@example.com", "role": "Project Manager"}

    try:
        from backend.auth import project_user
    except ImportError:
        from auth import project_user

    app.dependency_overrides[project_user] = lambda: fake_user
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# Test 1: path traversal via file_path stored in DB
# ---------------------------------------------------------------------------

def test_download_path_traversal_blocked():
    """
    If the DB row contains a file_path that resolves outside the project dir
    (e.g. /etc/passwd), the download route must return 403 — not serve the file.
    """
    app = _make_app_with_fake_auth()
    client = TestClient(app, raise_server_exceptions=False)

    pid = "proj-123"
    rid = "report-abc"

    # The malicious row that a tampered DB (or SQL-injection) might return
    evil_row = {"filename": "evil.txt", "file_path": "/etc/passwd"}

    with patch.object(reports_mod, "qone", return_value=evil_row):
        response = client.get(f"/api/v1/projects/{pid}/reports/{rid}/download")

    assert response.status_code == 403, (
        f"Expected 403 for path traversal, got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# Test 2: symlink traversal — symlink inside allowed dir points outside
# ---------------------------------------------------------------------------

def test_download_symlink_traversal_blocked():
    """
    A symlink inside the project directory that resolves to a path outside it
    (classic symlink traversal) must also be blocked with 403.
    """
    app = _make_app_with_fake_auth()
    client = TestClient(app, raise_server_exceptions=False)

    pid = "proj-symlink"
    rid = "report-sym"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a "project" directory inside tmpdir
        project_dir = Path(tmpdir) / "uploads" / pid
        project_dir.mkdir(parents=True)

        # Create a real file outside the project directory
        outside_file = Path(tmpdir) / "secret.txt"
        outside_file.write_text("TOP SECRET")

        # Create a symlink inside the project dir that points to the outside file
        symlink_path = project_dir / "legit_looking.pdf"
        symlink_path.symlink_to(outside_file)

        # Patch _uploads_dir to return our tmpdir/uploads
        uploads_base = Path(tmpdir) / "uploads"

        def fake_uploads_dir():
            return uploads_base

        evil_row = {"filename": "legit_looking.pdf", "file_path": str(symlink_path)}

        original_uploads_dir = reports_mod._uploads_dir

        def _patched_uploads_dir():
            return uploads_base

        with patch.object(reports_mod, "qone", return_value=evil_row), \
             patch.object(reports_mod, "_UPLOADS_DIR", uploads_base):
            reports_mod._uploads_dir = _patched_uploads_dir
            try:
                response = client.get(f"/api/v1/projects/{pid}/reports/{rid}/download")
            finally:
                reports_mod._uploads_dir = original_uploads_dir

    assert response.status_code == 403, (
        f"Expected 403 for symlink traversal, got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# Test 3: filename truncation in upload route
# ---------------------------------------------------------------------------

def test_upload_filename_truncated():
    """
    Uploading a file with a 200-character filename must result in the stored
    filename being truncated to at most 100 characters.
    """
    app = _make_app_with_fake_auth()
    client = TestClient(app, raise_server_exceptions=False)

    pid = "proj-upload"
    long_name = "A" * 200 + ".pdf"  # 204 chars total

    with tempfile.TemporaryDirectory() as tmpdir:
        uploads_base = Path(tmpdir) / "uploads"

        def _patched_uploads_dir():
            return uploads_base

        # We capture what execute() is called with so we can inspect the stored filename
        stored_filename = []

        def fake_execute(sql, params=None):
            # The INSERT call has safe_name at params[2]
            if params and len(params) > 2 and "INSERT INTO project_reports" in sql:
                stored_filename.append(params[2])
            return None

        original_uploads_dir = reports_mod._uploads_dir
        reports_mod._uploads_dir = _patched_uploads_dir
        try:
            with patch.object(reports_mod, "execute", side_effect=fake_execute):
                file_content = b"fake pdf content"
                response = client.post(
                    f"/api/v1/projects/{pid}/reports",
                    data={
                        "title": "Test Report",
                        "phase": "pfs",
                        "report_type": "interne",
                        "description": "",
                        "author": "Tester",
                    },
                    files={"file": (long_name, BytesIO(file_content), "application/pdf")},
                )
        finally:
            reports_mod._uploads_dir = original_uploads_dir

    # The route should succeed (201) or fail at DB level (500 because fake_execute
    # returns None not a real row) — what matters is the filename was truncated.
    # If execute didn't raise, we got a stored filename to check.
    assert stored_filename, (
        "execute() was never called — cannot verify truncation "
        f"(response status: {response.status_code})"
    )
    assert len(stored_filename[0]) <= 100, (
        f"Filename not truncated: {len(stored_filename[0])} chars — '{stored_filename[0]}'"
    )
