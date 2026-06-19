# backend/alembic_migrations/versions/20260602_000069_geomet_intelligence_imbo.py
"""Intelligence Géométallurgique — tables IMBO (blend_constraints,
blend_optimization_sessions, blend_sources, blend_optimization_results).

Depends on migration 000068 (geomet_intelligence_prd) — blend_optimization_sessions
references prd_analyses, and blend_sources references geomet_domains (from 000067).
"""
revision = "000069"
down_revision = "000068"
revises = "000068"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS blend_constraints (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id       UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name             TEXT NOT NULL,
            constraint_type  TEXT,
            parameter        TEXT NOT NULL,
            operator         TEXT NOT NULL CHECK (operator IN ('lte','gte','eq','between')),
            value            FLOAT NOT NULL,
            value_max        FLOAT,
            unit             TEXT NOT NULL DEFAULT '',
            severity         TEXT NOT NULL DEFAULT 'hard' CHECK (severity IN ('hard','soft')),
            penalty_per_unit FLOAT,
            description      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_blend_constraints_project ON blend_constraints(project_id);

        CREATE TABLE IF NOT EXISTS blend_optimization_sessions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            prd_analysis_id UUID REFERENCES prd_analyses(id),
            name            TEXT NOT NULL,
            target_year     INTEGER,
            target_variable TEXT NOT NULL DEFAULT 'maximize_au_oz'
                            CHECK (target_variable IN (
                                'maximize_au_oz','maximize_recovery',
                                'minimize_opex','maximize_npv'
                            )),
            gold_price      FLOAT NOT NULL DEFAULT 3200,
            status          TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','running','completed','failed')),
            celery_task_id  TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_blend_opt_sessions_project ON blend_optimization_sessions(project_id);

        CREATE TABLE IF NOT EXISTS blend_sources (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id               UUID NOT NULL REFERENCES blend_optimization_sessions(id) ON DELETE CASCADE,
            label                    TEXT NOT NULL,
            domain_id                UUID REFERENCES geomet_domains(id),
            tonnage_available        FLOAT NOT NULL DEFAULT 0,
            tonnage_min              FLOAT,
            tonnage_max              FLOAT,
            au_grade                 FLOAT NOT NULL DEFAULT 0,
            bwi                      FLOAT NOT NULL DEFAULT 14,
            s_sulphide               FLOAT NOT NULL DEFAULT 1,
            cu_ppm                   FLOAT NOT NULL DEFAULT 50,
            carbon_pct               FLOAT NOT NULL DEFAULT 0,
            preg_robbing_index       FLOAT NOT NULL DEFAULT 0,
            predicted_recovery       FLOAT NOT NULL DEFAULT 89,
            predicted_cn_consumption FLOAT NOT NULL DEFAULT 0.5,
            mining_cost_per_tonne    FLOAT NOT NULL DEFAULT 0,
            haulage_cost_per_tonne   FLOAT NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_blend_sources_session ON blend_sources(session_id);

        CREATE TABLE IF NOT EXISTS blend_optimization_results (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id              UUID NOT NULL UNIQUE REFERENCES blend_optimization_sessions(id) ON DELETE CASCADE,
            solver                  TEXT,
            status                  TEXT NOT NULL CHECK (status IN ('optimal','infeasible','unbounded','timeout')),
            solve_time_ms           FLOAT,
            optimal_allocation      JSONB NOT NULL DEFAULT '[]',
            blended_properties      JSONB NOT NULL DEFAULT '{}',
            predicted_recovery      FLOAT,
            predicted_gold_oz       FLOAT,
            predicted_opex_per_tonne FLOAT,
            objective_value         FLOAT,
            constraint_analysis     JSONB NOT NULL DEFAULT '[]',
            vs_baseline             JSONB NOT NULL DEFAULT '{}'
        );
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS blend_optimization_results CASCADE;
        DROP TABLE IF EXISTS blend_sources CASCADE;
        DROP TABLE IF EXISTS blend_optimization_sessions CASCADE;
        DROP TABLE IF EXISTS blend_constraints CASCADE;
    """)
