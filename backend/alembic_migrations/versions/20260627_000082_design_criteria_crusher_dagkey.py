"""design_criteria_crusher_dagkey — corriger dag_key + recalculer valeurs concasseur.

La ligne GIRATOIRE "Débit design alimentation" avait dag_key="target_tph" ce qui
causait la cascade DAG à écraser design_value avec target_tph brut (sans correction
disponibilité). Résultat: nominal > design affiché dans les critères de conception.

Cette migration:
1. Corrige dag_key → "crusher_design_tph"
2. Recalcule et écrase design_value + nominal_value avec les formules PDC correctes:
     nominal = target_tph × (availability_pct / 75.0)
     design  = nominal × 1.15
"""

from alembic import op

revision = "000082"
down_revision = "000081"
revises = "000081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Fix dag_key
    conn.execute("""
        UPDATE design_criteria_v2
        SET dag_key = 'crusher_design_tph',
            source_code = 'C',
            updated_at = NOW()
        WHERE UPPER(op_code) IN ('GIRATOIRE', 'JAW', 'PRIMARY_CRUSHER')
          AND (LOWER(item) LIKE '%d%bit%design%alimentation%'
               OR LOWER(item) LIKE '%debit%design%alimentation%')
          AND dag_key = 'target_tph'
    """)

    # 2. Recalculate design_value and nominal_value for ALL GIRATOIRE feed rate rows
    #    using the correct PDC formula:
    #      nominal = plant_tph × (grinding_avail / 75%)
    #      design  = nominal × 1.15
    conn.execute("""
        UPDATE design_criteria_v2 d
        SET design_value = ROUND(p.target_tph * (p.availability_pct / 100.0) / 0.75 * 1.15, 0),
            nominal_value = ROUND(p.target_tph * (p.availability_pct / 100.0) / 0.75, 0),
            source_code = 'C',
            dag_key = 'crusher_design_tph',
            updated_at = NOW()
        FROM circuit_templates t
        JOIN projects p ON p.id = t.project_id
        WHERE d.template_id = t.id
          AND d.enabled = TRUE
          AND UPPER(d.op_code) IN ('GIRATOIRE', 'JAW', 'PRIMARY_CRUSHER')
          AND (LOWER(d.item) LIKE '%d%bit%design%alimentation%'
               OR LOWER(d.item) LIKE '%debit%design%alimentation%')
          AND p.target_tph > 0
          AND p.availability_pct > 0
          AND COALESCE(d.source_code, 'X') NOT IN ('M', 'O')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE design_criteria_v2
        SET dag_key = 'target_tph'
        WHERE dag_key = 'crusher_design_tph'
    """)
