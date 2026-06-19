"""simulation_scenarios — study level, tolerance %, grouping for MetPlant-style audit trail.

Revision ID: 000055
Revises: 000054
Create Date: 2026-05-15
"""
from alembic import op


revision = "000055"
down_revision = "000054"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE simulation_scenarios ADD COLUMN IF NOT EXISTS study_level TEXT")
    op.execute(
        "ALTER TABLE simulation_scenarios ADD COLUMN IF NOT EXISTS capex_opex_tolerance_pct NUMERIC"
    )
    op.execute("ALTER TABLE simulation_scenarios ADD COLUMN IF NOT EXISTS scenario_group TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sim_scenarios_group "
        "ON simulation_scenarios(project_id, scenario_group)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_sim_scenarios_group")
    op.execute("ALTER TABLE simulation_scenarios DROP COLUMN IF EXISTS scenario_group")
    op.execute("ALTER TABLE simulation_scenarios DROP COLUMN IF EXISTS capex_opex_tolerance_pct")
    op.execute("ALTER TABLE simulation_scenarios DROP COLUMN IF EXISTS study_level")
