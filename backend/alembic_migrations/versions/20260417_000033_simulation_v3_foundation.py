"""Simulation v3 foundation: circuit_compilations, optimization_jobs,
simulation_comparison_sets, feature_flags on projects, compilation_id on runs."""

revision = "000033"
down_revision = "000032"

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, ARRAY, UUID


def upgrade():
    # 1) projects.feature_flags (JSONB) — pour SIM_V3_UI et futurs flags projet
    op.execute("""
        ALTER TABLE projects
        ADD COLUMN IF NOT EXISTS feature_flags JSONB DEFAULT '{}'::jsonb
    """)

    # 2) circuit_compilations — snapshot de compilation flowsheet → circuit_template
    op.execute("""
        CREATE TABLE IF NOT EXISTS circuit_compilations (
            id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id         UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            source_type        TEXT NOT NULL CHECK (source_type IN ('flowsheet','scenario_flowsheet','custom')),
            source_id          UUID,
            template_id        UUID NOT NULL REFERENCES circuit_templates(id),
            blocks_hash        TEXT NOT NULL,
            sections_resolved  JSONB DEFAULT '[]'::jsonb,
            branches_detected  JSONB DEFAULT '[]'::jsonb,
            topo_order         JSONB DEFAULT '[]'::jsonb,
            compile_warnings   JSONB DEFAULT '[]'::jsonb,
            created_at         TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_compilations_project ON circuit_compilations(project_id)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_compilations_hash ON circuit_compilations(project_id, blocks_hash)")

    # 3) simulation_runs_v2.compilation_id — lien optionnel run → compilation
    op.execute("""
        ALTER TABLE simulation_runs_v2
        ADD COLUMN IF NOT EXISTS compilation_id UUID REFERENCES circuit_compilations(id)
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_runs_v2_compilation ON simulation_runs_v2(compilation_id)")

    # 4) optimization_jobs (préparatif plan 3 — créée mais non exploitée ici)
    op.execute("""
        CREATE TABLE IF NOT EXISTS optimization_jobs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            compilation_id  UUID REFERENCES circuit_compilations(id),
            mode            TEXT NOT NULL CHECK (mode IN ('sweep','nsga2')),
            objective       TEXT,
            objectives      JSONB DEFAULT '[]'::jsonb,
            variables       JSONB DEFAULT '[]'::jsonb,
            constraints     JSONB DEFAULT '[]'::jsonb,
            status          TEXT DEFAULT 'queued' CHECK (status IN ('queued','running','done','failed')),
            result          JSONB,
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            completed_at    TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_opt_jobs_project ON optimization_jobs(project_id)")

    # 5) simulation_comparison_sets (préparatif plan 3)
    op.execute("""
        CREATE TABLE IF NOT EXISTS simulation_comparison_sets (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            run_ids     UUID[] NOT NULL,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_cmp_sets_project ON simulation_comparison_sets(project_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_cmp_sets_project")
    op.execute("DROP TABLE IF EXISTS simulation_comparison_sets")
    op.execute("DROP INDEX IF EXISTS idx_opt_jobs_project")
    op.execute("DROP TABLE IF EXISTS optimization_jobs")
    op.execute("DROP INDEX IF EXISTS idx_runs_v2_compilation")
    op.execute("ALTER TABLE simulation_runs_v2 DROP COLUMN IF EXISTS compilation_id")
    op.execute("DROP INDEX IF EXISTS idx_compilations_hash")
    op.execute("DROP INDEX IF EXISTS idx_compilations_project")
    op.execute("DROP TABLE IF EXISTS circuit_compilations")
    op.execute("ALTER TABLE projects DROP COLUMN IF EXISTS feature_flags")
