"""Sync queue and conflict resolution tables."""
revision = "000028"
down_revision = "000027"
revises = "000027"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS sync_queue (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id),
            user_id UUID NOT NULL REFERENCES users(id),
            entity_type VARCHAR(100) NOT NULL,
            entity_id UUID,
            action VARCHAR(20) NOT NULL,
            field_changes JSONB NOT NULL,
            client_timestamp TIMESTAMPTZ NOT NULL,
            server_timestamp TIMESTAMPTZ DEFAULT NOW(),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            resolved_at TIMESTAMPTZ
        );
        CREATE INDEX idx_sync_queue_project ON sync_queue(project_id, status);

        CREATE TABLE IF NOT EXISTS sync_conflicts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id),
            entity_type VARCHAR(100) NOT NULL,
            entity_id UUID NOT NULL,
            field_name VARCHAR(100) NOT NULL,
            local_value JSONB,
            remote_value JSONB,
            local_user_id UUID REFERENCES users(id),
            remote_user_id UUID REFERENCES users(id),
            local_timestamp TIMESTAMPTZ,
            remote_timestamp TIMESTAMPTZ,
            resolution VARCHAR(20) DEFAULT 'pending',
            resolved_by UUID REFERENCES users(id),
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_sync_conflicts_project ON sync_conflicts(project_id, resolution);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS sync_conflicts CASCADE")
    op.execute("DROP TABLE IF EXISTS sync_queue CASCADE")
