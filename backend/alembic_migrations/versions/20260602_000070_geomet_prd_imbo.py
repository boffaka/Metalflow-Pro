# backend/alembic_migrations/versions/20260602_000070_geomet_prd_imbo.py
"""Intelligence Géométallurgique v2 — tables PRD et IMBO v2.

prd_analyses et annual_metallurgical_predictions existent déjà depuis 000068.
Cette migration ajoute les colonnes manquantes via ADD COLUMN IF NOT EXISTS
et crée blend_sessions_v2 (nouvelle table).

Depends on migration 000069 (geomet_intelligence_imbo).
"""
revision = "000070"
down_revision = "000069"
revises = "000069"

from alembic import op


def upgrade():
    op.execute("""
        -- prd_analyses existe depuis 000068 (mine_plan_id NOT NULL, domaining_session_id NOT NULL).
        -- Rendre les colonnes legacy optionnelles pour le v2 (pas de mine plan requis).
        ALTER TABLE prd_analyses ALTER COLUMN mine_plan_id DROP NOT NULL;
        ALTER TABLE prd_analyses ALTER COLUMN domaining_session_id DROP NOT NULL;
        -- Ajouter les colonnes v2 manquantes.
        ALTER TABLE prd_analyses ADD COLUMN IF NOT EXISTS gade_session_id UUID REFERENCES gade_sessions(id) ON DELETE SET NULL;
        ALTER TABLE prd_analyses ADD COLUMN IF NOT EXISTS monte_carlo_runs INTEGER NOT NULL DEFAULT 500;
        ALTER TABLE prd_analyses ADD COLUMN IF NOT EXISTS thresholds JSONB NOT NULL DEFAULT '{"recovery_min_pct": 85, "bwi_max_kwh_t": 18, "cn_max_kg_t": 1.0}';
        ALTER TABLE prd_analyses ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
        CREATE INDEX IF NOT EXISTS idx_prd_analyses_session ON prd_analyses(gade_session_id);

        -- annual_metallurgical_predictions existe depuis 000068.
        -- Ajouter les colonnes v2 avec nommage explicite.
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS blended_feed_grade_g_t FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS blended_bwi_kwh_t FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS blended_s_sulphide_pct FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS blended_cu_ppm FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_recovery_p50 FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_recovery_p10 FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_recovery_p90 FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_gold_oz_p50 FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_cn_kg_t FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS predicted_opex_per_t FLOAT;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS is_critical BOOLEAN NOT NULL DEFAULT false;
        ALTER TABLE annual_metallurgical_predictions ADD COLUMN IF NOT EXISTS critical_reasons JSONB NOT NULL DEFAULT '[]';

        -- blend_sessions_v2 est une nouvelle table.
        CREATE TABLE IF NOT EXISTS blend_sessions_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            prd_analysis_id UUID REFERENCES prd_analyses(id) ON DELETE SET NULL,
            name            TEXT NOT NULL,
            gold_price      FLOAT NOT NULL DEFAULT 3200,
            target_variable TEXT NOT NULL DEFAULT 'maximize_au_oz',
            sources         JSONB NOT NULL DEFAULT '[]',
            constraints     JSONB NOT NULL DEFAULT '[]',
            result          JSONB NOT NULL DEFAULT '{}',
            solve_time_ms   FLOAT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_blend_v2_project ON blend_sessions_v2(project_id);
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS blend_sessions_v2 CASCADE;
        ALTER TABLE annual_metallurgical_predictions DROP COLUMN IF EXISTS is_critical;
        ALTER TABLE annual_metallurgical_predictions DROP COLUMN IF EXISTS critical_reasons;
        ALTER TABLE annual_metallurgical_predictions DROP COLUMN IF EXISTS predicted_recovery_p50;
        ALTER TABLE annual_metallurgical_predictions DROP COLUMN IF EXISTS predicted_recovery_p10;
        ALTER TABLE annual_metallurgical_predictions DROP COLUMN IF EXISTS predicted_recovery_p90;
        ALTER TABLE annual_metallurgical_predictions DROP COLUMN IF EXISTS predicted_gold_oz_p50;
        ALTER TABLE annual_metallurgical_predictions DROP COLUMN IF EXISTS predicted_cn_kg_t;
        ALTER TABLE prd_analyses DROP COLUMN IF EXISTS gade_session_id;
        ALTER TABLE prd_analyses DROP COLUMN IF EXISTS thresholds;
    """)
