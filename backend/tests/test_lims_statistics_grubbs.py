# backend/tests/test_lims_statistics_grubbs.py
"""Unit tests for Grubbs outlier detection (audit §1.1, spec
docs/superpowers/specs/2026-05-06-lims-grubbs-outliers-design.md)."""
from __future__ import annotations

import math
import os

import pytest

# These tests are pure-Python (no DB) — explicitly opt out of the global
# TEST_DATABASE_URL skip applied by conftest.py to integration tests.
os.environ.setdefault("DATABASE_URL", "postgresql://fake:fake@localhost:5432/fake")
os.environ.setdefault("JWT_SECRET", "test-secret-do-not-use-in-production-32chars-min")
os.environ.setdefault("ADMIN_EMAIL", "admin@test.dev")
os.environ.setdefault("ADMIN_PASSWORD", "TestPassword123!")

try:
    from engines.lims_statistics import grubbs_outliers
except ImportError:
    from backend.engines.lims_statistics import grubbs_outliers


def test_grubbs_handles_n_below_3():
    """N<3 returns all is_outlier=False with low_power=True; no exception."""
    out = grubbs_outliers([10.0, 20.0])
    assert len(out) == 2
    for r in out:
        assert r["is_outlier"] is False
        assert r["low_power"] is True


def test_grubbs_handles_zero_std_all_identical():
    """All identical values (std=0) → no outliers, early break, no exception."""
    out = grubbs_outliers([5.0, 5.0, 5.0, 5.0])
    assert len(out) == 4
    for r in out:
        assert r["is_outlier"] is False


def test_grubbs_returns_correct_shape():
    """Each result dict has the documented keys."""
    out = grubbs_outliers([10.0, 11.0, 12.0, 13.0, 14.0, 100.0])
    assert len(out) == 6
    expected_keys = {"index", "value", "grubbs_g", "critical_value", "is_outlier", "low_power"}
    for r in out:
        assert set(r.keys()) == expected_keys


