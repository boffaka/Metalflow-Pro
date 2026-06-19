from __future__ import annotations

from alembic import op


revision = "20260403_000005"
down_revision = "20260403_000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE lims_b1
          ADD COLUMN IF NOT EXISTS a_x_b              NUMERIC,
          ADD COLUMN IF NOT EXISTS ta                  NUMERIC,
          ADD COLUMN IF NOT EXISTS dwi_kwh_m3          NUMERIC,
          ADD COLUMN IF NOT EXISTS mia_kwh_t           NUMERIC,
          ADD COLUMN IF NOT EXISTS mb_kwh_t            NUMERIC,
          ADD COLUMN IF NOT EXISTS mic_kwh_t           NUMERIC,
          ADD COLUMN IF NOT EXISTS mih_kwh_t           NUMERIC,
          ADD COLUMN IF NOT EXISTS bulk_density_t_m3   NUMERIC
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE lims_b1
          DROP COLUMN IF EXISTS a_x_b,
          DROP COLUMN IF EXISTS ta,
          DROP COLUMN IF EXISTS dwi_kwh_m3,
          DROP COLUMN IF EXISTS mia_kwh_t,
          DROP COLUMN IF EXISTS mb_kwh_t,
          DROP COLUMN IF EXISTS mic_kwh_t,
          DROP COLUMN IF EXISTS mih_kwh_t,
          DROP COLUMN IF EXISTS bulk_density_t_m3
    """)
