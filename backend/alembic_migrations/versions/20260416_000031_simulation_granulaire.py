"""Add granular simulation support: run_mode, ops_simulated, feed tracking, suggestions log."""

revision = "000031"
down_revision = "000030"

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY


def upgrade():
    # ── Idempotency guard ─────────────────────────────────────────────────
    # All 7 columns below also exist on simulation_runs_v2 in the schema.sql
    # baseline (schema.sql:1483-1489). Fresh DBs initialised from schema.sql
    # already have them, so unconditionally calling op.add_column raises
    # "column already exists". Skip any column that's already present.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = {c["name"] for c in inspector.get_columns("simulation_runs_v2")}

    if "run_mode" not in existing_cols:
        op.add_column("simulation_runs_v2", sa.Column("run_mode", sa.Text(), server_default="global"))
    if "ops_simulated" not in existing_cols:
        op.add_column("simulation_runs_v2", sa.Column("ops_simulated", ARRAY(sa.Text())))
    if "feed_source" not in existing_cols:
        op.add_column("simulation_runs_v2", sa.Column("feed_source", sa.Text()))
    if "feed_stream" not in existing_cols:
        op.add_column("simulation_runs_v2", sa.Column("feed_stream", JSONB()))
    if "product_stream" not in existing_cols:
        op.add_column("simulation_runs_v2", sa.Column("product_stream", JSONB()))
    if "suggestion_id" not in existing_cols:
        op.add_column("simulation_runs_v2", sa.Column("suggestion_id", sa.Text()))
    if "label" not in existing_cols:
        op.add_column("simulation_runs_v2", sa.Column("label", sa.Text()))

    op.execute("""
        CREATE TABLE IF NOT EXISTS scenario_suggestions_log (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            suggestion_id   TEXT NOT NULL,
            title           TEXT NOT NULL,
            category        TEXT,
            confidence      TEXT,
            reasoning       TEXT,
            lims_basis      JSONB,
            history_basis   JSONB,
            ops_to_add      TEXT[],
            ops_to_remove   TEXT[],
            params_override JSONB,
            estimated_impact JSONB,
            status          TEXT DEFAULT 'proposed',
            accepted_at     TIMESTAMPTZ,
            run_id          UUID REFERENCES simulation_runs_v2(id),
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_suggestions_project ON scenario_suggestions_log(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_suggestions_status ON scenario_suggestions_log(project_id, status)")


def downgrade():
    op.execute("DROP TABLE IF EXISTS scenario_suggestions_log")
    op.drop_column("simulation_runs_v2", "label")
    op.drop_column("simulation_runs_v2", "suggestion_id")
    op.drop_column("simulation_runs_v2", "product_stream")
    op.drop_column("simulation_runs_v2", "feed_stream")
    op.drop_column("simulation_runs_v2", "feed_source")
    op.drop_column("simulation_runs_v2", "ops_simulated")
    op.drop_column("simulation_runs_v2", "run_mode")
