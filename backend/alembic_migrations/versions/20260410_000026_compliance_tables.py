"""QP approval workflow and data snapshots for NI 43-101."""
revision = "000026"
down_revision = "000025"
revises = "000025"

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE projects ADD COLUMN IF NOT EXISTS compliance_status VARCHAR(20) DEFAULT 'draft';
        ALTER TABLE users ADD COLUMN IF NOT EXISTS is_qp BOOLEAN DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS signature_hash VARCHAR(128);

        CREATE TABLE IF NOT EXISTS approval_workflows (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id),
            submitted_by UUID NOT NULL REFERENCES users(id),
            reviewed_by UUID REFERENCES users(id),
            status VARCHAR(20) NOT NULL DEFAULT 'draft',
            report_type VARCHAR(50) NOT NULL DEFAULT 'ni43101',
            title VARCHAR(500),
            submitted_at TIMESTAMPTZ,
            reviewed_at TIMESTAMPTZ,
            snapshot_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_approval_workflows_project ON approval_workflows(project_id, status);

        CREATE TABLE IF NOT EXISTS approval_comments (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workflow_id UUID NOT NULL REFERENCES approval_workflows(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES users(id),
            comment TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS data_snapshots (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id),
            workflow_id UUID REFERENCES approval_workflows(id),
            created_by UUID NOT NULL REFERENCES users(id),
            snapshot_data JSONB NOT NULL,
            checksum VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX idx_data_snapshots_project ON data_snapshots(project_id, created_at DESC);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS data_snapshots CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_comments CASCADE")
    op.execute("DROP TABLE IF EXISTS approval_workflows CASCADE")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS is_qp")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS signature_hash")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS compliance_status")
