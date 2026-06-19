"""test_campaigns table

Revision ID: 20260405_000012
Revises: 20260405_000011
Create Date: 2026-04-05
"""
from alembic import op

revision = '20260405_000012'
down_revision = '20260405_000011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS test_campaigns (
          id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          project_id    UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
          name          TEXT NOT NULL,
          description   TEXT,
          status        TEXT NOT NULL DEFAULT 'planned'
                        CHECK (status IN ('planned','active','complete','cancelled')),
          started_at    TIMESTAMPTZ,
          completed_at  TIMESTAMPTZ,
          created_at    TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_test_campaigns_project ON test_campaigns(project_id);

        CREATE TABLE IF NOT EXISTS campaign_samples (
          campaign_id  UUID REFERENCES test_campaigns(id) ON DELETE CASCADE,
          sample_id    UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
          added_at     TIMESTAMPTZ DEFAULT now(),
          PRIMARY KEY (campaign_id, sample_id)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS campaign_samples;")
    op.execute("DROP TABLE IF EXISTS test_campaigns;")
