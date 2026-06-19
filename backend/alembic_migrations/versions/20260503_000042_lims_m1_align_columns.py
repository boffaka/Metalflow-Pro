"""lims_m1_align_columns — Align lims_m1 DB schema with LIMS_FIELDS code definition

Root cause: migration 000008 created lims_m1 with a partial column set.
Migration 000029 recreated it with different columns (French mineralogy names).
Neither version matches the LIMS_FIELDS["m1"] list in routes/lims.py, which
causes "column does not exist" errors on every M1 import.

This migration adds all missing columns with ADD COLUMN IF NOT EXISTS so it is
safe to run on any DB state (idempotent). Existing data and existing columns
are never touched.

Columns added (all NUMERIC / FLOAT, nullable):
  k80_um                — Grind size at 80% passing (µm) — key for liberation analysis
  other_sulphides_pct   — Other sulphide minerals (%)
  k_feldspar_pct        — K-feldspar (%)
  other_silicates_pct   — Other silicate minerals (%)
  k_other_pct           — Other K-bearing minerals (%)
  muscovite_illite_pct  — Muscovite + illite combined (%)
  ca_minerals_pct       — Ca-bearing minerals (%)
  fe_oxides_pct         — Iron oxides (%)
  ilmenite_pct          — Ilmenite (%)
  ti_oxides_pct         — Ti oxides (%)
  other_oxides_pct      — Other oxide minerals (%)
  carbonates_pct        — Carbonate minerals (%)
  apatite_pct           — Apatite (%)
  other_pct             — Other minerals (%)
  au_free_pct           — Free gold (%) — critical for gravity/CIL circuit selection

Revision ID: 000042
Revises: 000041
Create Date: 2026-05-03
"""
from alembic import op

revision = "000042"
down_revision = "000041"
branch_labels = None
depends_on = None

# Columns required by LIMS_FIELDS["m1"] in routes/lims.py that may be absent
# from the DB depending on which migration path was taken.
_MISSING_COLUMNS = [
    "k80_um",
    "other_sulphides_pct",
    "k_feldspar_pct",
    "other_silicates_pct",
    "k_other_pct",
    "muscovite_illite_pct",
    "ca_minerals_pct",
    "fe_oxides_pct",
    "ilmenite_pct",
    "ti_oxides_pct",
    "other_oxides_pct",
    "carbonates_pct",
    "apatite_pct",
    "other_pct",
    "au_free_pct",
]


def upgrade() -> None:
    for col in _MISSING_COLUMNS:
        op.execute(
            f"ALTER TABLE lims_m1 ADD COLUMN IF NOT EXISTS {col} NUMERIC"
        )

    # Performance index on k80_um — used by liberation analysis queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_lims_m1_k80 "
        "ON lims_m1(project_id, k80_um) WHERE k80_um IS NOT NULL"
    )
    # Index on au_free_pct — used by scenario advisor gravity circuit rules
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_lims_m1_au_free "
        "ON lims_m1(project_id, au_free_pct) WHERE au_free_pct IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_lims_m1_au_free")
    op.execute("DROP INDEX IF EXISTS idx_lims_m1_k80")
    for col in _MISSING_COLUMNS:
        op.execute(f"ALTER TABLE lims_m1 DROP COLUMN IF EXISTS {col}")
