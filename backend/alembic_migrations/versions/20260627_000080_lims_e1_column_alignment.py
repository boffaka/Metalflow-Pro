"""lims_e1_column_alignment — keep Épaississement imports compatible.

The EPP-06 épaississement template sends the enriched E1 field set defined in
routes/lims.py (uf_density_pct, uf_density_t_m3, flux_t_m2_d, cn_overflow_ppm,
au_overflow_ppb, viscosity_mpa_s). Migration 000029 adds these columns but some
production databases still have only the legacy schema.sql structure
(mass_flux_t_m2_d / underflow_viscosity_mpa_s / underflow_sg), causing bulk
import to fail with "column does not exist".
"""

from alembic import op

revision = "000080"
down_revision = "000079"
revises = "000079"
branch_labels = None
depends_on = None


_E1_COLUMNS = [
    ("uf_density_pct", "NUMERIC"),
    ("uf_density_t_m3", "NUMERIC"),
    ("flux_t_m2_d", "NUMERIC"),
    ("cn_overflow_ppm", "NUMERIC"),
    ("au_overflow_ppb", "NUMERIC"),
    ("viscosity_mpa_s", "NUMERIC"),
]


def upgrade() -> None:
    conn = op.get_bind()
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='lims_e1'"
        )
    }
    for col, dtype in _E1_COLUMNS:
        if col not in existing:
            op.execute(f"ALTER TABLE lims_e1 ADD COLUMN IF NOT EXISTS {col} {dtype}")


def downgrade() -> None:
    for col, _ in _E1_COLUMNS:
        op.execute(f"ALTER TABLE lims_e1 DROP COLUMN IF EXISTS {col}")
