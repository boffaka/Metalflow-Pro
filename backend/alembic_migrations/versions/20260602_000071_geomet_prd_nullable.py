# backend/alembic_migrations/versions/20260602_000071_geomet_prd_nullable.py
"""Rendre mine_plan_id et domaining_session_id optionnels dans prd_analyses.

Requis pour l'API v2 GADE·PRD·IMBO qui n'utilise pas les mine_plans.
"""
revision = "000071"
down_revision = "000070"
revises = "000070"

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE prd_analyses ALTER COLUMN mine_plan_id DROP NOT NULL;
        ALTER TABLE prd_analyses ALTER COLUMN domaining_session_id DROP NOT NULL;
    """)


def downgrade():
    pass  # On ne remet pas NOT NULL pour éviter les erreurs si des rows NULL existent
