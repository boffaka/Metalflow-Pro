from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=False)
except ImportError:
    pass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip() and item.strip().lower() != "null"]


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str | None
    jwt_secret: str
    jwt_secret_generated: bool
    admin_email: str
    admin_password: str
    auto_migrate: bool
    bootstrap_schema: bool
    enable_hsts: bool
    cors_origins: list[str]
    max_report_size_bytes: int
    log_level: str
    log_json: bool
    security_store_backend: str
    # Auth tuning
    jwt_exp_h: int
    refresh_exp_days: int
    login_max_attempts: int
    login_window_s: int
    frontend_filename: str
    schema_bootstrap_lock_id: int
    admin_seed_role: str
    admin_seed_full_name: str
    default_ore_sg: float
    default_recovery_pct: float
    default_availability_pct: float
    default_operating_hours_day: float
    default_energy_rate: float
    default_nacn_price: float
    default_cao_price: float
    default_aux_energy_kwh_t: float
    default_sag_specific_energy: float
    default_bm_specific_energy: float
    default_nacn_consumption_kg_t: float
    default_cao_consumption_kg_t: float
    # ESG / carbon
    grid_co2_kg_kwh: float
    wgc_co2_benchmark: int
    # Alerting
    alert_webhook_url: str
    # External AI (optional — enables assistant LLM feature)
    anthropic_api_key: str
    # Async jobs
    worker_enabled: bool
    job_retention_days: int
    job_zombie_timeout_seconds: int
    job_heartbeat_interval_seconds: int
    job_progress_throttle_ms: int
    job_cancel_cache_ms: int
    job_artifact_max_bytes: int
    job_payload_max_bytes: int

    def validate(self) -> None:
        if not self.database_url:
            raise ValueError("DATABASE_URL is required")
        if not self.admin_email:
            raise ValueError("ADMIN_EMAIL env var is required")
        if not self.admin_password:
            raise ValueError("ADMIN_PASSWORD env var is required")
        if self.admin_password == "admin123":
            raise ValueError(
                "ADMIN_PASSWORD is set to the insecure default 'admin123'. "
                "Set a strong password via the ADMIN_PASSWORD environment variable before starting."
            )
        if self.max_report_size_bytes <= 0:
            raise ValueError("MAX_REPORT_SIZE_BYTES must be > 0")
        if self.auto_migrate and self.bootstrap_schema:
            raise ValueError("AUTO_MIGRATE and BOOTSTRAP_SCHEMA cannot both be enabled")
        if len(self.jwt_secret) < 16:
            raise ValueError("JWT_SECRET must be at least 16 characters long")
        if len(self.jwt_secret) < 32:
            import logging
            logging.getLogger("mpdpms.settings").warning(
                "JWT_SECRET is shorter than 32 characters — use a longer secret in production"
            )
        if len(self.admin_password) < 8:
            raise ValueError("ADMIN_PASSWORD must be at least 8 characters long")
        if self.security_store_backend not in {"memory", "redis"}:
            raise ValueError("SECURITY_STORE_BACKEND must be 'memory' or 'redis'")
        if self.security_store_backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when SECURITY_STORE_BACKEND=redis")
        if self.schema_bootstrap_lock_id <= 0:
            raise ValueError("SCHEMA_BOOTSTRAP_LOCK_ID must be > 0")
        if self.default_ore_sg <= 0:
            raise ValueError("DEFAULT_ORE_SG must be > 0")
        if not (0 < self.default_recovery_pct <= 100):
            raise ValueError("DEFAULT_RECOVERY_PCT must be > 0 and <= 100")
        if not (0 < self.default_availability_pct <= 100):
            raise ValueError("DEFAULT_AVAILABILITY_PCT must be > 0 and <= 100")
        if not (0 < self.default_operating_hours_day <= 24):
            raise ValueError("DEFAULT_OPERATING_HOURS_DAY must be > 0 and <= 24")
        if self.job_zombie_timeout_seconds <= self.job_heartbeat_interval_seconds * 3:
            raise ValueError(
                "JOB_ZOMBIE_TIMEOUT_SECONDS must be at least 3× JOB_HEARTBEAT_INTERVAL_SECONDS"
            )
        if self.job_payload_max_bytes < 1024:
            raise ValueError("JOB_PAYLOAD_MAX_BYTES must be >= 1024")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    jwt_secret = os.getenv("JWT_SECRET")
    generated = False
    if not jwt_secret:
        if os.getenv("ENABLE_HSTS", "").lower() in {"1", "true", "yes", "on"}:
            raise ValueError(
                "JWT_SECRET environment variable is required in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        import logging as _log
        _log.getLogger("mpdpms.settings").warning(
            "JWT_SECRET not set — generating ephemeral secret (tokens will not survive restart)"
        )
        jwt_secret = secrets.token_urlsafe(64)
        generated = True

    settings = Settings(
        database_url=os.getenv("DATABASE_URL", ""),
        redis_url=os.getenv("REDIS_URL"),
        jwt_secret=jwt_secret,
        jwt_secret_generated=generated,
        admin_email=os.getenv("ADMIN_EMAIL", ""),
        admin_password=os.getenv("ADMIN_PASSWORD", ""),
        auto_migrate=_env_bool("AUTO_MIGRATE", True),
        bootstrap_schema=_env_bool("BOOTSTRAP_SCHEMA", False),
        enable_hsts=_env_bool("ENABLE_HSTS", True),
        cors_origins=_env_list(
            "CORS_ORIGINS",
            "http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173,http://127.0.0.1:8000,http://localhost:8000",
        ),
        max_report_size_bytes=_env_int("MAX_REPORT_SIZE_BYTES", 25 * 1024 * 1024),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        log_json=_env_bool("LOG_JSON", True),
        security_store_backend=os.getenv("SECURITY_STORE_BACKEND", "memory").lower(),
        jwt_exp_h=_env_int("JWT_EXP_H", 1),
        refresh_exp_days=_env_int("REFRESH_EXP_DAYS", 14),
        login_max_attempts=_env_int("LOGIN_MAX_ATTEMPTS", 5),
        login_window_s=_env_int("LOGIN_WINDOW_S", 60),
        frontend_filename=os.getenv("FRONTEND_FILENAME", "MetalFlowPro_v3_1.html"),
        schema_bootstrap_lock_id=_env_int("SCHEMA_BOOTSTRAP_LOCK_ID", 987654321),
        admin_seed_role=os.getenv("ADMIN_SEED_ROLE", "Project Manager"),
        admin_seed_full_name=os.getenv("ADMIN_SEED_FULL_NAME", "Admin"),
        default_ore_sg=float(os.getenv("DEFAULT_ORE_SG", "2.75")),
        default_recovery_pct=float(os.getenv("DEFAULT_RECOVERY_PCT", "89.0")),
        default_availability_pct=float(os.getenv("DEFAULT_AVAILABILITY_PCT", "92.0")),
        default_operating_hours_day=float(os.getenv("DEFAULT_OPERATING_HOURS_DAY", "24.0")),
        default_energy_rate=float(os.getenv("DEFAULT_ENERGY_RATE", "0.08")),
        default_nacn_price=float(os.getenv("DEFAULT_NACN_PRICE", "3.50")),
        default_cao_price=float(os.getenv("DEFAULT_CAO_PRICE", "0.12")),
        default_aux_energy_kwh_t=float(os.getenv("DEFAULT_AUX_ENERGY_KWH_T", "5.0")),
        default_sag_specific_energy=float(os.getenv("DEFAULT_SAG_SPECIFIC_ENERGY", "8.0")),
        default_bm_specific_energy=float(os.getenv("DEFAULT_BM_SPECIFIC_ENERGY", "7.0")),
        default_nacn_consumption_kg_t=float(os.getenv("DEFAULT_NACN_CONSUMPTION_KG_T", "0.5")),
        default_cao_consumption_kg_t=float(os.getenv("DEFAULT_CAO_CONSUMPTION_KG_T", "1.2")),
        grid_co2_kg_kwh=float(os.getenv("GRID_CO2_KG_KWH", "0.50")),
        wgc_co2_benchmark=int(os.getenv("WGC_CO2_BENCHMARK", "800")),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        worker_enabled=_env_bool("WORKER_ENABLED", True),
        job_retention_days=_env_int("JOB_RETENTION_DAYS", 7),
        job_zombie_timeout_seconds=_env_int("JOB_ZOMBIE_TIMEOUT_SECONDS", 90),
        job_heartbeat_interval_seconds=_env_int("JOB_HEARTBEAT_INTERVAL_SECONDS", 5),
        job_progress_throttle_ms=_env_int("JOB_PROGRESS_THROTTLE_MS", 500),
        job_cancel_cache_ms=_env_int("JOB_CANCEL_CACHE_MS", 500),
        job_artifact_max_bytes=_env_int("JOB_ARTIFACT_MAX_BYTES", 20 * 1024 * 1024),
        job_payload_max_bytes=_env_int("JOB_PAYLOAD_MAX_BYTES", 1024 * 1024),
    )
    settings.validate()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
