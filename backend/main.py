"""
MPDPMS — MetalFlow Pro Backend
FastAPI + PostgreSQL + JWT Auth
Compatible avec test_api.py et test_mass_balance_e2e.py
"""

import importlib
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

try:
    from .websocket_handlers import ws_authenticate, ws_check_project, ws_run_connection, ws_run_live_connection
    from .ws_manager import ws_manager
except ImportError:
    from websocket_handlers import ws_authenticate, ws_check_project, ws_run_connection, ws_run_live_connection
    from ws_manager import ws_manager
from fastapi import Query, WebSocket

try:
    from .csrf import build_csrf_middleware as _csrf_build_middleware
    from .db import conn, release
    from .http_context import attach_request_id, reset_request_id
    from .metrics import metrics_endpoint, prometheus_middleware
    from .observability import configure_logging, security_headers, startup_mode
    from .routes.admin import router as admin_router
    from .routes.analysis import router as analysis_router
    from .routes.analytics import router as analytics_router
    from .routes.assistant import router as assistant_router
    from .routes.audit_trail import router as audit_trail_router
    from .routes.auth import router as auth_router
    from .routes.automation import router as automation_router
    from .routes.blockmodel import router as blockmodel_router
    from .routes.campaigns import router as campaigns_router
    from .routes.capex import router as capex_router
    from .routes.circuit import router as circuit_router
    from .routes.circuit_optimizer import router as circuit_optimizer_router
    from .routes.closure import router as closure_router
    from .routes.compliance import router as compliance_router
    from .routes.costs import router as costs_router
    from .routes.dashboard import router as dashboard_router
    from .routes.dc_pipeline import router as dc_pipeline_router
    from .routes.decisions import router as decisions_router
    from .routes.design import router as design_router
    from .routes.economics import router as economics_router
    from .routes.engineering_insights import router as engineering_insights_router
    from .routes.equipment import router as equipment_router
    from .routes.equipment_catalog import router as equipment_catalog_router
    from .routes.equipment_sizing import router as equipment_sizing_router
    from .routes.equipment_v2 import router as equipment_v2_router
    from .routes.exports import router as exports_router
    from .routes.flowsheet_engine import router as flowsheet_engine_router
    from .routes.flowsheet_graph import router as flowsheet_graph_router
    from .routes.flowsheet_templates_route import router as flowsheet_templates_router
    from .routes.flowsheet_tree import router as flowsheet_tree_router
    from .routes.flowsheets import router as flowsheets_router
    from .routes.funding import router as funding_router
    from .routes.geochemistry import router as geochemistry_router
    from .routes.geomet_intelligence import router as geomet_intelligence_router
    from .routes.geomet_v2 import router as geomet_v2_router
    from .routes.geotech import router as geotech_router
    from .routes.gistm import router as gistm_router
    from .routes.jobs import router as jobs_router
    from .routes.layout3d import router as layout3d_router
    from .routes.lims import router as lims_router
    from .routes.lims_alerts import router as lims_alerts_router
    from .routes.lims_outliers import router as lims_outliers_router
    from .routes.massbalance import router as massbalance_router
    from .routes.massbalance_v2 import router as massbalance_v2_router
    from .routes.metallurgical_decision import router as metallurgical_decision_router
    from .routes.modules import router as modules_router
    from .routes.monitoring import router as monitoring_router
    from .routes.ni43101 import readiness_router as ni43101_readiness_router
    from .routes.ni43101 import router as ni43101_router
    from .routes.opex_v2 import router as opex_v2_router
    from .routes.optimization import router as optimization_router
    from .routes.ore_to_bullion import router as ore_to_bullion_router
    from .routes.parameters import router as parameters_router
    from .routes.pid import router as pid_router
    from .routes.pipeline import router as pipeline_router
    from .routes.process_model import router as process_model_router
    from .routes.projects import router as projects_router
    from .routes.rampup import router as rampup_router
    from .routes.reports import router as reports_router
    from .routes.risks import router as risks_router
    from .routes.scenarios import router as scenarios_router
    from .routes.sim_module import router as sim_module_router
    from .routes.sim_pro import router as sim_pro_router
    from .routes.simulation import router as simulation_router
    from .routes.simulation_compile import router as simulation_compile_router
    from .routes.simulation_defaults import router as simulation_defaults_router
    from .routes.simulation_innovations import router as simulation_innovations_router
    from .routes.simulation_v2 import router as simulation_v2_router
    from .routes.stagegates import router as stagegates_router
    from .routes.sync import router as sync_router
    from .routes.tasks import router as tasks_router
    from .routes.traceability import router as traceability_router
    from .routes.working_capital import router as working_capital_router
    from .settings import get_settings
    from .telemetry import init_all as _telemetry_init_all
