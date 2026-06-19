from __future__ import annotations

from alembic import op


revision = "20260404_000009"
down_revision = "20260404_000008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old columns from lims_elution
    op.execute("""
        ALTER TABLE lims_elution
            DROP COLUMN IF EXISTS elution_efficiency_pct,
            DROP COLUMN IF EXISTS temperature_c,
            DROP COLUMN IF EXISTS nacn_concentration_g_l,
            DROP COLUMN IF EXISTS naoh_concentration_g_l,
            DROP COLUMN IF EXISTS cycle_time_h,
            DROP COLUMN IF EXISTS carbon_loading_g_t
    """)

    # Add new columns
    op.execute("""
        ALTER TABLE lims_elution
            ADD COLUMN IF NOT EXISTS type_test              TEXT,
            ADD COLUMN IF NOT EXISTS charbon_type           TEXT,
            ADD COLUMN IF NOT EXISTS charge_charbon_g_l     NUMERIC,
            ADD COLUMN IF NOT EXISTS au_solution_ini_mg_l   NUMERIC,
            ADD COLUMN IF NOT EXISTS au_solution_fin_mg_l   NUMERIC,
            ADD COLUMN IF NOT EXISTS kinetique_adsorption   TEXT,
            ADD COLUMN IF NOT EXISTS elution_t_c            NUMERIC,
            ADD COLUMN IF NOT EXISTS eluant                 TEXT,
            ADD COLUMN IF NOT EXISTS concentrations_g_l     NUMERIC,
            ADD COLUMN IF NOT EXISTS debit_bv_h             NUMERIC,
            ADD COLUMN IF NOT EXISTS temps_elution_h        NUMERIC,
            ADD COLUMN IF NOT EXISTS au_elue_mg_l           NUMERIC,
            ADD COLUMN IF NOT EXISTS recup_au_elution_pct   NUMERIC,
            ADD COLUMN IF NOT EXISTS fines_charbon_pct      NUMERIC,
            ADD COLUMN IF NOT EXISTS observations           TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE lims_elution
            DROP COLUMN IF EXISTS type_test,
            DROP COLUMN IF EXISTS charbon_type,
            DROP COLUMN IF EXISTS charge_charbon_g_l,
            DROP COLUMN IF EXISTS au_solution_ini_mg_l,
            DROP COLUMN IF EXISTS au_solution_fin_mg_l,
            DROP COLUMN IF EXISTS kinetique_adsorption,
            DROP COLUMN IF EXISTS elution_t_c,
            DROP COLUMN IF EXISTS eluant,
            DROP COLUMN IF EXISTS concentrations_g_l,
            DROP COLUMN IF EXISTS debit_bv_h,
            DROP COLUMN IF EXISTS temps_elution_h,
            DROP COLUMN IF EXISTS au_elue_mg_l,
            DROP COLUMN IF EXISTS recup_au_elution_pct,
            DROP COLUMN IF EXISTS fines_charbon_pct,
            DROP COLUMN IF EXISTS observations
    """)

    op.execute("""
        ALTER TABLE lims_elution
            ADD COLUMN IF NOT EXISTS elution_efficiency_pct  NUMERIC,
            ADD COLUMN IF NOT EXISTS temperature_c           NUMERIC,
            ADD COLUMN IF NOT EXISTS nacn_concentration_g_l  NUMERIC,
            ADD COLUMN IF NOT EXISTS naoh_concentration_g_l  NUMERIC,
            ADD COLUMN IF NOT EXISTS cycle_time_h            NUMERIC,
            ADD COLUMN IF NOT EXISTS carbon_loading_g_t      NUMERIC
    """)
