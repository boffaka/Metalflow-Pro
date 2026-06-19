"""simulation_redesign — equipment_id on circuit_template_operations + simulation_node_outputs

Adds:
  - circuit_template_operations.equipment_id (UUID FK, nullable)
  - simulation_node_outputs (per-run, per-node calculated metrics)

Revision ID: 000039
Revises: 000038
Create Date: 2026-04-29
"""
from alembic import op


revision = "000039"
down_revision = "000038"
branch_labels = None
depends_on = None


def upgrade():
    # ── Equipment linkage on flowsheet nodes ──────────────────────────────
    op.execute("""
        ALTER TABLE circuit_template_operations
          ADD COLUMN IF NOT EXISTS equipment_id UUID NULL
            REFERENCES equipment(id) ON DELETE SET NULL
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_cto_equipment ON circuit_template_operations(equipment_id)")

    # ── Per-run, per-node calculated metrics ──────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS simulation_node_outputs (
          id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          run_id       UUID NOT NULL REFERENCES simulation_runs_v2(id) ON DELETE CASCADE,
          operation_id UUID NOT NULL REFERENCES circuit_template_operations(id) ON DELETE CASCADE,
          metric_key   TEXT NOT NULL,
          value_num    NUMERIC NULL,
          value_unit   TEXT NULL,
          computed_at  TIMESTAMPTZ DEFAULT NOW(),
          UNIQUE (run_id, operation_id, metric_key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_sno_run_op ON simulation_node_outputs(run_id, operation_id)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS simulation_node_outputs")
    op.execute("DROP INDEX IF EXISTS idx_cto_equipment")
    op.execute("ALTER TABLE circuit_template_operations DROP COLUMN IF EXISTS equipment_id")
