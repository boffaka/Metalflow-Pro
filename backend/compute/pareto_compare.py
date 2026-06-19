"""Helpers to compare Pareto fronts from simulate_optimize / NSGA-style runs."""
from __future__ import annotations

from typing import Any


def _dominates(
    a_rec: float, a_eng: float, b_rec: float, b_eng: float,
    *, tol: float = 1e-9,
) -> bool:
    """True if A dominates B (maximize recovery, minimize energy)."""
    if a_rec < b_rec - tol:
        return False
    if a_eng > b_eng + tol:
        return False
    return (a_rec > b_rec + tol) or (a_eng < b_eng - tol)


def pareto_metrics(front: list[dict[str, Any]]) -> dict[str, Any]:
    """Scalar summaries for a pareto_front list (expected_recovery / expected_energy)."""
    if not front:
        return {
            "count": 0,
            "recovery_best_pct": None,
            "energy_best_kwh_t": None,
            "recovery_span_pct": None,
            "energy_span_kwh_t": None,
        }
    rec = [float(p["expected_recovery"]) for p in front]
    eng = [float(p["expected_energy"]) for p in front]
    return {
        "count": len(front),
        "recovery_best_pct": max(rec),
        "energy_best_kwh_t": min(eng),
        "recovery_span_pct": round(max(rec) - min(rec), 6),
        "energy_span_kwh_t": round(max(eng) - min(eng), 6),
    }


def compare_pareto_results(results_a: dict[str, Any], results_b: dict[str, Any]) -> dict[str, Any]:
    """Compare two full simulate_optimize-style result payloads."""
    fa = results_a.get("pareto_front") or []
    fb = results_b.get("pareto_front") or []
    ma, mb = pareto_metrics(fa), pareto_metrics(fb)

    a_exclusive = 0
    for pa in fa:
        ra, ea = float(pa["expected_recovery"]), float(pa["expected_energy"])
        dominated_by_b = False
        for pb in fb:
            rb, eb = float(pb["expected_recovery"]), float(pb["expected_energy"])
            if _dominates(rb, eb, ra, ea):
                dominated_by_b = True
                break
        if not dominated_by_b:
            a_exclusive += 1

    b_exclusive = 0
    for pb in fb:
        rb, eb = float(pb["expected_recovery"]), float(pb["expected_energy"])
        dominated_by_a = False
        for pa in fa:
            ra, ea = float(pa["expected_recovery"]), float(pa["expected_energy"])
            if _dominates(ra, ea, rb, eb):
                dominated_by_a = True
                break
        if not dominated_by_a:
            b_exclusive += 1

    return {
        "pareto_a": ma,
        "pareto_b": mb,
        "points_a_not_dominated_by_b": a_exclusive,
        "points_b_not_dominated_by_a": b_exclusive,
        "solver_a": results_a.get("solver"),
        "solver_b": results_b.get("solver"),
        "study_context_a": results_a.get("study_context"),
        "study_context_b": results_b.get("study_context"),
    }
