"""lims_d1_column_alignment — keep Lixiviation imports compatible.

The LIX-05 lixiviation template sends the enriched D1 field set defined in
routes/lims.py. Existing Railway databases can still have the legacy lims_d1
schema, causing bulk import to reject rows at the first missing column.
"""

from alembic import op

revision = "000076"
down_revision = "000075"
revises = "000075"
branch_labels = None
depends_on = None


_D1_COLUMNS = [
    ("au_leach_feed_g_t", "NUMERIC"),
    ("p80_alim_um", "NUMERIC"),
    ("solides_pulpe_pct", "NUMERIC"),
    ("nacn_initiale_ppm", "NUMERIC"),
    ("nacn_residuel_ppm", "NUMERIC"),
    ("nacn_consumption_kg_t", "NUMERIC"),
    ("ph_initial", "NUMERIC"),
    ("ph_final", "NUMERIC"),
    ("cao_consumption_kg_t", "NUMERIC"),
    ("o2_dissous_mg_l", "NUMERIC"),
    ("o2_consumption_kg_t", "NUMERIC"),
    ("temperature_c", "NUMERIC"),
    ("duree_h", "NUMERIC"),
    ("carbon_dose_g_l", "NUMERIC"),
    ("densite_solide_sg", "NUMERIC"),
    ("leach_rec_2h_pct", "NUMERIC"),
    ("leach_rec_4h_pct", "NUMERIC"),
    ("leach_rec_8h_pct", "NUMERIC"),
    ("leach_rec_12h_pct", "NUMERIC"),
    ("leach_rec_24h_pct", "NUMERIC"),
    ("leach_rec_48h_pct", "NUMERIC"),
    ("au_tail_g_t", "NUMERIC"),
    ("preg_rob_index", "NUMERIC"),
]


def upgrade() -> None:
    for column, dtype in _D1_COLUMNS:
        op.execute(f"ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS {column} {dtype}")


def downgrade() -> None:
    for column, _ in reversed(_D1_COLUMNS):
        op.execute(f"ALTER TABLE lims_d1 DROP COLUMN IF EXISTS {column}")
