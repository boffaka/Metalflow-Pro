"""Dead letter queue for failed Celery tasks."""
revision = "000027"
down_revision = "000026"
revises = "000026"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS failed_tasks (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task_name VARCHAR(200) NOT NULL,
            task_id VARCHAR(200),
            project_id UUID REFERENCES projects(id),
            args JSONB,
            kwargs JSONB,
            exception TEXT,
            traceback TEXT,
            retries INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            resolved_by UUID REFERENCES users(id)
        );
        CREATE INDEX idx_failed_tasks_project ON failed_tasks(project_id, created_at DESC);
        CREATE INDEX idx_failed_tasks_unresolved ON failed_tasks(resolved_at) WHERE resolved_at IS NULL;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS failed_tasks CASCADE")
