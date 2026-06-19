"""Optimisation multi-objectifs sur grille (comminution + CIL) avec front de Pareto.

Extensions MetPlant 2008 / Spencer–Scriba :
  - Contexte d'étude (niveau projet, tolérance CAPEX/OPEX déclarative)
  - Incertitude Monte Carlo sur paramètres de base (k_cil, WI, SPI, R∞…)
  - Évaluation « surrogate » rapide (Bond + cinétique inline) ou « rigorous » via simulate_circuit
"""
from __future__ import annotations

import itertools
import random
from typing import Any

try:
    from backend.tasks.simulation_tasks import _run_rigorous_engine
except ImportError:  # pragma: no cover
    from tasks.simulation_tasks import _run_rigorous_engine


_DEFAULT_GRID = {
    "p80": [68, 72, 76, 80],
    "cn": [275, 325, 375],
    "do": [6.0, 7.5, 8.0],
    "srt": [22, 26, 30],
}

_MAX_GRID_COMBINATIONS = 2500
_MAX_UNCERTAINTY_EVALS = 40000

_METPLANT_2008_REFERENCE = (
    "Spencer & Scriba, MetPlant 2008 — Best Practices in Process Simulation "
    "(data collection, scenario testing, auditable assumptions)."
)

_STUDY_LEVEL_HINTS = (
    "technology_review | desktop | scoping | pre_feasibility | feasibility | "
    "definitive | construction | commissioning | plant_optimisation"
)


def _pareto_front(points: list[dict[str, Any]], *, max_key: str, min_key: str) -> list[dict]:
    """Return points that are not dominated."""
    front: list[dict] = []
    for p in points:
        dominated = False
        for q in points:
            if q is p:
                continue
            if (q[max_key] >= p[max_key] and q[min_key] <= p[min_key]
                    and (q[max_key] > p[max_key] or q[min_key] < p[min_key])):
                dominated = True
                break
        if not dominated:
            front.append(p)
    return front


def _parse_circuit_eval(payload: dict[str, Any]) -> tuple[str, str | None, dict[str, Any]]:
    """Returns (mode, template_id, extra_override dict)."""
    block = payload.get("circuit_evaluation") or {}
    mode = str(block.get("mode") or "surrogate").lower().strip()
    if mode not in ("surrogate", "rigorous"):
        raise ValueError("circuit_evaluation.mode must be 'surrogate' or 'rigorous'")
    tid = block.get("template_id")
    template_id = str(tid).strip() if tid else None
    extra = dict(block.get("params_override") or {})
    if mode == "rigorous" and not template_id:
        raise ValueError("circuit_evaluation.template_id is required when mode is rigorous")
    return mode, template_id, extra


def _rigorous_combo(
    project_id: str,
    template_id: str,
    cursor: Any,
    p80: float,
    cn: float,
    do: float,
    srt: float,
    circuit_extra: dict[str, Any],
) -> dict[str, Any]:
    try:
        from backend.engines.process_simulator import simulate_circuit
    except ImportError:  # pragma: no cover
        from engines.process_simulator import simulate_circuit

    ov = dict(circuit_extra)
    ov["p80_um"] = float(p80)
    ov["cn_ppm"] = float(cn)
    ov["do_mg_l"] = float(do)
    ov["srt_h"] = float(srt)
    raw = simulate_circuit(project_id, template_id, params_override=ov, cursor=cursor)
    overall = raw.get("overall") or {}
    recovery = float(overall.get("total_recovery_pct") or 0.0)
    energy = float(overall.get("total_energy_kwh_t") or 0.0)
    out = {
        "recovery_pct": recovery,
        "energy_kwh_t": energy,
        "annual_oz": float(overall.get("annual_gold_oz") or 0.0),
    }
    if raw.get("warnings"):
        out["circuit_warnings"] = raw["warnings"]
    return out


def _surrogate_combo(base: dict[str, Any], p80: float, cn: float, do: float, srt: float) -> dict[str, Any]:
    params = dict(base)
    params.update({"p80": p80, "cn": cn, "do": do, "srt": srt})
    return _run_rigorous_engine(params)


