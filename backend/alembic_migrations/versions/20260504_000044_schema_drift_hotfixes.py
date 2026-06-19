"""schema_drift_hotfixes — Fix all column gaps found during production debugging

Adds missing columns to tables that were created by schema.sql with different
column sets than what the code expects. All ADD COLUMN IF NOT EXISTS — fully
idempotent, safe to run on any DB state.

Tables fixed:
  design_criteria       — nominal, min_val, max_val, comments, is_header
  ard_classifications   — computed_at, mitigation_strategy
  economic_indicators   — computed_at, cash_cost_usd_oz, margin_pct
  simulation_runs_v2    — duration_s

Revision ID: 000044
Revises: 000043
Create Date: 2026-05-04
"""
from alembic import op

revision = "000044"
down_revision = "000043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── design_criteria ──────────────────────────────────────────────────────
    op.execute("ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS nominal NUMERIC")
    op.execute("ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS min_val NUMERIC")
    op.execute("ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS max_val NUMERIC")
    op.execute("ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS comments TEXT")
    op.execute("ALTER TABLE design_criteria ADD COLUMN IF NOT EXISTS is_header BOOLEAN DEFAULT FALSE")

    # ── ard_classifications ───────────────────────────────────────────────────
    op.execute("ALTER TABLE ard_classifications ADD COLUMN IF NOT EXISTS computed_at TIMESTAMPTZ DEFAULT NOW()")
    op.execute("ALTER TABLE ard_classifications ADD COLUMN IF NOT EXISTS mitigation_strategy TEXT")
    # Sync computed_at with created_at for existing rows
    op.execute("UPDATE ard_classifications SET computed_at = created_at WHERE computed_at IS NULL")

    # ── economic_indicators ───────────────────────────────────────────────────
    op.execute("ALTER TABLE economic_indicators ADD COLUMN IF NOT EXISTS computed_at TIMESTAMPTZ DEFAULT NOW()")
    op.execute("ALTER TABLE economic_indicators ADD COLUMN IF NOT EXISTS cash_cost_usd_oz NUMERIC")
    op.execute("ALTER TABLE economic_indicators ADD COLUMN IF NOT EXISTS margin_pct NUMERIC")
    op.execute("UPDATE economic_indicators SET computed_at = created_at WHERE computed_at IS NULL")

    # ── simulation_runs_v2 ────────────────────────────────────────────────────
    op.execute("ALTER TABLE simulation_runs_v2 ADD COLUMN IF NOT EXISTS duration_s NUMERIC")


def downgrade() -> None:
    op.execute("ALTER TABLE simulation_runs_v2 DROP COLUMN IF EXISTS duration_s")
    op.execute("ALTER TABLE economic_indicators DROP COLUMN IF EXISTS margin_pct")
    op.execute("ALTER TABLE economic_indicators DROP COLUMN IF EXISTS cash_cost_usd_oz")
    op.execute("ALTER TABLE economic_indicators DROP COLUMN IF EXISTS computed_at")
    op.execute("ALTER TABLE ard_classifications DROP COLUMN IF EXISTS mitigation_strategy")
    op.execute("ALTER TABLE ard_classifications DROP COLUMN IF EXISTS computed_at")
    op.execute("ALTER TABLE design_criteria DROP COLUMN IF EXISTS is_header")
    op.execute("ALTER TABLE design_criteria DROP COLUMN IF EXISTS comments")
    op.execute("ALTER TABLE design_criteria DROP COLUMN IF EXISTS max_val")
    op.execute("ALTER TABLE design_criteria DROP COLUMN IF EXISTS min_val")
    op.execute("ALTER TABLE design_criteria DROP COLUMN IF EXISTS nominal")
