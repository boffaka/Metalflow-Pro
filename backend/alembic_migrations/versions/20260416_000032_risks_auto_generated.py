"""
Add is_auto_generated column to risks table.

Allows distinguishing between manually added risks and auto-generated risks.
Auto-generated risks can be deleted and regenerated when upstream data changes.
"""
revision = "000032"
down_revision = "000031"
revises = "000031"

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE risks
        ADD COLUMN IF NOT EXISTS is_auto_generated BOOLEAN DEFAULT FALSE;

        CREATE INDEX IF NOT EXISTS idx_risks_auto_gen
        ON risks(project_id, is_auto_generated);
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_risks_auto_gen")
    op.execute("ALTER TABLE risks DROP COLUMN IF EXISTS is_auto_generated")
