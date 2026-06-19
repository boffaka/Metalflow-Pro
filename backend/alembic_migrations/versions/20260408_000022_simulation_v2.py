"""Simulation v2 + mine-to-mill tables

Revision ID: 000022
Revises: 000021
Create Date: 2026-04-08
"""
from alembic import op
import sqlalchemy as sa

revision = "000022"
down_revision = "000021"
branch_labels = None
depends_on = None


def upgrade():
    # 1. simulation_runs_v2
    op.execute("""
        CREATE TABLE IF NOT EXISTS simulation_runs_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            template_id     UUID REFERENCES circuit_templates(id),
            run_type        TEXT NOT NULL,
            scenario_id     UUID,
            status          TEXT DEFAULT 'queued',
            params          JSONB,
            results         JSONB,
            circuit_snapshot JSONB,
            duration_s      NUMERIC,
            created_by      UUID REFERENCES users(id),
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sim_runs_v2_project
            ON simulation_runs_v2(project_id);
    """)

    # 2. sensitivity_results_v2
    op.execute("""
        CREATE TABLE IF NOT EXISTS sensitivity_results_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NOT NULL REFERENCES simulation_runs_v2(id) ON DELETE CASCADE,
            param_key       TEXT NOT NULL,
            param_label     TEXT,
            base_value      NUMERIC,
            delta_pct       NUMERIC,
            impact_npv      NUMERIC,
            impact_irr      NUMERIC,
            impact_aisc     NUMERIC,
            impact_recovery NUMERIC,
            rank            INTEGER
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_sens_v2_run
            ON sensitivity_results_v2(run_id);
    """)

    # 3. optimization_solutions (Pareto front NSGA-II)
    op.execute("""
        CREATE TABLE IF NOT EXISTS optimization_solutions (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NOT NULL REFERENCES simulation_runs_v2(id) ON DELETE CASCADE,
            generation      INTEGER,
            solution_index  INTEGER,
            objectives      JSONB NOT NULL,
            variables       JSONB NOT NULL,
            is_pareto       BOOLEAN DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_opt_sol_run
            ON optimization_solutions(run_id);
    """)

    # 4. mine_schedule (simplified mine schedule by period)
    op.execute("""
        CREATE TABLE IF NOT EXISTS mine_schedule (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            period_type     TEXT DEFAULT 'year',
            period_label    TEXT NOT NULL,
            period_order    INTEGER NOT NULL,
            bench_or_phase  TEXT,
            tonnage_planned NUMERIC NOT NULL,
            grade_au_avg    NUMERIC,
            bwi_avg         NUMERIC,
            s_pct_avg       NUMERIC,
            density_avg     NUMERIC,
            rock_type_mix   JSONB DEFAULT '{}',
            block_ids       JSONB DEFAULT '[]',
            notes           TEXT,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mine_sched_project
            ON mine_schedule(project_id);
    """)

    # 5. lom_profiles (LOM production profile per period)
    op.execute("""
        CREATE TABLE IF NOT EXISTS lom_profiles (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID NOT NULL REFERENCES simulation_runs_v2(id) ON DELETE CASCADE,
            period_order    INTEGER NOT NULL,
            period_label    TEXT NOT NULL,
            tonnage         NUMERIC,
            feed_grade_au   NUMERIC,
            bwi_avg         NUMERIC,
            throughput_tph  NUMERIC,
            recovery_pct    NUMERIC,
            production_oz   NUMERIC,
            revenue_usd     NUMERIC,
            opex_usd        NUMERIC,
            cashflow_usd    NUMERIC,
            cumulative_cf   NUMERIC,
            co2_kg_per_oz   NUMERIC,
            water_m3h       NUMERIC,
            is_critical     BOOLEAN DEFAULT FALSE,
            critical_reason TEXT,
            production_p10  NUMERIC,
            production_p50  NUMERIC,
            production_p90  NUMERIC
        );
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_lom_run
            ON lom_profiles(run_id);
    """)

    # 6. simulation_scenarios (named scenarios for comparison)
    op.execute("""
        CREATE TABLE IF NOT EXISTS simulation_scenarios (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id      UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            name            TEXT NOT NULL,
            description     TEXT,
            params_override JSONB DEFAULT '{}',
            is_base_case    BOOLEAN DEFAULT FALSE,
            color           TEXT DEFAULT '#3B82F6',
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(project_id, name)
        );
    """)


def downgrade():
    op.execute("DROP TABLE IF EXISTS simulation_scenarios CASCADE;")
    op.execute("DROP TABLE IF EXISTS lom_profiles CASCADE;")
    op.execute("DROP TABLE IF EXISTS mine_schedule CASCADE;")
    op.execute("DROP TABLE IF EXISTS optimization_solutions CASCADE;")
    op.execute("DROP TABLE IF EXISTS sensitivity_results_v2 CASCADE;")
    op.execute("DROP TABLE IF EXISTS simulation_runs_v2 CASCADE;")
