"""Smoke test for the `dag_key` column on `design_criteria_v2`.

Chunk 1.5.A — Living Circuit precondition (audit U1 + Option A).

The migration `20260507_000047_add_dag_key_to_design_criteria_v2` adds an
explicit `dag_key TEXT NULL` column so the cascade engine can map a row to a
DAG node without relying on the (non-matching) normalized `ref_number`.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


_TEST_DB_URL = os.getenv("TEST_DATABASE_URL")


def _connect():
    """Open a short-lived psycopg2 connection to the test DB."""
    import psycopg2  # imported lazily so the test file imports cleanly without psycopg2
    return psycopg2.connect(_TEST_DB_URL)


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping DB schema check")
def test_design_criteria_v2_has_dag_key_column():
    """`design_criteria_v2.dag_key` is a nullable TEXT column after migration."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'design_criteria_v2' AND column_name = 'dag_key'
            """
        )
        row = cur.fetchone()
        assert row is not None, "dag_key column should exist on design_criteria_v2"
        assert row[1] == "text", f"expected text, got {row[1]}"
        assert row[2] == "YES", "dag_key should be nullable"
    finally:
        conn.close()


@pytest.mark.skipif(not _TEST_DB_URL, reason="TEST_DATABASE_URL not set; skipping DB schema check")
def test_design_criteria_v2_has_dag_key_index():
    """A partial index over (template_id, dag_key) speeds up cascade lookups."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'design_criteria_v2' AND indexname = 'ix_dcv2_dag_key'
            """
        )
        assert cur.fetchone() is not None, "ix_dcv2_dag_key index should exist"
    finally:
        conn.close()
