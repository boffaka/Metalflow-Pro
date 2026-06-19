# backend/tests/test_vendor_catalog_seed.py
"""Verify vendor catalog seed migration references all equipment families."""
import pathlib

SEED_PATH = pathlib.Path(
    "alembic_migrations/versions/20260407_000017_vendor_catalog_seed.py"
)
REQUIRED_FAMILIES = [
    "SAG Mill", "Ball Mill", "HPGR", "Flotation", "Thickener", "IsaMill", "EW Cell"
]

def test_seed_file_exists():
    assert SEED_PATH.exists()

def test_all_equipment_families_seeded():
    content = SEED_PATH.read_text()
    missing = [f for f in REQUIRED_FAMILIES if f not in content]
    assert not missing, f"Missing equipment families in seed: {missing}"
