"""Reactivate the Gosselin Gold Mine circuit template.

The circuit template eb7c2704-ce38-446f-9c6e-cac68f194ee4 was marked
is_active=FALSE (likely by a soft-delete operation), which prevents
auto-generate from finding it. This migration reactivates it so the
mass balance can be regenerated with all sections (LEACHING, CIP, ADR,
DETOX, TAILINGS_THICKENER).

Revision ID: 000051
Revises: 000050
Create Date: 2026-05-12
"""
from alembic import op

revision = "000051"
down_revision = "000050"
branch_labels = None
depends_on = None

_TEMPLATE_ID = "eb7c2704-ce38-446f-9c6e-cac68f194ee4"
_PROJECT_ID  = "5d5e524d-c0dd-4259-b139-06a470df0738"


def upgrade():
    # Ensure only this template is active for the project
    op.execute(
        f"UPDATE circuit_templates SET is_active = FALSE "
        f"WHERE project_id = '{_PROJECT_ID}' AND id != '{_TEMPLATE_ID}'"
    )
    op.execute(
        f"UPDATE circuit_templates SET is_active = TRUE, updated_at = NOW() "
        f"WHERE id = '{_TEMPLATE_ID}' AND project_id = '{_PROJECT_ID}'"
    )


def downgrade():
    op.execute(
        f"UPDATE circuit_templates SET is_active = FALSE "
        f"WHERE id = '{_TEMPLATE_ID}'"
    )
