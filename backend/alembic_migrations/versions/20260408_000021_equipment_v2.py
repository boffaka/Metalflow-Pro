"""Equipment v2 (full Mechanical Equipment Register) and WBS reference codes

Revision ID: 000021
Revises: 000020
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "000021"
down_revision = "000020"
branch_labels = None
depends_on = None


def upgrade():
    # 1. wbs_codes reference table
    op.execute("""
        CREATE TABLE IF NOT EXISTS wbs_codes (
            code        TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            sort_order  INTEGER DEFAULT 0
        );
    """)

    op.execute("""
        INSERT INTO wbs_codes (code, description, sort_order) VALUES
            ('501', 'Water', 10),
            ('502', 'Air', 20),
            ('504', 'Fire Protection', 30),
            ('554', 'Primary Crushing', 100),
            ('555', 'Secondary Crushing', 110),
            ('557', 'Crushed Ore Storage', 120),
            ('558', 'Tertiary Crushing (HPGR)', 130),
            ('562', 'Grinding', 200),
            ('565', 'Flotation', 300),
            ('567', 'Regrinding', 310),
            ('571', 'Thickening', 400),
            ('576', 'Carbon-in-Pulp / In-Leach (CIP/CIL)', 500),
            ('580', 'Carbon Stripping & Regeneration', 510),
            ('581', 'Electrowinning and Refinery', 520),
            ('582', 'Cyanide Destruction', 600),
            ('583', 'Tailing Dewatering', 610),
            ('585', 'Reagent', 700),
            ('588', 'Oxygen Plant / System', 710),
            ('598', 'Mobile Equipment', 800),
            ('691', 'Reclaim Water Pumping Station', 900)
        ON CONFLICT (code) DO NOTHING;
    """)

    # 2. equipment_v2 (full Mechanical Equipment Register)
    op.execute("""
        CREATE TABLE IF NOT EXISTS equipment_v2 (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            template_id         UUID REFERENCES circuit_templates(id) ON DELETE SET NULL,
            op_code             TEXT,
            item_number         INTEGER,
            wbs_code            TEXT NOT NULL,
            wbs_description     TEXT NOT NULL,
            pfd_drawing         TEXT,
            revision            TEXT DEFAULT 'A',
            initials            TEXT,
            bu                  TEXT DEFAULT '47',
            eq_type             TEXT NOT NULL,
            seq_no              TEXT NOT NULL,
            equipment_tag       TEXT NOT NULL,
            equipment_name      TEXT NOT NULL,
            quantity            INTEGER DEFAULT 1,
            description         TEXT,
            comments            TEXT,
            specifications      TEXT,
            has_vfd             BOOLEAN DEFAULT FALSE,
            duty_status         TEXT DEFAULT 'Duty',
            installed_kw        NUMERIC DEFAULT 0,
            emergency_power     BOOLEAN DEFAULT FALSE,
            vendor              TEXT,
            price_cad           NUMERIC,
            installation_hours  NUMERIC,
            reference_doc       TEXT,
            is_long_lead        BOOLEAN DEFAULT FALSE,
            lead_time_weeks     INTEGER,
            weight_kg           NUMERIC,
            material            TEXT,
            enabled             BOOLEAN DEFAULT TRUE,
            sort_order          INTEGER DEFAULT 0,
            version             INTEGER DEFAULT 1,
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            updated_at          TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eq_v2_project
        ON equipment_v2(project_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eq_v2_wbs
        ON equipment_v2(project_id, wbs_code);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eq_v2_template
        ON equipment_v2(template_id);
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_eq_v2_opcode
        ON equipment_v2(op_code);
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS equipment_v2 CASCADE;")
    op.execute("DROP TABLE IF EXISTS wbs_codes CASCADE;")
