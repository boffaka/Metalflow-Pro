"""lims_all_tables_column_sync — Align all LIMS tables with LIMS_FIELDS code definition

Root cause: Multiple migrations created LIMS tables with partial or renamed
column sets that diverged from LIMS_FIELDS in routes/lims.py.

This migration adds ALL missing columns across every LIMS table using
ADD COLUMN IF NOT EXISTS — fully idempotent, safe to run on any DB state.

Tables fixed:
  lims_d1         — 4 columns (au_leach_feed_g_t, densite_solide_sg,
                                nacn_initiale_ppm, o2_dissous_mg_l)
  lims_c2         — 5 columns (au_concentre_g_t, duree_concentration_min,
                                grg_rec_pct, masse_concentre_g,
                                pression_fluidisation_psi)
  lims_flotation  — 7 columns (au_concentrate_g_t, collecteur_g_t,
                                concentrate_wt_pct, depressant_g_t,
                                moussant_g_t, recup_s_pct, temps_total_min)
  lims_elution    — 14 columns (au_elue_mg_l, au_solution_fin_mg_l,
                                 au_solution_ini_mg_l, charbon_type,
                                 charge_charbon_g_l, eluant_cn_g_l,
                                 eluant_naoh_g_l, elution_t_c,
                                 fines_charbon_pct, kinetique_adsorption,
                                 observations, recup_au_elution_pct,
                                 temps_elution_h, type_test)
  lims_m1         — already fixed by 000042 (k80_um + 14 others)

Revision ID: 000043
Revises: 000042
Create Date: 2026-05-04
"""
from alembic import op

revision = "000043"
down_revision = "000042"
branch_labels = None
depends_on = None

# ── lims_d1 ──────────────────────────────────────────────────────────────────
_D1_COLS = [
    ("au_leach_feed_g_t",    "NUMERIC"),  # Au feed grade (g/t) — was au_feed_g_t
    ("densite_solide_sg",    "NUMERIC"),  # Solid density SG
    ("nacn_initiale_ppm",    "NUMERIC"),  # Initial NaCN (ppm) — was nacn_initial_ppm
    ("o2_dissous_mg_l",      "NUMERIC"),  # Dissolved O2 (mg/L) — was do_mg_l
]

# ── lims_c2 ───────────────────────────────────────────────────────────────────
_C2_COLS = [
    ("au_concentre_g_t",         "NUMERIC"),  # Au concentrate grade (g/t)
    ("duree_concentration_min",  "NUMERIC"),  # Concentration duration (min)
    ("grg_rec_pct",              "NUMERIC"),  # GRG recovery (%)
    ("masse_concentre_g",        "NUMERIC"),  # Concentrate mass (g)
    ("pression_fluidisation_psi","NUMERIC"),  # Fluidisation pressure (psi)
]

# ── lims_flotation (g1) ───────────────────────────────────────────────────────
_G1_COLS = [
    ("au_concentrate_g_t", "NUMERIC"),  # Au concentrate grade (g/t)
    ("collecteur_g_t",     "NUMERIC"),  # Collector dosage (g/t)
    ("concentrate_wt_pct", "NUMERIC"),  # Concentrate weight (%)
    ("depressant_g_t",     "NUMERIC"),  # Depressant dosage (g/t)
    ("moussant_g_t",       "NUMERIC"),  # Frother dosage (g/t)
    ("recup_s_pct",        "NUMERIC"),  # Sulphide recovery (%)
    ("temps_total_min",    "NUMERIC"),  # Total flotation time (min)
]

# ── lims_elution (h1) ─────────────────────────────────────────────────────────
_H1_COLS = [
    ("au_elue_mg_l",          "NUMERIC"),  # Eluted Au (mg/L)
    ("au_solution_fin_mg_l",  "NUMERIC"),  # Final Au in solution (mg/L)
    ("au_solution_ini_mg_l",  "NUMERIC"),  # Initial Au in solution (mg/L)
    ("charbon_type",          "TEXT"),     # Carbon type
    ("charge_charbon_g_l",    "NUMERIC"),  # Carbon loading (g/L)
    ("eluant_cn_g_l",         "NUMERIC"),  # Eluant CN concentration (g/L)
    ("eluant_naoh_g_l",       "NUMERIC"),  # Eluant NaOH concentration (g/L)
    ("elution_t_c",           "NUMERIC"),  # Elution temperature (°C)
    ("fines_charbon_pct",     "NUMERIC"),  # Carbon fines (%)
    ("kinetique_adsorption",  "TEXT"),     # Adsorption kinetics description
    ("observations",          "TEXT"),     # Observations
    ("recup_au_elution_pct",  "NUMERIC"),  # Au elution recovery (%)
    ("temps_elution_h",       "NUMERIC"),  # Elution time (h)
    ("type_test",             "TEXT"),     # Test type
]


def upgrade() -> None:
    for col, dtype in _D1_COLS:
        op.execute(
            f"ALTER TABLE lims_d1 ADD COLUMN IF NOT EXISTS {col} {dtype}"
        )
    for col, dtype in _C2_COLS:
        op.execute(
            f"ALTER TABLE lims_c2 ADD COLUMN IF NOT EXISTS {col} {dtype}"
        )
    for col, dtype in _G1_COLS:
        op.execute(
            f"ALTER TABLE lims_flotation ADD COLUMN IF NOT EXISTS {col} {dtype}"
        )
    for col, dtype in _H1_COLS:
        op.execute(
            f"ALTER TABLE lims_elution ADD COLUMN IF NOT EXISTS {col} {dtype}"
        )


def downgrade() -> None:
    for col, _ in _H1_COLS:
        op.execute(f"ALTER TABLE lims_elution DROP COLUMN IF EXISTS {col}")
    for col, _ in _G1_COLS:
        op.execute(f"ALTER TABLE lims_flotation DROP COLUMN IF EXISTS {col}")
    for col, _ in _C2_COLS:
        op.execute(f"ALTER TABLE lims_c2 DROP COLUMN IF EXISTS {col}")
    for col, _ in _D1_COLS:
        op.execute(f"ALTER TABLE lims_d1 DROP COLUMN IF EXISTS {col}")
