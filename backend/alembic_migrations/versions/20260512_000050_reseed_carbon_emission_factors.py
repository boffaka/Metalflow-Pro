"""Reseed global carbon emission factors (fix missing defaults in production).

Migration 000020 created the carbon_emission_factors table and seeded global
defaults, but the seed INSERT used ON CONFLICT DO NOTHING with a UNIQUE
constraint on (project_id, factor_key). Because project_id is NULL for global
defaults, the UNIQUE constraint on (NULL, factor_key) is not enforced by
PostgreSQL (NULLs are not considered equal in UNIQUE constraints), so the
ON CONFLICT clause never fires and the rows may have been silently skipped
on databases that already had the table from an earlier schema bootstrap.

This migration uses an explicit DELETE + re-INSERT to guarantee the global
defaults are present with the correct values.

Revision ID: 000050
Revises: 000049
Create Date: 2026-05-12
"""
from alembic import op

revision = "000050"
down_revision = "000049"
branch_labels = None
depends_on = None


def upgrade():
    # Remove any existing global defaults (project_id IS NULL) so we can
    # re-insert with the correct values idempotently.
    op.execute(
        "DELETE FROM carbon_emission_factors WHERE project_id IS NULL"
    )

    op.execute("""
        INSERT INTO carbon_emission_factors
            (project_id, factor_key, factor_label, factor_value, unit, source, is_default)
        VALUES
            (NULL, 'grid_kgco2_kwh',       'Facteur emission reseau (Ontario)',  0.03,  'kgCO2/kWh',  'Environnement Canada 2025', true),
            (NULL, 'nacn_kgco2_kg',        'NaCN (procede Andrussow)',           1.87,  'kgCO2/kg',   'IPCC AR6',                  true),
            (NULL, 'cao_kgco2_kg',         'CaO (calcination)',                  0.75,  'kgCO2/kg',   'IPCC AR6',                  true),
            (NULL, 'h2o2_kgco2_kg',        'H2O2 (acide de Caro)',               0.50,  'kgCO2/kg',   'IPCC AR6',                  true),
            (NULL, 'cuso4_kgco2_kg',       'CuSO4',                              2.30,  'kgCO2/kg',   'IPCC AR6',                  true),
            (NULL, 'so2_kgco2_kg',         'SO2 liquide (sous-produit)',          0.00,  'kgCO2/kg',   'Sous-produit',              true),
            (NULL, 'transport_kgco2_tkm',  'Transport routier',                  0.062, 'kgCO2/t.km', 'GHG Protocol',              true),
            (NULL, 'smelt_kgco2_oz',       'Fonderie dore (Scope 1)',            5.0,   'kgCO2/oz',   'WGC 2023',                  true),
            (NULL, 'pax_kgco2_kg',         'PAX production',                     1.20,  'kgCO2/kg',   'Litterature',               true),
            (NULL, 'mibc_kgco2_kg',        'MIBC production',                    0.80,  'kgCO2/kg',   'Litterature',               true),
            (NULL, 'flocculant_kgco2_kg',  'Floculant production',               2.50,  'kgCO2/kg',   'Litterature',               true)
    """)


def downgrade():
    op.execute(
        "DELETE FROM carbon_emission_factors WHERE project_id IS NULL"
    )
