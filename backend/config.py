"""
MPDPMS — Centralized configuration.

All environment-configurable settings in one place.
Every hardcoded value that should be tunable is defined here
with a sensible default that can be overridden via environment variables.
"""
from __future__ import annotations

import os


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_required(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Variable d'environnement requise manquante: {key}")
    return val


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("1", "true", "yes")


# =============================================================================
# JWT / Authentication
# =============================================================================
JWT_SECRET = _env("JWT_SECRET", "change-me")
JWT_ALGORITHM = _env("JWT_ALGORITHM", "HS256")
# DEPRECATED — auth.py reads jwt_exp_h from settings.py via JWT_EXP_H env var.
# JWT_EXP_HOURS is no longer consumed anywhere; kept here only so that operators
# who set it receive a visible warning (see auth.py) rather than silent no-op.
# Remove once migration to settings.py is complete.
JWT_EXP_HOURS = _env_int("JWT_EXP_HOURS", 4)  # unused — set JWT_EXP_H instead

# =============================================================================
# Rate limiting (login endpoint)
# =============================================================================
LOGIN_RATE_WINDOW_SEC = _env_int("LOGIN_RATE_WINDOW_SEC", 60)
LOGIN_MAX_ATTEMPTS = _env_int("LOGIN_MAX_ATTEMPTS", 5)
LOGIN_MAX_IPS = _env_int("LOGIN_MAX_IPS", 10_000)
LOGIN_CLEANUP_INTERVAL_SEC = _env_int("LOGIN_CLEANUP_INTERVAL_SEC", 300)

# =============================================================================
# File uploads
# =============================================================================
UPLOADS_DIR = _env("UPLOADS_DIR", "storage/reports")
MAX_UPLOAD_SIZE_BYTES = _env_int("MAX_UPLOAD_SIZE_MB", 50) * 1024 * 1024
ALLOWED_UPLOAD_EXTENSIONS = frozenset(
    _env("ALLOWED_UPLOAD_EXTENSIONS", ".pdf,.docx,.xlsx,.csv,.pptx,.zip,.png,.jpg,.jpeg")
    .split(",")
)

# =============================================================================
# Database
# =============================================================================
DATABASE_URL = _env_required("DATABASE_URL")
DB_POOL_MIN = _env_int("DB_POOL_MIN", 2)
DB_POOL_MAX = _env_int("DB_POOL_MAX", 20)
DB_ADVISORY_LOCK_ID = _env_int("DB_ADVISORY_LOCK_ID", 987654321)

# =============================================================================
# Admin
# =============================================================================
ADMIN_EMAIL = _env("ADMIN_EMAIL", "admin@mpdpms.dev")
ADMIN_PASSWORD = _env_required("ADMIN_PASSWORD")

# =============================================================================
# CORS
# =============================================================================
CORS_ORIGINS_RAW = _env(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:8000,http://localhost:8000",
)

# =============================================================================
# Metallurgical defaults (used when no LIMS data or sim params exist)
# =============================================================================
DEFAULT_ORE_SG = _env_float("DEFAULT_ORE_SG", 2.75)
DEFAULT_BWI = _env_float("DEFAULT_BWI", 14.0)
# Aligned with industry_defaults.yaml (89 %) and settings.py (DEFAULT_RECOVERY_PCT=89.0).
# Previously 91.0 — inconsistent with the two other authoritative sources.
DEFAULT_RECOVERY_PCT = _env_float("DEFAULT_RECOVERY_PCT", 89.0)
DEFAULT_NACN_KG_T = _env_float("DEFAULT_NACN_KG_T", 0.5)
# Aligned with industry_defaults.yaml (1.2 kg/t) and settings.py (DEFAULT_CAO_CONSUMPTION_KG_T=1.2).
# Previously 0.8 — below the industry reference range minimum (0.3–10 kg/t midpoint).
DEFAULT_CAO_KG_T = _env_float("DEFAULT_CAO_KG_T", 1.2)
DEFAULT_UNIT_AREA = _env_float("DEFAULT_UNIT_AREA", 0.08)
DEFAULT_FLOC_DOSAGE = _env_float("DEFAULT_FLOC_DOSAGE", 25.0)
DEFAULT_P80_UM = _env_float("DEFAULT_P80_UM", 75.0)
DEFAULT_ABRASION_INDEX = _env_float("DEFAULT_ABRASION_INDEX", 0.3)
DEFAULT_BM_F80_UM = _env_float("DEFAULT_BM_F80_UM", 3000.0)
DEFAULT_AVAILABILITY_PCT = _env_float("DEFAULT_AVAILABILITY_PCT", 92.0)

# =============================================================================
# CIL tank sizing
# =============================================================================
MAX_CIL_TANK_VOLUME_M3 = _env_float("MAX_CIL_TANK_VOLUME_M3", 4000.0)
MIN_CIL_TANKS = _env_int("MIN_CIL_TANKS", 6)

# =============================================================================
# Thickener sizing
# =============================================================================
MAX_THICKENER_DIAMETER_M = _env_float("MAX_THICKENER_DIAMETER_M", 45.0)
THICKENER_DESIGN_SAFETY_FACTOR = _env_float("THICKENER_DESIGN_SAFETY_FACTOR", 1.15)

# =============================================================================
# Elution / ADR defaults (when no LIMS data)
# =============================================================================
DEFAULT_ELUTION_EFFICIENCY_PCT = _env_float("DEFAULT_ELUTION_EFFICIENCY_PCT", 98.0)
DEFAULT_ELUTION_TEMP_C = _env_float("DEFAULT_ELUTION_TEMP_C", 135.0)
DEFAULT_ELUTION_CYCLE_TIME_H = _env_float("DEFAULT_ELUTION_CYCLE_TIME_H", 12.0)
DEFAULT_CARBON_LOADING_G_T = _env_float("DEFAULT_CARBON_LOADING_G_T", 2000.0)
CARBON_OUTLET_LOADING_G_T = _env_float("CARBON_OUTLET_LOADING_G_T", 50.0)
CARBON_MAKEUP_RATE_KG_T = _env_float("CARBON_MAKEUP_RATE_KG_T", 0.04)
AARL_TEMP_THRESHOLD_C = _env_float("AARL_TEMP_THRESHOLD_C", 130.0)

# =============================================================================
# Environmental / compliance thresholds
# =============================================================================
WAD_CN_COMPLIANCE_LIMIT_MG_L = _env_float("WAD_CN_COMPLIANCE_LIMIT_MG_L", 2.0)
DETOX_CN_TARGET_MG_L = _env_float("DETOX_CN_TARGET_MG_L", 2.0)

# =============================================================================
# NI 43-101 report thresholds
# =============================================================================
GRG_CIRCUIT_THRESHOLD_PCT = _env_float("GRG_CIRCUIT_THRESHOLD_PCT", 15.0)
FLOTATION_S_THRESHOLD_PCT = _env_float("FLOTATION_S_THRESHOLD_PCT", 3.0)

# =============================================================================
# Economics & funding (single source for gold price & quick-estimate fallbacks)
# =============================================================================
_gold_price_raw = os.getenv("DEFAULT_GOLD_PRICE_USD_OZ") or os.getenv("ECON_DEFAULT_GOLD_PRICE_USD_OZ")
# Aligned with industry_defaults.yaml (2200 USD/oz — 2024/2025 spot reference).
# Previously 1900.0 — below current market and inconsistent with the YAML reference.
DEFAULT_GOLD_PRICE_USD_OZ = float(_gold_price_raw) if _gold_price_raw not in (None, "") else 2200.0
LOW_GOLD_PRICE_ALERT_THR_USD_OZ = _env_float("LOW_GOLD_PRICE_ALERT_THR_USD_OZ", 2000.0)

FUNDING_FALLBACK_TPH = _env_float("FUNDING_FALLBACK_TPH", 500.0)
FUNDING_FALLBACK_GRADE_G_T = _env_float("FUNDING_FALLBACK_GRADE_G_T", 2.0)
FUNDING_CAPEX_USD_PER_DAILY_TONNE = _env_float("FUNDING_CAPEX_USD_PER_DAILY_TONNE", 40000.0)
FUNDING_FALLBACK_RECOVERY = _env_float("FUNDING_FALLBACK_RECOVERY", 0.89)
FUNDING_FALLBACK_OPEX_USD_PER_T = _env_float("FUNDING_FALLBACK_OPEX_USD_PER_T", 35.0)

# design.py — LIMS average fallback when elution carbon_loading is absent (typical 1800–2000 g/t)
CARBON_LOADING_ELUTION_AVG_FALLBACK_G_T = _env_float("CARBON_LOADING_ELUTION_AVG_FALLBACK_G_T", 1900.0)

# Rough equipment sizing CAPEX proxies (USD, analytical — not vendor quotes)
SIZING_CAPEX_MILL_USD_COEFF = _env_float("SIZING_CAPEX_MILL_USD_COEFF", 1500.0)
SIZING_CAPEX_MILL_POWER_EXP = _env_float("SIZING_CAPEX_MILL_POWER_EXP", 0.62)
SIZING_CAPEX_TANK_USD_PER_M3 = _env_float("SIZING_CAPEX_TANK_USD_PER_M3", 8000.0)
SIZING_CAPEX_EW_USD_PER_CELL = _env_float("SIZING_CAPEX_EW_USD_PER_CELL", 50000.0)
SIZING_CAPEX_THICKENER_USD_PER_M2 = _env_float("SIZING_CAPEX_THICKENER_USD_PER_M2", 5000.0)
DEFAULT_TARGET_TPH_FALLBACK = _env_float("DEFAULT_TARGET_TPH_FALLBACK", 500.0)

# =============================================================================
# Mass balance factors
# =============================================================================
GRAVITY_CONCENTRATE_ENRICHMENT = _env_float("GRAVITY_CONCENTRATE_ENRICHMENT", 50.0)
MAX_CIL_RECOVERY = _env_float("MAX_CIL_RECOVERY", 0.96)
MAX_GRAVITY_RECOVERY = _env_float("MAX_GRAVITY_RECOVERY", 0.50)
WATER_EVAPORATION_FACTOR = _env_float("WATER_EVAPORATION_FACTOR", 0.015)
THICKENER_WATER_RECOVERY = _env_float("THICKENER_WATER_RECOVERY", 0.60)

# =============================================================================
# Logging
# =============================================================================
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
