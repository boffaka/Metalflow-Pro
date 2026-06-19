"""Add missing performance indexes on projects, users, audit_log,
lims_samples, and refresh_sessions.

Revision ID: 000024
Revises: 000023
Create Date: 2026-04-10
"""
from alembic import op

revision = "000024"
down_revision = "000023"
branch_labels = None
depends_on = None


def upgrade():
    # projects: speed up ORDER BY / filtering on created_at
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_projects_created_at
            ON projects(created_at);
    """)

    # users: speed up role-based filtering
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_role
            ON users(role);
    """)

    # audit_log: speed up time-range queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
            ON audit_log(created_at);
    """)

    # lims_samples: composite index for per-project time-ordered queries
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lims_samples_project_created
            ON lims_samples(project_id, created_at);
    """)

    # refresh_sessions: composite index for active-session lookups
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_refresh_sessions_user_revoked
            ON refresh_sessions(user_id, revoked_at);
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_refresh_sessions_user_revoked;")
    op.execute("DROP INDEX IF EXISTS idx_lims_samples_project_created;")
    op.execute("DROP INDEX IF EXISTS idx_audit_log_created_at;")
    op.execute("DROP INDEX IF EXISTS idx_users_role;")
    op.execute("DROP INDEX IF EXISTS idx_projects_created_at;")
