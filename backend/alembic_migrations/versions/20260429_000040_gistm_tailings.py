"""gistm_tailings — Design Basis, violations and overrides

Adds three append-only tables for the GISTM tailings module:
  - gistm_design_basis    : versioned, draft|active|superseded
  - gistm_violations      : typed checks against the active basis
  - gistm_overrides       : owner-signed deviations (Principle 6)

Extends tsf_design with snapshot columns:
  - gistm_basis_id        : FK to the basis that was active at design time
  - consequence_class_at_design : denormalized for traceability

Revision ID: 000040
Revises: 000039
Create Date: 2026-04-29
"""
from alembic import op


revision = "000040"
down_revision = "000039"
branch_labels = None
depends_on = None


def upgrade():
    # Pre-flight: ensure the geotech tables exist. They are defined in schema.sql
    # but were never carried into an Alembic migration, so Alembic-managed
    # databases lack them. CREATE TABLE IF NOT EXISTS keeps this idempotent on
    # databases that already created them via schema.sql.
    op.execute("""
        CREATE TABLE IF NOT EXISTS geotech_tests (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            sample_id       UUID REFERENCES lims_samples(id) ON DELETE SET NULL,
            test_code       TEXT NOT NULL,
            results         JSONB DEFAULT '{}',
            laboratory      TEXT,
            test_date       DATE,
            notes           TEXT,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_geotech_tests_project ON geotech_tests(project_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS slope_analyses (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            location            TEXT,
            slope_angle_deg     NUMERIC,
            slope_height_m      NUMERIC,
            cohesion_kpa        NUMERIC,
            friction_angle_deg  NUMERIC,
            gamma_kn_m3         NUMERIC,
            pore_pressure_ratio NUMERIC,
            method              TEXT DEFAULT 'Bishop',
            fs_static           NUMERIC,
            fs_seismic          NUMERIC,
            is_compliant        BOOLEAN DEFAULT false,
            failure_surface     JSONB DEFAULT '{}',
            created_at          TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_slope_analyses_project ON slope_analyses(project_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS tsf_design (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            version             INTEGER DEFAULT 1,
            construction_method TEXT,
            total_capacity_m3   NUMERIC,
            annual_deposition_t NUMERIC,
            raise_height_m      NUMERIC,
            embankment_area_ha  NUMERIC,
            fs_static           NUMERIC,
            fs_seismic          NUMERIC,
            is_mac_compliant    BOOLEAN DEFAULT false,
            water_balance       JSONB DEFAULT '{}',
            notes               TEXT,
            created_at          TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    # gistm_design_basis ------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS gistm_design_basis (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            version         INTEGER NOT NULL,
            status          TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','active','superseded')),
            par_count                INTEGER NOT NULL CHECK (par_count >= 0),
            env_damage_class         TEXT NOT NULL
                            CHECK (env_damage_class IN ('none','minor','moderate','major','catastrophic')),
            economic_damage_usd_m    NUMERIC NOT NULL CHECK (economic_damage_usd_m >= 0),
            critical_infra_downstream BOOLEAN NOT NULL DEFAULT false,
            consequence_class        TEXT NOT NULL
                            CHECK (consequence_class IN ('low','significant','high','very_high','extreme')),
            idf_return_period_yr     INTEGER NOT NULL CHECK (idf_return_period_yr > 0),
            mde_return_period_yr     INTEGER NOT NULL CHECK (mde_return_period_yr > 0),
            fs_static_min            NUMERIC NOT NULL CHECK (fs_static_min > 0),
            fs_seismic_min           NUMERIC NOT NULL CHECK (fs_seismic_min > 0),
            fs_post_liquefaction_min NUMERIC NOT NULL CHECK (fs_post_liquefaction_min > 0),
            allowed_construction_methods TEXT[] NOT NULL,
            pga_threshold_g          NUMERIC,
            created_by      UUID NOT NULL REFERENCES users(id),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            activated_by    UUID REFERENCES users(id),
            activated_at    TIMESTAMPTZ,
            notes           TEXT,
            UNIQUE (project_id, version)
        )
    """)
    # Partial unique index : only one 'active' basis per project at any time
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gistm_basis_one_active
            ON gistm_design_basis(project_id) WHERE status = 'active'
    """)

    # gistm_violations --------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS gistm_violations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            basis_id        UUID NOT NULL REFERENCES gistm_design_basis(id),
            tsf_design_id   UUID NOT NULL REFERENCES tsf_design(id) ON DELETE CASCADE,
            rule_code       TEXT NOT NULL,
            severity        TEXT NOT NULL CHECK (severity IN ('error','warning')),
            observed_value  JSONB NOT NULL,
            required_value  JSONB NOT NULL,
            message         TEXT NOT NULL,
            detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_gistm_violations_tsf ON gistm_violations(tsf_design_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_gistm_violations_basis ON gistm_violations(basis_id)")

    # gistm_overrides ---------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS gistm_overrides (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            violation_id    UUID NOT NULL UNIQUE
                            REFERENCES gistm_violations(id) ON DELETE CASCADE,
            justification   TEXT NOT NULL CHECK (length(justification) >= 50),
            signed_by       UUID NOT NULL REFERENCES users(id),
            signed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    # tsf_design extension ----------------------------------------------------
    op.execute("""
        ALTER TABLE tsf_design
            ADD COLUMN IF NOT EXISTS gistm_basis_id UUID
                REFERENCES gistm_design_basis(id),
            ADD COLUMN IF NOT EXISTS consequence_class_at_design TEXT
                CHECK (consequence_class_at_design IS NULL OR consequence_class_at_design
                       IN ('low','significant','high','very_high','extreme'))
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_tsf_design_basis ON tsf_design(gistm_basis_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_tsf_design_basis")
    op.execute("""
        ALTER TABLE tsf_design
            DROP COLUMN IF EXISTS gistm_basis_id,
            DROP COLUMN IF EXISTS consequence_class_at_design
    """)
    op.execute("DROP TABLE IF EXISTS gistm_overrides")
    op.execute("DROP INDEX IF EXISTS idx_gistm_violations_basis")
    op.execute("DROP INDEX IF EXISTS idx_gistm_violations_tsf")
    op.execute("DROP TABLE IF EXISTS gistm_violations")
    op.execute("DROP INDEX IF EXISTS idx_gistm_basis_one_active")
    op.execute("DROP TABLE IF EXISTS gistm_design_basis")
