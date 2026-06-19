"""rampup_factors table

Revision ID: 20260405_000013
Revises: 20260405_000012
Create Date: 2026-04-05
"""
from alembic import op

revision = '20260405_000013'
down_revision = '20260405_000012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS rampup_factors (
          id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          month       INTEGER NOT NULL CHECK (month BETWEEN 1 AND 60),
          factor_pct  NUMERIC(5,2) NOT NULL CHECK (factor_pct BETWEEN 0 AND 100),
          notes       TEXT,
          UNIQUE (project_id, month)
        );
        CREATE INDEX IF NOT EXISTS idx_rampup_factors_project ON rampup_factors(project_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rampup_factors;")
