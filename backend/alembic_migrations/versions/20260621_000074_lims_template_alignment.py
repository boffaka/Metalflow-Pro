"""lims_template_alignment — keep LIMS Excel templates importable.

The frontend B1 comminution template exposes SCSE, SG and bulk-density fields.
Older databases can miss one or more of these columns, which makes otherwise
valid Excel imports reject rows. This migration is idempotent and only adds the
columns used by the template/import allowlist.
"""

from alembic import op

revision = "000074"
down_revision = "000073"
revises = "000073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS brwi_kwh_t NUMERIC")
    op.execute("ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS crushing_wi_kwh_t NUMERIC")
    op.execute("ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS a_x_b NUMERIC")
    op.execute("ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS smc_scse_kwh_t NUMERIC")
    op.execute("ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS sg NUMERIC")
    op.execute("ALTER TABLE lims_b1 ADD COLUMN IF NOT EXISTS bulk_density_t_m3 NUMERIC")


def downgrade() -> None:
    op.execute("ALTER TABLE lims_b1 DROP COLUMN IF EXISTS bulk_density_t_m3")
    op.execute("ALTER TABLE lims_b1 DROP COLUMN IF EXISTS sg")
    op.execute("ALTER TABLE lims_b1 DROP COLUMN IF EXISTS smc_scse_kwh_t")
    op.execute("ALTER TABLE lims_b1 DROP COLUMN IF EXISTS a_x_b")
    op.execute("ALTER TABLE lims_b1 DROP COLUMN IF EXISTS crushing_wi_kwh_t")
    op.execute("ALTER TABLE lims_b1 DROP COLUMN IF EXISTS brwi_kwh_t")
