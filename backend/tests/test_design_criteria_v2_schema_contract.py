from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def test_design_criteria_v2_schema_exposes_metadata_columns_and_conflict_key():
    schema = (BACKEND / "schema.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS design_criteria_v2" in schema
    assert "version INTEGER DEFAULT 1" in schema
    assert "updated_by UUID REFERENCES users(id)" in schema
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_dc_v2_template_ref ON design_criteria_v2(template_id, ref_number);" in schema


def test_design_criteria_v2_alignment_migration_exists():
    migration = (
        BACKEND
        / "alembic_migrations"
        / "versions"
        / "20260622_000079_design_criteria_v2_metadata_alignment.py"
    ).read_text(encoding="utf-8")

    assert 'revision = "000079"' in migration
    assert 'down_revision = "000078"' in migration
    assert "ALTER TABLE design_criteria_v2 ADD COLUMN IF NOT EXISTS version INTEGER DEFAULT 1" in migration
    assert "ALTER TABLE design_criteria_v2 ADD COLUMN IF NOT EXISTS updated_by UUID REFERENCES users(id)" in migration
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_dc_v2_template_ref" in migration


def test_startup_schema_compatibility_repairs_design_criteria_v2_drift():
    main_py = (BACKEND / "main.py").read_text(encoding="utf-8")

    assert "_ensure_design_criteria_v2_columns(cur)" in main_py
    assert '("version", "INTEGER DEFAULT 1")' in main_py
    assert '("updated_by", "UUID REFERENCES users(id)")' in main_py
    assert "ALTER TABLE design_criteria_v2 ADD COLUMN IF NOT EXISTS {col} {dtype}" in main_py
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_dc_v2_template_ref" in main_py
    assert "ON design_criteria_v2(template_id, ref_number)" in main_py
