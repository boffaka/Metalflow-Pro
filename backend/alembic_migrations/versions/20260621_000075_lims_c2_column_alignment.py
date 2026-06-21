"""lims_c2_column_alignment — keep Knelson/Falcon imports compatible.

The GRV-03a Knelson/Falcon template sends the full C2 field set defined in
routes/lims.py. Some existing Railway databases only have the legacy summary
columns on lims_c2, so bulk import rejects rows at the first missing column.
"""

from alembic import op

revision = "000075"
down_revision = "000074"
revises = "000074"
branch_labels = None
depends_on = None


_C2_COLUMNS = [
    ("p80_alim_um", "NUMERIC"),
    ("solides_alim_pct", "NUMERIC"),
    ("masse_alim_kg", "NUMERIC"),
    ("au_alim_g_t", "NUMERIC"),
    ("vitesse_knelson_rpm", "NUMERIC"),
    ("pression_fluidisation_psi", "NUMERIC"),
    ("duree_concentration_min", "NUMERIC"),
    ("masse_concentre_g", "NUMERIC"),
    ("au_concentre_g_t", "NUMERIC"),
    ("au_tail_g_t", "NUMERIC"),
    ("grg_rec_pct", "NUMERIC"),
    ("mass_pull_pct", "NUMERIC"),
]


def upgrade() -> None:
    for column, dtype in _C2_COLUMNS:
        op.execute(f"ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS {column} {dtype}")


def downgrade() -> None:
    for column, _ in reversed(_C2_COLUMNS):
        op.execute(f"ALTER TABLE lims_c2 DROP COLUMN IF EXISTS {column}")
