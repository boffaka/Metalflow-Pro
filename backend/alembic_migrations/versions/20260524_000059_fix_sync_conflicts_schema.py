"""Align sync_conflicts schema with routes/sync.py (field-level conflict model)."""
revision = "000059"
down_revision = "000058"
revises = "000058"

from alembic import op


def upgrade():
    op.execute("""
        DROP TABLE IF EXISTS sync_conflicts CASCADE;
        CREATE TABLE sync_conflicts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id),
            entity_type VARCHAR(100) NOT NULL,
            entity_id UUID NOT NULL,
            field_name VARCHAR(100) NOT NULL,
            client_value JSONB,
            server_value JSONB,
            client_timestamp TIMESTAMPTZ NOT NULL,
            server_timestamp TIMESTAMPTZ,
            resolution VARCHAR(20),
            resolved_value JSONB,
            resolved_by UUID REFERENCES users(id),
            resolved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_sync_conflicts_open
            ON sync_conflicts(project_id, created_at DESC)
            WHERE resolved_at IS NULL;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS sync_conflicts CASCADE")
    op.execute("""
        CREATE TABLE sync_conflicts (
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
