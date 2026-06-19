"""Add `dag_key` column to design_criteria_v2 (Chunk 1.5.A — Option A)

Revision ID: 000047
Revises: 000046
Create Date: 2026-05-07

The cascade engine (`backend/routes/dc_pipeline.py:run_cascade`) needs to map
each design criterion row to a node in the DC DAG (`dc_dag_registry.yaml`).
Today it derives a key from `ref_number` via
`lower().replace('.','_').replace('-','_')`, which produces e.g. `"2_1_05"` —
matching no real DAG node. This was confirmed by the Chunk 0 audit
(`docs/superpowers/audits/2026-05-07-pdc-two-table-audit.md`).

This migration adds an explicit `dag_key TEXT NULL` column. New rows are
populated by `circuit_catalog.py` / `routes/circuit.py:_generate_default_criteria`
and the LIMS / calculator writer paths in `dc_generator.py` and
`dc_calculator.py`.

A best-effort backfill is included for already-deployed databases: rows whose
normalized `ref_number` happens to equal a canonical DAG key receive that key.
In practice this is a *very* small set (the audit confirmed the formats almost
never coincide), so most existing rows stay NULL until the project re-runs
`_generate_default_criteria`.

A partial index `ix_dcv2_dag_key` on `(template_id, dag_key)` filtered to
`dag_key IS NOT NULL` keeps the cascade query fast.
"""
from alembic import op
import sqlalchemy as sa


revision = "000047"
down_revision = "000046"
branch_labels = None
depends_on = None


# Canonical DAG keys — kept in sync with `backend/engines/dc_dag_registry.yaml`.
# Listed inline so the migration can be applied even if the registry file
# happens to be unavailable at upgrade time (alembic upgrades sometimes run
# in environments where engine modules aren't importable).
_CANONICAL_DAG_KEYS = (
    # inputs
    "target_tph", "gold_grade_g_t", "operating_hours_day", "availability_pct",
    "ore_sg", "avg_bwi", "avg_p80_um", "avg_f80_um", "avg_grg_pct",
    "avg_au_recovery_pct", "avg_nacn_kg_t", "avg_cao_kg_t", "avg_unit_area",
    "flot_mass_pull_pct", "has_flotation", "has_gravity", "has_hpgr",
    "cil_srt_h", "cil_pct_solids", "mech_efficiency", "energy_rate_usd_kwh",
    "rom_f80_mm", "pc_css_mm", "sc_css_mm", "sag_p80_mm", "bm_circ_load_pct",
    "cil_hd_ratio", "max_vol_per_tank", "thickener_safety_factor",
    "thickener_max_diameter_m", "underflow_pct_solids", "evap_factor",
    # nodes
    "pc_p80_mm", "sc_p80_mm",
    "sag_f80_mm", "sag_power_kw", "bm_f80_um", "bm_power_kw",
    "total_commin_power_kw",
    "cyc_feed_tph",
    "flot_conc_tph",
    "leach_feed_tph", "slurry_sg", "vol_flow_m3h", "cil_volume_m3",
    "cil_n_tanks", "cil_tank_diameter_m", "nacn_kg_h", "cao_kg_h",
    "thickener_area_m2", "thickener_diameter_m", "n_thickeners",
    "process_water_m3h", "tailings_water_loss_m3h", "evaporation_m3h",
    "fresh_water_m3h",
    "annual_gold_oz", "total_installed_power_kw", "energy_cost_usd_t",
)


def upgrade():
    op.add_column(
        "design_criteria_v2",
        sa.Column("dag_key", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_dcv2_dag_key",
        "design_criteria_v2",
        ["template_id", "dag_key"],
        unique=False,
        postgresql_where=sa.text("dag_key IS NOT NULL"),
    )

    # Best-effort backfill — only writes for rows whose normalized ref_number
    # happens to equal a canonical DAG key. In practice the formats don't
    # coincide so this matches very few rows; most pre-existing rows will stay
    # NULL until their project re-runs `_generate_default_criteria` (which now
    # writes `dag_key` directly from the catalog mapping).
    keys_csv = ", ".join(f"'{k}'" for k in _CANONICAL_DAG_KEYS)
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE design_criteria_v2
            SET dag_key = LOWER(REPLACE(REPLACE(ref_number, '.', '_'), '-', '_'))
            WHERE dag_key IS NULL
              AND LOWER(REPLACE(REPLACE(ref_number, '.', '_'), '-', '_')) IN ({keys_csv})
            """
        )
    )

    # Re-seed unit_operations_catalog so the JSONB default_criteria column
    # carries the new `dag_key` field on each entry. New projects will then
    # pick up the dag_key when `_generate_default_criteria` reads from the
    # catalog. Existing projects' rows are unaffected (they live in
    # design_criteria_v2 and were handled by the backfill above + the
    # opportunistic back-fill in dc_generator/dc_calculator writers).
    try:
        import sys
        import os
        import json
        backend_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        from engines.circuit_catalog import CATALOG  # type: ignore[import-not-found]

        for entry in CATALOG:
            bind.execute(
                sa.text(
                    """
                    UPDATE unit_operations_catalog
                    SET default_criteria = :criteria
                    WHERE op_code = :op_code
                    """
                ),
                {
                    "op_code": entry["op_code"],
                    "criteria": json.dumps(entry.get("default_criteria", [])),
                },
            )
    except Exception:  # noqa: BLE001 - reseed is best-effort
        # If the engines module isn't importable at upgrade time (e.g. running
        # alembic in a stripped CI image), skip the reseed; new projects will
        # still get dag_key once the catalog seed runs at app startup.
        pass


def downgrade():
    # Use `if_exists=True` so a downgrade run after a partial upgrade (where
    # the index never got created) doesn't error out. Alembic 1.13+ supports
    # the kwarg directly; on older alembic it raises TypeError, in which case
    # we fall back to a guarded raw-SQL drop.
    try:
        op.drop_index("ix_dcv2_dag_key", table_name="design_criteria_v2", if_exists=True)
    except TypeError:  # pragma: no cover - alembic <1.13
        op.execute("DROP INDEX IF EXISTS ix_dcv2_dag_key")
    op.drop_column("design_criteria_v2", "dag_key")
