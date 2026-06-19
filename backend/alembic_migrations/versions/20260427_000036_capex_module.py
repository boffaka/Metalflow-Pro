"""Add CAPEX module: circuit_type, equipment columns, capex_factors table.

Revision ID: 20260427_000036
Revises: 20260426_000035
Create Date: 2026-04-27 00:00:00.000000

NOTE: All existing projects are backfilled with circuit_type='cil_conventional'.
This is a placeholder label — projects whose actual flowsheet differs (e.g. HPGR+Ball,
SABC, refractory) MUST update their circuit_type before re-seeding from a template.
Existing equipment is backfilled with is_overridden=true so the seed pass cannot
overwrite it without force=true.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "000036"
down_revision = "000035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("circuit_type", sa.String(50), nullable=False, server_default="cil_conventional")
    )
    op.create_check_constraint(
        "ck_projects_circuit_type",
        "projects",
        "circuit_type IN ('cil_conventional', 'cip_conventional', 'sabc', 'sag_ball', 'hpgr_ball', "
        "'three_stage_crush_ball', 'heap_leach', 'gravity_flotation_cil', "
        "'pox_refractory', 'biox_refractory', 'roaster_refractory')"
    )

    op.add_column("equipment_v2", sa.Column("template_key", sa.String(100), nullable=True))
    op.add_column("equipment_v2", sa.Column("parametric_alpha", sa.Numeric(18, 2), nullable=True))
    op.add_column("equipment_v2", sa.Column("parametric_beta", sa.Numeric(5, 2), nullable=True))
    op.add_column("equipment_v2", sa.Column("is_overridden", sa.Boolean, nullable=False, server_default="false"))
    op.add_column("equipment_v2", sa.Column("seeded_from_template", sa.Boolean, nullable=False, server_default="false"))

    op.create_table(
        "capex_factors",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("indirect_pct", sa.Numeric(5, 3), nullable=False, server_default="0.300"),
        sa.Column("epcm_pct", sa.Numeric(5, 3), nullable=False, server_default="0.150"),
        sa.Column("contingency_pct", sa.Numeric(5, 3), nullable=False, server_default="0.150"),
        sa.Column("is_overridden_indirect", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_overridden_epcm", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_overridden_contingency", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id"),
        sa.CheckConstraint("indirect_pct >= 0 AND indirect_pct <= 2", name="ck_capex_factors_indirect_range"),
        sa.CheckConstraint("epcm_pct >= 0 AND epcm_pct <= 2", name="ck_capex_factors_epcm_range"),
        sa.CheckConstraint("contingency_pct >= 0 AND contingency_pct <= 2", name="ck_capex_factors_contingency_range")
    )
    op.create_index("ix_capex_factors_project_id", "capex_factors", ["project_id"])

    op.execute("""INSERT INTO capex_factors (project_id, indirect_pct, epcm_pct, contingency_pct, updated_at)
      SELECT id, 0.30, 0.15, 0.15, now() FROM projects
      ON CONFLICT (project_id) DO NOTHING""")

    op.execute("UPDATE equipment_v2 SET is_overridden = true WHERE is_overridden = false")


def downgrade() -> None:
    op.drop_index("ix_capex_factors_project_id", table_name="capex_factors")
    op.drop_table("capex_factors")
    op.drop_column("equipment_v2", "seeded_from_template")
    op.drop_column("equipment_v2", "is_overridden")
    op.drop_column("equipment_v2", "parametric_beta")
    op.drop_column("equipment_v2", "parametric_alpha")
    op.drop_column("equipment_v2", "template_key")
    op.drop_constraint("ck_projects_circuit_type", "projects", type_="check")
    op.drop_column("projects", "circuit_type")
