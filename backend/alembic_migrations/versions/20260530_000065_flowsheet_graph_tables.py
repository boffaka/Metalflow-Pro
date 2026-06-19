# backend/alembic_migrations/versions/20260530_000061_flowsheet_graph_tables.py
"""Add flowsheet graph tables for Flowsheet Simulator v4.

Tables: flowsheet_graphs, fg_nodes, fg_edges, flowsheet_runs,
        fg_stream_results, flowsheet_starters
"""
revision = "000065"
down_revision = "000064"
revises = "000064"

from alembic import op


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS flowsheet_graphs (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id  UUID REFERENCES projects(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            version     INT DEFAULT 1,
            canvas_state JSONB DEFAULT '{}',
            created_at  TIMESTAMPTZ DEFAULT now(),
            updated_at  TIMESTAMPTZ DEFAULT now()
        );

        CREATE TABLE IF NOT EXISTS fg_nodes (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            graph_id    UUID REFERENCES flowsheet_graphs(id) ON DELETE CASCADE,
            op_code     TEXT NOT NULL,
            label       TEXT,
            params      JSONB DEFAULT '{}',
            position_x  FLOAT NOT NULL DEFAULT 0,
            position_y  FLOAT NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS fg_edges (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            graph_id     UUID REFERENCES flowsheet_graphs(id) ON DELETE CASCADE,
            source_node  UUID REFERENCES fg_nodes(id) ON DELETE CASCADE,
            target_node  UUID REFERENCES fg_nodes(id) ON DELETE CASCADE,
            stream_label TEXT,
            port_source  TEXT DEFAULT 'out',
            port_target  TEXT DEFAULT 'in',
            is_tear_stream BOOLEAN DEFAULT false
        );

        CREATE TABLE IF NOT EXISTS flowsheet_runs (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            graph_id     UUID REFERENCES flowsheet_graphs(id) ON DELETE CASCADE,
            triggered_at TIMESTAMPTZ DEFAULT now(),
            status       TEXT DEFAULT 'running',
            iterations   INT DEFAULT 0,
            kpis         JSONB DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS fg_stream_results (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id          UUID REFERENCES flowsheet_runs(id) ON DELETE CASCADE,
            edge_id         UUID REFERENCES fg_edges(id) ON DELETE CASCADE,
            iteration       INT DEFAULT 0,
            solids_tph      FLOAT,
            water_tph       FLOAT,
            slurry_tph      FLOAT,
            au_g_t          FLOAT,
            au_recovery_pct FLOAT,
            p80_um          FLOAT,
            energy_kwh_t    FLOAT,
            cn_kg_t         FLOAT,
            extra           JSONB DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS flowsheet_starters (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            code        TEXT UNIQUE NOT NULL,
            family      TEXT,
            name        TEXT,
            description TEXT,
            graph_json  JSONB NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_fg_nodes_graph ON fg_nodes(graph_id);
        CREATE INDEX IF NOT EXISTS idx_fg_edges_graph ON fg_edges(graph_id);
        CREATE INDEX IF NOT EXISTS idx_fg_stream_results_run ON fg_stream_results(run_id);
        CREATE INDEX IF NOT EXISTS idx_flowsheet_runs_graph ON flowsheet_runs(graph_id);
        CREATE INDEX IF NOT EXISTS idx_fg_stream_results_edge ON fg_stream_results(edge_id);
        CREATE INDEX IF NOT EXISTS idx_flowsheet_graphs_project ON flowsheet_graphs(project_id);
    """)


def downgrade():
    op.execute("""
        DROP TABLE IF EXISTS fg_stream_results CASCADE;
        DROP TABLE IF EXISTS flowsheet_runs CASCADE;
        DROP TABLE IF EXISTS fg_edges CASCADE;
        DROP TABLE IF EXISTS fg_nodes CASCADE;
        DROP TABLE IF EXISTS flowsheet_graphs CASCADE;
        DROP TABLE IF EXISTS flowsheet_starters CASCADE;
    """)
