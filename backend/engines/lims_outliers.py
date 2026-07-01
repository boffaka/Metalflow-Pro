"""
MPDPMS — LIMS Outlier Detection Engine.

Implements statistical outlier detection methods for QA/QC:
- Grubbs' test (single outlier, assumes normality)
- Modified Z-score (MAD-based, robust to non-normality)

All functions are pure (no DB, no I/O) — testable in isolation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class OutlierResult:
    """Result of an outlier test on a single value."""
    index: int
    value: float
    method: Literal["grubbs", "modified_zscore"]
    statistic: float
    threshold: float
    is_outlier: bool
    p_value: float | None = None


def grubbs_test(
    values: list[float],
    alpha: float = 0.05,
) -> list[OutlierResult]:
    """
    Grubbs' test for a single outlier in a univariate dataset.

    Identifies the value with the largest absolute deviation from the mean
    and tests whether it is statistically significant as an outlier.

    Parameters
    ----------
    values : list of float
        The dataset to test. Must have at least 3 values.
    alpha : float
        Significance level (default 0.05).

    Returns
    -------
    list of OutlierResult
        One result per detected outlier (iterative removal until none found).
    """
    if len(values) < 3:
        return []

    results: list[OutlierResult] = []
    remaining = list(enumerate(values))  # (original_index, value)

    while len(remaining) >= 3:
        vals = np.array([v for _, v in remaining])
        n = len(vals)
        mean = np.mean(vals)
        std = np.std(vals, ddof=1)

        if std < 1e-12:
            break  # All values identical

        # Find the value with max |deviation|
        deviations = np.abs(vals - mean)
        max_idx = int(np.argmax(deviations))
        g_stat = deviations[max_idx] / std

        # Critical value from t-distribution
        t_crit = stats.t.ppf(1 - alpha / (2 * n), n - 2)
        g_crit = ((n - 1) / math.sqrt(n)) * math.sqrt(t_crit**2 / (n - 2 + t_crit**2))

        # Two-sided p-value approximation
        # P(G > g) ≈ n * P(|t| > t_g) where t_g = sqrt(n*(n-2)*g^2 / (n-1)^2 - g^2)
        if g_stat > 0 and (n - 1)**2 > n * g_stat**2:
            t_g = math.sqrt(n * (n - 2) * g_stat**2 / ((n - 1)**2 - n * g_stat**2))
            p_val = float(2 * n * stats.t.sf(t_g, n - 2))
            p_val = min(p_val, 1.0)
        else:
            p_val = 0.0

        is_outlier = g_stat > g_crit

        if is_outlier:
            orig_idx, orig_val = remaining[max_idx]
            results.append(OutlierResult(
                index=orig_idx,
                value=orig_val,
                method="grubbs",
                statistic=float(g_stat),
                threshold=float(g_crit),
                is_outlier=True,
                p_value=p_val,
            ))
            remaining.pop(max_idx)
        else:
            break

    return results


def modified_zscore_test(
    values: list[float],
    threshold: float = 3.5,
) -> list[OutlierResult]:
    """
    Modified Z-score test using Median Absolute Deviation (MAD).

    More robust than standard Z-score for non-normal distributions,
    which is common in geological/metallurgical data.

    Parameters
    ----------
    values : list of float
        The dataset to test. Must have at least 3 values.
    threshold : float
        Modified Z-score threshold (default 3.5, per Iglewicz & Hoaglin).

    Returns
    -------
    list of OutlierResult
        One result per value exceeding the threshold.
    """
    if len(values) < 3:
        return []

    arr = np.array(values, dtype=float)
    median = np.median(arr)
    mad = np.median(np.abs(arr - median))
    center = median
    const = 0.6745  # 0.75-quantile of N(0,1): MAD ≈ 0.6745·σ

    if mad < 1e-12:
        # MAD is zero — fall back to the mean absolute deviation about the MEAN.
        # MeanAD ≈ 0.7979·σ, so use the 0.7979 consistency constant, not the MAD
        # 0.6745 (which would deflate the scores ~15% and under-flag outliers).
        center = float(np.mean(arr))
        mad = np.mean(np.abs(arr - center))
        const = 0.7979
        if mad < 1e-12:
            return []  # All values identical

    modified_z = const * (arr - center) / mad

    results: list[OutlierResult] = []
    for i, (val, mz) in enumerate(zip(values, modified_z)):
        abs_mz = abs(float(mz))
        if abs_mz > threshold:
            results.append(OutlierResult(
                index=i,
                value=val,
                method="modified_zscore",
                statistic=abs_mz,
                threshold=threshold,
                is_outlier=True,
                p_value=None,
            ))

    return results


def detect_outliers(
    values: list[float],
    method: Literal["grubbs", "modified_zscore", "both"] = "both",
    alpha: float = 0.05,
    zscore_threshold: float = 3.5,
) -> list[OutlierResult]:
    """
    Detect outliers using one or both methods.

    When method="both", a value is flagged only if BOTH methods agree
    (conservative approach suitable for regulatory contexts).

    Parameters
    ----------
    values : list of float
        The dataset to test.
    method : str
        "grubbs", "modified_zscore", or "both" (intersection).
    alpha : float
        Significance level for Grubbs' test.
    zscore_threshold : float
        Threshold for modified Z-score.

    Returns
    -------
    list of OutlierResult
        Detected outliers (from Grubbs if method="both" and confirmed by MAD).
    """
    if len(values) < 3:
        return []

    if method == "grubbs":
        return grubbs_test(values, alpha)
    elif method == "modified_zscore":
        return modified_zscore_test(values, zscore_threshold)
    else:
        # Both — intersection (conservative)
        grubbs_results = grubbs_test(values, alpha)
        mad_results = modified_zscore_test(values, zscore_threshold)

        grubbs_indices = {r.index for r in grubbs_results}
        mad_indices = {r.index for r in mad_results}
        confirmed = grubbs_indices & mad_indices

        # Return Grubbs results for confirmed outliers (has p-value)
        return [r for r in grubbs_results if r.index in confirmed]
