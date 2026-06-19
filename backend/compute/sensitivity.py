"""Sensitivity compute functions (spider, tornado).

Pure functions: no DB access, no FastAPI. They take a payload dict and a
JobContext-like object that provides `check_cancelled()` and `report_progress()`.
The deterministic inner engine is reused from the legacy tasks module so existing
numerical behaviour is preserved.
"""
from __future__ import annotations

from typing import Any

try:
    from backend.tasks.simulation_tasks import _run_rigorous_engine
except ImportError:  # pragma: no cover
    from tasks.simulation_tasks import _run_rigorous_engine


def _coerce_params(payload: dict[str, Any]) -> tuple[dict, list[str], list[float]]:
    base = dict(payload.get("base_params") or {})
    vary = list(payload.get("params_to_vary") or [])
    deltas = [float(d) for d in (payload.get("delta_pcts") or [10.0])]
    if not vary:
        raise ValueError("params_to_vary must be a non-empty list")
    if not deltas:
        raise ValueError("delta_pcts must be a non-empty list")
    return base, vary, deltas


def run_spider(payload: dict[str, Any], ctx) -> dict[str, Any]:
    """Spider chart: for each param, run the engine across a range of deltas.

    Returns: {"series": [{"param_key": str, "points": [{"delta_pct", "recovery_pct", "energy_kwh_t"}]}]}
    """
    base, vary, deltas = _coerce_params(payload)
    total = len(vary) * len(deltas)
    done = 0
    series: list[dict[str, Any]] = []
    for param in vary:
        ctx.check_cancelled()
        base_val = float(base.get(param, 1.0)) or 1.0
        points: list[dict[str, Any]] = []
        for delta in deltas:
            ctx.check_cancelled()
            test_params = dict(base)
            test_params[param] = base_val * (1.0 + delta / 100.0)
            r = _run_rigorous_engine(test_params)
            points.append({
                "delta_pct": delta,
                "recovery_pct": r.get("recovery_pct"),
                "energy_kwh_t": r.get("energy_kwh_t"),
            })
            done += 1
            ctx.report_progress(done, total, f"{param} {delta:+.1f}%")
        series.append({"param_key": param, "points": points})
    ctx.check_cancelled()
    return {"series": series, "base_params": base}


def run_tornado(payload: dict[str, Any], ctx) -> dict[str, Any]:
    """Tornado chart: rank parameters by absolute recovery impact at +/-delta."""
    base, vary, deltas = _coerce_params(payload)
    base_result = _run_rigorous_engine(base)
    total = len(vary) * len(deltas) * 2
    done = 0
    rows: list[dict[str, Any]] = []
    for param in vary:
        ctx.check_cancelled()
        base_val = float(base.get(param, 1.0)) or 1.0
        for delta in deltas:
            for sign in (+1, -1):
                ctx.check_cancelled()
                test_params = dict(base)
                test_params[param] = base_val * (1.0 + sign * delta / 100.0)
                r = _run_rigorous_engine(test_params)
                rows.append({
                    "param_key": param,
                    "delta_pct": sign * delta,
                    "impact_recovery": round(r["recovery_pct"] - base_result["recovery_pct"], 4),
                    "impact_energy": round(r["energy_kwh_t"] - base_result["energy_kwh_t"], 4),
                    "impact_opex": 0.0,
                })
                done += 1
                ctx.report_progress(done, total, f"{param} {sign * delta:+.1f}%")
    rows.sort(key=lambda x: abs(x["impact_recovery"]), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    ctx.check_cancelled()
    return {"rows": rows, "base": base_result}
