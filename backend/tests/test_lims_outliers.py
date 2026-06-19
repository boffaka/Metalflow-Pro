"""Tests for the LIMS outlier detection engine (pure functions, no DB)."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engines.lims_outliers import (
    grubbs_test,
    modified_zscore_test,
    detect_outliers,
    OutlierResult,
)

pytestmark = pytest.mark.no_db


class TestGrubbsTest:
    """Grubbs' test for single outlier detection."""

    def test_no_outlier_in_normal_data(self):
        """Normally distributed data should have no outliers."""
        values = [10.1, 10.2, 9.9, 10.0, 10.3, 9.8, 10.1, 10.0, 9.9, 10.2]
        results = grubbs_test(values, alpha=0.05)
        assert len(results) == 0

    def test_detects_obvious_outlier(self):
        """A value far from the mean should be detected."""
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 10.0, 10.1, 9.9, 50.0]  # 50 is outlier
        results = grubbs_test(values, alpha=0.05)
        assert len(results) >= 1
        assert results[0].value == 50.0
        assert results[0].is_outlier is True
        assert results[0].method == "grubbs"

    def test_too_few_values(self):
        """Less than 3 values should return empty."""
        assert grubbs_test([1.0, 2.0]) == []
        assert grubbs_test([1.0]) == []
        assert grubbs_test([]) == []

    def test_identical_values(self):
        """All identical values should return empty (std=0)."""
        values = [5.0, 5.0, 5.0, 5.0, 5.0]
        results = grubbs_test(values)
        assert len(results) == 0

    def test_multiple_outliers_iterative(self):
        """Should detect multiple outliers iteratively."""
        # Need more data points for Grubbs to have statistical power
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 10.0, 10.1, 9.9, 10.2, 9.8, 100.0, -50.0]
        results = grubbs_test(values, alpha=0.05)
        assert len(results) >= 2
        outlier_values = {r.value for r in results}
        assert 100.0 in outlier_values
        assert -50.0 in outlier_values

    def test_p_value_is_small_for_outlier(self):
        """P-value should be small for a clear outlier."""
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 10.0, 10.1, 9.9, 50.0]
        results = grubbs_test(values, alpha=0.05)
        assert results[0].p_value is not None
        assert results[0].p_value < 0.05


class TestModifiedZscore:
    """Modified Z-score (MAD-based) outlier detection."""

    def test_no_outlier_in_tight_data(self):
        """Tight data should have no outliers."""
        values = [10.0, 10.1, 9.9, 10.0, 10.2, 9.8, 10.1, 10.0]
        results = modified_zscore_test(values, threshold=3.5)
        assert len(results) == 0

    def test_detects_obvious_outlier(self):
        """A value far from the median should be detected."""
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 10.0, 10.1, 9.9, 50.0]
        results = modified_zscore_test(values, threshold=3.5)
        assert len(results) >= 1
        assert any(r.value == 50.0 for r in results)

    def test_too_few_values(self):
        """Less than 3 values should return empty."""
        assert modified_zscore_test([1.0, 2.0]) == []

    def test_identical_values(self):
        """All identical values should return empty."""
        values = [5.0, 5.0, 5.0, 5.0, 5.0]
        results = modified_zscore_test(values)
        assert len(results) == 0

    def test_robust_to_multiple_outliers(self):
        """MAD is robust — multiple outliers don't mask each other."""
        # With mean-based methods, masking can occur
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 100.0, 95.0, 105.0]
        results = modified_zscore_test(values, threshold=3.5)
        outlier_values = {r.value for r in results}
        assert 100.0 in outlier_values
        assert 95.0 in outlier_values
        assert 105.0 in outlier_values


class TestDetectOutliers:
    """Combined outlier detection."""

    def test_method_grubbs(self):
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 50.0]
        results = detect_outliers(values, method="grubbs")
        assert len(results) >= 1
        assert all(r.method == "grubbs" for r in results)

    def test_method_modified_zscore(self):
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 50.0]
        results = detect_outliers(values, method="modified_zscore")
        assert len(results) >= 1
        assert all(r.method == "modified_zscore" for r in results)

    def test_method_both_conservative(self):
        """'both' mode should only flag values detected by BOTH methods."""
        values = [10.0, 10.1, 9.9, 10.2, 9.8, 50.0]
        results_both = detect_outliers(values, method="both")
        results_grubbs = detect_outliers(values, method="grubbs")
        results_mad = detect_outliers(values, method="modified_zscore")

        # 'both' should be a subset of each individual method
        both_indices = {r.index for r in results_both}
        grubbs_indices = {r.index for r in results_grubbs}
        mad_indices = {r.index for r in results_mad}

        assert both_indices <= grubbs_indices
        assert both_indices <= mad_indices

    def test_empty_input(self):
        assert detect_outliers([]) == []
        assert detect_outliers([1.0, 2.0]) == []

    def test_realistic_bwi_data(self):
        """Realistic Bond Work Index data with one anomalous sample."""
        # Typical BWi values for gold ore: 12-18 kWh/t
        bwi_values = [14.2, 15.1, 13.8, 14.5, 15.3, 14.0, 14.8, 13.9, 14.6, 15.0,
                      14.3, 14.7, 13.5, 14.9, 14.1, 25.0]  # 25.0 is anomalous
        results = detect_outliers(bwi_values, method="both")
        assert len(results) >= 1
        assert any(r.value == 25.0 for r in results)

    def test_realistic_recovery_data(self):
        """Realistic leach recovery data — all within normal range."""
        # Typical CIL recovery: 85-95%
        recovery_values = [89.2, 91.5, 88.7, 90.1, 92.3, 87.8, 91.0, 89.5, 90.8, 88.9]
        results = detect_outliers(recovery_values, method="both")
        assert len(results) == 0  # No outliers in normal data
