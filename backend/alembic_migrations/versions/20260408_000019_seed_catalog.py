"""Seed unit_operations_catalog with 60 operations

Revision ID: 000019
Revises: 000018
Create Date: 2026-04-08
"""
from alembic import op
import json

revision = "000019"
down_revision = "000018"
branch_labels = None
depends_on = None


def upgrade():
    import sys, os
    # Add backend to path so we can import engines
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from engines.circuit_catalog import CATALOG

    conn = op.get_bind()
    for entry in CATALOG:
        conn.execute(
            __import__('sqlalchemy').text("""
            INSERT INTO unit_operations_catalog (op_code, category, label, sort_order, dependencies, lims_triggers, default_criteria)
            VALUES (:op_code, :category, :label, :sort_order, :deps, :triggers, :criteria)
            ON CONFLICT (op_code) DO UPDATE SET
                category = EXCLUDED.category, label = EXCLUDED.label,
                sort_order = EXCLUDED.sort_order, dependencies = EXCLUDED.dependencies,
                lims_triggers = EXCLUDED.lims_triggers, default_criteria = EXCLUDED.default_criteria
            """),
            {
                "op_code": entry["op_code"],
                "category": entry["category"],
                "label": entry["label"],
                "sort_order": entry["sort_order"],
                "deps": json.dumps(entry.get("dependencies", [])),
                "triggers": json.dumps(entry.get("lims_triggers", {})),
                "criteria": json.dumps(entry.get("default_criteria", [])),
            }
        )


def downgrade():
    op.execute("DELETE FROM unit_operations_catalog;")
