"""Add provenance payloads to mass balance v2 streams.

Revision ID: 000049
Revises: 000048
Create Date: 2026-05-11
"""
from alembic import op

revision = "000049"
down_revision = "000048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE mass_balance_streams_v2
            ADD COLUMN IF NOT EXISTS extras JSONB DEFAULT '{}'::jsonb
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mass_balance_streams_v2 DROP COLUMN IF EXISTS extras")
