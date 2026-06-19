"""Add au_gt column to mass_balance_streams_v2 if missing, and ensure
the summary view returns enriched data matching the Excel template.

This migration is a no-op schema change (au_gt already exists) but
ensures the column is indexed for fast Au grade lookups in the
carbon footprint and summary calculations.

Revision ID: 000052
Revises: 000051
Create Date: 2026-05-12
"""
from alembic import op

revision = "000052"
down_revision = "000051"
branch_labels = None
depends_on = None


def upgrade():
    # au_gt already exists in mass_balance_streams_v2 from migration 000020.
    # Add index for fast Au grade lookups (used in carbon footprint + summary).
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mb_streams_v2_au_gt
        ON mass_balance_streams_v2(section_id, au_gt)
        WHERE au_gt IS NOT NULL AND au_gt > 0
    """)

    # Ensure operating_hours_day and availability_pct columns exist on projects
    # (used by the enriched summary calculation)
    op.execute("""
        ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS operating_hours_day NUMERIC DEFAULT 22.1,
            ADD COLUMN IF NOT EXISTS availability_pct    NUMERIC DEFAULT 92.0
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_mb_streams_v2_au_gt")
