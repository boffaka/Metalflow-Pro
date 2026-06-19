"""Re-seed unit_operations_catalog with extended default_criteria from PDFs

Revision ID: 000046
Revises: 000045
Create Date: 2026-05-05

This re-runs the catalog seed to propagate the comprehensive
default_criteria added in backend/engines/circuit_catalog.py — 22 ops
now have 12-36 parameters each (vs previously 5-12), organised into
sub-sections (e.g. "HPGR — Paramètres opératoires", "HPGR — Géométrie
des rouleaux", "HPGR — Force de pressage", etc.) so that the design
criteria table renders multiple cards per equipment.

Idempotent — uses the same ON CONFLICT DO UPDATE SET pattern as the
original 000019 seed; just re-runs against the now-richer CATALOG.
"""
from alembic import op
import json

revision = "000046"
down_revision = "000045"
branch_labels = None
depends_on = None


def upgrade():
    import sys, os
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from engines.circuit_catalog import CATALOG
    import sqlalchemy as sa

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
    # Cannot meaningfully revert — the seed is idempotent and the previous
    # default_criteria values were less comprehensive. A real rollback would
    # require restoring the older catalog snapshot, which isn't tracked.
    pass
