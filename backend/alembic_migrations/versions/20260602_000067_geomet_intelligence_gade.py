# backend/alembic_migrations/versions/20260602_000067_geomet_intelligence_gade.py
"""Intelligence Géométallurgique — tables GADE (gade_sessions, geomet_domains,
recovery_models, block_domain_assignments).

Depends on migration 000066 (sim_module_v2).
"""
revision = "000067"
down_revision = "000066"
revises = "000066"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS gade_sessions (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id           UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name                 TEXT NOT NULL,
            algorithm            TEXT NOT NULL CHECK (algorithm IN ('kmeans','gmm','hdbscan','hierarchical')),
            n_domains_requested  INTEGER,
            n_domains_found      INTEGER,
            features_used        JSONB NOT NULL DEFAULT '[]',
            feature_weights      JSONB,
            normalization        TEXT NOT NULL DEFAULT 'robust'
                                 CHECK (normalization IN ('zscore','minmax','robust')),
            n_samples_used       INTEGER,
            silhouette_score     FLOAT,
            davies_bouldin_score FLOAT,
            status               TEXT NOT NULL DEFAULT 'pending'
                                 CHECK (status IN ('pending','running','completed','failed','archived')),
            celery_task_id       TEXT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at         TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_gade_sessions_project ON gade_sessions(project_id);

        CREATE TABLE IF NOT EXISTS geomet_domains (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id              UUID REFERENCES gade_sessions(id) ON DELETE CASCADE,
            project_id              UUID REFERENCES projects(id) ON DELETE CASCADE,
            domain_code             TEXT NOT NULL,
            label                   TEXT,
            color                   TEXT NOT NULL DEFAULT '#0D9488',
            n_samples               INTEGER NOT NULL DEFAULT 0,
            pct_of_total            FLOAT NOT NULL DEFAULT 0,
            statistics              JSONB NOT NULL DEFAULT '{}',
            metallurgical_signature JSONB NOT NULL DEFAULT '{}',
            discriminating_features JSONB NOT NULL DEFAULT '[]'
        );
        -- geomet_domains peut exister depuis schema.sql sans session_id — ajouter si absent
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES gade_sessions(id) ON DELETE CASCADE;
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS label TEXT;
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS color TEXT NOT NULL DEFAULT '#0D9488';
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS n_samples INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS pct_of_total FLOAT NOT NULL DEFAULT 0;
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS statistics JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS metallurgical_signature JSONB NOT NULL DEFAULT '{}';
        ALTER TABLE geomet_domains ADD COLUMN IF NOT EXISTS discriminating_features JSONB NOT NULL DEFAULT '[]';
        CREATE INDEX IF NOT EXISTS idx_geomet_domains_session ON geomet_domains(session_id);
        CREATE INDEX IF NOT EXISTS idx_geomet_domains_project ON geomet_domains(project_id);

        CREATE TABLE IF NOT EXISTS recovery_models (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            domain_id           UUID NOT NULL REFERENCES geomet_domains(id) ON DELETE CASCADE,
            model_type          TEXT NOT NULL CHECK (model_type IN ('random_forest','xgboost','gradient_boosting')),
            target_variable     TEXT NOT NULL,
            input_features      JSONB NOT NULL DEFAULT '[]',
            training_samples    INTEGER,
            test_r2             FLOAT,
            test_rmse           FLOAT,
            test_mae            FLOAT,
            cross_val_scores    JSONB,
            feature_importances JSONB,
            model_artifact_path TEXT,
            trained_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_active           BOOLEAN NOT NULL DEFAULT TRUE
        );
        CREATE INDEX IF NOT EXISTS idx_recovery_models_domain ON recovery_models(domain_id);

        CREATE TABLE IF NOT EXISTS block_domain_assignments (
            id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id               UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            session_id               UUID NOT NULL REFERENCES gade_sessions(id) ON DELETE CASCADE,
            block_id                 UUID NOT NULL,
            domain_id                UUID NOT NULL REFERENCES geomet_domains(id) ON DELETE CASCADE,
            confidence_score         FLOAT NOT NULL DEFAULT 0,
            predicted_recovery       FLOAT,
            predicted_cn_consumption FLOAT,
            predicted_bwi            FLOAT,
            prediction_uncertainty   FLOAT
        );
        CREATE INDEX IF NOT EXISTS idx_bda_project ON block_domain_assignments(project_id);
        CREATE INDEX IF NOT EXISTS idx_bda_session ON block_domain_assignments(session_id);
        CREATE INDEX IF NOT EXISTS idx_bda_block   ON block_domain_assignments(block_id);
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS block_domain_assignments CASCADE;
        DROP TABLE IF EXISTS recovery_models CASCADE;
        DROP TABLE IF EXISTS geomet_domains CASCADE;
        DROP TABLE IF EXISTS gade_sessions CASCADE;
    """)
