"""Telemetry init must be safe without DSN/endpoint configured."""
from __future__ import annotations

import os

import pytest

from telemetry import init_sentry, init_opentelemetry, init_all


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for key in (
        "SENTRY_DSN",
        "SENTRY_ENVIRONMENT",
        "SENTRY_TRACES_SAMPLE_RATE",
        "SENTRY_RELEASE",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_SERVICE_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    yield


def test_init_sentry_skips_without_dsn():
    assert init_sentry() is False


def test_init_sentry_skips_when_dsn_blank(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "   ")
    assert init_sentry() is False


def test_init_otel_skips_without_endpoint():
    assert init_opentelemetry() is False


def test_init_otel_skips_when_endpoint_blank(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    assert init_opentelemetry() is False


def test_init_all_returns_status_dict():
    status = init_all()
    assert status == {"sentry": False, "otel": False}


def test_instrument_helpers_are_noop_without_otel():
    """Instrumentation helpers must not raise when OTel SDK is missing/inactive."""
    from telemetry import (
        instrument_celery,
        instrument_psycopg2,
        instrument_redis,
        instrument_sqlalchemy,
    )

    instrument_celery()
    instrument_psycopg2()
    instrument_redis()
    instrument_sqlalchemy(engine=None)
