"""Legacy Simulation Pro compatibility layer (/api/v1/sim-pro).

The monolith frontend calls these project-scoped endpoints with inline feed/nodes
payloads. Routes delegate to the Sim Module v2 unit library for chain simulation.
"""
from __future__ import annotations

import logging
import random
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

try:
    from ..auth import project_user
except ImportError:
    from auth import project_user

try:
    from ..engines.sim_unit_library import SimStream, calculate_unit
except ImportError:
    from engines.sim_unit_library import SimStream, calculate_unit

logger = logging.getLogger("mpdpms.sim_pro")

router = APIRouter(prefix="/api/v1/sim-pro", tags=["sim-pro"])


def _feed_stream(feed: dict | None) -> SimStream:
    feed = feed or {}
    return SimStream.from_feed(
        feed_rate=float(feed.get("feed_rate_tph") or feed.get("feed_rate") or 100),
        gold_grade=float(feed.get("gold_grade") or feed.get("grade_g_t") or 2.5),
        p80=float(feed.get("p80_um") or feed.get("p80") or 75),
        silver_grade=float(feed.get("silver_grade") or 0),
        sulphide_pct=float(feed.get("sulphide_pct") or feed.get("sulphide_content") or 0),
    )


def _run_node_chain(feed: dict | None, nodes: list[dict]) -> dict[str, Any]:
    if not nodes:
        raise HTTPException(400, "Au moins une unité est requise dans le flowsheet.")
    feed_input = feed or {}
    inlet = {"feed": _feed_stream(feed_input)}
    node_results: list[dict] = []
    head_grade = inlet["feed"].gold_grade
    final_stream = inlet["feed"]
    total_energy = 0.0

    for node in nodes:
        unit_type = node.get("type") or node.get("unit_type") or "unknown"
        params = node.get("params") or {}
        output = calculate_unit(unit_type, inlet, params, feed_input)
        product = output.streams.get("product") or final_stream
        recovery = float(output.kpis.get("recovery_pct") or output.kpis.get("au_recovery_pct") or 100.0)
        energy = float(output.kpis.get("energy_kwh_t") or product.energy_kwh_t or 0.0)
        total_energy += energy
        node_results.append({
            "node_id": node.get("id"),
            "unit_type": unit_type,
            "recovery_pct": recovery,
            "energy_kwh_t": energy,
            "gold_grade_g_t": product.gold_grade,
            "p80_um": product.p80_um,
            "kpis": output.kpis,
        })
        inlet = {"feed": product}
        final_stream = product

    overall_recovery = (
        (final_stream.gold_flow / (head_grade * final_stream.mass_flow / 1000) * 100)
        if head_grade > 0 and final_stream.mass_flow > 0
        else 0.0
    )
    return {
        "overall": {
            "overall_recovery_pct": round(overall_recovery, 3),
            "head_grade_g_t": head_grade,
            "product_grade_g_t": final_stream.gold_grade,
            "energy_kwh_t": round(total_energy, 3),
        },
        "node_results": node_results,
    }


@router.post("/{pid}/simulate")
def simulate(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    return _run_node_chain(body.get("feed"), body.get("nodes") or [])


@router.post("/{pid}/optimize")
def optimize(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    variables = body.get("variables") or []
    objectives = body.get("objectives") or []
    if not variables:
        raise HTTPException(400, "Sélectionnez au moins une variable d'optimisation.")
    if not objectives:
        raise HTTPException(400, "Sélectionnez au moins un objectif.")

    feed = body.get("feed") or {}
    nodes = list(body.get("nodes") or [])
    generations = int(body.get("generations") or 20)
    pop_size = int(body.get("pop_size") or 12)
    solutions: list[dict] = []

    for _ in range(min(pop_size, 24)):
        trial_nodes = []
        for node in nodes:
            trial = dict(node)
            params = dict(trial.get("params") or {})
            for var in variables:
                if var.get("node_id") == node.get("id") and var.get("param") in params:
                    lo = float(var.get("min", params[var["param"]]))
                    hi = float(var.get("max", params[var["param"]]))
                    params[var["param"]] = lo + random.random() * max(hi - lo, 0)
            trial["params"] = params
            trial_nodes.append(trial)
        result = _run_node_chain(feed, trial_nodes)
        score = float(result["overall"]["overall_recovery_pct"])
        solutions.append({"params": trial_nodes, "score": score, "result": result})

    solutions.sort(key=lambda s: s["score"], reverse=True)
    return {
        "n_solutions": len(solutions),
        "generations": generations,
        "pop_size": pop_size,
        "pareto_front": solutions[: min(10, len(solutions))],
        "best": solutions[0] if solutions else None,
    }


@router.post("/{pid}/monte-carlo")
def monte_carlo(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    uncertain = body.get("uncertain_params") or []
    if not uncertain:
        raise HTTPException(400, "Sélectionnez au moins un paramètre incertain.")
    iterations = int(body.get("iterations") or 500)
    feed = body.get("feed") or {}
    nodes = list(body.get("nodes") or [])
    samples: list[dict] = []

    for _ in range(min(iterations, 5000)):
        trial_nodes = []
        for node in nodes:
            trial = dict(node)
            params = dict(trial.get("params") or {})
            for u in uncertain:
                if u.get("node_id") == node.get("id") and u.get("param") in params:
                    mean = float(u.get("mean", params[u["param"]]))
                    std = abs(float(u.get("std") or mean * 0.1))
                    params[u["param"]] = max(0.0, random.gauss(mean, std))
            trial["params"] = params
            trial_nodes.append(trial)
        result = _run_node_chain(feed, trial_nodes)
        samples.append(result["overall"]["overall_recovery_pct"])

    samples.sort()
    p10 = samples[int(len(samples) * 0.1)] if samples else 0
    p50 = samples[int(len(samples) * 0.5)] if samples else 0
    p90 = samples[int(len(samples) * 0.9)] if samples else 0
    return {
        "iterations": len(samples),
        "recovery_pct": {"p10": p10, "p50": p50, "p90": p90, "mean": round(sum(samples) / len(samples), 3) if samples else 0},
        "samples": samples[:100],
    }


@router.post("/{pid}/ai-advisor")
def ai_advisor(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    question = str(body.get("user_question") or "").strip()
    if not question:
        raise HTTPException(400, "user_question requis.")
    sim_result = body.get("sim_result") or {}
    overall = sim_result.get("overall") or {}
    recovery = overall.get("overall_recovery_pct")
    context = (
        f"Récupération simulée: {recovery}% — "
        f"grade tête {overall.get('head_grade_g_t')} g/t — "
        f"énergie {overall.get('energy_kwh_t')} kWh/t."
        if recovery is not None
        else "Aucune simulation récente — lancez une simulation d'abord."
    )
    try:
        try:
            from ..engines.assistant import chat
            from ..db import qone, qall
        except ImportError:
            from engines.assistant import chat
            from db import qone, qall
        result = chat(pid, f"{question}\n\nContexte Simulation Pro: {context}", qone, qall)
        answer = result.get("response") or str(result)
    except Exception as exc:
        logger.warning("Sim Pro AI advisor fallback: %s", exc)
        answer = (
            f"{context}\n\n"
            f"Question: {question}\n\n"
            "Conseil métallurgique (mode hors-ligne): vérifiez la libération au broyage, "
            "la récupération gravimétrique pré-CIL et la consommation de cyanure avant d'ajuster le CIL."
        )
    return {"answer": answer, "context": context}
