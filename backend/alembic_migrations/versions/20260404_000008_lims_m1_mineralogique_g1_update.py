from __future__ import annotations

from alembic import op


revision = "20260404_000008"
down_revision = "20260403_000007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── M1 : Analyse Minéralogique (QEMSCAN / MLA Modal Mineralogy) ──────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS lims_m1 (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
            sample_id               UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
            pyrite_pct              NUMERIC,
            pyrrhotite_pct          NUMERIC,
            other_sulphides_pct     NUMERIC,
            quartz_pct              NUMERIC,
            plagioclase_pct         NUMERIC,
            k_feldspar_pct          NUMERIC,
            kaolinite_pct           NUMERIC,
            other_silicates_pct     NUMERIC,
            k_other_pct             NUMERIC,
            muscovite_illite_pct    NUMERIC,
            ca_minerals_pct         NUMERIC,
            fe_oxides_pct           NUMERIC,
            ilmenite_pct            NUMERIC,
            ti_oxides_pct           NUMERIC,
            other_oxides_pct        NUMERIC,
            carbonates_pct          NUMERIC,
            apatite_pct             NUMERIC,
            other_pct               NUMERIC,
            au_free_pct             NUMERIC,
            created_at              TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_lims_m1_project ON lims_m1(project_id)")

    # ── G1 : Flottation — add new columns to lims_flotation ──────────────────
    op.execute("""
        ALTER TABLE lims_flotation
            ADD COLUMN IF NOT EXISTS au_alim_g_t        NUMERIC,
            ADD COLUMN IF NOT EXISTS p80_alim_um         NUMERIC,
            ADD COLUMN IF NOT EXISTS concentrate_wt_pct  NUMERIC,
            ADD COLUMN IF NOT EXISTS au_concentrate_g_t  NUMERIC,
            ADD COLUMN IF NOT EXISTS au_tail_g_t         NUMERIC,
            ADD COLUMN IF NOT EXISTS temps_total_min     NUMERIC,
            ADD COLUMN IF NOT EXISTS collecteur_g_t      NUMERIC,
            ADD COLUMN IF NOT EXISTS moussant_g_t        NUMERIC,
            ADD COLUMN IF NOT EXISTS depressant_g_t      NUMERIC,
            ADD COLUMN IF NOT EXISTS recup_s_pct         NUMERIC
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE lims_flotation
            DROP COLUMN IF EXISTS au_alim_g_t,
            DROP COLUMN IF EXISTS p80_alim_um,
            DROP COLUMN IF EXISTS concentrate_wt_pct,
            DROP COLUMN IF EXISTS au_concentrate_g_t,
            DROP COLUMN IF EXISTS au_tail_g_t,
            DROP COLUMN IF EXISTS temps_total_min,
            DROP COLUMN IF EXISTS collecteur_g_t,
            DROP COLUMN IF EXISTS moussant_g_t,
            DROP COLUMN IF EXISTS depressant_g_t,
            DROP COLUMN IF EXISTS recup_s_pct
    """)
    op.execute("DROP TABLE IF EXISTS lims_m1")
