"""Add jobs and job_artifacts tables for async heavy compute.

Creates:
- jobs: coordination table for async work units
- job_artifacts: bytea storage for binary results (NI 43-101 exports)
- Indexes for pickup, listing, and zombie reaping
- AFTER INSERT trigger that NOTIFYs 'jobs_new' channel
- CHECK constraint on payload size (1 MB)
"""
revision = "000035"
down_revision = "000034"
revises = "000034"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            kind TEXT NOT NULL CHECK (kind IN (
                'sensitivity_spider', 'sensitivity_tornado',
                'simulate_optimize', 'ni43101_export'
            )),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            created_by UUID NOT NULL REFERENCES users(id),
            payload JSONB NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                'queued', 'running', 'success', 'failed', 'cancelled'
            )),
            progress INT NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
            progress_message TEXT,
            result_ref JSONB,
            error TEXT,
            cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
            worker_id TEXT,
            last_heartbeat_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            started_at TIMESTAMPTZ,
            finished_at TIMESTAMPTZ,
            CONSTRAINT jobs_payload_size_check CHECK (length(payload::text) <= 1048576)
        );

        CREATE INDEX idx_jobs_pickup ON jobs (status, created_at);
        CREATE INDEX idx_jobs_project_listing
            ON jobs (project_id, status, kind, created_at DESC);
        CREATE INDEX idx_jobs_running_heartbeat
            ON jobs (last_heartbeat_at) WHERE status = 'running';

        CREATE TABLE job_artifacts (
            id SERIAL PRIMARY KEY,
            job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            data BYTEA NOT NULL,
            byte_size INT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX idx_job_artifacts_job ON job_artifacts (job_id);

        CREATE OR REPLACE FUNCTION notify_jobs_new()
        RETURNS TRIGGER AS $$
        BEGIN
            PERFORM pg_notify('jobs_new', NEW.id::text);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER jobs_new_notify
            AFTER INSERT ON jobs
            FOR EACH ROW
            EXECUTE FUNCTION notify_jobs_new();
    """)


def downgrade():
    op.execute("""
        DROP TRIGGER IF EXISTS jobs_new_notify ON jobs;
        DROP FUNCTION IF EXISTS notify_jobs_new();
        DROP TABLE IF EXISTS job_artifacts;
        DROP TABLE IF EXISTS jobs;
    """)
