"""Sentry + OpenTelemetry initialization.

Both stacks initialise *graciously*: if their SDK is missing or no DSN/endpoint
is configured, init is a no-op. This keeps local dev lightweight while still
giving production-grade observability when env vars are set.

Env vars
--------
SENTRY_DSN                          Activates Sentry. Empty/unset → disabled.
SENTRY_ENVIRONMENT                  Tag for the env (default: ENV or "dev").
SENTRY_TRACES_SAMPLE_RATE           0.0–1.0 (default: 0.1 in prod, 0 in dev).
SENTRY_RELEASE                      Release version (default: app version).

OTEL_EXPORTER_OTLP_ENDPOINT         Activates OTel. Empty/unset → disabled.
OTEL_SERVICE_NAME                   Service name (default: "mpdpms-api").
OTEL_RESOURCE_ATTRIBUTES            Standard k=v,k=v overrides.
OTEL_TRACES_SAMPLER                 Standard OTel sampler (default: parentbased_traceidratio).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger("mpdpms.telemetry")


def _truthy(value: str | None) -> bool:
    return bool(value) and value.strip().lower() not in {"", "0", "false", "no"}


def init_sentry(service: str = "mpdpms-api") -> bool:
    """Initialise Sentry if SENTRY_DSN is set. Returns True if active."""
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.debug("sentry: DSN not set, skipping init")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError as exc:
        logger.warning("sentry: SDK not installed (%s) — install sentry-sdk[fastapi,celery,sqlalchemy]", exc)
        return False

    env = os.getenv("SENTRY_ENVIRONMENT") or os.getenv("ENV") or "dev"
    is_prod = env in {"prod", "production"}
    sample_rate = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1" if is_prod else "0"))
    release = os.getenv("SENTRY_RELEASE")

    sentry_sdk.init(
        dsn=dsn,
        environment=env,
        release=release,
        integrations=[
            FastApiIntegration(transaction_style="endpoint"),
            SqlalchemyIntegration(),
            CeleryIntegration(),
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        traces_sample_rate=sample_rate,
        send_default_pii=False,
        attach_stacktrace=True,
        in_app_include=["backend", "tasks"],
    )
    sentry_sdk.set_tag("service", service)
    logger.info("sentry: initialised env=%s sample_rate=%s", env, sample_rate)
    return True


def init_opentelemetry(service: str = "mpdpms-api") -> bool:
    """Initialise OpenTelemetry tracing if OTEL_EXPORTER_OTLP_ENDPOINT is set."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        logger.debug("otel: OTLP endpoint not set, skipping init")
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning("otel: SDK not installed (%s) — install opentelemetry-distro and exporters", exc)
        return False

    resource = Resource.create({
        "service.name": os.getenv("OTEL_SERVICE_NAME", service),
        "service.namespace": "mpdpms",
        "deployment.environment": os.getenv("ENV", "dev"),
    })
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    logger.info("otel: initialised service=%s endpoint=%s", service, endpoint)
    return True


def instrument_fastapi(app: Any) -> None:
    """Auto-instrument a FastAPI app. No-op if OTel SDK is missing."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        return
    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine: Any) -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    except ImportError:
        return
    SQLAlchemyInstrumentor().instrument(engine=engine)


def instrument_celery() -> None:
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
    except ImportError:
        return
    CeleryInstrumentor().instrument()


def instrument_psycopg2() -> None:
    try:
        from opentelemetry.instrumentation.psycopg2 import Psycopg2Instrumentor
    except ImportError:
        return
    Psycopg2Instrumentor().instrument()


def instrument_redis() -> None:
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor
    except ImportError:
        return
    RedisInstrumentor().instrument()


def init_all(service: str = "mpdpms-api", *, fastapi_app: Any = None) -> dict[str, bool]:
    """One-shot init for both stacks plus auto-instrumentation. Returns status."""
    sentry_ok = init_sentry(service)
    otel_ok = init_opentelemetry(service)
    if otel_ok:
        if fastapi_app is not None:
            instrument_fastapi(fastapi_app)
        instrument_psycopg2()
        instrument_redis()
    return {"sentry": sentry_ok, "otel": otel_ok}
