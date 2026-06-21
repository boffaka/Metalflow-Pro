"""Contracts for LIMS code-to-table routing."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

try:
    from routes.lims import LIMS_TABLES
except ImportError:
    from backend.routes.lims import LIMS_TABLES


def test_m1_routes_to_bootstrap_mineralogy_table():
    assert LIMS_TABLES["m1"] == "lims_mineralogy"
