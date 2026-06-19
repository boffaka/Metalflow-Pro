"""Tests for LIMS import log recording."""
import os
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

try:
    from routes.lims import _build_import_log_entry
except ImportError:
    from backend.routes.lims import _build_import_log_entry


def test_build_import_log_entry():
    entry = _build_import_log_entry(
        project_id="p1",
        user_id="u1",
        import_type="manual",
        test_type="a1",
        samples_count=5,
        accepted_count=4,
        rejected_count=1,
        rejected_details=[{"sample_id": "S1", "field": "au_g_t", "reason": "negative"}],
    )
    assert entry["project_id"] == "p1"
    assert entry["samples_count"] == 5
    assert entry["rejected_count"] == 1
    assert len(entry["rejected_details"]) == 1


def test_build_import_log_defaults():
    entry = _build_import_log_entry(
        project_id="p1",
        user_id="u1",
        import_type="csv",
        test_type="b1",
        samples_count=10,
        accepted_count=10,
    )
    assert entry["rejected_count"] == 0
    assert entry["rejected_details"] == []
    assert entry["filename"] is None
    assert entry["checksum_sha256"] is None
