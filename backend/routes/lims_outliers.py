"""
MPDPMS — LIMS Outlier Detection API.

Provides endpoints to detect statistical outliers in LIMS test data
using Grubbs' test and Modified Z-score (MAD-based).
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

try:
    from ..auth import project_user
    from ..db import qall
    from ..engines.lims_outliers import detect_outliers
except ImportError:  # pragma: no cover
    from auth import project_user
    from db import qall
    from engines.lims_outliers import detect_outliers

logger = logging.getLogger("mpdpms.lims_outliers")

router = APIRouter(prefix="/api/v1/projects", tags=["lims-outliers"])


# ─── Supported test types and their numeric fields ────────────────────────────

_TEST_FIELDS: dict[str, dict[str, str]] = {
    "a1": {
        "table": "lims_a1",
        "fields": {
            "au_ppm": "Au (ppm)",
            "ag_ppm": "Ag (ppm)",
            "fe_pct": "Fe (%)",
            "s_total_pct": "S total (%)",
            "as_ppm": "As (ppm)",
            "cu_ppm": "Cu (ppm)",
            "sg": "SG",
        },
    },
    "b1": {
        "table": "lims_b1",
        "fields": {
            "bwi_kwh_t": "BWi (kWh/t)",
            "ai_g": "Ai (g)",
            "sg": "SG",
            "p80_um": "P80 (µm)",
        },
    },
    "d1": {
        "table": "lims_d1",
        "fields": {
            "recovery_pct": "Recovery (%)",
            "nacn_kg_t": "NaCN (kg/t)",
            "residue_au_ppm": "Residue Au (ppm)",
            "feed_grade_ppm": "Feed grade (ppm)",
        },
    },
}


class OutlierResponse(BaseModel):
    test_type: str
    field: str
    field_label: str
    method: str
    sample_count: int
    outliers: list[dict]


@router.get("/{pid}/lims/outliers")
def detect_lims_outliers(
    pid: str,
    test_type: str = Query(..., description="Test type code (a1, b1, d1)"),
    field: str = Query(..., description="Numeric field to analyze"),
    method: Literal["grubbs", "modified_zscore", "both"] = Query("both"),
    alpha: float = Query(0.05, ge=0.001, le=0.2),
    zscore_threshold: float = Query(3.5, ge=2.0, le=5.0),
    user=Depends(project_user),
) -> OutlierResponse:
    """
    Detect statistical outliers in LIMS test data for a given field.

    Returns flagged samples with their test statistic and threshold.
    """
    if test_type not in _TEST_FIELDS:
        raise HTTPException(400, f"Test type '{test_type}' not supported. Options: {list(_TEST_FIELDS.keys())}")

    config = _TEST_FIELDS[test_type]
    if field not in config["fields"]:
        raise HTTPException(400, f"Field '{field}' not available for {test_type}. Options: {list(config['fields'].keys())}")

    table = config["table"]
    field_label = config["fields"][field]

    # Fetch all non-null values for this field
    rows = qall(
        f"SELECT id, sample_id, {field} FROM {table} "
        f"WHERE project_id = %s AND {field} IS NOT NULL "
        f"ORDER BY created_at",
        (pid,),
    )

    if not rows or len(rows) < 3:
        return OutlierResponse(
            test_type=test_type,
            field=field,
            field_label=field_label,
            method=method,
            sample_count=len(rows) if rows else 0,
            outliers=[],
        )

    values = [float(r[field]) for r in rows]
    results = detect_outliers(values, method=method, alpha=alpha, zscore_threshold=zscore_threshold)

    outliers = []
    for r in results:
        row = rows[r.index]
        outliers.append({
            "id": str(row["id"]),
            "sample_id": row.get("sample_id", ""),
            "value": r.value,
            "method": r.method,
            "statistic": round(r.statistic, 4),
            "threshold": round(r.threshold, 4),
            "p_value": round(r.p_value, 6) if r.p_value is not None else None,
        })

    return OutlierResponse(
        test_type=test_type,
        field=field,
        field_label=field_label,
        method=method,
        sample_count=len(values),
        outliers=outliers,
    )


@router.get("/{pid}/lims/outliers/summary")
def outlier_summary(
    pid: str,
    method: Literal["grubbs", "modified_zscore", "both"] = Query("both"),
    alpha: float = Query(0.05, ge=0.001, le=0.2),
    user=Depends(project_user),
) -> dict:
    """
    Run outlier detection across all supported test types and fields.
    Returns a summary of flagged values per test type.
    """
    summary: dict[str, list[dict]] = {}

    for test_type, config in _TEST_FIELDS.items():
        table = config["table"]
        flagged_fields: list[dict] = []

        for field_name, field_label in config["fields"].items():
            rows = qall(
                f"SELECT id, sample_id, {field_name} FROM {table} "
                f"WHERE project_id = %s AND {field_name} IS NOT NULL",
                (pid,),
            )
            if not rows or len(rows) < 3:
                continue

            values = [float(r[field_name]) for r in rows]
            results = detect_outliers(values, method=method, alpha=alpha)

            if results:
                flagged_fields.append({
                    "field": field_name,
                    "label": field_label,
                    "total_samples": len(values),
                    "outlier_count": len(results),
                    "outlier_indices": [r.index for r in results],
                })

        if flagged_fields:
            summary[test_type] = flagged_fields

    total_outliers = sum(
        item["outlier_count"]
        for fields in summary.values()
        for item in fields
    )

    return {
        "project_id": pid,
        "method": method,
        "alpha": alpha,
        "total_outliers": total_outliers,
        "by_test_type": summary,
    }
