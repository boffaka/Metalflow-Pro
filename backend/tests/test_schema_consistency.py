"""Guard-rail: every ORM-declared table must exist in the Alembic schema.

Run via `python scripts/check_schema_consistency.py` for a richer report;
this test wraps it for pytest/CI.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "backend" / "scripts" / "check_schema_consistency.py"


@pytest.mark.skipif(not SCRIPT.exists(), reason="consistency script missing")
def test_orm_tables_match_alembic():
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--quiet"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"ORM/Alembic schema drift detected:\n{proc.stderr}\n"
        "Either ship an Alembic migration for the new ORM table, or remove "
        "the orphan __tablename__ from backend/orm_models/."
    )
