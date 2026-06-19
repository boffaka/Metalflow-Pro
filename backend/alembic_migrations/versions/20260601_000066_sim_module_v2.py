# backend/alembic_migrations/versions/20260601_000066_sim_module_v2.py
"""Sim Module v2 — extend existing flowsheet tables and add node-results,
expansion-scenarios, and optimization-jobs tables.

Depends on migration 000065 (flowsheet_graph_tables).
"""
revision = "000066"
down_revision = "000065"
revises = "000065"

from alembic import op


def upgrade():
    op.execute("""
        ALTER TABLE flowsheet_graphs ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft';
        ALTER TABLE flowsheet_graphs ADD COLUMN IF NOT EXISTS ore_type TEXT DEFAULT 'free_milling';
        ALTER TABLE flowsheet_graphs ADD COLUMN IF NOT EXISTS description TEXT;

        ALTER TABLE fg_nodes ADD COLUMN IF NOT EXISTS design_capacity_tph FLOAT DEFAULT 0;
        ALTER TABLE fg_nodes ADD COLUMN IF NOT EXISTS availability_pct FLOAT DEFAULT 95;
        ALTER TABLE fg_nodes ADD COLUMN IF NOT EXISTS category TEXT;
        ALTER TABLE fg_nodes ADD COLUMN IF NOT EXISTS stream_type TEXT DEFAULT 'pulp';

        ALTER TABLE flowsheet_runs ADD COLUMN IF NOT EXISTS feed_input JSONB DEFAULT '{}';
        ALTER TABLE flowsheet_runs ADD COLUMN IF NOT EXISTS global_results JSONB DEFAULT '{}';
        ALTER TABLE flowsheet_runs ADD COLUMN IF NOT EXISTS mode TEXT DEFAULT 'steady_state';
        ALTER TABLE flowsheet_runs ADD COLUMN IF NOT EXISTS scenario_label TEXT;
        ALTER TABLE flowsheet_runs ADD COLUMN IF NOT EXISTS convergence_error FLOAT DEFAULT 0;
        ALTER TABLE flowsheet_runs ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

        ALTER TABLE fg_stream_results ADD COLUMN IF NOT EXISTS volume_flow_m3h FLOAT;
        ALTER TABLE fg_stream_results ADD COLUMN IF NOT EXISTS ph FLOAT;
        ALTER TABLE fg_stream_results ADD COLUMN IF NOT EXISTS temperature_c FLOAT;
        ALTER TABLE fg_stream_results ADD COLUMN IF NOT EXISTS dissolved_gold_mg_l FLOAT;
        ALTER TABLE fg_stream_results ADD COLUMN IF NOT EXISTS cyanide_conc_ppm FLOAT;

        CREATE TABLE IF NOT EXISTS fg_node_results (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id UUID REFERENCES flowsheet_runs(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL,
            feed_rate_tph FLOAT,
            product_rate_tph FLOAT,
            recovery_pct FLOAT,
            energy_kwh_t FLOAT,
            utilization_rate FLOAT,
            is_bottleneck BOOLEAN DEFAULT false,
            kpis JSONB DEFAULT '{}',
            reagent_consumptions JSONB DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_fg_node_results_run ON fg_node_results(run_id);

        CREATE TABLE IF NOT EXISTS fg_expansion_scenarios (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            graph_id UUID REFERENCES flowsheet_graphs(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            target_increase_pct FLOAT DEFAULT 0,
            modifications JSONB DEFAULT '[]',
            simulation_run_id UUID,
            economics JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS idx_fg_expansion_graph ON fg_expansion_scenarios(graph_id);

        CREATE TABLE IF NOT EXISTS fg_optimization_jobs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            graph_id UUID REFERENCES flowsheet_graphs(id) ON DELETE CASCADE,
            objective TEXT NOT NULL,
            variables JSONB DEFAULT '[]',
            constraints JSONB DEFAULT '[]',
            method TEXT DEFAULT 'genetic_algorithm',
            status TEXT DEFAULT 'queued',
            feed_input JSONB DEFAULT '{}',
            results JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT now(),
            completed_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_fg_optjobs_graph ON fg_optimization_jobs(graph_id);
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS fg_optimization_jobs CASCADE;
        DROP TABLE IF EXISTS fg_expansion_scenarios CASCADE;
        DROP TABLE IF EXISTS fg_node_results CASCADE;

        ALTER TABLE fg_stream_results DROP COLUMN IF EXISTS volume_flow_m3h;
        ALTER TABLE fg_stream_results DROP COLUMN IF EXISTS ph;
        ALTER TABLE fg_stream_results DROP COLUMN IF EXISTS temperature_c;
        ALTER TABLE fg_stream_results DROP COLUMN IF EXISTS dissolved_gold_mg_l;
        ALTER TABLE fg_stream_results DROP COLUMN IF EXISTS cyanide_conc_ppm;

        ALTER TABLE flowsheet_runs DROP COLUMN IF EXISTS feed_input;
        ALTER TABLE flowsheet_runs DROP COLUMN IF EXISTS global_results;
        ALTER TABLE flowsheet_runs DROP COLUMN IF EXISTS mode;
        ALTER TABLE flowsheet_runs DROP COLUMN IF EXISTS scenario_label;
        ALTER TABLE flowsheet_runs DROP COLUMN IF EXISTS convergence_error;
        ALTER TABLE flowsheet_runs DROP COLUMN IF EXISTS completed_at;

        ALTER TABLE fg_nodes DROP COLUMN IF EXISTS design_capacity_tph;
        ALTER TABLE fg_nodes DROP COLUMN IF EXISTS availability_pct;
        ALTER TABLE fg_nodes DROP COLUMN IF EXISTS category;
        ALTER TABLE fg_nodes DROP COLUMN IF EXISTS stream_type;

        ALTER TABLE flowsheet_graphs DROP COLUMN IF EXISTS status;
        ALTER TABLE flowsheet_graphs DROP COLUMN IF EXISTS ore_type;
        ALTER TABLE flowsheet_graphs DROP COLUMN IF EXISTS description;
    """)
