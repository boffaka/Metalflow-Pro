"""Tests for LIMS anomaly detection engine."""
import math

try:
    from engines.lims_intelligence import detect_outliers, detect_cross_test_issues
except ImportError:
    from backend.engines.lims_intelligence import detect_outliers, detect_cross_test_issues


def test_detect_outlier_in_grades():
    samples = [
        {"au_g_t": 2.0, "domain": "oxide"},
        {"au_g_t": 2.1, "domain": "oxide"},
        {"au_g_t": 1.9, "domain": "oxide"},
        {"au_g_t": 2.0, "domain": "oxide"},
        {"au_g_t": 2.05, "domain": "oxide"},
        {"au_g_t": 1.95, "domain": "oxide"},
        {"au_g_t": 2.0, "domain": "oxide"},
        {"au_g_t": 45.0, "domain": "oxide"},
    ]
    alerts = detect_outliers("a1", samples, "au_g_t")
    assert len(alerts) >= 1
    assert alerts[0]["severity"] in ("warning", "critical")


def test_no_outlier_in_normal_data():
    samples = [
        {"au_g_t": 2.0, "domain": "oxide"},
        {"au_g_t": 2.1, "domain": "oxide"},
        {"au_g_t": 1.9, "domain": "oxide"},
    ]
    alerts = detect_outliers("a1", samples, "au_g_t")
    assert alerts == []


def test_detect_cross_test_high_recovery_sulfide():
    a1_data = [{"s_total_pct": 6.0}]
    d1_data = [{"au_recovery_pct": 97.0}]
    alerts = detect_cross_test_issues(a1_data, d1_data=d1_data)
    assert len(alerts) >= 1


def test_no_cross_test_issue_normal():
    a1_data = [{"s_total_pct": 1.0}]
    d1_data = [{"au_recovery_pct": 88.0}]
    alerts = detect_cross_test_issues(a1_data, d1_data=d1_data)
    assert alerts == []


def test_detect_hard_ore_low_grade():
    a1_data = [{"au_g_t": 0.3, "s_total_pct": 1.0}]
    b1_data = [{"bwi_kwh_t": 22.0}]
    alerts = detect_cross_test_issues(a1_data, b1_data=b1_data)
    assert len(alerts) >= 1
    assert "dur" in alerts[0]["message"].lower() or "bwi" in alerts[0]["message"].lower()
