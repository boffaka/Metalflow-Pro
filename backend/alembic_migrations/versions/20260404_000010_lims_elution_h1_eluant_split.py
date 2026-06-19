from __future__ import annotations

from alembic import op


revision = "20260404_000010"
down_revision = "20260404_000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lims_elution
            DROP COLUMN IF EXISTS eluant,
            DROP COLUMN IF EXISTS concentrations_g_l,
            ADD COLUMN IF NOT EXISTS eluant_cn_g_l   NUMERIC,
            ADD COLUMN IF NOT EXISTS eluant_naoh_g_l NUMERIC
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE lims_elution
            DROP COLUMN IF EXISTS eluant_cn_g_l,
            DROP COLUMN IF EXISTS eluant_naoh_g_l,
            ADD COLUMN IF NOT EXISTS eluant           TEXT,
            ADD COLUMN IF NOT EXISTS concentrations_g_l NUMERIC
    """)
