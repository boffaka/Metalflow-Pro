# backend/alembic_migrations/versions/20260603_000073_project_members.py
"""project_members — multi-user membership (Lot C Phase 1, F2).

Adds a membership table so access is granted to project members rather than the
single owner column (projects.user_id). Backfills every existing owner as an
'owner' member so behaviour is preserved. No RLS here — that is Lot C Phase 3.

Idempotent (IF NOT EXISTS / ON CONFLICT) so it is safe to re-run.
"""

revision = "000073"
down_revision = "000072"
revises = "000072"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS project_members (
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
            role        TEXT NOT NULL DEFAULT 'member',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (project_id, user_id)
        );
    """)
    # Reverse lookup: "which projects can this user see?"
    op.execute("CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id);")
    # Backfill existing owners as members (idempotent).
    op.execute("""
        INSERT INTO project_members (project_id, user_id, role)
        SELECT id, user_id, 'owner'
        FROM projects
        WHERE user_id IS NOT NULL
        ON CONFLICT (project_id, user_id) DO NOTHING;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS project_members;")
