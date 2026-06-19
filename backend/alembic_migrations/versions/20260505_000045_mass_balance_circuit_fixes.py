"""mass_balance_circuit_fixes — Fix all mass balance and circuit schema gaps

Adds missing columns to:
  mass_balance_streams_v2  — section_id FK, slurry_pct_w, au_gt, s_pct,
                             is_balance_check, is_recirculation, source
  circuit_templates        — created_by, is_active, description
  lims_environmental       — sort_order
  lims_dtx                 — sort_order

Seeds unit_operations_catalog with 60 standard gold plant operations.

Revision ID: 000045
Revises: 000044
Create Date: 2026-05-05
"""
from alembic import op

revision = "000045"
down_revision = "000044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── mass_balance_streams_v2 ───────────────────────────────────────────────
    op.execute("""
        ALTER TABLE mass_balance_streams_v2
            ADD COLUMN IF NOT EXISTS section_id UUID
                REFERENCES mass_balance_sections_v2(id) ON DELETE CASCADE,
            ADD COLUMN IF NOT EXISTS slurry_pct_w NUMERIC,
            ADD COLUMN IF NOT EXISTS au_gt NUMERIC,
            ADD COLUMN IF NOT EXISTS s_pct NUMERIC,
            ADD COLUMN IF NOT EXISTS is_balance_check BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS is_recirculation BOOLEAN DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS source TEXT
    """)
    # Make template_id and section nullable (section_id FK is the real reference)
    op.execute("ALTER TABLE mass_balance_streams_v2 ALTER COLUMN template_id DROP NOT NULL")
    op.execute("ALTER TABLE mass_balance_streams_v2 ALTER COLUMN section DROP NOT NULL")
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mb_streams_section_id
            ON mass_balance_streams_v2(section_id)
    """)

    # ── projects missing columns ──────────────────────────────────────────────
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS ore_sg NUMERIC DEFAULT 2.75")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS plant_throughput_tpd NUMERIC")
    op.execute("UPDATE projects SET ore_sg = 2.75 WHERE ore_sg IS NULL")

    # ── circuit_templates ─────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE circuit_templates
            ADD COLUMN IF NOT EXISTS created_by UUID REFERENCES users(id),
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE,
            ADD COLUMN IF NOT EXISTS description TEXT
    """)

    # ── lims sort_order ───────────────────────────────────────────────────────
    op.execute("ALTER TABLE lims_environmental ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0")
    op.execute("ALTER TABLE lims_dtx ADD COLUMN IF NOT EXISTS sort_order INTEGER DEFAULT 0")

    # ── Seed unit_operations_catalog ──────────────────────────────────────────
    # Import and run the catalog seed function
    try:
        import sys
        from pathlib import Path
        backend_dir = Path(__file__).resolve().parents[2]
        if str(backend_dir) not in sys.path:
            sys.path.insert(0, str(backend_dir))
        from engines.circuit_catalog import seed_catalog
        conn = op.get_bind()
        seed_catalog(conn)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("catalog seed skipped: %s", e)
    op.execute("DROP INDEX IF EXISTS idx_mb_streams_section_id")
    op.execute("""
        ALTER TABLE mass_balance_streams_v2
            DROP COLUMN IF EXISTS section_id,
            DROP COLUMN IF EXISTS slurry_pct_w,
            DROP COLUMN IF EXISTS au_gt,
            DROP COLUMN IF EXISTS s_pct,
            DROP COLUMN IF EXISTS is_balance_check,
            DROP COLUMN IF EXISTS is_recirculation,
            DROP COLUMN IF EXISTS source
    """)
    op.execute("""
        ALTER TABLE circuit_templates
            DROP COLUMN IF EXISTS created_by,
            DROP COLUMN IF EXISTS is_active,
            DROP COLUMN IF EXISTS description
    """)
    op.execute("ALTER TABLE lims_environmental DROP COLUMN IF EXISTS sort_order")
    op.execute("ALTER TABLE lims_dtx DROP COLUMN IF EXISTS sort_order")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mb_streams_section_id")
    op.execute("""
        ALTER TABLE mass_balance_streams_v2
            DROP COLUMN IF EXISTS section_id,
            DROP COLUMN IF EXISTS slurry_pct_w,
            DROP COLUMN IF EXISTS au_gt,
            DROP COLUMN IF EXISTS s_pct,
            DROP COLUMN IF EXISTS is_balance_check,
            DROP COLUMN IF EXISTS is_recirculation,
            DROP COLUMN IF EXISTS source
    """)
    op.execute("""
        ALTER TABLE circuit_templates
            DROP COLUMN IF EXISTS created_by,
            DROP COLUMN IF EXISTS is_active,
            DROP COLUMN IF EXISTS description
    """)
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS ore_sg")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS plant_throughput_tpd")
    op.execute("ALTER TABLE lims_environmental DROP COLUMN IF EXISTS sort_order")
    op.execute("ALTER TABLE lims_dtx DROP COLUMN IF EXISTS sort_order")
