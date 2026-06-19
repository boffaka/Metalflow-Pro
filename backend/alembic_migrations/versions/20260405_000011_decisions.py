"""decisions table

Revision ID: 20260405_000011
Revises: 20260404_000010
Create Date: 2026-04-05
"""
from alembic import op

revision = '20260405_000011'
down_revision = '20260404_000010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          gate_id      UUID REFERENCES stage_gates(id) ON DELETE SET NULL,
          title        TEXT NOT NULL,
          description  TEXT,
          status       TEXT NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open','accepted','rejected','deferred')),
          decided_by   UUID REFERENCES users(id) ON DELETE SET NULL,
          decided_at   TIMESTAMPTZ,
          created_at   TIMESTAMPTZ DEFAULT now(),
          updated_at   TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS decisions;")
