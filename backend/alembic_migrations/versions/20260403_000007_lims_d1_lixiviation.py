from __future__ import annotations

from alembic import op


revision = "20260403_000007"
down_revision = "20260403_000006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lims_d1
          ADD COLUMN IF NOT EXISTS au_leach_feed_g_t   NUMERIC,
          ADD COLUMN IF NOT EXISTS p80_alim_um          NUMERIC,
          ADD COLUMN IF NOT EXISTS solides_pulpe_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS nacn_initiale_ppm    NUMERIC,
          ADD COLUMN IF NOT EXISTS ph_initial           NUMERIC,
          ADD COLUMN IF NOT EXISTS ph_final             NUMERIC,
          ADD COLUMN IF NOT EXISTS o2_dissous_mg_l      NUMERIC,
          ADD COLUMN IF NOT EXISTS o2_consumption_kg_t  NUMERIC,
          ADD COLUMN IF NOT EXISTS temperature_c        NUMERIC,
          ADD COLUMN IF NOT EXISTS densite_solide_sg    NUMERIC,
          ADD COLUMN IF NOT EXISTS leach_rec_8h_pct     NUMERIC,
          ADD COLUMN IF NOT EXISTS leach_rec_24h_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS leach_rec_32h_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS leach_rec_48h_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS au_tail_g_t          NUMERIC
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE lims_d1
          DROP COLUMN IF EXISTS au_leach_feed_g_t,
          DROP COLUMN IF EXISTS p80_alim_um,
          DROP COLUMN IF EXISTS solides_pulpe_pct,
          DROP COLUMN IF EXISTS nacn_initiale_ppm,
          DROP COLUMN IF EXISTS ph_initial,
          DROP COLUMN IF EXISTS ph_final,
          DROP COLUMN IF EXISTS o2_dissous_mg_l,
          DROP COLUMN IF EXISTS o2_consumption_kg_t,
          DROP COLUMN IF EXISTS temperature_c,
          DROP COLUMN IF EXISTS densite_solide_sg,
          DROP COLUMN IF EXISTS leach_rec_8h_pct,
          DROP COLUMN IF EXISTS leach_rec_24h_pct,
          DROP COLUMN IF EXISTS leach_rec_32h_pct,
          DROP COLUMN IF EXISTS leach_rec_48h_pct,
          DROP COLUMN IF EXISTS au_tail_g_t
    """)
