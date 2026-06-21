"""Contracts for LIMS code-to-table routing."""
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://localhost/test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-at-least-32-chars-long!!")
os.environ.setdefault("ADMIN_EMAIL", "test@test.com")
os.environ.setdefault("ADMIN_PASSWORD", "testpassword123")

try:
    from routes.lims import LIMS_FIELDS, LIMS_TABLES
except ImportError:
    from backend.routes.lims import LIMS_FIELDS, LIMS_TABLES


def test_m1_routes_to_bootstrap_mineralogy_table():
    assert LIMS_TABLES["m1"] == "lims_mineralogy"


def test_c2_knelson_template_columns_are_bootstrapped():
    schema = (Path(__file__).resolve().parents[1] / "schema.sql").read_text()
    for column in LIMS_FIELDS["c2"]:
        if column == "sample_id":
            continue
        assert (
            f"ALTER TABLE IF EXISTS lims_c2 ADD COLUMN IF NOT EXISTS {column}" in schema
            or f"{column} NUMERIC" in schema
        )
