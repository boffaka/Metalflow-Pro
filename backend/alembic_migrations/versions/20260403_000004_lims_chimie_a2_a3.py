from __future__ import annotations

from alembic import op


revision = "20260403_000004"
down_revision = "20260402_000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extend lims_a1 with XRF / LECO / Hg columns ──────────────────────────
    op.execute("""
        ALTER TABLE lims_a1
          ADD COLUMN IF NOT EXISTS hg_ppm     NUMERIC,
          ADD COLUMN IF NOT EXISTS sio2_pct   NUMERIC,
          ADD COLUMN IF NOT EXISTS al2o3_pct  NUMERIC,
          ADD COLUMN IF NOT EXISTS cao_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS mgo_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS na2o_pct   NUMERIC,
          ADD COLUMN IF NOT EXISTS k2o_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS tio2_pct   NUMERIC,
          ADD COLUMN IF NOT EXISTS mno_pct    NUMERIC,
          ADD COLUMN IF NOT EXISTS loi_pct    NUMERIC
    """)

    # ── B. Analyse Granulométrique ────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS lims_a2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
            sample_id       UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
            p80_um                  NUMERIC,
            d50_um                  NUMERIC,
            ret_plus500_pct         NUMERIC,
            ret_plus212_pct         NUMERIC,
            ret_plus150_pct         NUMERIC,
            ret_plus106_pct         NUMERIC,
            ret_plus75_pct          NUMERIC,
            ret_plus53_pct          NUMERIC,
            ret_plus38_pct          NUMERIC,
            ret_minus38_pct         NUMERIC,
            au_head_g_t             NUMERIC,
            au_plus212_g_t          NUMERIC,
            au_plus75_g_t           NUMERIC,
            au_minus38_g_t          NUMERIC,
            au_dist_plus212_pct     NUMERIC,
            au_dist_plus75_pct      NUMERIC,
            au_dist_minus38_pct     NUMERIC,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_lims_a2_project ON lims_a2(project_id)")

    # ── C. Analyse de Libération de l'Or (MLA) ────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS lims_a3 (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id                  UUID REFERENCES projects(id) ON DELETE CASCADE,
            sample_id                   UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
            p80_broyage_um              NUMERIC,
            au_libre_pct                NUMERIC,
            au_assoc_sulfures_pct       NUMERIC,
            au_assoc_silicates_pct      NUMERIC,
            au_assoc_oxydes_pct         NUMERIC,
            au_occlus_pct               NUMERIC,
            au_preg_rob_pct             NUMERIC,
            created_at                  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_lims_a3_project ON lims_a3(project_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lims_a3")
    op.execute("DROP TABLE IF EXISTS lims_a2")
    op.execute("""
        ALTER TABLE lims_a1
          DROP COLUMN IF EXISTS hg_ppm,
          DROP COLUMN IF EXISTS sio2_pct,
          DROP COLUMN IF EXISTS al2o3_pct,
          DROP COLUMN IF EXISTS cao_pct,
          DROP COLUMN IF EXISTS mgo_pct,
          DROP COLUMN IF EXISTS na2o_pct,
          DROP COLUMN IF EXISTS k2o_pct,
          DROP COLUMN IF EXISTS tio2_pct,
          DROP COLUMN IF EXISTS mno_pct,
          DROP COLUMN IF EXISTS loi_pct
    """)
