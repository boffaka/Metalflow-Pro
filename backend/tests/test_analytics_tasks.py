# backend/tests/test_analytics_tasks.py
"""Tests for analytics task functions."""
import pytest
import numpy as np


def test_train_isolation_forest_returns_model():
    try:
        from backend.tasks.analytics_tasks import train_isolation_forest
    except ImportError:
        from tasks.analytics_tasks import train_isolation_forest

    data = np.random.normal(loc=650.0, scale=10.0, size=500).tolist()
    model_bytes = train_isolation_forest(data)
    assert isinstance(model_bytes, bytes)
    assert len(model_bytes) > 0


def test_detect_anomalies_flags_outlier():
    try:
        from backend.tasks.analytics_tasks import train_isolation_forest, detect_anomaly
    except ImportError:
        from tasks.analytics_tasks import train_isolation_forest, detect_anomaly

    normal = np.random.normal(650.0, 10.0, 500).tolist()
    model_bytes = train_isolation_forest(normal)

    # Normal value should NOT be anomaly
    is_anomaly, sigma = detect_anomaly(model_bytes, value=650.0, history=normal)
    assert not is_anomaly

    # Extreme outlier SHOULD be detected
    is_anomaly2, sigma2 = detect_anomaly(model_bytes, value=9999.0, history=normal)
    assert is_anomaly2


def test_compute_kpi_snapshot_returns_dict():
    try:
        from backend.tasks.analytics_tasks import compute_kpi_snapshot
    except ImportError:
        from tasks.analytics_tasks import compute_kpi_snapshot

    result = compute_kpi_snapshot(
        annual_oz=170_000,
        avail_pct=92.0,
        recovery_pct=89.5,
        energy_kwh_t=18.5,
        nacn_kg_t=0.35,
        aisc_usd_oz=850.0,
    )
    assert "oz_produced_daily" in result
    assert "recovery_pct" in result
    assert result["recovery_pct"] == 89.5
