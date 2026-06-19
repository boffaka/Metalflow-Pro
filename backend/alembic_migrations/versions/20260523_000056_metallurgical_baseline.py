"""project_metallurgical_baseline — locked PFS / feasibility plan for Décideur Métallurgique.

Revision ID: 000056
Revises: 000055
Create Date: 2026-05-23
"""
from alembic import op


revision = "000056"
down_revision = "000055"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS project_metallurgical_baseline (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            source_run_id UUID REFERENCES simulation_runs_v2(id) ON DELETE SET NULL,
            mode TEXT NOT NULL CHECK (mode IN ('study_lock', 'feasibility_adopted')),
            levers_json JSONB NOT NULL DEFAULT '{}',
            kpis_p50_json JSONB NOT NULL DEFAULT '{}',
            locked_by UUID,
            locked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            notes TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_met_baseline_active_project
        ON project_metallurgical_baseline (project_id)
        WHERE is_active = TRUE
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_met_baseline_project "
        "ON project_metallurgical_baseline (project_id, locked_at DESC)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_met_baseline_project")
    op.execute("DROP INDEX IF EXISTS uq_met_baseline_active_project")
    op.execute("DROP TABLE IF EXISTS project_metallurgical_baseline")
