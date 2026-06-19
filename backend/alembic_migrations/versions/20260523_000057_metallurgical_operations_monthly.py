"""metallurgical_operations_monthly — monthly actuals vs locked plan.

Revision ID: 000057
Revises: 000056
Create Date: 2026-05-23
"""
from alembic import op


revision = "000057"
down_revision = "000056"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS metallurgical_operations_monthly (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            period_yyyy_mm TEXT NOT NULL,
            actuals_json JSONB NOT NULL DEFAULT '{}',
            variance_json JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (project_id, period_yyyy_mm)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_met_ops_monthly_project_period "
        "ON metallurgical_operations_monthly (project_id, period_yyyy_mm DESC)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_met_ops_monthly_project_period")
    op.execute("DROP TABLE IF EXISTS metallurgical_operations_monthly")
