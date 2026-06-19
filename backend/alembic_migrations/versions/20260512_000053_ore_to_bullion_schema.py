"""Ore to Bullion Simulator — schema tables.

Revision ID: 000053
Revises: 000052
Create Date: 2026-05-12
"""
from alembic import op

revision = "000053"
down_revision = "000052"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS ore_to_bullion_runs (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name                TEXT NOT NULL DEFAULT 'Simulation sans nom',
            feed_params         JSONB NOT NULL,
            circuit_config      JSONB NOT NULL,
            overrides           JSONB,
            results             JSONB,
            status              TEXT NOT NULL DEFAULT 'pending',
            celery_task_id      TEXT,
            created_by          UUID REFERENCES users(id),
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            updated_at          TIMESTAMPTZ DEFAULT NOW(),
            computation_time_s  FLOAT
        );
        CREATE INDEX IF NOT EXISTS idx_otb_runs_project ON ore_to_bullion_runs(project_id);
        CREATE INDEX IF NOT EXISTS idx_otb_runs_status ON ore_to_bullion_runs(project_id, status);

        CREATE TABLE IF NOT EXISTS ore_to_bullion_circuit_results (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NOT NULL REFERENCES ore_to_bullion_runs(id) ON DELETE CASCADE,
            circuit_name    TEXT NOT NULL,
            circuit_order   INT NOT NULL,
            input_stream    JSONB NOT NULL,
            output_stream   JSONB NOT NULL,
            mass_balance    JSONB NOT NULL,
            equipment       JSONB NOT NULL DEFAULT '[]',
            energy_kwh_t    FLOAT NOT NULL DEFAULT 0,
            power_kw        FLOAT NOT NULL DEFAULT 0,
            reagents        JSONB NOT NULL DEFAULT '{}',
            alerts          JSONB NOT NULL DEFAULT '[]',
            metadata        JSONB DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_otb_circuit_run ON ore_to_bullion_circuit_results(run_id);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS ore_to_bullion_circuit_results CASCADE;")
    op.execute("DROP TABLE IF EXISTS ore_to_bullion_runs CASCADE;")