except ImportError:  # pragma: no cover - supports direct script imports
    from csrf import build_csrf_middleware as _csrf_build_middleware
    from db import conn, release
    from http_context import attach_request_id, reset_request_id
    from metrics import metrics_endpoint, prometheus_middleware
    from observability import configure_logging, security_headers, startup_mode
    from routes.admin import router as admin_router
    from routes.analysis import router as analysis_router
    from routes.analytics import router as analytics_router
    from routes.assistant import router as assistant_router
    from routes.audit_trail import router as audit_trail_router
    from routes.auth import router as auth_router
    from routes.automation import router as automation_router
    from routes.blockmodel import router as blockmodel_router
    from routes.campaigns import router as campaigns_router
    from routes.capex import router as capex_router
    from routes.circuit import router as circuit_router
    from routes.circuit_optimizer import router as circuit_optimizer_router
    from routes.closure import router as closure_router
    from routes.compliance import router as compliance_router
    from routes.costs import router as costs_router
    from routes.dashboard import router as dashboard_router
    from routes.dc_pipeline import router as dc_pipeline_router
    from routes.decisions import router as decisions_router
    from routes.design import router as design_router
    from routes.economics import router as economics_router
    from routes.engineering_insights import router as engineering_insights_router
    from routes.equipment import router as equipment_router
    from routes.equipment_catalog import router as equipment_catalog_router
    from routes.equipment_sizing import router as equipment_sizing_router
    from routes.equipment_v2 import router as equipment_v2_router
    from routes.exports import router as exports_router
    from routes.flowsheet_engine import router as flowsheet_engine_router
    from routes.flowsheet_graph import router as flowsheet_graph_router
    from routes.flowsheet_templates_route import router as flowsheet_templates_router
    from routes.flowsheet_tree import router as flowsheet_tree_router
    from routes.flowsheets import router as flowsheets_router
    from routes.funding import router as funding_router
    from routes.geochemistry import router as geochemistry_router
    from routes.geomet_intelligence import router as geomet_intelligence_router
    from routes.geomet_v2 import router as geomet_v2_router
    from routes.geotech import router as geotech_router
    from routes.gistm import router as gistm_router
    from routes.jobs import router as jobs_router
    from routes.layout3d import router as layout3d_router
    from routes.lims import router as lims_router
    from routes.lims_alerts import router as lims_alerts_router
    from routes.lims_outliers import router as lims_outliers_router
    from routes.massbalance import router as massbalance_router
    from routes.massbalance_v2 import router as massbalance_v2_router
    from routes.metallurgical_decision import router as metallurgical_decision_router
    from routes.modules import router as modules_router
    from routes.monitoring import router as monitoring_router
    from routes.ni43101 import readiness_router as ni43101_readiness_router
    from routes.ni43101 import router as ni43101_router
    from routes.opex_v2 import router as opex_v2_router
    from routes.optimization import router as optimization_router
    from routes.ore_to_bullion import router as ore_to_bullion_router
    from routes.parameters import router as parameters_router
    from routes.pid import router as pid_router
    from routes.pipeline import router as pipeline_router
    from routes.process_model import router as process_model_router
    from routes.projects import router as projects_router
    from routes.rampup import router as rampup_router
    from routes.reports import router as reports_router
    from routes.risks import router as risks_router
    from routes.scenarios import router as scenarios_router
    from routes.sim_module import router as sim_module_router
    from routes.sim_pro import router as sim_pro_router
    from routes.simulation import router as simulation_router
    from routes.simulation_compile import router as simulation_compile_router
    from routes.simulation_defaults import router as simulation_defaults_router
    from routes.simulation_innovations import router as simulation_innovations_router
    from routes.simulation_v2 import router as simulation_v2_router
    from routes.stagegates import router as stagegates_router
    from routes.sync import router as sync_router
    from routes.tasks import router as tasks_router
    from routes.traceability import router as traceability_router
    from routes.working_capital import router as working_capital_router
    from settings import get_settings
    from telemetry import init_all as _telemetry_init_all

try:
    from .build_meta import APP_BUILD_ID
except ImportError:  # pragma: no cover - supports direct script imports
    from build_meta import APP_BUILD_ID


# ─── Fail-fast env-var check ────────────────────────────────────────────────
def _check_required_env_vars() -> None:
    """Abort startup immediately if critical environment variables are missing.

    This runs before any DB connection or settings initialisation so the
    operator gets a clear, actionable error instead of a cryptic traceback.
    """
    required: dict[str, str] = {
        "DATABASE_URL": "PostgreSQL connection string  (e.g. postgresql://user:pass@host:5432/db)",
        "JWT_SECRET": "JWT signing secret — min 32 chars  "
        '(generate: python -c "import secrets; print(secrets.token_urlsafe(64))")',
        "ADMIN_EMAIL": "Bootstrap admin e-mail address",
        "ADMIN_PASSWORD": "Bootstrap admin password — min 8 chars, not 'admin123'",
    }
    missing = [f"  {var}\n    → {desc}" for var, desc in required.items() if not os.environ.get(var)]
    if missing:
        raise SystemExit(
            "\n\nFATAL: Missing required environment variables:\n\n"
            + "\n\n".join(missing)
            + "\n\nCopy backend/.env.example to backend/.env and fill in the values.\n"
        )