def _jitter_base_params(
    base: dict[str, Any],
    rng: random.Random,
    relative_sigma: dict[str, Any],
) -> dict[str, Any]:
    """Gaussian relative jitter on selected keys (fractional sigma)."""
    out = dict(base)
    for key, sig in relative_sigma.items():
        if key not in out:
            continue
        try:
            mu = float(out[key])
        except (TypeError, ValueError):
            continue
        sigma = abs(float(sig)) * abs(mu)
        if sigma <= 0:
            continue
        draw = rng.gauss(mu, sigma)
        if key in ("k_cil", "wi", "spi_kwh_t", "r_inf", "grade_g_t", "tph", "f80_um"):
            if key == "r_inf" and draw > 1.5:
                draw = min(draw / 100.0, 1.0)
            if key in ("k_cil", "wi", "spi_kwh_t", "tph", "grade_g_t", "f80_um"):
                draw = max(draw, 1e-9)
        out[key] = draw
    return out


def _combo_result_row(
    base: dict[str, Any],
    p80: float,
    cn: float,
    do: float,
    srt: float,
    *,
    eval_mode: str,
    project_id: str | None,
    template_id: str | None,
    circuit_cursor: Any,
    circuit_extra: dict[str, Any],
) -> dict[str, Any]:
    if eval_mode == "rigorous":
        assert project_id and template_id and circuit_cursor is not None
        r = _rigorous_combo(project_id, template_id, circuit_cursor, p80, cn, do, srt, circuit_extra)
        row: dict[str, Any] = {
            "p80": p80,
            "cn": cn,
            "do": do,
            "srt": srt,
            "expected_recovery": round(float(r["recovery_pct"]), 3),
            "expected_energy": round(float(r["energy_kwh_t"]), 3),
            "evaluation_engine": "simulate_circuit",
        }
        if r.get("circuit_warnings"):
            row["circuit_warnings"] = r["circuit_warnings"]
        return row

    r = _surrogate_combo(base, p80, cn, do, srt)
    row = {
        "p80": p80,
        "cn": cn,
        "do": do,
        "srt": srt,
        "expected_recovery": round(float(r["recovery_pct"]), 3),
        "expected_energy": round(float(r["energy_kwh_t"]), 3),
        "evaluation_engine": "bond_cil_surrogate",
    }
    ke = r.get("k_cil_effective")
    if ke is not None:
        row["k_cil_effective"] = ke
    return row


