"""flowsheet_tree — extend circuit_template_operations for per-project flowsheet

Adds:
  - parent_op_id : tree topology (FK self, ON DELETE CASCADE)
  - node_label   : custom display name
  - product_kind : 'bullion' | 'tailings' | 'concentrate' | NULL (leaf marker)
  - recovery_pct, throughput_tph, water_m3h, grade_au_gt : per-node metrics
  - values_source : 'manual' | 'lims_auto' (set automatically by API)
  - index on parent_op_id

Revision ID: 000038
Revises: 000037
Create Date: 2026-04-29
"""
from alembic import op


revision = "000038"
down_revision = "000037"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE circuit_template_operations
          ADD COLUMN IF NOT EXISTS parent_op_id   UUID NULL
              REFERENCES circuit_template_operations(id) ON DELETE CASCADE,
          ADD COLUMN IF NOT EXISTS node_label     TEXT NULL,
          ADD COLUMN IF NOT EXISTS product_kind   TEXT NULL
              CHECK (product_kind IS NULL OR product_kind IN ('bullion','tailings','concentrate')),
          ADD COLUMN IF NOT EXISTS recovery_pct   NUMERIC(6,3) NULL
              CHECK (recovery_pct IS NULL OR (recovery_pct >= 0 AND recovery_pct <= 100)),
          ADD COLUMN IF NOT EXISTS throughput_tph NUMERIC(10,2) NULL
              CHECK (throughput_tph IS NULL OR throughput_tph >= 0),
          ADD COLUMN IF NOT EXISTS water_m3h      NUMERIC(10,2) NULL
              CHECK (water_m3h IS NULL OR water_m3h >= 0),
          ADD COLUMN IF NOT EXISTS grade_au_gt    NUMERIC(8,3) NULL
              CHECK (grade_au_gt IS NULL OR grade_au_gt >= 0),
          ADD COLUMN IF NOT EXISTS values_source  TEXT NOT NULL DEFAULT 'manual'
              CHECK (values_source IN ('manual','lims_auto'))
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_cto_parent ON circuit_template_operations(parent_op_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_cto_parent")
    op.execute("""
        ALTER TABLE circuit_template_operations
          DROP COLUMN IF EXISTS parent_op_id,
          DROP COLUMN IF EXISTS node_label,
          DROP COLUMN IF EXISTS product_kind,
          DROP COLUMN IF EXISTS recovery_pct,
          DROP COLUMN IF EXISTS throughput_tph,
          DROP COLUMN IF EXISTS water_m3h,
          DROP COLUMN IF EXISTS grade_au_gt,
          DROP COLUMN IF EXISTS values_source
    """)
