"""design_criteria_v2_metadata_alignment.

Bring long-lived Railway databases back in line with the design-criteria v2
contract used by circuit.py and dc_pipeline.py. Older databases can still miss
`version` / `updated_by`, which makes operation insertion fail when default
criteria are generated for a newly selected equipment.
"""

from alembic import op

revision = "000079"
down_revision = "000078"
revises = "000078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE design_criteria_v2 ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1")
    op.execute("ALTER TABLE design_criteria_v2 ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES users(id)")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dc_v2_template_ref "
        "ON design_criteria_v2(template_id, ref_number)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_dc_v2_template_ref")
    op.execute("ALTER TABLE design_criteria_v2 DROP COLUMN IF EXISTS updated_by")
    op.execute("ALTER TABLE design_criteria_v2 DROP COLUMN IF EXISTS version")
