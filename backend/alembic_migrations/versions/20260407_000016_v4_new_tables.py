"""v4 new tables — all 32 domain tables

Revision ID: 000016
Revises: 20260406_000015
Create Date: 2026-04-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, BYTEA
import uuid

revision = "000016"
down_revision = "20260406_000015"
branch_labels = None
depends_on = None


_CONFLICTING_TABLES = {
    # Tables that ALSO exist in schema.sql baseline. Skip if already created
    # so this migration is idempotent on schema.sql-bootstrapped databases.
    "simulation_runs",
    "dcf_models",
    "monte_carlo_runs",
    "economic_indicators",
    "process_tags",
    "tag_readings",
    "kpi_snapshots",
    "data_connectors",
    "aba_nag_results",
    "ard_classifications",
    "geotech_tests",
    "slope_analyses",
    "tsf_design",
}


def upgrade():
    # ── Enable TimescaleDB extension (optional for managed Postgres) ──────
    # On Railway / managed Postgres / vanilla Postgres without the
    # timescaledb extension installed, CREATE EXTENSION fails. Catch and
    # continue without hypertables — tag_readings stays a normal table,
    # which works fine for non-SCADA-heavy workloads (PFS-stage projects).
    bind_for_ext = op.get_bind()
    try:
        bind_for_ext.execute(sa.text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE"))
        has_timescale = True
    except Exception:
        # Roll back the failed transaction so the rest of the migration can proceed
        bind_for_ext.execute(sa.text("ROLLBACK"))
        has_timescale = False

    # ── Idempotency guard ─────────────────────────────────────────────────
    # 13 of the tables below also exist in the schema.sql baseline used to
    # bootstrap fresh Postgres instances (e.g. mpdpms_test). Without this
    # guard, `alembic upgrade head` against a schema.sql-initialised database
    # raises "relation already exists". We wrap op.create_table so any name
    # in _CONFLICTING_TABLES already present in the DB is silently skipped;
    # all other tables (which only exist in this migration) are created
    # normally.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    _real_create_table = op.create_table

    def _idempotent_create_table(name, *args, **kwargs):
        if name in _CONFLICTING_TABLES and name in existing_tables:
            return None
        return _real_create_table(name, *args, **kwargs)

    op.create_table = _idempotent_create_table  # type: ignore[assignment]
    try:
        _do_upgrade(has_timescale)
    finally:
        op.create_table = _real_create_table  # type: ignore[assignment]


def _do_upgrade(has_timescale: bool = True):
    # ── Domain ①: Simulation Engine ──────────────────────────────────────
    op.create_table(
        "simulation_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("celery_task_id", sa.Text),
        sa.Column("params", JSONB),
        sa.Column("results", JSONB),
        sa.Column("duration_s", sa.Float),
        sa.Column("created_by", UUID(as_uuid=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "pareto_fronts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("run_id", UUID(as_uuid=True),
                  sa.ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("solutions", JSONB),
        sa.Column("n_solutions", sa.Integer),
        sa.Column("generated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "sensitivity_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("run_id", UUID(as_uuid=True),
                  sa.ForeignKey("simulation_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("param_key", sa.Text),
        sa.Column("delta_pct", sa.Float),
        sa.Column("impact_recovery", sa.Float),
        sa.Column("impact_opex", sa.Float),
        sa.Column("impact_energy", sa.Float),
        sa.Column("rank", sa.Integer),
    )

    op.create_table(
        "model_artifacts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("model_type", sa.Text),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("is_active", sa.Boolean, server_default="false"),
        sa.Column("artifact", BYTEA),
        sa.Column("training_samples_n", sa.Integer),
        sa.Column("training_score", sa.Float),
        sa.Column("trained_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("trained_by", sa.Text),
    )

    # ── Domain ②: Economic Analysis ──────────────────────────────────────
    op.create_table(
        "dcf_models",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("discount_rate", sa.Float),
        sa.Column("tax_rate", sa.Float),
        sa.Column("mine_life_years", sa.Integer),
        sa.Column("cashflows", JSONB),
        sa.Column("npv", sa.Float),
        sa.Column("irr", sa.Float),
        sa.Column("payback_years", sa.Float),
        sa.Column("aisc", sa.Float),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "monte_carlo_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("n_iterations", sa.Integer),
        sa.Column("variables", JSONB),
        sa.Column("results", JSONB),
        sa.Column("celery_task_id", sa.Text),
        sa.Column("status", sa.Text, server_default="queued"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "economic_indicators",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dcf_model_id", UUID(as_uuid=True),
                  sa.ForeignKey("dcf_models.id", ondelete="SET NULL"), nullable=True),
        sa.Column("npv_usd", sa.Float),
        sa.Column("irr_pct", sa.Float),
        sa.Column("payback_years", sa.Float),
        sa.Column("aisc_usd_oz", sa.Float),
        sa.Column("cash_cost_usd_oz", sa.Float),
        sa.Column("margin_pct", sa.Float),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "sensitivity_vars",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("var_name", sa.Text),
        sa.Column("distribution", sa.Text),
        sa.Column("param_a", sa.Float),
        sa.Column("param_b", sa.Float),
        sa.Column("param_c", sa.Float),
        sa.Column("is_active", sa.Boolean, server_default="true"),
    )

    # ── Domain ③: Equipment Sizing ────────────────────────────────────────
    op.create_table(
        "equipment_sizing",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.Text),
        sa.Column("inputs", JSONB),
        sa.Column("outputs", JSONB),
        sa.Column("capex_estimate_usd", sa.Float),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "vendor_catalog",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("equipment_family", sa.Text),
        sa.Column("manufacturer", sa.Text),
        sa.Column("model_series", sa.Text),
        sa.Column("power_range_kw_min", sa.Float),
        sa.Column("power_range_kw_max", sa.Float),
        sa.Column("capacity_range_min", sa.Float),
        sa.Column("capacity_range_max", sa.Float),
        sa.Column("capacity_unit", sa.Text),
        sa.Column("lead_time_weeks", sa.Integer),
        sa.Column("reference_capex_usd", sa.Float),
        sa.Column("reference_capacity", sa.Float),
        sa.Column("cepci_year", sa.Integer),
        sa.Column("correlation_a", sa.Float),
        sa.Column("correlation_b", sa.Float),
    )

    op.create_table(
        "equipment_selections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("catalog_id", UUID(as_uuid=True),
                  sa.ForeignKey("vendor_catalog.id", ondelete="SET NULL"), nullable=True),
        sa.Column("quantity", sa.Integer, server_default="1"),
        sa.Column("is_spare", sa.Boolean, server_default="false"),
        sa.Column("capex_usd", sa.Float),
        sa.Column("notes", sa.Text),
        sa.Column("selected_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "capex_correlations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("equipment_family", sa.Text),
        sa.Column("factor_name", sa.Text),
        sa.Column("factor_value", sa.Float),
        sa.Column("reference", sa.Text),
    )

    # ── Domain ④: P&ID + 3D Layout ────────────────────────────────────────
    op.create_table(
        "pid_diagrams",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sheet_number", sa.Integer),
        sa.Column("title", sa.Text),
        sa.Column("area_code", sa.Text),
        sa.Column("elements", JSONB),
        sa.Column("connections", JSONB),
        sa.Column("revision", sa.Text, server_default="A"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "pid_instruments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("diagram_id", UUID(as_uuid=True),
                  sa.ForeignKey("pid_diagrams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("tag", sa.Text, unique=True),
        sa.Column("service", sa.Text),
        sa.Column("instrument_type", sa.Text),
        sa.Column("loop_number", sa.Text),
        sa.Column("area", sa.Text),
        sa.Column("p_rating", sa.Text),
        sa.Column("t_rating", sa.Text),
        sa.Column("fluid", sa.Text),
        sa.Column("line_size", sa.Text),
        sa.Column("notes", sa.Text),
    )

    op.create_table(
        "plant_layout_3d",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False),
        sa.Column("x", sa.Float, server_default="0"),
        sa.Column("y", sa.Float, server_default="0"),
        sa.Column("z", sa.Float, server_default="0"),
        sa.Column("rotation_deg", sa.Float, server_default="0"),
        sa.Column("zone", sa.Text),
        sa.Column("geometry_overrides", JSONB),
    )

    op.create_table(
        "layout_zones",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("zone_code", sa.Text),
        sa.Column("zone_name", sa.Text),
        sa.Column("color_hex", sa.Text),
        sa.Column("bbox", JSONB),
    )

    # ── Domain ⑤: Analytics / KPI ────────────────────────────────────────
    op.create_table(
        "process_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_name", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("area", sa.Text),
        sa.Column("unit", sa.Text),
        sa.Column("data_type", sa.Text, server_default="float"),
        sa.Column("normal_min", sa.Float),
        sa.Column("normal_target", sa.Float),
        sa.Column("normal_max", sa.Float),
        sa.Column("source", sa.Text, server_default="manual"),
    )

    op.create_table(
        "tag_readings",
        sa.Column("time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("tag_id", UUID(as_uuid=True),
                  sa.ForeignKey("process_tags.id", ondelete="CASCADE"), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("quality", sa.SmallInteger, server_default="192"),
    )
    op.execute(
        "SELECT create_hypertable('tag_readings', 'time', "
        "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
    ) if has_timescale else None

    op.create_table(
        "kpi_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("snapshot_time", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("period", sa.Text),
        sa.Column("kpi_data", JSONB),
    )

    op.create_table(
        "data_connectors",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("protocol", sa.Text),
        sa.Column("config", JSONB),
        sa.Column("poll_interval_s", sa.Integer, server_default="60"),
        sa.Column("is_active", sa.Boolean, server_default="false"),
        sa.Column("last_connected_at", sa.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "anomaly_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_id", UUID(as_uuid=True),
                  sa.ForeignKey("process_tags.id", ondelete="CASCADE"), nullable=False),
        sa.Column("detected_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("value_observed", sa.Float),
        sa.Column("value_expected", sa.Float),
        sa.Column("sigma_deviation", sa.Float),
        sa.Column("severity", sa.Text),
        sa.Column("acknowledged_by", UUID(as_uuid=True)),
        sa.Column("recommendation", sa.Text),
    )

    # ── Domain ⑥: SCADA / DCS ────────────────────────────────────────────
    op.create_table(
        "pid_loops",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("instrument_id", UUID(as_uuid=True),
                  sa.ForeignKey("pid_instruments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("loop_tag", sa.Text),
        sa.Column("pv_tag_id", UUID(as_uuid=True),
                  sa.ForeignKey("process_tags.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mv_tag_id", UUID(as_uuid=True),
                  sa.ForeignKey("process_tags.id", ondelete="SET NULL"), nullable=True),
        sa.Column("sp_value", sa.Float),
        sa.Column("kp", sa.Float),
        sa.Column("ti_s", sa.Float),
        sa.Column("td_s", sa.Float),
        sa.Column("tuning_method", sa.Text),
        sa.Column("fopdt_gain", sa.Float),
        sa.Column("fopdt_tau_s", sa.Float),
        sa.Column("fopdt_theta_s", sa.Float),
        sa.Column("is_cascade", sa.Boolean, server_default="false"),
        sa.Column("master_loop_id", UUID(as_uuid=True),
                  sa.ForeignKey("pid_loops.id", ondelete="SET NULL"), nullable=True),
    )

    op.create_table(
        "grafcet_sequences",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_name", sa.Text),
        sa.Column("area", sa.Text),
        sa.Column("steps", JSONB),
        sa.Column("transitions", JSONB),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("is_verified", sa.Boolean, server_default="false"),
    )

    op.create_table(
        "cause_effect_matrix",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cause_tag", sa.Text),
        sa.Column("cause_description", sa.Text),
        sa.Column("effects", JSONB),
    )

    op.create_table(
        "fat_sat_checklists",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("checklist_type", sa.Text),
        sa.Column("equipment_id", UUID(as_uuid=True),
                  sa.ForeignKey("equipment.id", ondelete="SET NULL"), nullable=True),
        sa.Column("instrument_id", UUID(as_uuid=True),
                  sa.ForeignKey("pid_instruments.id", ondelete="SET NULL"), nullable=True),
        sa.Column("test_description", sa.Text),
        sa.Column("acceptance_criteria", sa.Text),
        sa.Column("result", sa.Text),
        sa.Column("is_passed", sa.Boolean),
        sa.Column("tested_by", UUID(as_uuid=True)),
        sa.Column("tested_at", sa.TIMESTAMP(timezone=True)),
    )

    op.create_table(
        "dynamic_sim_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scenario", sa.Text),
        sa.Column("duration_s", sa.Float),
        sa.Column("params", JSONB),
        sa.Column("time_series", JSONB),
        sa.Column("celery_task_id", sa.Text),
        sa.Column("status", sa.Text, server_default="queued"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    # ── Domain ⑧: Geotech / Env ───────────────────────────────────────────
    op.create_table(
        "geotech_tests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sample_id", UUID(as_uuid=True)),
        sa.Column("test_code", sa.Text),
        sa.Column("results", JSONB),
        sa.Column("laboratory", sa.Text),
        sa.Column("test_date", sa.Date),
        sa.Column("notes", sa.Text),
    )

    op.create_table(
        "slope_analyses",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("location", sa.Text),
        sa.Column("slope_angle_deg", sa.Float),
        sa.Column("slope_height_m", sa.Float),
        sa.Column("cohesion_kpa", sa.Float),
        sa.Column("friction_angle_deg", sa.Float),
        sa.Column("gamma_kn_m3", sa.Float),
        sa.Column("pore_pressure_ratio", sa.Float),
        sa.Column("method", sa.Text, server_default="Bishop"),
        sa.Column("fs_static", sa.Float),
        sa.Column("fs_seismic", sa.Float),
        sa.Column("is_compliant", sa.Boolean),
        sa.Column("failure_surface", JSONB),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "aba_nag_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sample_id", UUID(as_uuid=True)),
        sa.Column("total_s_pct", sa.Float),
        sa.Column("sulfide_s_pct", sa.Float),
        sa.Column("sulfate_s_pct", sa.Float),
        sa.Column("ap_kg_caco3_t", sa.Float),
        sa.Column("np_kg_caco3_t", sa.Float),
        sa.Column("nnp", sa.Float),
        sa.Column("npr", sa.Float),
        sa.Column("ph_nag", sa.Float),
        sa.Column("pag_classification", sa.Text),
        sa.Column("test_date", sa.Date),
        sa.Column("laboratory", sa.Text),
    )

    op.create_table(
        "ard_classifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("domain_code", sa.Text),
        sa.Column("pag_count", sa.Integer),
        sa.Column("non_pag_count", sa.Integer),
        sa.Column("uncertain_count", sa.Integer),
        sa.Column("pag_pct", sa.Float),
        sa.Column("ard_risk_level", sa.Text),
        sa.Column("mitigation_strategy", sa.Text),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )

    op.create_table(
        "closure_plan_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("phase", sa.Text),
        sa.Column("component", sa.Text),
        sa.Column("activity", sa.Text),
        sa.Column("year_target", sa.Integer),
        sa.Column("unit_cost_usd", sa.Float),
        sa.Column("quantity", sa.Float),
        sa.Column("unit", sa.Text),
        sa.Column("total_cost_usd", sa.Float),
        sa.Column("success_criteria", sa.Text),
        sa.Column("responsible", sa.Text),
    )

    op.create_table(
        "tsf_design",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("project_id", UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer, server_default="1"),
        sa.Column("construction_method", sa.Text),
        sa.Column("total_capacity_m3", sa.Float),
        sa.Column("annual_deposition_t", sa.Float),
        sa.Column("raise_height_m", sa.Float),
        sa.Column("embankment_area_ha", sa.Float),
        sa.Column("fs_static", sa.Float),
        sa.Column("fs_seismic", sa.Float),
        sa.Column("is_mac_compliant", sa.Boolean),
        sa.Column("water_balance", JSONB),
        sa.Column("notes", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
    )


def downgrade():
    # Drop in reverse dependency order
    for table in [
        "tsf_design", "closure_plan_items", "ard_classifications",
        "aba_nag_results", "slope_analyses", "geotech_tests",
        "dynamic_sim_runs", "fat_sat_checklists", "cause_effect_matrix",
        "grafcet_sequences", "pid_loops",
        "anomaly_events", "data_connectors", "kpi_snapshots",
        "tag_readings", "process_tags",
        "layout_zones", "plant_layout_3d", "pid_instruments", "pid_diagrams",
        "capex_correlations", "equipment_selections", "vendor_catalog", "equipment_sizing",
        "sensitivity_vars", "economic_indicators", "monte_carlo_runs", "dcf_models",
        "model_artifacts", "sensitivity_analyses", "pareto_fronts", "simulation_runs",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
