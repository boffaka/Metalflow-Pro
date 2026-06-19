"""Restore circuit_templates columns dropped by a corrupted 000045 upgrade tail.

Revision ID: 000062
Revises: 000061
"""
from alembic import op

revision = "000062"
down_revision = "000061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE circuit_templates
            ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id),
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS description TEXT
    """)
    op.execute("""
        UPDATE circuit_templates
        SET is_active = TRUE
        WHERE is_active IS NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE circuit_templates
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS is_active,
            DROP COLUMN IF EXISTS created_by
    """)
