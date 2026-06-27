"""fix_crusher_design_values — recalculer design_value et nominal_value GIRATOIRE.

Migration 000082 avait corrigé le dag_key mais Alembic l'avait déjà marquée
comme appliquée avant l'ajout du SQL de recalcul → les valeurs stockées
n'ont jamais été mises à jour.

Cette migration recalcule directement les valeurs dans design_criteria_v2:
    nominal = target_tph × (availability_pct / 75%)
    design  = nominal × 1.15
"""

from alembic import op

revision = "000083"
down_revision = "000082"
revises = "000082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute("""
        UPDATE design_criteria_v2 d
        SET design_value  = ROUND(p.target_tph * (COALESCE(p.availability_pct, 92) / 100.0)
                                  / 0.75 * 1.15, 0),
            nominal_value = ROUND(p.target_tph * (COALESCE(p.availability_pct, 92) / 100.0)
                                  / 0.75, 0),
            source_code   = 'C',
            dag_key       = 'crusher_design_tph',
            updated_at    = NOW()
        FROM circuit_templates t
        JOIN projects p ON p.id = t.project_id
        WHERE d.template_id = t.id
          AND d.enabled = TRUE
          AND UPPER(d.op_code) IN ('GIRATOIRE', 'JAW', 'PRIMARY_CRUSHER')
          AND (LOWER(d.item) LIKE '%design%alimentation%'
               OR LOWER(d.item) LIKE '%d_bit%design%alimentation%')
          AND p.target_tph > 0
          AND COALESCE(d.source_code, 'X') NOT IN ('M', 'O')
    """)


def downgrade() -> None:
    # Cannot restore original values without backup — no-op
    pass
