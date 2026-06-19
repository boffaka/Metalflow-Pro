# backend/tests/test_v4_migration.py
"""Verify all 32 v4 tables are declared in the migration."""
import ast, pathlib

MIGRATION_PATH = pathlib.Path(
    "alembic_migrations/versions/20260407_000016_v4_new_tables.py"
)

EXPECTED_TABLES = [
    # Domain ①
    "simulation_runs", "pareto_fronts", "sensitivity_analyses", "model_artifacts",
    # Domain ②
    "dcf_models", "monte_carlo_runs", "economic_indicators", "sensitivity_vars",
    # Domain ③
    "equipment_sizing", "vendor_catalog", "equipment_selections", "capex_correlations",
    # Domain ④
    "pid_diagrams", "pid_instruments", "plant_layout_3d", "layout_zones",
    # Domain ⑤
    "process_tags", "tag_readings", "kpi_snapshots", "data_connectors", "anomaly_events",
    # Domain ⑥
    "pid_loops", "grafcet_sequences", "cause_effect_matrix",
    "fat_sat_checklists", "dynamic_sim_runs",
    # Domain ⑧
    "geotech_tests", "slope_analyses", "aba_nag_results",
    "ard_classifications", "closure_plan_items", "tsf_design",
]

def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"Migration file not found: {MIGRATION_PATH}"

def test_all_32_tables_referenced():
    content = MIGRATION_PATH.read_text()
    missing = [t for t in EXPECTED_TABLES if t not in content]
    assert not missing, f"Tables missing from migration: {missing}"

def test_migration_has_downgrade():
    content = MIGRATION_PATH.read_text()
    assert "def downgrade" in content
    for table in EXPECTED_TABLES:
        assert table in content