_check_required_env_vars()

# ─── Config ────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "storage" / "reports"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS = get_settings()
FRONTEND_FILE = BASE_DIR / SETTINGS.frontend_filename
MAX_REPORT_SIZE_BYTES = SETTINGS.max_report_size_bytes
CORS_ORIGINS = SETTINGS.cors_origins

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_ADMIN_EMAIL = SETTINGS.admin_email
_ADMIN_PASSWORD = SETTINGS.admin_password
AUTO_MIGRATE = SETTINGS.auto_migrate
BOOTSTRAP_SCHEMA = SETTINGS.bootstrap_schema
ENABLE_HSTS = SETTINGS.enable_hsts

configure_logging()
logger = logging.getLogger("mpdpms")

SEED_ADMIN = """
INSERT INTO users (email, password_hash, role, full_name)
VALUES (%s, crypt(%s, gen_salt('bf')), %s, %s)
ON CONFLICT (email) DO NOTHING;
"""


def run_migrations() -> None:
    from alembic.config import Config

    removed = False
    backend_path = str(BASE_DIR)
    if backend_path in sys.path:
        sys.path.remove(backend_path)
        removed = True
    try:
        command = importlib.import_module("alembic.command")
    finally:
        if removed:
            sys.path.insert(0, backend_path)

    alembic_ini = BASE_DIR / "alembic.ini"
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", SETTINGS.database_url)
    command.upgrade(config, "head")
    logger.info("database migrations applied", extra={"startup_mode": "alembic"})


def seed_admin_user() -> None:
    # ── Production password hardening ──────────────────────────────────────
    _is_production = os.getenv("RAILWAY_ENVIRONMENT") == "production"
    if _is_production:
        if _ADMIN_PASSWORD == "admin123":
            raise RuntimeError("Insecure default admin password 'admin123' cannot be used in production")
        if len(_ADMIN_PASSWORD) < 8:
            raise RuntimeError(
                f"Admin password must be at least 8 characters in production (got {len(_ADMIN_PASSWORD)})"
            )

    c = conn()
    cur = None
    try:
        cur = c.cursor()
        cur.execute(
            SEED_ADMIN, (_ADMIN_EMAIL, _ADMIN_PASSWORD, SETTINGS.admin_seed_role, SETTINGS.admin_seed_full_name)
        )
        c.commit()
        logger.info("admin seed complete", extra={"startup_mode": startup_mode(AUTO_MIGRATE, BOOTSTRAP_SCHEMA)})
        if _ADMIN_PASSWORD == "admin123":
            logger.warning("default admin password in use")
    except Exception:
        c.rollback()
        logger.exception("admin seed failed")
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)


_CATALOG_SEED_LOCK_ID = 987654322  # distinct from schema_bootstrap_lock_id


def seed_unit_operations_catalog() -> None:
    """Refresh equipment parameter templates from the code catalog.

    Uses a Postgres advisory lock so concurrent Uvicorn workers don't race
    each other on unit_operations_catalog, which would cause statement-timeout
    errors and a crash loop on multi-worker deployments.

    Seed failure is non-fatal: the catalog is populated on first deploy and
    persists in the DB. A timeout on a subsequent deploy is logged and skipped.
    """
    c = conn()
    cur = None
    try:
        try:
            from .engines.circuit_catalog import seed_catalog
        except ImportError:  # pragma: no cover - direct script imports
            from engines.circuit_catalog import seed_catalog

        cur = c.cursor()
        # Try non-blocking advisory lock — skip if another worker is seeding
        cur.execute("SELECT pg_try_advisory_lock(%s)", (_CATALOG_SEED_LOCK_ID,))
        locked = cur.fetchone()[0]
        if not locked:
            logger.info("unit operations catalog seed skipped (another worker is seeding)")
            c.rollback()
            return

        try:
            seed_catalog(cur)
            c.commit()
            logger.info("unit operations catalog seed complete")
        finally:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (_CATALOG_SEED_LOCK_ID,))
                c.commit()
            except Exception:
                pass
    except Exception:
        try:
            c.rollback()
        except Exception:
            pass
        logger.warning("unit operations catalog seed failed — continuing startup", exc_info=True)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        release(c)


