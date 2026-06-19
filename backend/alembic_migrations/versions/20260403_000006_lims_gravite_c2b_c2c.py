from __future__ import annotations

from alembic import op


revision = "20260403_000006"
down_revision = "20260403_000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Extend lims_c2 (Knelson / Falcon Concentrator — GRG) ─────────────────
    op.execute("""
        ALTER TABLE lims_c2
          ADD COLUMN IF NOT EXISTS p80_alim_um                NUMERIC,
          ADD COLUMN IF NOT EXISTS solides_alim_pct           NUMERIC,
          ADD COLUMN IF NOT EXISTS masse_alim_kg              NUMERIC,
          ADD COLUMN IF NOT EXISTS au_alim_g_t                NUMERIC,
          ADD COLUMN IF NOT EXISTS vitesse_knelson_rpm        NUMERIC,
          ADD COLUMN IF NOT EXISTS pression_fluidisation_psi  NUMERIC,
          ADD COLUMN IF NOT EXISTS duree_concentration_min    NUMERIC,
          ADD COLUMN IF NOT EXISTS masse_concentre_g          NUMERIC,
          ADD COLUMN IF NOT EXISTS au_concentre_g_t           NUMERIC,
          ADD COLUMN IF NOT EXISTS au_tail_g_t                NUMERIC,
          ADD COLUMN IF NOT EXISTS grg_rec_pct                NUMERIC
    """)

    # ── B. GRG Progressif — Progressive Gravity Recovery (Multi-stage) ────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS lims_c2b (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id          UUID REFERENCES projects(id) ON DELETE CASCADE,
            sample_id           UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
            p80_alim_um         NUMERIC,
            au_conc_grade_g_t   NUMERIC,
            au_recovery_pct     NUMERIC,
            cumul_recovery_pct  NUMERIC,
            au_tail_g_t         NUMERIC,
            mass_pull_pct       NUMERIC,
            created_at          TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_lims_c2b_project ON lims_c2b(project_id)")

    # ── C. Table à secousses / MGS — Multi-Gravity Separator ──────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS lims_c2c (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
            sample_id               UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
            inclinaison_table_deg   NUMERIC,
            freq_vibration_hz       NUMERIC,
            debit_eau_lavage_l_min  NUMERIC,
            densite_coupure_t_m3    NUMERIC,
            temps_residence_min     NUMERIC,
            vitesse_mgs_rpm         NUMERIC,
            au_alim_g_t             NUMERIC,
            au_conc_g_t             NUMERIC,
            au_tail_g_t             NUMERIC,
            mass_pull_pct           NUMERIC,
            recup_au_pct            NUMERIC,
            created_at              TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_lims_c2c_project ON lims_c2c(project_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lims_c2c")
    op.execute("DROP TABLE IF EXISTS lims_c2b")
    op.execute("""
        ALTER TABLE lims_c2
          DROP COLUMN IF EXISTS p80_alim_um,
          DROP COLUMN IF EXISTS solides_alim_pct,
          DROP COLUMN IF EXISTS masse_alim_kg,
          DROP COLUMN IF EXISTS au_alim_g_t,
          DROP COLUMN IF EXISTS vitesse_knelson_rpm,
          DROP COLUMN IF EXISTS pression_fluidisation_psi,
          DROP COLUMN IF EXISTS duree_concentration_min,
          DROP COLUMN IF EXISTS masse_concentre_g,
          DROP COLUMN IF EXISTS au_concentre_g_t,
          DROP COLUMN IF EXISTS au_tail_g_t,
          DROP COLUMN IF EXISTS grg_rec_pct
    """)
