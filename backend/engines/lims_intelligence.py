"""
LIMS Intelligence Engine — Anomaly detection for metallurgical test data.

Detects:
  - Statistical outliers (>2.5 sigma, log-transformed for grades)
  - Cross-test inconsistencies (e.g., high recovery + high sulfur)
"""
from __future__ import annotations

import math
import logging

logger = logging.getLogger("mpdpms.lims_intelligence")

_LOG_FIELDS = {"au_g_t", "ag_g_t", "cu_pct", "as_ppm"}
_SIGMA_THRESHOLD = 2.5


def detect_outliers(
    test_type: str,
    samples: list[dict],
    field: str,
    sigma: float = _SIGMA_THRESHOLD,
) -> list[dict]:
    try:
        values = []
        for s in samples:
            v = s.get(field)
            if v is not None:
                try:
                    values.append((float(v), s))
                except (TypeError, ValueError):
                    continue

        if len(values) < 5:
            return []

        use_log = field in _LOG_FIELDS
        nums = []
        for v, _ in values:
            if use_log and v > 0:
                nums.append(math.log(v))
            elif not use_log:
                nums.append(v)

        if len(nums) < 5:
            return []

        mean = sum(nums) / len(nums)
        # Sample variance (n-1): with the small n typical of LIMS testwork the
        # population divisor (n) under-estimates σ and inflates every z-score.
        variance = sum((x - mean) ** 2 for x in nums) / (len(nums) - 1)
        std = math.sqrt(variance) if variance > 0 else 0

        if std == 0:
            return []

        alerts = []
        for raw_val, sample in values:
            # For log-scaled fields a non-positive value cannot be log-transformed
            # and is not part of the log-space mean/std; skip it rather than
            # comparing a linear value against a log-space distribution (bogus z).
            if use_log and raw_val <= 0:
                continue
            transformed = math.log(raw_val) if use_log else raw_val
            z_score = abs(transformed - mean) / std
            if z_score > sigma:
                alerts.append({
                    "alert_type": "outlier",
                    "severity": "critical" if z_score > 4.0 else "warning",
                    "test_type": test_type,
                    "field": field,
                    "value": raw_val,
                    "z_score": round(z_score, 2),
                    "message": (
                        f"{field}: {raw_val} est a {z_score:.1f} sigma de la moyenne "
                        f"(moyenne geometrique={math.exp(mean):.2f}, GSD=x{math.exp(std):.2f})"
                        if use_log else
                        f"{field}: {raw_val} est a {z_score:.1f} sigma de la moyenne "
                        f"(mean={mean:.2f}, std={std:.2f})"
                    ),
                })
        return alerts
    except Exception as e:
        logger.error("detect_outliers failed (test_type=%s, field=%s): %s", test_type, field, e)
        return []


def detect_cross_test_issues(
    a1_data: list[dict],
    *,
    d1_data: list[dict] | None = None,
    c2_data: list[dict] | None = None,
    b1_data: list[dict] | None = None,
    e1_data: list[dict] | None = None,
) -> list[dict]:
    try:
        alerts = []

        def _avg(rows, field):
            vals = [float(r[field]) for r in rows if r.get(field) is not None]
            return sum(vals) / len(vals) if vals else None

        avg_s = _avg(a1_data, "s_total_pct")

        if d1_data and avg_s is not None:
            avg_rec = _avg(d1_data, "au_recovery_pct")
            if avg_rec is not None and avg_rec > 95 and avg_s > 5:
                alerts.append({
                    "alert_type": "cross_test",
                    "severity": "warning",
                    "test_type": "d1+a1",
                    "message": (
                        f"Recovery elevee ({avg_rec:.1f}%) suspecte avec sulfure eleve "
                        f"({avg_s:.1f}%) — verifier mineralogie refractaire"
                    ),
                })

        if c2_data and avg_s is not None:
            avg_grg = _avg(c2_data, "au_recovery_pct")
            if avg_grg is not None and avg_grg > 80 and avg_s > 5:
                alerts.append({
                    "alert_type": "cross_test",
                    "severity": "warning",
                    "test_type": "c2+a1",
                    "message": (
                        f"GRG eleve ({avg_grg:.1f}%) incoherent avec sulfure "
                        f"({avg_s:.1f}%) — verifier nugget effect vs refractaire"
                    ),
                })

        if b1_data:
            avg_bwi = _avg(b1_data, "bwi_kwh_t")
            avg_au = _avg(a1_data, "au_g_t")
            if avg_bwi is not None and avg_au is not None:
                if avg_bwi > 20 and avg_au < 0.5:
                    alerts.append({
                        "alert_type": "cross_test",
                        "severity": "info",
                        "test_type": "b1+a1",
                        "message": (
                            f"Minerai dur (BWi={avg_bwi:.1f} kWh/t) a faible teneur "
                            f"({avg_au:.2f} g/t) — verifier viabilite economique"
                        ),
                    })

        if e1_data and b1_data:
            avg_p80 = _avg(b1_data, "p80_target_um")
            avg_ua = _avg(e1_data, "unit_area_m2_t_d")
            if avg_p80 is not None and avg_ua is not None:
                if avg_p80 < 53 and avg_ua > 0.15:
                    alerts.append({
                        "alert_type": "cross_test",
                        "severity": "warning",
                        "test_type": "b1+e1",
                        "message": (
                            f"Grind fin (P80={avg_p80:.0f} um) genere fines problematiques "
                            f"pour epaississement (UA={avg_ua:.3f} m2·t/d)"
                        ),
                    })
        return alerts
    except Exception as e:
        logger.error("detect_cross_test_issues failed: %s", e)
        return []
