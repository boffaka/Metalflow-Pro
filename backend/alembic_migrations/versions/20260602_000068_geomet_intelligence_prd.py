# backend/alembic_migrations/versions/20260602_000068_geomet_intelligence_prd.py
"""Intelligence Géométallurgique — tables PRD (mine_plans, annual_mining_schedules,
prd_analyses, annual_metallurgical_predictions, critical_periods, lom_summaries).

Depends on migration 000067 (geomet_intelligence_gade) — prd_analyses references gade_sessions.
"""
revision = "000068"
down_revision = "000067"
revises = "000067"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS mine_plans (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            mine_life_years INTEGER NOT NULL DEFAULT 15,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_mine_plans_project ON mine_plans(project_id);

        CREATE TABLE IF NOT EXISTS annual_mining_schedules (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            mine_plan_id      UUID NOT NULL REFERENCES mine_plans(id) ON DELETE CASCADE,
            year              INTEGER NOT NULL,
            total_ore_mined   FLOAT NOT NULL DEFAULT 0,
            total_waste_mined FLOAT NOT NULL DEFAULT 0,
            strip_ratio       FLOAT NOT NULL DEFAULT 0,
            block_ids_mined   JSONB NOT NULL DEFAULT '[]',
            feed_to_plant     FLOAT NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_ams_mine_plan ON annual_mining_schedules(mine_plan_id);

        CREATE TABLE IF NOT EXISTS prd_analyses (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id           UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            mine_plan_id         UUID NOT NULL REFERENCES mine_plans(id),
            domaining_session_id UUID NOT NULL REFERENCES gade_sessions(id),
            name                 TEXT NOT NULL,
            status               TEXT NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending','running','completed','failed')),
            monte_carlo_runs     INTEGER NOT NULL DEFAULT 500,
            celery_task_id       TEXT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_prd_analyses_project ON prd_analyses(project_id);

        CREATE TABLE IF NOT EXISTS annual_metallurgical_predictions (
            id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prd_analysis_id            UUID NOT NULL REFERENCES prd_analyses(id) ON DELETE CASCADE,
            year                       INTEGER NOT NULL,
            domain_mix                 JSONB NOT NULL DEFAULT '[]',
            blended_feed_grade         FLOAT,
            blended_bwi                FLOAT,
            blended_s_sulphide         FLOAT,
            blended_cu_ppm             FLOAT,
            blended_carbon_pct         FLOAT,
            blended_preg_robbing_index FLOAT,
            predicted_recovery         FLOAT,
            recovery_ci                JSONB,
            predicted_cn_consumption   FLOAT,
            predicted_lime_consumption FLOAT,
            predicted_grind_target_p80 FLOAT,
            predicted_sag_energy       FLOAT,
            predicted_total_opex       FLOAT,
            predicted_gold_produced_oz FLOAT,
            predicted_gold_oz_p10      FLOAT,
            predicted_gold_oz_p90      FLOAT,
            predicted_residue_grade    FLOAT
        );
        CREATE INDEX IF NOT EXISTS idx_amp_analysis ON annual_metallurgical_predictions(prd_analysis_id);

        CREATE TABLE IF NOT EXISTS critical_periods (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prd_analysis_id          UUID NOT NULL REFERENCES prd_analyses(id) ON DELETE CASCADE,
            year_start               INTEGER NOT NULL,
            year_end                 INTEGER NOT NULL,
            severity                 TEXT NOT NULL
                                     CHECK (severity IN ('low','medium','high','critical')),
            trigger_type             TEXT NOT NULL,
            description              TEXT,
            predicted_recovery_drop  FLOAT,
            economic_impact_musd     FLOAT,
            recommended_actions      JSONB NOT NULL DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_critical_periods_analysis ON critical_periods(prd_analysis_id);

        CREATE TABLE IF NOT EXISTS lom_summaries (
            id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            prd_analysis_id       UUID NOT NULL UNIQUE REFERENCES prd_analyses(id) ON DELETE CASCADE,
            total_ore_processed   FLOAT,
            total_gold_produced_oz FLOAT,
            average_recovery_lom  FLOAT,
            recovery_range        JSONB,
            total_cn_consumption  FLOAT,
            total_opex_musd       FLOAT,
            n_critical_periods    INTEGER,
            worst_year            INTEGER,
            best_year             INTEGER
        );
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS lom_summaries CASCADE;
        DROP TABLE IF EXISTS critical_periods CASCADE;
        DROP TABLE IF EXISTS annual_metallurgical_predictions CASCADE;
        DROP TABLE IF EXISTS prd_analyses CASCADE;
        DROP TABLE IF EXISTS annual_mining_schedules CASCADE;
        DROP TABLE IF EXISTS mine_plans CASCADE;
    """)
