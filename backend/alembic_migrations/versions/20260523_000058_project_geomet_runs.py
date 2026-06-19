"""project_geomet_runs — persisted GMIE GADE results (JSONB snapshot).

Revision ID: 000058
Revises: 000057
Create Date: 2026-05-23
"""
from alembic import op


revision = "000058"
down_revision = "000057"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_geomet_runs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            config_json JSONB NOT NULL DEFAULT '{}',
            result_json JSONB NOT NULL DEFAULT '{}',
            computed_by UUID REFERENCES users(id) ON DELETE SET NULL,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_geomet_run_active_project
        ON project_geomet_runs (project_id)
        WHERE is_active = TRUE
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_geomet_runs_project "
        "ON project_geomet_runs (project_id, computed_at DESC)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_geomet_runs_project")
    op.execute("DROP INDEX IF EXISTS uq_geomet_run_active_project")
    op.execute("DROP TABLE IF EXISTS project_geomet_runs")
