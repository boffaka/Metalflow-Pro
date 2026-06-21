"""lims_g1_column_alignment — keep Flotation imports compatible.

The FLT-04 flotation template sends the enriched G1 field set defined in
routes/lims.py. Existing Railway databases can still have the legacy summary
lims_flotation schema, causing bulk import to reject rows at the first missing
column.
"""

from alembic import op

revision = "000077"
down_revision = "000076"
revises = "000076"
branch_labels = None
depends_on = None


_G1_COLUMNS = [
    ("au_alim_g_t", "NUMERIC"),
    ("p80_alim_um", "NUMERIC"),
    ("concentrate_wt_pct", "NUMERIC"),
    ("au_concentrate_g_t", "NUMERIC"),
    ("au_recovery_pct", "NUMERIC"),
    ("au_tail_g_t", "NUMERIC"),
    ("temps_total_min", "NUMERIC"),
    ("collecteur_g_t", "NUMERIC"),
    ("moussant_g_t", "NUMERIC"),
    ("depressant_g_t", "NUMERIC"),
    ("recup_s_pct", "NUMERIC"),
]


def upgrade() -> None:
    for column, dtype in _G1_COLUMNS:
        op.execute(f"ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS {column} {dtype}")


def downgrade() -> None:
    for column, _ in reversed(_G1_COLUMNS):
        op.execute(f"ALTER TABLE lims_flotation DROP COLUMN IF EXISTS {column}")
