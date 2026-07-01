# backend/engines/lims_statistics.py
"""
LIMS Statistical Analysis Engine.

Provides outlier detection, variability analysis, and data quality
assessment for metallurgical testwork data.

Methods implemented:
  - Z-score outlier detection
  - Grubbs test (1969) — significance-level-aware iterative outlier detection
  - Modified Z-score (Iglewicz & Hoaglin 1993) — robust to non-normal data
  - IQR fence method (Tukey 1977)
  - Coefficient of Variation (CV) analysis
  - Correlation analysis between LIMS parameters

References:
  - Grubbs (1969) "Procedures for Detecting Outlying Observations in Samples"
  - Iglewicz & Hoaglin (1993) "How to Detect and Handle Outliers"
  - Tukey (1977) "Exploratory Data Analysis"
  - QAQC standards: Abzalov (2008) "Quality Control of Assay Data"
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any

logger = logging.getLogger(__name__)


def _percentile_interp(sorted_vals: list[float], p: float) -> float:
    """Linear-interpolation percentile (p in 0-100) on an already-sorted list.

    Matches the method used by descriptive_stats so quartiles/fences are
    consistent across the module.
    """
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    idx = (p / 100.0) * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


# ─── Z-Score Outlier Detection ───────────────────────────────────────────────

def zscore_outliers(
    values: list[float],
    threshold: float = 3.0,
) -> list[dict]:
    """
    Detect outliers using Z-score method.

    Z = (x - mean) / std

    Args:
        values: List of numeric values
        threshold: Z-score threshold (default 3.0 = 99.7% confidence)
    Returns:
        List of {index, value, zscore, is_outlier}
    """
    if len(values) < 3:
        return [{"index": i, "value": v, "zscore": 0.0, "is_outlier": False}
                for i, v in enumerate(values)]

    try:
        mean = statistics.mean(values)
        std = statistics.stdev(values)
        if std == 0:
            return [{"index": i, "value": v, "zscore": 0.0, "is_outlier": False}
                    for i, v in enumerate(values)]

        results = []
        for i, v in enumerate(values):
            z = abs((v - mean) / std)
            results.append({
                "index": i,
                "value": v,
                "zscore": round(z, 3),
                "is_outlier": z > threshold,
            })
        return results
    except Exception as e:
        logger.error("zscore_outliers failed: %s", e)
        return []


def modified_zscore_outliers(
    values: list[float],
    threshold: float = 3.5,
) -> list[dict]:
    """
    Detect outliers using Modified Z-score (Iglewicz & Hoaglin 1993).

    More robust than standard Z-score for non-normal distributions.
    M_i = 0.6745 × (x_i - median) / MAD

    Args:
        values: List of numeric values
        threshold: Modified Z-score threshold (default 3.5)
    Returns:
        List of {index, value, modified_zscore, is_outlier}
    """
    if len(values) < 3:
        return [{"index": i, "value": v, "modified_zscore": 0.0, "is_outlier": False}
                for i, v in enumerate(values)]

    try:
        median = statistics.median(values)
        deviations = [abs(v - median) for v in values]
        mad = statistics.median(deviations)
        center = median
        const = 0.6745  # MAD ≈ 0.6745·σ (0.75-quantile of N(0,1))

        if mad == 0:
            # Fall back to the mean absolute deviation about the MEAN. MeanAD ≈
            # 0.7979·σ, so the consistency constant is 0.7979, not the MAD 0.6745
            # (using 0.6745 here would deflate the scores ~15% and under-flag).
            mean = statistics.mean(values)
            mad = statistics.mean([abs(v - mean) for v in values])
            center = mean
            const = 0.7979
            if mad == 0:
                return [{"index": i, "value": v, "modified_zscore": 0.0, "is_outlier": False}
                        for i, v in enumerate(values)]

        results = []
        for i, v in enumerate(values):
            mz = abs(const * (v - center) / mad)
            results.append({
                "index": i,
                "value": v,
                "modified_zscore": round(mz, 3),
                "is_outlier": mz > threshold,
            })
        return results
    except Exception as e:
        logger.error("modified_zscore_outliers failed: %s", e)
        return []


def iqr_outliers(
    values: list[float],
    k: float = 1.5,
) -> list[dict]:
    """
    Detect outliers using Tukey IQR fence method.

    Lower fence = Q1 - k × IQR
    Upper fence = Q3 + k × IQR

    Args:
        values: List of numeric values
        k: IQR multiplier (1.5 = mild outliers, 3.0 = extreme outliers)
    Returns:
        List of {index, value, is_outlier, fence_low, fence_high}
    """
    if len(values) < 4:
        return [{"index": i, "value": v, "is_outlier": False,
                 "fence_low": None, "fence_high": None}
                for i, v in enumerate(values)]

    try:
        sorted_vals = sorted(values)
        # Linear-interpolation quartiles (same method as descriptive_stats.percentile),
        # instead of nearest-rank sorted_vals[n//4] which mislocates Q1/Q3 and yields
        # fences inconsistent with the reported percentiles.
        q1 = _percentile_interp(sorted_vals, 25.0)
        q3 = _percentile_interp(sorted_vals, 75.0)
        iqr = q3 - q1
        fence_low = q1 - k * iqr
        fence_high = q3 + k * iqr

        return [
            {
                "index": i,
                "value": v,
                "is_outlier": v < fence_low or v > fence_high,
                "fence_low": round(fence_low, 4),
                "fence_high": round(fence_high, 4),
            }
            for i, v in enumerate(values)
        ]
    except Exception as e:
        logger.error("iqr_outliers failed: %s", e)
        return []


# ─── Grubbs Test (1969) — Significance-Level-Aware Outlier Detection ─────────

def grubbs_outliers(
    values: list[float],
    alpha: float = 0.05,
) -> list[dict]:
    """
    Detect outliers using the iterative two-sided Grubbs test.

    Each iteration computes G = max|x - mean| / std and compares it to a
    critical value derived from the Student's t distribution at significance
    level alpha. The test is **two-sided**: the critical t uses upper-tail
    α/(2N) — the factor 2 is two-sidedness, the N is the Bonferroni
    correction for "max-of-N" candidate selection. The detected outlier is
    removed and the test is re-applied until no further outlier is
    significant or N < 3.

    `statistics.stdev` (Bessel-corrected, denominator n-1) is used,
    matching Grubbs (1969).

    For N < 6 the test is statistically weak (β can exceed 0.5 for
    moderate effect sizes). Each result carries `low_power: True` in that
    case so QAQC consumers can disclose the limitation in their reports.

    For non-outlier points, `critical_value` is the threshold of the final
    iteration of the test (i.e., the strictest threshold actually used in
    a comparison that did not flag).

    Worked example for hand-verification (verified against scipy.stats.t):
        values=[10,11,12,13,14,100], alpha=0.05, N=6
        → mean=26.667, std=35.954 (Bessel-corrected, n-1)
        → G_max = |100-26.667|/35.954 = 2.040
        → t_crit = t.ppf(1 - 0.05/12, 4) = 4.851
        → G_critical = (5/√6) × √(t²/(4+t²)) = 1.887
        → 2.040 > 1.887, index 5 flagged as outlier
        → iteration 2 on N=5 finds no further outlier

    Args:
        values: List of numeric values. NaN/Inf are filtered with a warning.
        alpha: Significance level in the open interval (0, 1) (default 0.05 =
            95% confidence). Values outside (0, 1) raise ValueError.
    Returns:
        List of {index, value, grubbs_g, critical_value, is_outlier, low_power}

    Raises:
        ValueError: If alpha is not in the open interval (0, 1).

    Reference:
        Grubbs (1969) "Procedures for Detecting Outlying Observations in Samples"
        NIST/SEMATECH e-Handbook of Statistical Methods §1.3.5.17
    """
    # Validate alpha
    if not (0 < alpha < 1):
        raise ValueError(f"alpha must be in the open interval (0, 1), got {alpha}")

    # Filter NaN/Inf early; results for filtered indices keep default flags
    # (is_outlier=False, grubbs_g=0.0, critical_value=None). The original
    # values are preserved in the results so callers see what was passed in.
    clean_pairs: list[tuple[int, float]] = [
        (i, v) for i, v in enumerate(values) if math.isfinite(v)
    ]
    if len(clean_pairs) != len(values):
        logger.warning(
            "grubbs_outliers: filtered %d non-finite values from input of size %d",
            len(values) - len(clean_pairs), len(values),
        )

    # Initialize results for ALL original indices (NaN/Inf get default flags)
    low_power_global = len(clean_pairs) < 6
    results = [
        {
            "index": i,
            "value": values[i],
            "grubbs_g": 0.0,
            "critical_value": None,
            "is_outlier": False,
            "low_power": low_power_global,
        }
        for i in range(len(values))
    ]

    # N<3 (after cleaning): cannot run Grubbs (degrees of freedom <1)
    if len(clean_pairs) < 3:
        return results

    # std=0 (all identical, after cleaning): no outliers possible
    clean_values = [v for _, v in clean_pairs]
    try:
        std = statistics.stdev(clean_values)
    except statistics.StatisticsError:
        return results
    if std == 0:
        return results

    # Iterative two-sided Grubbs test
    from scipy import stats as _scipy_stats

    # Working set uses the cleaned pairs (original indices preserved)
    working_set: list[tuple[int, float]] = list(clean_pairs)
    g_critical_last: float | None = None
    g_values_last: dict[int, float] = {}

    # Bounded by len(values) iterations (worst case: every point is an outlier)
    for _ in range(len(values)):
        if len(working_set) < 3:
            break
        ws_values = [v for _, v in working_set]
        try:
            ws_std = statistics.stdev(ws_values)
        except statistics.StatisticsError:
            break
        if ws_std == 0:
            break

        n = len(working_set)
        mean = statistics.mean(ws_values)
        g_values = {
            orig_i: abs(v - mean) / ws_std
            for orig_i, v in working_set
        }
        idx_max, g_max = max(g_values.items(), key=lambda kv: kv[1])

        t_crit = _scipy_stats.t.ppf(1 - alpha / (2 * n), n - 2)
        g_critical = ((n - 1) / math.sqrt(n)) * math.sqrt(
            (t_crit ** 2) / (n - 2 + t_crit ** 2)
        )

        # Save the latest g_values + threshold for non-flagged points
        g_values_last = g_values
        g_critical_last = g_critical

        if g_max > g_critical:
            # Flag the outlier and remove from the working set
            results[idx_max]["is_outlier"] = True
            results[idx_max]["grubbs_g"] = round(g_max, 4)
            results[idx_max]["critical_value"] = round(g_critical, 4)
            working_set = [(i, v) for (i, v) in working_set if i != idx_max]
        else:
            # No more outliers — break (the non-flagged g_values + threshold
            # are already in g_values_last / g_critical_last)
            break

    # Populate non-flagged results with their last-seen G + critical value
    if g_critical_last is not None:
        for orig_i, g in g_values_last.items():
            if not results[orig_i]["is_outlier"]:
                results[orig_i]["grubbs_g"] = round(g, 4)
                results[orig_i]["critical_value"] = round(g_critical_last, 4)

    return results


# ─── Coefficient of Variation ─────────────────────────────────────────────────

def coefficient_of_variation(values: list[float]) -> float:
    """
    Coefficient of Variation (CV = std/mean × 100%).

    Interpretation for metallurgical data:
      CV < 15%  : Low variability — representative dataset
      CV 15–30% : Moderate variability — acceptable for PFS
      CV 30–50% : High variability — additional sampling recommended
      CV > 50%  : Very high variability — domain segregation required

    Args:
        values: List of numeric values (must be > 0)
    Returns:
        CV as percentage (%)
    """
    if len(values) < 2:
        return 0.0
    try:
        mean = statistics.mean(values)
        if mean == 0:
            return 0.0
        std = statistics.stdev(values)
        return (std / mean) * 100.0
    except Exception as e:
        logger.error("coefficient_of_variation failed: %s", e)
        return 0.0


def classify_variability(cv_pct: float) -> str:
    """Classify variability from CV percentage."""
    if cv_pct < 15:
        return "Low"
    elif cv_pct < 30:
        return "Moderate"
    elif cv_pct < 50:
        return "High"
    else:
        return "Very High"


# ─── Descriptive Statistics ───────────────────────────────────────────────────

def descriptive_stats(values: list[float]) -> dict:
    """
    Compute full descriptive statistics for a dataset.

    Args:
        values: List of numeric values
    Returns:
        dict with count, mean, median, std, cv, min, max, p10, p25, p75, p90
    """
    if not values:
        return {"count": 0}

    try:
        clean = [v for v in values if v is not None and not math.isnan(v)]
        if not clean:
            return {"count": 0}

        n = len(clean)
        sorted_vals = sorted(clean)
        mean = statistics.mean(clean)
        median = statistics.median(clean)
        std = statistics.stdev(clean) if n > 1 else 0.0
        cv = (std / mean * 100.0) if mean != 0 else 0.0

        def percentile(p: float) -> float:
            idx = (p / 100.0) * (n - 1)
            lo = int(idx)
            hi = min(lo + 1, n - 1)
            frac = idx - lo
            return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

        return {
            "count": n,
            "mean": round(mean, 4),
            "median": round(median, 4),
            "std": round(std, 4),
            "cv_pct": round(cv, 2),
            "variability_class": classify_variability(cv),
            "min": round(sorted_vals[0], 4),
            "max": round(sorted_vals[-1], 4),
            "p10": round(percentile(10), 4),
            "p25": round(percentile(25), 4),
            "p75": round(percentile(75), 4),
            "p90": round(percentile(90), 4),
            "range": round(sorted_vals[-1] - sorted_vals[0], 4),
        }
    except Exception as e:
        logger.error("descriptive_stats failed: %s", e)
        return {"count": len(values), "error": str(e)}


# ─── LIMS Dataset Analysis ────────────────────────────────────────────────────

def analyze_lims_dataset(
    rows: list[dict[str, Any]],
    fields: list[str],
    outlier_method: str = "modified_zscore",
    zscore_threshold: float = 3.5,
    alpha: float | None = None,
) -> dict:
    """
    Analyze a LIMS dataset for outliers and variability.

    Args:
        rows: List of LIMS data rows (dicts)
        fields: List of field names to analyze
        outlier_method: "zscore", "modified_zscore", "iqr", or "grubbs"
        zscore_threshold: Threshold for zscore/modified_zscore detection
        alpha: Significance level for Grubbs (default 0.05 when
            method="grubbs", ignored otherwise)
    Returns:
        dict with per-field statistics and outlier flags
    """
    results: dict[str, Any] = {}

    for field in fields:
        values_with_idx = [
            (i, float(row[field]))
            for i, row in enumerate(rows)
            if row.get(field) is not None
        ]

        if not values_with_idx:
            results[field] = {"count": 0, "outliers": []}
            continue

        indices, values = zip(*values_with_idx)

        # Descriptive stats
        stats = descriptive_stats(list(values))

        # Outlier detection
        if outlier_method == "zscore":
            outlier_results = zscore_outliers(list(values), threshold=zscore_threshold)
        elif outlier_method == "iqr":
            outlier_results = iqr_outliers(list(values))
        elif outlier_method == "grubbs":
            outlier_results = grubbs_outliers(
                list(values),
                alpha=alpha if alpha is not None else 0.05,
            )
        else:  # default: modified_zscore
            outlier_results = modified_zscore_outliers(list(values), threshold=zscore_threshold)

        # Map back to original row indices.
        # Score field uses key-presence dispatch (review I-4) — replaces an
        # `or`-chain that short-circuited on score=0.0. Precedence: Grubbs
        # (most informative) > modified_zscore > zscore > 0.0.
        def _score_for(r: dict) -> float:
            if "grubbs_g" in r:
                return r["grubbs_g"]
            if "modified_zscore" in r:
                return r["modified_zscore"]
            if "zscore" in r:
                return r["zscore"]
            return 0.0

        outliers = [
            {
                "row_index": indices[r["index"]],
                "value": r["value"],
                "score": _score_for(r),
            }
            for r in outlier_results
            if r.get("is_outlier")
        ]

        results[field] = {
            **stats,
            "outlier_count": len(outliers),
            "outlier_pct": round(len(outliers) / len(values) * 100, 1) if values else 0,
            "outliers": outliers[:20],  # cap at 20 for response size
        }

    return results


# ─── QAQC Duplicate Analysis ─────────────────────────────────────────────────

def analyze_duplicates(
    originals: list[float],
    duplicates: list[float],
) -> dict:
    """
    Analyze field duplicate precision (QAQC).

    Computes:
      - HARD (Half Absolute Relative Difference) = |orig - dup| / ((orig + dup) / 2) × 100
      - Acceptable if HARD < 10% for Au assays (industry standard)

    Args:
        originals: Original assay values
        duplicates: Duplicate assay values
    Returns:
        dict with HARD statistics and pass/fail assessment
    """
    if len(originals) != len(duplicates) or not originals:
        return {"error": "Mismatched or empty arrays"}

    try:
        hard_values = []
        for orig, dup in zip(originals, duplicates):
            if orig is None or dup is None:
                continue
            avg = (orig + dup) / 2.0
            if avg > 0:
                hard = abs(orig - dup) / avg * 100.0
                hard_values.append(hard)

        if not hard_values:
            return {"count": 0}

        stats = descriptive_stats(hard_values)
        # Industry standard: HARD < 10% for Au assays
        pass_count = sum(1 for h in hard_values if h < 10.0)
        fail_count = len(hard_values) - pass_count

        return {
            **stats,
            "hard_p90": stats.get("p90"),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "pass_rate_pct": round(pass_count / len(hard_values) * 100, 1),
            "assessment": "PASS" if stats.get("p90", 100) < 10.0 else "FAIL",
            "threshold_pct": 10.0,
        }
    except Exception as e:
        logger.error("analyze_duplicates failed: %s", e)
        return {"error": str(e)}


# ─── CRM (Certified Reference Material) Analysis ─────────────────────────────

def analyze_crm(
    measured_values: list[float],
    certified_value: float,
    tolerance_pct: float = 10.0,
) -> dict:
    """
    Analyze CRM (Certified Reference Material) performance.

    Checks if measured values are within tolerance of the certified value.
    Industry standard: ±10% for Au assays (±5% for high-grade).

    Args:
        measured_values: List of measured CRM values
        certified_value: Certified reference value
        tolerance_pct: Acceptable tolerance (%)
    Returns:
        dict with bias, precision, and pass/fail assessment
    """
    if not measured_values or certified_value <= 0:
        return {"error": "Invalid inputs"}

    try:
        stats = descriptive_stats(measured_values)
        mean = stats.get("mean", 0)
        bias_pct = (mean - certified_value) / certified_value * 100.0

        # Count values within tolerance
        in_tolerance = sum(
            1 for v in measured_values
            if abs(v - certified_value) / certified_value * 100.0 <= tolerance_pct
        )

        return {
            **stats,
            "certified_value": certified_value,
            "bias_pct": round(bias_pct, 2),
            "bias_assessment": (
                "Acceptable" if abs(bias_pct) <= tolerance_pct else "Unacceptable"
            ),
            "in_tolerance_count": in_tolerance,
            "in_tolerance_pct": round(in_tolerance / len(measured_values) * 100, 1),
            "tolerance_pct": tolerance_pct,
            "assessment": "PASS" if abs(bias_pct) <= tolerance_pct else "FAIL",
        }
    except Exception as e:
        logger.error("analyze_crm failed: %s", e)
        return {"error": str(e)}