def ensure_schema_compatibility() -> None:
    c = conn()
    cur = None
    try:
        cur = c.cursor()
        cur.execute("SELECT to_regclass('public.flowsheets')")
        has_flowsheets = cur.fetchone()[0] is not None
        cur.execute("SELECT to_regclass('public.flowshheets')")
        has_legacy = cur.fetchone()[0] is not None

        if has_legacy and not has_flowsheets:
            cur.execute("ALTER TABLE flowshheets RENAME TO flowsheets")
            logger.warning("schema compatibility: renamed legacy table flowshheets -> flowsheets")

        # ── Design Criteria v2 metadata alignment (defensive migration) ──────
        # Some production databases were created from an older partial schema
        # where design_criteria_v2 existed without metadata columns used by the
        # circuit operation workflow. Keep this idempotent so startup repairs
        # the drift even when Alembic is already marked as current.
        _ensure_design_criteria_v2_columns(cur)

        # ── lims_m1 column alignment (defensive fix for migration drift) ──────
        # Migrations 000008 and 000029 created lims_m1 with different column
        # sets, neither matching LIMS_FIELDS["m1"] in routes/lims.py.
        # Migration 000042 adds the missing columns via Alembic, but this
        # guard ensures the fix is applied even if Alembic is not running
        # (BOOTSTRAP_SCHEMA mode, manual DB restore, etc.).
        _ensure_lims_m1_columns(cur)

        # ── All other LIMS tables (defensive fix — migration 000043) ─────────
        _ensure_all_lims_columns(cur)

        # ── lims_detox table (defensive fix — migration 000029 drift) ────────
        # schema.sql creates lims_dtx; migration 000029 creates lims_detox.
        # The routes and template use lims_detox. Create it if absent and
        # copy any existing data from lims_dtx.
        _ensure_lims_detox_table(cur)

        c.commit()
    except Exception:
        c.rollback()
        logger.exception("schema compatibility check failed")
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)


_DESIGN_CRITERIA_V2_REQUIRED_COLUMNS = [
    ("version", "INTEGER DEFAULT 1"),
    ("updated_by", "UUID REFERENCES users(id)"),
]


def _ensure_design_criteria_v2_columns(cur) -> None:
    """Repair design_criteria_v2 metadata drift (idempotent, non-destructive)."""
    cur.execute("SELECT to_regclass('public.design_criteria_v2')")
    if cur.fetchone()[0] is None:
        return

    cur.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'design_criteria_v2'"
    )
    existing = {row[0] for row in cur.fetchall()}
    missing = [(col, dtype) for col, dtype in _DESIGN_CRITERIA_V2_REQUIRED_COLUMNS if col not in existing]

    if missing:
        logger.warning(
            "design_criteria_v2 schema drift: adding %d missing column(s): %s",
            len(missing),
            [col for col, _ in missing],
        )
        for col, dtype in missing:
            cur.execute(f"ALTER TABLE design_criteria_v2 ADD COLUMN IF NOT EXISTS {col} {dtype}")

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_dc_v2_template_ref "
        "ON design_criteria_v2(template_id, ref_number)"
    )

    if missing:
        logger.info("design_criteria_v2 schema drift corrected — %d column(s) added", len(missing))


# Columns required by LIMS_FIELDS["m1"] in routes/lims.py that were missing
# from the DB due to migration drift between 000008 and 000029.
_LIMS_M1_REQUIRED_COLUMNS = [
    "k80_um",
    "other_sulphides_pct",
    "k_feldspar_pct",
    "other_silicates_pct",
    "k_other_pct",
    "muscovite_illite_pct",
    "ca_minerals_pct",
    "fe_oxides_pct",
    "ilmenite_pct",
    "ti_oxides_pct",
    "other_oxides_pct",
    "carbonates_pct",
    "apatite_pct",
    "other_pct",
    "au_free_pct",
]


def _ensure_lims_m1_columns(cur) -> None:
    """Add any missing columns to lims_m1 (idempotent, non-destructive).

    Uses information_schema to detect which columns are absent, then adds
    only those. Safe to call on every startup — ADD COLUMN IF NOT EXISTS
    is a no-op when the column already exists.
    """
    # Check if lims_m1 exists at all before touching it
    cur.execute("SELECT to_regclass('public.lims_m1')")
    if cur.fetchone()[0] is None:
        return  # Table doesn't exist yet — Alembic will create it

    cur.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'lims_m1'"
    )
    existing = {row[0] for row in cur.fetchall()}

    missing = [col for col in _LIMS_M1_REQUIRED_COLUMNS if col not in existing]
    if not missing:
        return  # All columns present — nothing to do

    logger.warning(
        "lims_m1 schema drift detected — adding %d missing column(s): %s",
        len(missing),
        missing,
    )
    for col in missing:
        cur.execute(f"ALTER TABLE lims_m1 ADD COLUMN IF NOT EXISTS {col} NUMERIC")

    # Indexes (IF NOT EXISTS — safe to re-run)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_lims_m1_k80 ON lims_m1(project_id, k80_um) WHERE k80_um IS NOT NULL")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_lims_m1_au_free "
        "ON lims_m1(project_id, au_free_pct) WHERE au_free_pct IS NOT NULL"
    )
    logger.info("lims_m1 schema drift corrected — %d column(s) added", len(missing))


