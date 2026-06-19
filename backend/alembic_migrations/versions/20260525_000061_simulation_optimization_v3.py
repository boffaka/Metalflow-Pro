"""Simulation & optimisation v3 — runs, jobs, surrogate cache tables."""
revision = "000061"
down_revision = "000060"
revises = "000060"

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE optimization_jobs DROP CONSTRAINT IF EXISTS optimization_jobs_mode_check;
        ALTER TABLE optimization_jobs ADD CONSTRAINT optimization_jobs_mode_check
            CHECK (mode IN ('sweep', 'nsga2', 'nsga3', 'bayesian'));

        CREATE TABLE IF NOT EXISTS simulation_runs_v3 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            template_id     UUID,
            compilation_id  UUID REFERENCES circuit_compilations(id),
            run_type        TEXT DEFAULT 'rigorous',
            status          TEXT DEFAULT 'streaming',
            params          JSONB,
            node_results    JSONB,
            final_kpis      JSONB,
            surrogate_grid  JSONB,
            label           TEXT,
            created_by      UUID REFERENCES users(id),
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            completed_at    TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_runs_v3_project ON simulation_runs_v3(project_id);
        CREATE INDEX IF NOT EXISTS idx_runs_v3_status ON simulation_runs_v3(project_id, status);

        CREATE TABLE IF NOT EXISTS optimization_jobs_v3 (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id       UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            compilation_id   UUID REFERENCES circuit_compilations(id),
            algorithm        TEXT DEFAULT 'nsga3',
            objectives       TEXT[],
            variables        JSONB,
            config           JSONB,
            status           TEXT DEFAULT 'queued',
            pareto_history   JSONB,
            pareto_final     JSONB,
            knee_point       JSONB,
            created_by       UUID REFERENCES users(id),
            created_at       TIMESTAMPTZ DEFAULT NOW(),
            completed_at     TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_optjobs_v3_project ON optimization_jobs_v3(project_id);

        CREATE TABLE IF NOT EXISTS surrogate_caches (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NOT NULL UNIQUE REFERENCES simulation_runs_v3(id) ON DELETE CASCADE,
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            grid_params     JSONB NOT NULL,
            grid_values     JSONB NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_surrogate_run ON surrogate_caches(run_id);
    """)


def downgrade():
    op.execute("""
        DROP INDEX IF EXISTS idx_surrogate_run;
        DROP TABLE IF EXISTS surrogate_caches;
        DROP INDEX IF EXISTS idx_optjobs_v3_project;
        DROP TABLE IF EXISTS optimization_jobs_v3;
        DROP INDEX IF EXISTS idx_runs_v3_status;
        DROP INDEX IF EXISTS idx_runs_v3_project;
        DROP TABLE IF EXISTS simulation_runs_v3;

        ALTER TABLE optimization_jobs DROP CONSTRAINT IF EXISTS optimization_jobs_mode_check;
        ALTER TABLE optimization_jobs ADD CONSTRAINT optimization_jobs_mode_check
            CHECK (mode IN ('sweep', 'nsga2'));
    """)
