"""Circuit design criteria tables — composable circuit templates

Revision ID: 000018
Revises: 000017
Create Date: 2026-04-08
"""
from alembic import op

revision = "000018"
down_revision = "000017"
branch_labels = None
depends_on = None


def upgrade():
    # ── Table 1: unit_operations_catalog ─────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS unit_operations_catalog (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            op_code         TEXT UNIQUE NOT NULL,
            category        TEXT NOT NULL,
            label           TEXT NOT NULL,
            sort_order      INTEGER NOT NULL DEFAULT 0,
            dependencies    JSONB DEFAULT '[]',
            lims_triggers   JSONB DEFAULT '{}',
            default_criteria JSONB DEFAULT '[]'
        )
    """)

    # ── Table 2: circuit_templates ───────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS circuit_templates (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            is_active       BOOLEAN DEFAULT TRUE,
            created_by      UUID REFERENCES users(id),
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(project_id, name)
        )
    """)

    # ── Table 3: circuit_operations ──────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS circuit_operations (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            template_id     UUID REFERENCES circuit_templates(id) ON DELETE CASCADE,
            op_code         TEXT NOT NULL REFERENCES unit_operations_catalog(op_code),
            enabled         BOOLEAN DEFAULT TRUE,
            sort_order      INTEGER DEFAULT 0,
            created_by      UUID REFERENCES users(id),
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(template_id, op_code)
        )
    """)

    # ── Table 4: design_criteria_v2 ──────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS design_criteria_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            template_id     UUID NOT NULL REFERENCES circuit_templates(id) ON DELETE CASCADE,
            op_code         TEXT NOT NULL REFERENCES unit_operations_catalog(op_code),
            ref_number      TEXT NOT NULL,
            section_title   TEXT,
            item            TEXT NOT NULL,
            unit            TEXT,
            design_value    NUMERIC,
            nominal_value   NUMERIC,
            min_value       NUMERIC,
            max_value       NUMERIC,
            source_code     TEXT DEFAULT 'X',
            revision        TEXT DEFAULT 'A',
            author          TEXT,
            comments        TEXT,
            lims_value      NUMERIC,
            industry_default NUMERIC,
            enabled         BOOLEAN DEFAULT TRUE,
            sort_order      INTEGER DEFAULT 0,
            version         INTEGER DEFAULT 1,
            updated_at      TIMESTAMPTZ DEFAULT NOW(),
            updated_by      UUID REFERENCES users(id),
            UNIQUE(template_id, ref_number)
        )
    """)

    # ── Indexes ──────────────────────────────────────────────────────────
    op.execute("CREATE INDEX idx_circuit_ops_template ON circuit_operations(template_id)")
    op.execute("CREATE INDEX idx_dc_v2_template ON design_criteria_v2(template_id)")
    op.execute("CREATE INDEX idx_dc_v2_project ON design_criteria_v2(project_id)")
    op.execute("CREATE INDEX idx_dc_v2_opcode ON design_criteria_v2(template_id, op_code)")


def downgrade():
    for table in [
        "design_criteria_v2",
        "circuit_operations",
        "circuit_templates",
        "unit_operations_catalog",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