# ── Defensive fix for all other LIMS tables (migration 000043) ───────────────

_LIMS_TABLE_FIXES: dict[str, list[tuple[str, str]]] = {
    "lims_d1": [
        ("au_leach_feed_g_t", "NUMERIC"),
        ("densite_solide_sg", "NUMERIC"),
        ("nacn_initiale_ppm", "NUMERIC"),
        ("o2_dissous_mg_l", "NUMERIC"),
    ],
    "lims_c2": [
        ("au_concentre_g_t", "NUMERIC"),
        ("duree_concentration_min", "NUMERIC"),
        ("grg_rec_pct", "NUMERIC"),
        ("masse_concentre_g", "NUMERIC"),
        ("pression_fluidisation_psi", "NUMERIC"),
    ],
    "lims_flotation": [
        ("au_concentrate_g_t", "NUMERIC"),
        ("collecteur_g_t", "NUMERIC"),
        ("concentrate_wt_pct", "NUMERIC"),
        ("depressant_g_t", "NUMERIC"),
        ("moussant_g_t", "NUMERIC"),
        ("recup_s_pct", "NUMERIC"),
        ("temps_total_min", "NUMERIC"),
    ],
    "lims_elution": [
        ("au_elue_mg_l", "NUMERIC"),
        ("au_solution_fin_mg_l", "NUMERIC"),
        ("au_solution_ini_mg_l", "NUMERIC"),
        ("charbon_type", "TEXT"),
        ("charge_charbon_g_l", "NUMERIC"),
        ("eluant_cn_g_l", "NUMERIC"),
        ("eluant_naoh_g_l", "NUMERIC"),
        ("elution_t_c", "NUMERIC"),
        ("fines_charbon_pct", "NUMERIC"),
        ("kinetique_adsorption", "TEXT"),
        ("observations", "TEXT"),
        ("recup_au_elution_pct", "NUMERIC"),
        ("temps_elution_h", "NUMERIC"),
        ("type_test", "TEXT"),
    ],
    # lims_e1 (Épaississement): colonnes ajoutées par migration 029 absentes sur certaines DB prod.
    # schema.sql utilise mass_flux_t_m2_d / underflow_viscosity_mpa_s / underflow_sg —
    # migration 029 et le template LIMS utilisent flux_t_m2_d / viscosity_mpa_s / uf_density_pct.
    "lims_e1": [
        ("uf_density_pct", "NUMERIC"),
        ("uf_density_t_m3", "NUMERIC"),
        ("flux_t_m2_d", "NUMERIC"),
        ("cn_overflow_ppm", "NUMERIC"),
        ("au_overflow_ppb", "NUMERIC"),
        ("viscosity_mpa_s", "NUMERIC"),
    ],
}


_LIMS_DETOX_COLUMNS = [
    ("cn_wad_mg_l", "NUMERIC"), ("cn_total_mg_l", "NUMERIC"), ("cn_free_mg_l", "NUMERIC"),
    ("scn_mg_l", "NUMERIC"), ("ph_final", "NUMERIC"), ("cu_mg_l", "NUMERIC"),
    ("fe_mg_l", "NUMERIC"), ("ni_mg_l", "NUMERIC"), ("zn_mg_l", "NUMERIC"),
    ("as_mg_l", "NUMERIC"), ("hg_ug_l", "NUMERIC"), ("pb_mg_l", "NUMERIC"),
    ("consomm_so2_kg_t", "NUMERIC"), ("consomm_h2o2_kg_t", "NUMERIC"),
    ("consomm_cuso4_kg_t", "NUMERIC"), ("consomm_cao_kg_t", "NUMERIC"),
    ("duree_traitement_min", "NUMERIC"), ("cn_wad_rebound_24h", "NUMERIC"),
    ("cn_wad_rebound_7d", "NUMERIC"),
]


