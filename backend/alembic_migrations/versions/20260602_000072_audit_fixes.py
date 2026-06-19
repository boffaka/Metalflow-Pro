# backend/alembic_migrations/versions/20260602_000072_audit_fixes.py
"""Audit fixes — index FK, CHECK constraints, simulation_runs_v2 status.

FIX v2 (2026-06-03):
- Remplacer CREATE INDEX directs par DO $$ ... $$ conditionnels pour les tables
  qui n'existent que dans schema.sql (lims_kinetics, lims_granulometry, etc.)
- Corriger lims_dtx vs lims_detox (les deux cas couverts)
"""
revision = "000072"
down_revision = "000071"
revises = "000071"

from alembic import op


def upgrade():
    # Tables garanties d'exister via Alembic : index directs
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lims_c2_sample        ON lims_c2(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_c3_sample        ON lims_c3(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_d1_sample        ON lims_d1(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_e1_sample        ON lims_e1(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_e2_sample        ON lims_e2(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_flotation_sample ON lims_flotation(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_elution_sample   ON lims_elution(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_environmental_sample ON lims_environmental(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_a2_sample        ON lims_a2(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_a3_sample        ON lims_a3(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_c2b_sample       ON lims_c2b(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_c2c_sample       ON lims_c2c(sample_id);
        CREATE INDEX IF NOT EXISTS idx_lims_m1_sample        ON lims_m1(sample_id);
    """)

    # Tables qui existent SEULEMENT via schema.sql ou certaines migrations :
    # utiliser DO $$ ... $$ pour vérifier l'existence avant de créer l'index.
    op.execute("""
        DO $$
        BEGIN
            -- lims_dtx (créé dans schema.sql) ou lims_detox (créé dans migration 000029)
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lims_dtx') THEN
                CREATE INDEX IF NOT EXISTS idx_lims_dtx_sample ON lims_dtx(sample_id);
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lims_detox') THEN
                CREATE INDEX IF NOT EXISTS idx_lims_detox_sample ON lims_detox(sample_id);
            END IF;

            -- Tables présentes uniquement dans schema.sql (bootstrap)
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lims_kinetics') THEN
                CREATE INDEX IF NOT EXISTS idx_lims_kinetics_sample ON lims_kinetics(sample_id);
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lims_granulometry') THEN
                CREATE INDEX IF NOT EXISTS idx_lims_granulometry_sample ON lims_granulometry(sample_id);
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lims_liberation') THEN
                CREATE INDEX IF NOT EXISTS idx_lims_liberation_sample ON lims_liberation(sample_id);
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'lims_mineralogy') THEN
                CREATE INDEX IF NOT EXISTS idx_lims_mineralogy_sample ON lims_mineralogy(sample_id);
            END IF;
        END
        $$;
    """)

    # simulation_runs_v2 : colonnes manquantes depuis schema.sql
    op.execute("""
        ALTER TABLE simulation_runs_v2 ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'queued';
        ALTER TABLE simulation_runs_v2 ADD COLUMN IF NOT EXISTS n_simulations INTEGER;
        ALTER TABLE simulation_runs_v2 ADD COLUMN IF NOT EXISTS progress_pct  FLOAT DEFAULT 0;
        ALTER TABLE simulation_runs_v2 ADD COLUMN IF NOT EXISTS started_at    TIMESTAMPTZ;
        ALTER TABLE simulation_runs_v2 ADD COLUMN IF NOT EXISTS completed_at  TIMESTAMPTZ;
    """)

    # annual_metallurgical_predictions : colonnes utilisées dans geomet_v2.py mais absentes de 000070
    op.execute("""
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS domain_mix JSONB DEFAULT '{}';
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_gold_oz_p10 FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_gold_oz_p90 FLOAT;
    """)

    # CHECK constraints sur champs status critiques
    op.execute("""
        ALTER TABLE simulation_runs_v2 DROP CONSTRAINT IF EXISTS chk_simv2_status;
        ALTER TABLE simulation_runs_v2 ADD CONSTRAINT chk_simv2_status
            CHECK (status IN ('queued','running','completed','failed','cancelled'));

        ALTER TABLE prd_analyses DROP CONSTRAINT IF EXISTS chk_prd_status;
        ALTER TABLE prd_analyses ADD CONSTRAINT chk_prd_status
            CHECK (status IN ('pending','running','completed','failed'));

        ALTER TABLE blend_sessions_v2 DROP CONSTRAINT IF EXISTS chk_blend_status;
    """)

    # Supprimer colonne dc_snapshots.data orpheline (si elle existe encore)
    op.execute("ALTER TABLE dc_snapshots DROP COLUMN IF EXISTS data;")


def downgrade():
    op.execute("""
        ALTER TABLE simulation_runs_v2 DROP CONSTRAINT IF EXISTS chk_simv2_status;
        ALTER TABLE prd_analyses DROP CONSTRAINT IF EXISTS chk_prd_status;
    """)
