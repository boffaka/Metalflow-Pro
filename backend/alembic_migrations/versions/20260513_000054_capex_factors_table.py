"""Create capex_factors table for CAPEX module.

The capex.py route references this table for indirect/EPCM/contingency
factor percentages. It was never created by a migration.

Revision ID: 000054
Revises: 000053
Create Date: 2026-05-13
"""
from alembic import op

revision = "000054"
down_revision = "000053"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS capex_factors (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id                  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            indirect_pct                NUMERIC NOT NULL DEFAULT 15.0,
            epcm_pct                    NUMERIC NOT NULL DEFAULT 12.0,
            contingency_pct             NUMERIC NOT NULL DEFAULT 20.0,
            is_overridden_indirect      BOOLEAN DEFAULT FALSE,
            is_overridden_epcm          BOOLEAN DEFAULT FALSE,
            is_overridden_contingency   BOOLEAN DEFAULT FALSE,
            created_at                  TIMESTAMPTZ DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(project_id)
        );
    """)
    # Seed default factors for existing projects that don't have them
    op.execute("""
        INSERT INTO capex_factors (project_id, indirect_pct, epcm_pct, contingency_pct)
        SELECT id, 15.0, 12.0, 20.0 FROM projects
        WHERE id NOT IN (SELECT project_id FROM capex_factors)
        ON CONFLICT (project_id) DO NOTHING;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS capex_factors CASCADE;")
