"""design_criteria_crusher_dagkey — corriger dag_key concasseur primaire.

La ligne GIRATOIRE "Débit design alimentation" avait dag_key="target_tph" ce qui
causait la cascade DAG à écraser design_value avec target_tph brut (sans correction
disponibilité). Résultat: nominal > design affiché dans les critères de conception.

Correction: dag_key → "crusher_design_tph" (valeur calculée par la formule DAG
crusher_design_rate qui applique correctement grinding_avail/crushing_avail × design_factor).
"""

from alembic import op

revision = "000082"
down_revision = "000081"
revises = "000081"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute("""
        UPDATE design_criteria_v2
        SET dag_key = 'crusher_design_tph',
            source_code = 'C',
            updated_at = NOW()
        WHERE dag_key = 'target_tph'
          AND LOWER(item) LIKE '%débit%design%alimentation%'
          AND UPPER(op_code) IN ('GIRATOIRE', 'JAW', 'PRIMARY_CRUSHER')
    """)
    conn.execute("""
        UPDATE design_criteria_v2
        SET dag_key = 'crusher_design_tph',
            source_code = 'C',
            updated_at = NOW()
        WHERE dag_key = 'target_tph'
          AND LOWER(item) LIKE '%debit%design%alimentation%'
          AND UPPER(op_code) IN ('GIRATOIRE', 'JAW', 'PRIMARY_CRUSHER')
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE design_criteria_v2
        SET dag_key = 'target_tph'
        WHERE dag_key = 'crusher_design_tph'
    """)