def run_optimize(payload: dict[str, Any], ctx, circuit_cursor: Any | None = None) -> dict[str, Any]:
    base = dict(payload.get("base_params") or {})
    grid = dict(payload.get("grid") or _DEFAULT_GRID)
    raw_study = payload.get("study_context")
    study_context_payload = raw_study if isinstance(raw_study, dict) else {}

    eval_mode, template_id, circuit_extra = _parse_circuit_eval(payload)
    project_id = payload.get("_project_id") or payload.get("project_id")

    if eval_mode == "rigorous":
        if not project_id:
            raise ValueError("_project_id or project_id required for rigorous circuit evaluation")
        if circuit_cursor is None:
            raise ValueError("circuit_cursor is required when circuit_evaluation.mode is rigorous")

    unc = payload.get("uncertainty") or {}
    n_samples = int(unc.get("n_samples") or 0)
    rel_sigma = dict(unc.get("relative_sigma") or {})
    seed = unc.get("seed")
    rng = random.Random(int(seed) if seed is not None else 42)

    p80s = list(grid.get("p80") or _DEFAULT_GRID["p80"])
    cns = list(grid.get("cn") or _DEFAULT_GRID["cn"])
    dos = list(grid.get("do") or _DEFAULT_GRID["do"])
    srts = list(grid.get("srt") or _DEFAULT_GRID["srt"])

    combos = list(itertools.product(p80s, cns, dos, srts))
    total = len(combos)
    if total == 0:
        raise ValueError("grid produced zero combinations")
    if total > _MAX_GRID_COMBINATIONS:
        raise ValueError(
            f"grid has {total} combinations (max {_MAX_GRID_COMBINATIONS}); "
            "reduce levels or split into staged scenario runs"
        )

    samples_eff = n_samples if (n_samples > 0 and rel_sigma) else 1
    if samples_eff > 1:
        total_evals = total * samples_eff
        if total_evals > _MAX_UNCERTAINTY_EVALS:
            raise ValueError(
                f"uncertainty budget exceeded ({total_evals} evals, max {_MAX_UNCERTAINTY_EVALS}); "
                "reduce n_samples, grid size, or relative_sigma keys"
            )

    all_results: list[dict[str, Any]] = []
    idx = 0
    for i, (p80, cn, do, srt) in enumerate(combos, start=1):
        ctx.check_cancelled()
        rec_vals: list[float] = []
        eng_vals: list[float] = []
        merged_rows: dict[str, Any] | None = None

        for s in range(samples_eff):
            ctx.check_cancelled()
            sample_base = _jitter_base_params(base, rng, rel_sigma) if samples_eff > 1 else base
            row = _combo_result_row(
                sample_base, p80, cn, do, srt,
                eval_mode=eval_mode,
                project_id=str(project_id) if project_id else None,
                template_id=template_id,
                circuit_cursor=circuit_cursor,
                circuit_extra=circuit_extra,
            )
            rec_vals.append(float(row["expected_recovery"]))
            eng_vals.append(float(row["expected_energy"]))
            merged_rows = row
            idx += 1
            if samples_eff > 1:
                if idx % 7 == 0 or idx == total * samples_eff:
                    ctx.report_progress(idx, total * samples_eff, f"eval {idx}/{total * samples_eff}")

        assert merged_rows is not None
        out_row = dict(merged_rows)
        out_row["expected_recovery"] = round(sum(rec_vals) / len(rec_vals), 3)
        out_row["expected_energy"] = round(sum(eng_vals) / len(eng_vals), 3)
        if samples_eff > 1:
            sr = sorted(rec_vals)
            se = sorted(eng_vals)
            p10_i = max(0, int(round(0.1 * (len(sr) - 1))))
            p90_i = max(0, int(round(0.9 * (len(sr) - 1))))
            out_row["expected_recovery_p10"] = round(sr[p10_i], 3)
            out_row["expected_recovery_p90"] = round(sr[p90_i], 3)
            out_row["expected_energy_p10"] = round(se[p10_i], 3)
            out_row["expected_energy_p90"] = round(se[p90_i], 3)
            out_row["uncertainty_n_samples"] = samples_eff

        all_results.append(out_row)

        if samples_eff == 1 and (i % 5 == 0 or i == total):
            ctx.report_progress(i, total, f"combo {i}/{total}")

    front = _pareto_front(
        [{"p80": p["p80"], "cn": p["cn"], "do": p["do"], "srt": p["srt"],
          "recovery": p["expected_recovery"], "energy": p["expected_energy"]}
         for p in all_results],
        max_key="recovery", min_key="energy",
    )
    front_keys = {(p["p80"], p["cn"], p["do"], p["srt"], p["recovery"], p["energy"]) for p in front}
    pareto_points: list[dict[str, Any]] = []
    for p in all_results:
        key = (p["p80"], p["cn"], p["do"], p["srt"], p["expected_recovery"], p["expected_energy"])
        if key not in front_keys:
            continue
        entry = {k: v for k, v in p.items() if not k.startswith("_")}
        pareto_points.append(entry)

    pareto_points.sort(key=lambda x: (-x["expected_recovery"], x["expected_energy"]))
    recommended = pareto_points[0] if pareto_points else None
    all_results.sort(key=lambda x: (-x["expected_recovery"], x["expected_energy"]))
    top = all_results[:100]

    ctx.check_cancelled()

    methodology_notes = {
        "reference": _METPLANT_2008_REFERENCE,
        "study_level_hints": _STUDY_LEVEL_HINTS,
        "sla_stages_reflected": (
            "Stage 5 — model optimisation & scenario testing; explicit operating assumptions."
        ),
        "parameter_mapping": (
            "Grid keys p80→p80_um (µm), srt→srt_h (h), cn→nacn_mg_l / cn_ppm, do→do_mg_l; "
            "surrogate path scales k_cil via effective_k_cil; rigorous path pushes overrides "
            "into simulate_circuit (Bond mill + CIL retention/chemistry)."
        ),
        "audit_hints": (
            "Compare k_cil_effective / circuit warnings against LIMS D1 kinetics and pilot "
            "mass balance; align declared capex_opex_tolerance_pct with MetPlant Table 2 expectations."
        ),
    }
    if study_context_payload:
        methodology_notes["study_context_echo"] = study_context_payload

    return {
        "ok": True,
        "solver": (
            "Grid sweep + Pareto | "
            + ("simulate_circuit" if eval_mode == "rigorous" else "Bond+CIL surrogate")
            + (" + Monte Carlo" if samples_eff > 1 else "")
        ),
        "evaluation_mode": eval_mode,
        "iterations": total * samples_eff,
        "grid_combinations": total,
        "pareto_front": pareto_points,
        "recommended_optimum": recommended,
        "all_results": top,
        "study_context": raw_study if isinstance(raw_study, dict) else None,
        "uncertainty": {"n_samples": samples_eff, "relative_sigma": rel_sigma} if samples_eff > 1 else None,
        "methodology_notes": methodology_notes,
    }
