from __future__ import annotations

from alembic import op


revision = "20260402_000003"
down_revision = "20260402_000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_sessions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ,
            replaced_by_id UUID REFERENCES refresh_sessions(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS idx_refresh_sessions_user ON refresh_sessions(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_refresh_sessions_token_hash ON refresh_sessions(token_hash)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_sessions")
