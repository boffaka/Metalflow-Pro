"""project_members — equipe par projet avec role specifique

Revision ID: 000037
Revises: 000036
Create Date: 2026-04-27
"""
from alembic import op

revision = "000037"
down_revision = "000036"
branch_labels = None
depends_on = None

# Roles valides pour les membres d'un projet (sous-ensemble des roles globaux)
MEMBER_ROLES = (
    "Process Engineer",
    "Metallurgist",
    "Project Manager",
    "Cost Engineer",
    "Reviewer",
    "Read-only",
)


def upgrade():
    # ── Table project_members ─────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS project_members (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role        TEXT NOT NULL DEFAULT 'Read-only'
                        CHECK (role IN (
                            'Process Engineer','Metallurgist','Project Manager',
                            'Cost Engineer','Reviewer','Read-only'
                        )),
            invited_by  UUID REFERENCES users(id),
            invited_at  TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (project_id, user_id)
        )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS idx_project_members_project ON project_members(project_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_project_members_user    ON project_members(user_id)")

    # ── Migrer les projets existants: le créateur devient membre PM ───────
    op.execute("""
        INSERT INTO project_members (project_id, user_id, role)
        SELECT p.id, p.user_id, 'Project Manager'
        FROM   projects p
        WHERE  p.user_id IS NOT NULL
        ON CONFLICT (project_id, user_id) DO NOTHING
    """)

    # ── Ajouter archived_at aux projets pour soft-delete ─────────────────
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ DEFAULT NULL")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS description TEXT DEFAULT NULL")
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS tags       TEXT[] DEFAULT '{}'")

    # ── Index composite pour performance liste projets ────────────────────
    op.execute("CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status) WHERE archived_at IS NULL")


def downgrade():
    op.execute("DROP TABLE IF EXISTS project_members CASCADE")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS archived_at")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS description")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS tags")
    op.execute("DROP INDEX IF EXISTS idx_projects_status")
