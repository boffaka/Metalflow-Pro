"""lims_h1_column_alignment — keep Elution ADR imports compatible.

The ELU-08 hydrometallurgy template sends the enriched H1 field set defined in
routes/lims.py. Existing Railway databases can still have the legacy summary
lims_elution schema, causing bulk import to reject rows at the first missing
column.
"""

from alembic import op

revision = "000078"
down_revision = "000077"
revises = "000077"
branch_labels = None
depends_on = None


_H1_COLUMNS = [
    ("type_test", "TEXT"),
    ("charbon_type", "TEXT"),
    ("charge_charbon_g_l", "NUMERIC"),
    ("au_solution_ini_mg_l", "NUMERIC"),
    ("au_solution_fin_mg_l", "NUMERIC"),
    ("kinetique_adsorption", "TEXT"),
    ("elution_t_c", "NUMERIC"),
    ("eluant_cn_g_l", "NUMERIC"),
    ("eluant_naoh_g_l", "NUMERIC"),
    ("debit_bv_h", "NUMERIC"),
    ("temps_elution_h", "NUMERIC"),
    ("au_elue_mg_l", "NUMERIC"),
    ("recup_au_elution_pct", "NUMERIC"),
    ("fines_charbon_pct", "NUMERIC"),
    ("observations", "TEXT"),
]


def upgrade() -> None:
    for column, dtype in _H1_COLUMNS:
        op.execute(f"ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS {column} {dtype}")


def downgrade() -> None:
    for column, _ in reversed(_H1_COLUMNS):
        op.execute(f"ALTER TABLE lims_elution DROP COLUMN IF EXISTS {column}")
