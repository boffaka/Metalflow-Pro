"""
Project metallurgical analysis — LIMS aggregates + route recovery (CIM / Laplante).

Used by monolithic UI (granulometry fallback) and external clients.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

try:
    from ..auth import project_user
    from ..db import qall
    from ..engines.metallurgical_formulas import (
        FORMULA_REFERENCES,
        route_recovery_estimates,
    )
    from .lims import LIMS_TABLES, safe_table_name
except ImportError:
    from auth import project_user
    from db import qall
    from engines.metallurgical_formulas import FORMULA_REFERENCES, route_recovery_estimates
    from routes.lims import LIMS_TABLES, safe_table_name

logger = logging.getLogger("mpdpms.analysis")

router = APIRouter(prefix="/api/v1/projects", tags=["analysis"])

_ANALYSIS_CODES = ("a1", "a2", "a3", "b1", "c2", "c2b", "c2c", "c3", "d1", "g1", "m1")


def _load_lims_tests(pid: str) -> dict[str, list[dict[str, Any]]]:
    tests: dict[str, list[dict[str, Any]]] = {}
    for code in _ANALYSIS_CODES:
        tbl = LIMS_TABLES.get(code)
        if not tbl:
            continue
        safe_tbl = safe_table_name(tbl)
        rows = qall(
            f"SELECT * FROM {safe_tbl} WHERE project_id=%s ORDER BY created_at",
            (pid,),
        ) or []
        tests[code] = [dict(r) for r in rows]
    return tests


def _avg(rows: list[dict], key: str) -> float | None:
    vals = [float(r[key]) for r in rows if r.get(key) not in (None, "") and _is_num(r[key])]
    return sum(vals) / len(vals) if vals else None


def _is_num(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _grg_avg(tests: dict[str, list[dict]]) -> float | None:
    grav_vals: list[float] = []
    for code in ("c2", "c2b", "c2c", "c3"):
        for row in tests.get(code) or []:
            for key in ("grg_rec_pct", "recovery_pct", "total_rec_pct"):
                if row.get(key) not in (None, "") and _is_num(row[key]):
                    v = float(row[key])
                    if v > 0:
                        grav_vals.append(v)
                    break
    if grav_vals:
        return sum(grav_vals) / len(grav_vals)
    return None


def _leach_avg(tests: dict[str, list[dict]]) -> float | None:
    d1 = tests.get("d1") or []
    return (
        _avg(d1, "leach_rec_48h_pct")
        or _avg(d1, "leach_rec_24h_pct")
        or _avg(d1, "au_recovery_pct")
    )


@router.get("/{pid}/analysis")
def get_project_analysis(pid: str, user=Depends(project_user)):
    """LIMS test bundles + metallurgical route recovery estimates for the project."""
    try:
        tests = _load_lims_tests(pid)
        grg = _grg_avg(tests)
        leach = _leach_avg(tests)
        flot = _avg(tests.get("g1") or [], "au_recovery_pct")
        routes = route_recovery_estimates(
            leach_whole_ore_pct=leach,
            grg_lims_avg_pct=grg,
            flotation_pct=flot,
        )
        counts = {code: len(tests.get(code) or []) for code in _ANALYSIS_CODES}
        return {
            "project_id": pid,
            "tests": tests,
            "b1": tests.get("b1") or [],
            "a1": tests.get("a1") or [],
            "counts": counts,
            "averages": {
                "grg_pct": grg,
                "leach_recovery_pct": leach,
                "flotation_recovery_pct": flot,
                "bwi_kwh_t": _avg(tests.get("b1") or [], "bwi_kwh_t")
                or _avg(tests.get("b1") or [], "mb_kwh_t"),
                "au_grade_g_t": _avg(tests.get("a1") or [], "au_g_t"),
            },
            "route_metallurgique": routes,
            "formula_references": FORMULA_REFERENCES,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_project_analysis failed pid=%s: %s", pid, e, exc_info=True)
        raise HTTPException(500, detail=f"Analysis failed: {e}") from e
