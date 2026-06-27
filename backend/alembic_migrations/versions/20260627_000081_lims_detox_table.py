"""lims_detox_table — créer lims_detox si absent, migrer depuis lims_dtx.

schema.sql bootstrappe lims_dtx (ancien nom). Migration 000029 crée lims_detox
(nouveau nom utilisé par les routes et le template LIMS). Cette migration garantit
que lims_detox existe sur toutes les DB de production, quelle que soit la voie
de déploiement (Alembic ou bootstrap).
"""

from alembic import op

revision = "000081"
down_revision = "000080"
revises = "000080"
branch_labels = None
depends_on = None


_DETOX_COLS = [
    ("cn_wad_mg_l", "NUMERIC"), ("cn_total_mg_l", "NUMERIC"), ("cn_free_mg_l", "NUMERIC"),
    ("scn_mg_l", "NUMERIC"), ("ph_final", "NUMERIC"), ("cu_mg_l", "NUMERIC"),
    ("fe_mg_l", "NUMERIC"), ("ni_mg_l", "NUMERIC"), ("zn_mg_l", "NUMERIC"),
    ("as_mg_l", "NUMERIC"), ("hg_ug_l", "NUMERIC"), ("pb_mg_l", "NUMERIC"),
    ("consomm_so2_kg_t", "NUMERIC"), ("consomm_h2o2_kg_t", "NUMERIC"),
    ("consomm_cuso4_kg_t", "NUMERIC"), ("consomm_cao_kg_t", "NUMERIC"),
    ("duree_traitement_min", "NUMERIC"), ("cn_wad_rebound_24h", "NUMERIC"),
    ("cn_wad_rebound_7d", "NUMERIC"),
]


def upgrade() -> None:
    conn = op.get_bind()

    has_detox = conn.execute(
        "SELECT to_regclass('public.lims_detox')"
    ).scalar()

    if has_detox:
        return  # Déjà créée par migration 000029

    cols_ddl = ", ".join(f"{col} {dtype}" for col, dtype in _DETOX_COLS)
    conn.execute(f"""
        CREATE TABLE lims_detox (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            sample_id   UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
            {cols_ddl},
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lims_detox_project ON lims_detox(project_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_lims_detox_sample ON lims_detox(sample_id)"
    )

    # Migrer depuis lims_dtx si existant
    has_dtx = conn.execute("SELECT to_regclass('public.lims_dtx')").scalar()
    if has_dtx:
        col_list = ", ".join(col for col, _ in _DETOX_COLS)
        conn.execute(f"""
            INSERT INTO lims_detox (project_id, sample_id, {col_list}, created_at)
            SELECT project_id, sample_id, {col_list}, created_at FROM lims_dtx
            ON CONFLICT DO NOTHING
        """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS lims_detox CASCADE")
