"""Re-seed unit operation catalog with equipment parameter criteria

Revision ID: 000048
Revises: 000047
Create Date: 2026-05-09

Re-runs the code catalog seed so production databases receive the
equipment-specific design criteria parameter sets used by the DC table.
"""
from alembic import op
import json

revision = "000048"
down_revision = "000047"
branch_labels = None
depends_on = None


def upgrade():
    import os
    import sys

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    import sqlalchemy as sa
    from engines.circuit_catalog import CATALOG

    conn = op.get_bind()
    for entry in CATALOG:
        conn.execute(
            sa.text(
                """
                INSERT INTO unit_operations_catalog
                    (op_code, category, label, sort_order, dependencies, lims_triggers, default_criteria)
                VALUES
                    (:op_code, :category, :label, :sort_order, :deps, :triggers, :criteria)
                ON CONFLICT (op_code) DO UPDATE SET
                    category = EXCLUDED.category,
                    label = EXCLUDED.label,
                    sort_order = EXCLUDED.sort_order,
                    dependencies = EXCLUDED.dependencies,
                    lims_triggers = EXCLUDED.lims_triggers,
                    default_criteria = EXCLUDED.default_criteria
                """
            ),
            {
                "op_code": entry["op_code"],
                "category": entry["category"],
                "label": entry["label"],
                "sort_order": entry["sort_order"],
                "deps": json.dumps(entry.get("dependencies", [])),
                "triggers": json.dumps(entry.get("lims_triggers", {})),
                "criteria": json.dumps(entry.get("default_criteria", [])),
            },
        )


def downgrade():
    pass
