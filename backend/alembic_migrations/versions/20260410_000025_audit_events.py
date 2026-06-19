"""Immutable audit events table + validation flags for NI 43-101 compliance."""
revision = "000025"
down_revision = "000024"
revises = "000024"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            user_id UUID REFERENCES users(id),
            project_id UUID REFERENCES projects(id),
            entity_type VARCHAR(100) NOT NULL,
            entity_id UUID,
            action VARCHAR(50) NOT NULL,
            field_name VARCHAR(100),
            old_value JSONB,
            new_value JSONB,
            source VARCHAR(50) NOT NULL DEFAULT 'web',
            ip_address INET,
            checksum VARCHAR(64) NOT NULL
        );

        CREATE INDEX idx_audit_events_project ON audit_events(project_id, timestamp DESC);
        CREATE INDEX idx_audit_events_entity ON audit_events(entity_type, entity_id);
        CREATE INDEX idx_audit_events_user ON audit_events(user_id, timestamp DESC);
        CREATE INDEX idx_audit_events_action ON audit_events(action);

        CREATE OR REPLACE FUNCTION prevent_audit_mutation() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events is append-only: % not allowed', TG_OP;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER audit_events_immutable
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

        CREATE TABLE IF NOT EXISTS validation_flags (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id),
            sample_id UUID,
            entity_type VARCHAR(100) NOT NULL,
            entity_id UUID NOT NULL,
            rule_code VARCHAR(50) NOT NULL,
            severity VARCHAR(20) NOT NULL DEFAULT 'warning',
            message TEXT NOT NULL,
            field_name VARCHAR(100),
            field_value JSONB,
            status VARCHAR(20) NOT NULL DEFAULT 'open',
            acknowledged_by UUID REFERENCES users(id),
            acknowledged_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX idx_validation_flags_project ON validation_flags(project_id, status);
        CREATE INDEX idx_validation_flags_entity ON validation_flags(entity_type, entity_id);

        CREATE TABLE IF NOT EXISTS alert_log (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            level VARCHAR(20) NOT NULL,
            title VARCHAR(200) NOT NULL,
            message TEXT,
            context JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_alert_log_created ON alert_log(created_at DESC);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS alert_log CASCADE")
    op.execute("DROP TABLE IF EXISTS validation_flags CASCADE")
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable ON audit_events")
    op.execute("DROP TABLE IF EXISTS audit_events CASCADE")
    op.execute("DROP FUNCTION IF EXISTS prevent_audit_mutation CASCADE")
