"""Mass balance v2 tables, snapshots, Monte Carlo runs, carbon emission factors

Revision ID: 000020
Revises: 000019
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "000020"
down_revision = "000019"
branch_labels = None
depends_on = None


def upgrade():
    # 1. mass_balance_sections_v2
    op.execute("""
        CREATE TABLE IF NOT EXISTS mass_balance_sections_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            template_id     UUID REFERENCES circuit_templates(id) ON DELETE CASCADE,
            section_name    TEXT NOT NULL,
            op_code         TEXT,
            sort_order      INTEGER DEFAULT 0,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mb_sections_project
        ON mass_balance_sections_v2(project_id);
    """)

    # 2. mass_balance_streams_v2
    op.execute("""
        CREATE TABLE IF NOT EXISTS mass_balance_streams_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            section_id      UUID NOT NULL REFERENCES mass_balance_sections_v2(id) ON DELETE CASCADE,
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            stream_name     TEXT NOT NULL,
            hours_per_day   NUMERIC DEFAULT 22.1,
            solids_tpd      NUMERIC DEFAULT 0,
            solids_tph      NUMERIC DEFAULT 0,
            solids_m3h      NUMERIC DEFAULT 0,
            solids_sg       NUMERIC DEFAULT 2.74,
            water_tpd       NUMERIC DEFAULT 0,
            water_tph       NUMERIC DEFAULT 0,
            water_m3h       NUMERIC DEFAULT 0,
            water_sg        NUMERIC DEFAULT 1.0,
            slurry_tpd      NUMERIC DEFAULT 0,
            slurry_tph      NUMERIC DEFAULT 0,
            slurry_m3h      NUMERIC DEFAULT 0,
            slurry_pct_w    NUMERIC DEFAULT 0,
            slurry_sg       NUMERIC DEFAULT 1.0,
            au_gt           NUMERIC,
            s_pct           NUMERIC,
            is_balance_check BOOLEAN DEFAULT FALSE,
            is_recirculation BOOLEAN DEFAULT FALSE,
            source          TEXT DEFAULT 'calculated',
            sort_order      INTEGER DEFAULT 0,
            version         INTEGER DEFAULT 1,
            updated_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mb_streams_section
        ON mass_balance_streams_v2(section_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mb_streams_project
        ON mass_balance_streams_v2(project_id);
    """)

    # 3. mass_balance_snapshots
    op.execute("""
        CREATE TABLE IF NOT EXISTS mass_balance_snapshots (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            snapshot_data   JSONB NOT NULL,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            created_by      UUID REFERENCES users(id)
        );
    """)

    # 4. monte_carlo_runs
    op.execute("""
        CREATE TABLE IF NOT EXISTS monte_carlo_runs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            template_id     UUID REFERENCES circuit_templates(id),
            run_type        TEXT NOT NULL DEFAULT 'balance',
            n_simulations   INTEGER NOT NULL DEFAULT 5000,
            status          TEXT DEFAULT 'queued',
            progress_pct    INTEGER DEFAULT 0,
            params          JSONB,
            results         JSONB,
            circuit_snapshot JSONB,
            started_at      TIMESTAMPTZ,
            completed_at    TIMESTAMPTZ,
            created_by      UUID REFERENCES users(id),
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # 5. carbon_emission_factors
    op.execute("""
        CREATE TABLE IF NOT EXISTS carbon_emission_factors (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
            factor_key      TEXT NOT NULL,
            factor_label    TEXT NOT NULL,
            factor_value    NUMERIC NOT NULL,
            unit            TEXT,
            source          TEXT,
            is_default      BOOLEAN DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(project_id, factor_key)
        );
    """)

    # 6. Seed default carbon factors (global defaults with project_id=NULL)
    op.execute("""
        INSERT INTO carbon_emission_factors
            (project_id, factor_key, factor_label, factor_value, unit, source, is_default)
        VALUES
            (NULL, 'grid_kgco2_kwh',       'Grid emission factor (Ontario)', 0.03,  'kgCO2/kWh',  'Environment Canada 2025', true),
            (NULL, 'nacn_kgco2_kg',        'NaCN (Andrussow process)',       1.87,  'kgCO2/kg',   'IPCC',                    true),
            (NULL, 'cao_kgco2_kg',         'CaO (calcination)',              0.75,  'kgCO2/kg',   'IPCC',                    true),
            (NULL, 'h2o2_kgco2_kg',        'H2O2 (Caros acid)',             0.50,  'kgCO2/kg',   'IPCC',                    true),
            (NULL, 'cuso4_kgco2_kg',       'CuSO4',                         2.30,  'kgCO2/kg',   'IPCC',                    true),
            (NULL, 'so2_kgco2_kg',         'SO2 liquide (by-product)',       0.00,  'kgCO2/kg',   'By-product',              true),
            (NULL, 'transport_kgco2_tkm',  'Transport routier',             0.062, 'kgCO2/t.km', 'GHG Protocol',            true),
            (NULL, 'smelt_kgco2_oz',       'Fonderie dore (Scope 1)',       5.0,   'kgCO2/oz',   'WGC',                     true),
            (NULL, 'pax_kgco2_kg',         'PAX production',                1.20,  'kgCO2/kg',   'Literature',              true),
            (NULL, 'mibc_kgco2_kg',        'MIBC production',               0.80,  'kgCO2/kg',   'Literature',              true),
            (NULL, 'flocculant_kgco2_kg',  'Flocculant production',         2.50,  'kgCO2/kg',   'Literature',              true)
        ON CONFLICT DO NOTHING;
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS carbon_emission_factors CASCADE;")
    op.execute("DROP TABLE IF EXISTS monte_carlo_runs CASCADE;")
    op.execute("DROP TABLE IF EXISTS mass_balance_snapshots CASCADE;")
    op.execute("DROP TABLE IF EXISTS mass_balance_streams_v2 CASCADE;")
    op.execute("DROP TABLE IF EXISTS mass_balance_sections_v2 CASCADE;")
