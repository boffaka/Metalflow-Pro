"""Professional OPEX model — manpower, power, reagents, mobile, inputs

Revision ID: 000023
Revises: 000022
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "000023"
down_revision = "000022"
branch_labels = None
depends_on = None


def upgrade():
    # 1. Manpower register
    op.execute("""
        CREATE TABLE IF NOT EXISTS opex_manpower (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            department TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT DEFAULT 'Staff',
            schedule TEXT DEFAULT 'Office',
            num_employees INTEGER DEFAULT 1,
            base_salary_hourly NUMERIC,
            bonus_pct NUMERIC DEFAULT 5,
            benefits_pct NUMERIC DEFAULT 20,
            overtime_pct NUMERIC DEFAULT 0,
            total_salary NUMERIC,
            total_cost NUMERIC,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_opex_man_proj
            ON opex_manpower(project_id);
    """)

    # 2. Power consumption by WBS area
    op.execute("""
        CREATE TABLE IF NOT EXISTS opex_power (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            wbs_code TEXT NOT NULL,
            wbs_description TEXT NOT NULL,
            operating_kw NUMERIC DEFAULT 0,
            electrical_efficiency NUMERIC DEFAULT 0.92,
            load_factor NUMERIC DEFAULT 0.80,
            area_availability NUMERIC DEFAULT 0.92,
            hours_per_day NUMERIC DEFAULT 22,
            hours_per_year NUMERIC,
            consumption_kwh_year NUMERIC,
            consumption_kwh_mt NUMERIC,
            total_cost NUMERIC,
            unit_cost_mt NUMERIC,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_opex_pow_proj
            ON opex_power(project_id);
    """)

    # 3. Reagents and consumables
    op.execute("""
        CREATE TABLE IF NOT EXISTS opex_reagents (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            unit_consumption TEXT,
            consumption_rate NUMERIC,
            yearly_consumption NUMERIC,
            unit_cost_cad NUMERIC,
            source TEXT DEFAULT 'A',
            total_cost NUMERIC,
            unit_cost_mt NUMERIC,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_opex_reag_proj
            ON opex_reagents(project_id);
    """)

    # 4. Mobile equipment operating costs
    op.execute("""
        CREATE TABLE IF NOT EXISTS opex_mobile (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            equipment_type TEXT,
            quantity INTEGER DEFAULT 1,
            operating_hours_year NUMERIC,
            cost_per_hour NUMERIC,
            total_cost NUMERIC,
            unit_cost_mt NUMERIC,
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # 5. General OPEX inputs/parameters
    op.execute("""
        CREATE TABLE IF NOT EXISTS opex_inputs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            param_key TEXT NOT NULL,
            param_label TEXT NOT NULL,
            param_value NUMERIC,
            param_unit TEXT,
            source TEXT,
            UNIQUE(project_id, param_key)
        );
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS opex_inputs CASCADE;")
    op.execute("DROP TABLE IF EXISTS opex_mobile CASCADE;")
    op.execute("DROP TABLE IF EXISTS opex_reagents CASCADE;")
    op.execute("DROP TABLE IF EXISTS opex_power CASCADE;")
    op.execute("DROP TABLE IF EXISTS opex_manpower CASCADE;")
