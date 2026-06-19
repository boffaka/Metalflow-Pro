"""working_capital table

Revision ID: 20260405_000014
Revises: 20260405_000013
Create Date: 2026-04-05
"""
from alembic import op

revision = '20260405_000014'
down_revision = '20260405_000013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS working_capital (
          id                        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id                UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          receivable_days           INTEGER NOT NULL DEFAULT 30 CHECK (receivable_days >= 0),
          inventory_days            INTEGER NOT NULL DEFAULT 45 CHECK (inventory_days >= 0),
          payable_days              INTEGER NOT NULL DEFAULT 30 CHECK (payable_days >= 0),
          other_current_assets      NUMERIC(14,2) NOT NULL DEFAULT 0,
          other_current_liabilities NUMERIC(14,2) NOT NULL DEFAULT 0,
          currency                  TEXT NOT NULL DEFAULT 'USD',
          updated_at                TIMESTAMPTZ DEFAULT now(),
          UNIQUE (project_id)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS working_capital;")