def test_grubbs_no_detection_on_normal_data():
    """Tight, normal-ish data → no outliers."""
    out = grubbs_outliers([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    for r in out:
        assert r["is_outlier"] is False


def test_grubbs_detects_obvious_outlier_with_known_g_value():
    """Hand-verifiable test: [10,11,12,13,14,100] α=0.05 → index 5 is outlier.

    Computed values (scipy.stats.t):
      mean=26.667, std=35.954 (Bessel-corrected)
      G_max = |100-26.667|/35.954 = 2.040
      t_crit = t.ppf(1 - 0.05/12, 4) = 4.851
      G_critical = (5/√6) × √(t²/(4+t²)) = 1.887
      2.040 > 1.887 → outlier
    """
    out = grubbs_outliers([10.0, 11.0, 12.0, 13.0, 14.0, 100.0], alpha=0.05)
    assert out[5]["is_outlier"] is True
    assert out[5]["grubbs_g"] == pytest.approx(2.040, abs=0.005)
    assert out[5]["critical_value"] == pytest.approx(1.887, abs=0.005)
    # Indices 0-4 must NOT be outliers
    for i in range(5):
        assert out[i]["is_outlier"] is False


def test_grubbs_iterates_for_multiple_outliers():
    """Two clear outliers → both flagged via 2 iterations.

    Data calibrated so that:
      - Iteration 1: N=7, G_max(200) ≈ 2.19 > G_crit(2.054) → 200 flagged
      - Iteration 2: N=6 working set [10..14, 60], G_max(60) ≈ 2.04 >
        G_crit(1.887) → 60 flagged
    """
    out = grubbs_outliers([10.0, 11.0, 12.0, 13.0, 14.0, 60.0, 200.0], alpha=0.05)
    # Indices 5 and 6 are the two outliers
    assert out[6]["is_outlier"] is True, "index 6 (value 200) must be flagged in iteration 1"
    assert out[5]["is_outlier"] is True, "index 5 (value 60) must be flagged in iteration 2"
    # Indices 0-4 are tight cluster, must NOT be flagged
    for i in range(5):
        assert out[i]["is_outlier"] is False


def test_grubbs_alpha_sensitivity():
    """A borderline point: outlier at α=0.10 but not at α=0.01.

    Empirically calibrated: with [10, 11, 12, 13, 14, 24],
    G_max = 14/std ≈ 2.34 / std. Computed against scipy.stats.t critical
    values, this falls between the α=0.10 and α=0.01 thresholds.
    """
    # Borderline outlier value calibrated empirically
    values = [10.0, 11.0, 12.0, 13.0, 14.0, 24.0]
    loose = grubbs_outliers(values, alpha=0.10)
    strict = grubbs_outliers(values, alpha=0.01)
    # At loose alpha (0.10), index 5 should be flagged.
    # At strict alpha (0.01), index 5 should NOT be flagged.
    assert loose[5]["is_outlier"] is True, "Expected detection at α=0.10"
    assert strict[5]["is_outlier"] is False, "Expected no detection at α=0.01"


def test_grubbs_n5_sets_low_power_flag():
    """N<6 → low_power=True for all results, but detection still works."""
    out = grubbs_outliers([10.0, 11.0, 12.0, 13.0, 100.0], alpha=0.05)
    assert all(r["low_power"] is True for r in out)
    assert out[4]["is_outlier"] is True


def test_grubbs_with_nan_or_inf_filters_or_handles_gracefully():
    """NaN/Inf must not crash the function. They must be filtered or
    return a clean result; never raise."""
    # NaN
    out_nan = grubbs_outliers([10.0, 11.0, float("nan"), 13.0, 14.0])
    # Either filtered (entries still in result with is_outlier=False) or all
    # is_outlier=False; never raise an exception.
    assert all("is_outlier" in r for r in out_nan)
    # Inf
    out_inf = grubbs_outliers([10.0, 11.0, float("inf"), 13.0, 14.0])
    assert all("is_outlier" in r for r in out_inf)


def test_grubbs_with_duplicate_values_one_outlier():
    """Many identical values plus one outlier — std is small but non-zero;
    the outlier should still be flagged."""
    out = grubbs_outliers([5.0, 5.0, 5.0, 5.0, 5.0, 100.0], alpha=0.05)
    assert out[5]["is_outlier"] is True
    for i in range(5):
        assert out[i]["is_outlier"] is False


def test_grubbs_two_sided_detects_low_outlier():
    """Two-sided test — a low outlier (below the cluster) is flagged."""
    out = grubbs_outliers([100.0, 101.0, 102.0, 103.0, 104.0, 5.0], alpha=0.05)
    assert out[5]["is_outlier"] is True


def test_grubbs_iteration_terminates_in_bounded_steps():
    """Iteration must not exceed N steps even with adversarial input."""
    import time
    # Worst case: every point is a candidate outlier in succession
    values = [1.0, 2.0, 100.0, 200.0, 300.0, 400.0]
    t0 = time.monotonic()
    out = grubbs_outliers(values, alpha=0.05)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1, f"Iteration took {elapsed:.3f}s — should be <0.1s"
    assert len(out) == len(values)


# ─── Integration tests (Task 9) ──────────────────────────────────────────────

def test_analyze_lims_dataset_dispatches_grubbs():
    """Integration: analyze_lims_dataset(..., outlier_method="grubbs", alpha=0.05)
    routes to grubbs_outliers and the score field carries grubbs_g."""
    try:
        from engines.lims_statistics import analyze_lims_dataset
    except ImportError:
        from backend.engines.lims_statistics import analyze_lims_dataset

    rows = [{"x": v} for v in [10.0, 11.0, 12.0, 13.0, 14.0, 100.0]]
    result = analyze_lims_dataset(
        rows, ["x"], outlier_method="grubbs", alpha=0.05,
    )
    assert "x" in result
    field = result["x"]
    assert field["outlier_count"] == 1, f"Expected 1 outlier, got {field['outlier_count']}"
    outlier = field["outliers"][0]
    assert outlier["row_index"] == 5
    # The score must be the grubbs_g (≈ 2.04), not 0.0 or modified_zscore
    assert outlier["score"] == pytest.approx(2.04, abs=0.01)


def test_analyze_lims_dataset_score_dispatch_handles_zero():
    """Regression test for review I-4: a row whose score is exactly 0.0
    must not fall through the dispatch to a non-zero default. With all
    identical values, modified_zscore=0 for all → outlier_count=0 (not
    a crash from missing keys)."""
    try:
        from engines.lims_statistics import analyze_lims_dataset
    except ImportError:
        from backend.engines.lims_statistics import analyze_lims_dataset

    rows = [{"x": v} for v in [5.0, 5.0, 5.0, 5.0, 5.0]]
    result = analyze_lims_dataset(rows, ["x"], outlier_method="modified_zscore")
    assert result["x"]["outlier_count"] == 0


def test_lims_statistics_route_exposes_alpha_query_param():
    """The /lims/statistics/{code} route must expose `alpha` as a query
    parameter so callers can use method=grubbs with an explicit
    significance level. Inspects the function signature without needing
    a live DB (review I-4 follow-up added during plan review)."""
    import inspect
    try:
        from routes.lims import router as lims_router
    except ImportError:
        from backend.routes.lims import router as lims_router

    # Find the route whose path ends with /lims/statistics/{code}
    target = None
    for route in lims_router.routes:
        path = getattr(route, "path", "")
        if path.endswith("/lims/statistics/{code}"):
            target = route
            break
    assert target is not None, "/lims/statistics/{code} route not registered"

    sig = inspect.signature(target.endpoint)
    assert "alpha" in sig.parameters, (
        f"Expected 'alpha' query parameter on /lims/statistics/{{code}}; "
        f"found {list(sig.parameters)}"
    )