def _ensure_lims_detox_table(cur) -> None:
    """Create lims_detox if absent and migrate data from legacy lims_dtx.

    schema.sql bootstraps lims_dtx; migration 000029 (not always applied)
    creates lims_detox under the name used by routes and the LIMS template.
    """
    cur.execute("SELECT to_regclass('public.lims_detox')")
    if cur.fetchone()[0] is not None:
        return  # Table already exists — nothing to do

    logger.warning("schema drift: lims_detox absent — creating table")
    cols_ddl = ", ".join(f"{col} {dtype}" for col, dtype in _LIMS_DETOX_COLUMNS)
    cur.execute(f"""
        CREATE TABLE lims_detox (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            project_id  UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            sample_id   UUID REFERENCES lims_samples(id) ON DELETE CASCADE,
            {cols_ddl},
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_lims_detox_project ON lims_detox(project_id)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_lims_detox_sample ON lims_detox(sample_id)"
    )

    # Migrate existing data from legacy lims_dtx (if it exists and has rows)
    cur.execute("SELECT to_regclass('public.lims_dtx')")
    if cur.fetchone()[0] is not None:
        col_list = ", ".join(col for col, _ in _LIMS_DETOX_COLUMNS)
        cur.execute(f"""
            INSERT INTO lims_detox (project_id, sample_id, {col_list}, created_at)
            SELECT project_id, sample_id, {col_list}, created_at FROM lims_dtx
            ON CONFLICT DO NOTHING
        """)
        logger.info("schema drift: migrated rows from lims_dtx -> lims_detox")

    logger.info("schema drift: lims_detox created and populated")


def _ensure_all_lims_columns(cur) -> None:
    """Add missing columns to all LIMS tables (idempotent, non-destructive).

    Covers the drift fixed by migration 000043. Safe to call on every startup.
    """
    for table, cols in _LIMS_TABLE_FIXES.items():
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if cur.fetchone()[0] is None:
            continue  # Table doesn't exist yet

        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s",
            (table,),
        )
        existing = {row[0] for row in cur.fetchall()}
        missing = [(col, dtype) for col, dtype in cols if col not in existing]
        if not missing:
            continue

        logger.warning(
            "%s schema drift: adding %d column(s): %s",
            table,
            len(missing),
            [c for c, _ in missing],
        )
        for col, dtype in missing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {dtype}")
        logger.info("%s schema drift corrected — %d column(s) added", table, len(missing))


def bootstrap_schema() -> None:
    schema_sql = _SCHEMA_PATH.read_text(encoding="utf-8")
    c = conn()
    cur = None
    try:
        cur = c.cursor()
        cur.execute("SELECT pg_advisory_lock(%s)", (SETTINGS.schema_bootstrap_lock_id,))
        cur.execute(schema_sql)
        c.commit()
        logger.warning("schema bootstrap executed", extra={"startup_mode": "bootstrap"})
    except Exception:
        c.rollback()
        logger.exception("schema bootstrap failed")
        raise
    finally:
        if cur is not None:
            try:
                cur.execute("SELECT pg_advisory_unlock(%s)", (SETTINGS.schema_bootstrap_lock_id,))
            except Exception:
                pass
            cur.close()
        release(c)


def startup() -> None:
    logger.info("application startup", extra={"startup_mode": startup_mode(AUTO_MIGRATE, BOOTSTRAP_SCHEMA)})
    if AUTO_MIGRATE:
        run_migrations()
    elif BOOTSTRAP_SCHEMA:
        logger.warning("BOOTSTRAP_SCHEMA enabled - fallback mode only, prefer Alembic migrations")
        bootstrap_schema()
    ensure_schema_compatibility()
    seed_admin_user()
    seed_unit_operations_catalog()

    # Seed parameter registry from YAML
    try:
        from .db import execute as db_execute
        from .seed_parameter_registry import seed_registry

        seed_registry(db_execute)
    except Exception as e:
        logger.warning("Parameter registry seed skipped: %s", e)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    startup()
    yield


_telemetry_status = _telemetry_init_all(service="mpdpms-api")
logger.info("telemetry initialised", extra=_telemetry_status)

app = FastAPI(title="MPDPMS API", version="4.0.0", lifespan=lifespan)

# ── Rate limiter (slowapi) — doit être enregistré immédiatement après la création de l'app
try:
    try:
        from .rate_limiter import limiter, RateLimitExceeded, _rate_limit_handler
    except ImportError:
        from rate_limiter import limiter, RateLimitExceeded, _rate_limit_handler
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    logger.info("rate limiter registered (slowapi)")
except ImportError:
    logger.warning("slowapi not installed; rate limiting disabled")
except Exception as _e:
    logger.warning("rate limiter init failed: %s", _e)

if _telemetry_status.get("otel"):
    try:
        from telemetry import instrument_fastapi as _instrument_fastapi
    except ImportError:
        from .telemetry import instrument_fastapi as _instrument_fastapi
    _instrument_fastapi(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    allow_credentials=True,
    expose_headers=["X-Request-ID"],
)

# ─── Wire route modules ─────────────────────────────────────────────────────
app.include_router(admin_router)
app.include_router(stagegates_router)
app.include_router(risks_router)
app.include_router(costs_router)
app.include_router(ni43101_router)
app.include_router(ni43101_readiness_router)
app.include_router(blockmodel_router)
app.include_router(reports_router)
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(geotech_router, prefix="/api/v1/projects", tags=["geotech"])
app.include_router(gistm_router, prefix="/api/v1/projects", tags=["gistm"])
app.include_router(lims_router)
app.include_router(analysis_router)
app.include_router(tasks_router)
app.include_router(flowsheets_router)
app.include_router(design_router)
app.include_router(massbalance_router)
app.include_router(equipment_router)
# Register simulation_innovations BEFORE simulation_router so that the
# specific path /simulation/runs/diff is not shadowed by the legacy
# parameterised route /simulation/runs/{run_id} (chunk 9).
app.include_router(simulation_innovations_router)
app.include_router(simulation_router)
app.include_router(jobs_router)
app.include_router(dashboard_router)
app.include_router(exports_router)
app.include_router(decisions_router)
app.include_router(campaigns_router)
app.include_router(automation_router)
app.include_router(scenarios_router)
app.include_router(rampup_router)
app.include_router(working_capital_router)
app.include_router(modules_router)
app.include_router(pipeline_router)
app.include_router(economics_router, prefix="/api/v1/projects", tags=["economics"])
app.include_router(funding_router)
app.include_router(equipment_sizing_router, prefix="/api/v1/projects", tags=["equipment-sizing"])
app.include_router(pid_router, prefix="/api/v1/projects/{pid}/pid", tags=["pid"])
app.include_router(layout3d_router, prefix="/api/v1/projects/{pid}/layout3d", tags=["layout3d"])
app.include_router(analytics_router, prefix="/api/v1/projects/{pid}/analytics", tags=["analytics"])
app.include_router(process_model_router, prefix="/api/v1/projects", tags=["process-model"])
app.include_router(geochemistry_router, prefix="/api/v1/projects", tags=["geochemistry"])
app.include_router(closure_router, prefix="/api/v1/projects", tags=["closure"])
app.include_router(circuit_router)
app.include_router(flowsheet_tree_router)
app.include_router(simulation_defaults_router)
app.include_router(equipment_catalog_router)
app.include_router(flowsheet_templates_router)
# simulation_innovations_router was moved earlier (before simulation_router)
# to ensure /simulation/runs/diff isn't shadowed by /simulation/runs/{run_id}.
app.include_router(massbalance_v2_router)
app.include_router(equipment_v2_router)
app.include_router(metallurgical_decision_router)
app.include_router(capex_router)
app.include_router(simulation_v2_router)
app.include_router(simulation_compile_router)
app.include_router(optimization_router)
app.include_router(opex_v2_router)

# ─── Industrial hardening routers ────────────────────────────────────────
app.include_router(audit_trail_router, prefix="/api/v1/projects", tags=["audit"])
app.include_router(compliance_router, prefix="/api/v1/projects", tags=["compliance"])
app.include_router(sync_router, prefix="/api/v1/projects", tags=["sync"])
app.include_router(monitoring_router)
app.include_router(circuit_optimizer_router, prefix="/api/v1/projects", tags=["circuit-optimizer"])
app.include_router(assistant_router, prefix="/api/v1/projects", tags=["assistant"])
app.include_router(parameters_router)
app.include_router(traceability_router)
app.include_router(lims_alerts_router)
app.include_router(lims_outliers_router)
app.include_router(dc_pipeline_router)
app.include_router(flowsheet_engine_router)
app.include_router(flowsheet_graph_router)
app.include_router(ore_to_bullion_router)
app.include_router(engineering_insights_router)
app.include_router(geomet_intelligence_router, prefix="/api/v1/projects", tags=["geomet-intelligence"])
app.include_router(geomet_v2_router, prefix="/api/v2/projects", tags=["geomet-v2"])
app.include_router(sim_module_router)
app.include_router(sim_pro_router)
app.add_api_route("/metrics", metrics_endpoint, include_in_schema=False)


@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    """Record request metrics for Prometheus."""
    return await prometheus_middleware(request, call_next)


_csrf_middleware = _csrf_build_middleware(CORS_ORIGINS)


@app.middleware("http")
async def csrf_protection_middleware(request: Request, call_next):
    """Origin/Referer-based CSRF protection — see backend/csrf.py."""
    return await _csrf_middleware(request, call_next)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id
    rid_token = attach_request_id(request_id)
    started_at = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            logger.exception(
                "request failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                    "client_ip": request.client.host if request.client else "unknown",
                },
            )
            raise

        duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
        for header, value in security_headers(
            request_id, enable_hsts=ENABLE_HSTS, path=request.url.path, request=request
        ).items():
            response.headers[header] = value
        if (
            request.method == "GET"
            and 200 <= response.status_code < 300
            and "cache-control" not in (h.lower() for h in response.headers)
        ):
            path = request.url.path
            if path.startswith("/api/v1/") and any(
                seg in path
                for seg in (
                    "/dashboard",
                    "/stats",
                    "/summary",
                    "/completeness",
                    "/blocks/cutoff",
                    "/staleness",
                    "/automation/",
                    "/analytics/",
                    "/equipment-catalog",
                    "/unit-operations-catalog",
                )
            ):
                response.headers["Cache-Control"] = "private, max-age=10"
        logger.info(
            "request complete",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        return response
    finally:
        reset_request_id(rid_token)


# ─── Root: serve the canonical v3 monolithic HTML application ───────────────
# The v4 React SPA at frontend/ is no longer the primary frontend. Its routes
# are disabled below; deletion of frontend/ files is a separate operation.
_cached_html: str | None = None


@app.get("/", include_in_schema=False)
def root(request: Request):
    from fastapi.responses import HTMLResponse

    global _cached_html
    if FRONTEND_FILE.exists():
        # Always re-read in production to pick up deployments immediately
        _cached_html = FRONTEND_FILE.read_text(encoding="utf-8")
        return HTMLResponse(
            content=_cached_html,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
                "X-MetalFlow-Build": APP_BUILD_ID,
            },
        )
    return {"status": "ok", "app": "MPDPMS", "version": app.version}


def _redis_probe() -> dict:
    """Sonde Redis optionnelle pour observabilité (timeouts courts)."""
    try:
        s = get_settings()
    except Exception as exc:  # noqa: BLE001
        return {"redis": "unknown", "redis_detail": str(exc)[:120]}
    if not s.redis_url:
        return {"redis": "not_configured"}
    try:
        import redis as redis_lib

        r = redis_lib.from_url(s.redis_url, socket_connect_timeout=1.0, socket_timeout=1.0)
        r.ping()
        return {"redis": "ok"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("redis probe failed: %s", exc)
        return {"redis": "error", "redis_detail": str(exc)[:120]}


@app.get("/api/v1/health")
def health():
    c = conn()
    cur = None
    try:
        cur = c.cursor()
        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
        payload = {"status": "ok", "db": "connected", "check": row[0] if row else None}
        payload.update(_redis_probe())
        payload["app_build_id"] = APP_BUILD_ID
        return payload
    except Exception:
        logger.exception("health check failed")
        detail = {"status": "error", "db": "disconnected"}
        detail.update(_redis_probe())
        detail["app_build_id"] = APP_BUILD_ID
        raise HTTPException(status_code=503, detail=detail)
    finally:
        if cur is not None:
            cur.close()
        release(c)


@app.get("/api/v1/ready")
def readiness():
    c = conn()
    cur = None
    try:
        cur = c.cursor()
        cur.execute("SELECT 1 AS ok")
        row = cur.fetchone()
        return {
            "status": "ok",
            "db": "connected",
            "check": row[0] if row else None,
            "startup_mode": startup_mode(AUTO_MIGRATE, BOOTSTRAP_SCHEMA),
            "uploads_dir": str(UPLOADS_DIR),
            **_redis_probe(),
            "app_build_id": APP_BUILD_ID,
        }
    except Exception:
        logger.exception("readiness check failed")
        raise HTTPException(
            status_code=503,
            detail={
                "status": "error",
                "db": "disconnected",
                "startup_mode": startup_mode(AUTO_MIGRATE, BOOTSTRAP_SCHEMA),
                **_redis_probe(),
                "app_build_id": APP_BUILD_ID,
            },
        )
    finally:
        if cur is not None:
            cur.close()
        release(c)


@app.get("/ws/projects/{project_id}", status_code=400, include_in_schema=False)
async def websocket_endpoint_get_probe():
    """Return 400 for plain GET requests — WebSocket upgrade required."""
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": "WebSocket upgrade required"}, status_code=400)


async def _ws_authenticate(websocket: WebSocket, token: str | None) -> dict | None:
    """Validate JWT from WebSocket query string.

    Delegates to :func:`websocket_handlers.ws_authenticate`.
    """
    return await ws_authenticate(websocket, token)


async def _ws_check_project(websocket: WebSocket, project_id: str) -> bool:
    """Verify the project exists.

    Delegates to :func:`websocket_handlers.ws_check_project`.
    """
    return await ws_check_project(websocket, project_id)


@app.websocket("/ws/projects/{project_id}")
async def websocket_endpoint(websocket: WebSocket, project_id: str, token: str | None = Query(None)):
    await ws_run_connection(ws_manager, project_id, websocket, token, project_id)


@app.websocket("/ws/projects/{project_id}/analytics/live")
async def live_tags_ws(websocket: WebSocket, project_id: str, token: str | None = Query(None)):
    """Live process tag WebSocket — clients subscribe and receive tag updates."""
    await ws_run_live_connection(ws_manager, f"{project_id}:live", websocket, token, project_id)


# React SPA at /app/ is decommissioned. The v3 monolithic HTML at / is the
# primary frontend. Static-asset mount and SPA routes are no longer registered.
