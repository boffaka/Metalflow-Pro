from __future__ import annotations

from pathlib import Path

from alembic import op


revision = "20260402_000001"
down_revision = None
branch_labels = None
depends_on = None


def _schema_sql() -> str:
    return (Path(__file__).resolve().parents[2] / "schema.sql").read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute(_schema_sql())


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS public CASCADE")
    op.execute("CREATE SCHEMA public")
    op.execute("GRANT ALL ON SCHEMA public TO postgres")
    op.execute("GRANT ALL ON SCHEMA public TO public")
