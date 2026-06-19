"""
Module generation status table — tracks pipeline state across all modules.

Each row represents the current generation state of one module for one project.
Supports staleness detection, readiness checks, and audit traceability.
"""
revision = "000030"
down_revision = "000029"
revises = "000029"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS module_generation_status (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            module_code     VARCHAR(50) NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending','ready','generating','complete','stale','error','skipped')),
            generated_at    TIMESTAMPTZ,
            generated_by    UUID REFERENCES users(id) ON DELETE SET NULL,
            -- Hash of key input values at generation time (SHA-256 hex, first 16 chars)
            source_hash     VARCHAR(64),
            -- Number of input records used (LIMS rows, DC rows, etc.)
            input_count     INTEGER DEFAULT 0,
            -- Module that triggered this generation (cascade chain)
            triggered_by    VARCHAR(50),
            warnings        JSONB NOT NULL DEFAULT '[]',
            errors          JSONB NOT NULL DEFAULT '[]',
            -- Snapshot of key values used for this generation (for audit traceability)
            input_snapshot  JSONB NOT NULL DEFAULT '{}',
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(project_id, module_code)
        );

        CREATE INDEX idx_mgs_project     ON module_generation_status(project_id);
        CREATE INDEX idx_mgs_status      ON module_generation_status(project_id, status);
        CREATE INDEX idx_mgs_module_code ON module_generation_status(module_code);
        CREATE INDEX idx_mgs_generated   ON module_generation_status(generated_at DESC NULLS LAST);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS module_generation_status CASCADE")
