"""lims_statistics_and_economics — New endpoints support tables

Adds:
  - lims_import_log.checksum_sha256 column (if missing)
  - economic_indicators.payback_years column (if missing)
  - dcf_models.sensitivity_data JSONB column (if missing)
  - Index on lims_import_log(project_id, created_at) for statistics queries
  - Index on economic_indicators(project_id, computed_at) for dashboard

Revision ID: 000041
Revises: 000040
Create Date: 2026-05-03
"""
from alembic import op

revision = "000041"
down_revision = "000040"
branch_labels = None
depends_on = None


def upgrade():
    # lims_import_log — add checksum column if not present
    op.execute("""
        ALTER TABLE lims_import_log
            ADD COLUMN IF NOT EXISTS checksum_sha256 TEXT
    """)

    # economic_indicators — add payback_years if not present
    op.execute("""
        ALTER TABLE economic_indicators
            ADD COLUMN IF NOT EXISTS payback_years NUMERIC
    """)

    # dcf_models — add sensitivity_data JSONB for tornado chart storage
    op.execute("""
        ALTER TABLE dcf_models
            ADD COLUMN IF NOT EXISTS sensitivity_data JSONB
    """)

    # Performance indexes for new statistics endpoints
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lims_import_log_project_created
            ON lims_import_log(project_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_economic_indicators_project_computed
            ON economic_indicators(project_id, created_at DESC)
    """)

    # Partial index for active LIMS samples (used by statistics queries)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lims_d1_project_recovery
            ON lims_d1(project_id, au_recovery_pct)
            WHERE au_recovery_pct IS NOT NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lims_b1_project_bwi
            ON lims_b1(project_id, bwi_kwh_t)
            WHERE bwi_kwh_t IS NOT NULL
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_lims_b1_project_bwi")
    op.execute("DROP INDEX IF EXISTS idx_lims_d1_project_recovery")
    op.execute("DROP INDEX IF EXISTS idx_economic_indicators_project_computed")
    op.execute("DROP INDEX IF EXISTS idx_lims_import_log_project_created")
    op.execute("ALTER TABLE dcf_models DROP COLUMN IF EXISTS sensitivity_data")
    op.execute("ALTER TABLE economic_indicators DROP COLUMN IF EXISTS payback_years")
    op.execute("ALTER TABLE lims_import_log DROP COLUMN IF EXISTS checksum_sha256")
