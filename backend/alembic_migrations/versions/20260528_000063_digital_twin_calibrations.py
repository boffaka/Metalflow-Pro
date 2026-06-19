"""digital_twin_calibrations — calibration history for digital twin module

Adds:
  - digital_twin_calibrations table for tracking model recalibration events

Revision ID: 000063
Revises: 000062
Create Date: 2026-05-28
"""
from alembic import op


revision = "000063"
down_revision = "000062"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS digital_twin_calibrations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            trigger VARCHAR(20) NOT NULL,
            fidelity_before FLOAT NOT NULL,
            fidelity_after FLOAT NOT NULL,
            success BOOLEAN NOT NULL,
            parameters_adjusted JSONB,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dtc_project_id ON digital_twin_calibrations(project_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_dtc_created_at ON digital_twin_calibrations(created_at)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_dtc_created_at")
    op.execute("DROP INDEX IF EXISTS idx_dtc_project_id")
    op.execute("DROP TABLE IF EXISTS digital_twin_calibrations")
