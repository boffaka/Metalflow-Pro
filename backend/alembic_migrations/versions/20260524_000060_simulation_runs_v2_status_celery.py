"""Add status and celery_task_id columns to simulation_runs_v2.

These columns are required by Celery tasks run_nsga2_optimization and
run_monte_carlo_lom which write:
    UPDATE simulation_runs_v2 SET status='running', celery_task_id=%s WHERE id=%s

Without them both tasks crash immediately with UndefinedColumn, leaving
every async optimisation/Monte-Carlo run permanently stuck at 'queued'.
"""
revision = "000060"
down_revision = "000059"
revises = "000059"

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE simulation_runs_v2
            ADD COLUMN IF NOT EXISTS status        TEXT    DEFAULT 'queued',
            ADD COLUMN IF NOT EXISTS celery_task_id TEXT,
            ADD COLUMN IF NOT EXISTS duration_s    FLOAT;

        -- Backfill: any existing row without a status → 'done'
        UPDATE simulation_runs_v2
        SET status = 'done'
        WHERE status IS NULL AND results IS NOT NULL;

        UPDATE simulation_runs_v2
        SET status = 'queued'
        WHERE status IS NULL;

        CREATE INDEX IF NOT EXISTS idx_simulation_runs_v2_status
            ON simulation_runs_v2(project_id, status);
    """)


def downgrade():
    op.execute("""
        DROP INDEX IF EXISTS idx_simulation_runs_v2_status;
        ALTER TABLE simulation_runs_v2
            DROP COLUMN IF EXISTS status,
            DROP COLUMN IF EXISTS celery_task_id,
            DROP COLUMN IF EXISTS duration_s;
    """)
