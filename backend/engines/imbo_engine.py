# backend/engines/imbo_engine.py
"""IMBO — Intelligent Metallurgical Blend Optimizer.

Fonction pure : LP linéarisé via PuLP+CBC. < 5s pour 20 sources × 10 contraintes.
"""
from __future__ import annotations
import logging
import time

logger = logging.getLogger("mpdpms.imbo_engine")

TROY_OZ_PER_GRAM = 1 / 31.1035
DEFAULT_RECOVERY  = 0.89


def optimize_blend(
    sources: list[dict],
    constraints: list[dict],
    gold_price: float = 3200.0,
    target_variable: str = "maximize_au_oz",
) -> dict:
    """
    Optimise l'allocation de sources de minerai sous contraintes.

    sources : [{
        id, label,
        tonnage_available, tonnage_min?, tonnage_max?,
        au_grade,              # g/t
        predicted_recovery,    # fraction [0,1] ou % — normalisé en interne
        bwi?,                  # kWh/t
        s_sulphide?,           # %
        cu_ppm?,               # ppm
        carbon_pct?,           # %
        predicted_cn_kg_t?,    # kg/t
        mining_cost_per_tonne?, haulage_cost_per_tonne?
    }]
    constraints : [{
        id, name, parameter, operator ('lte'|'gte'|'eq'), value, unit,
        severity ('hard'|'soft'), penalty_per_unit?
    }]

    Retourne : {
        status: 'optimal'|'infeasible'|'error',
        allocation: [{source_id, label, tonnes, pct_of_blend, ...blended props}],
        blended_properties: {au_grade, recovery, bwi, cn_kg_t, ...},
        predicted_recovery: float,
        predicted_gold_oz: float,
        objective_value: float,
        shadow_prices: [{constraint_id, name, shadow_price, slack, is_binding}],
        binding_constraint: str | None,
        vs_baseline: {},
        solve_time_ms: float,
    }
    """
    t0 = time.perf_counter()

    try:
        import pulp
    except ImportError:
        return {"status": "error", "message": "PuLP non installé"}

    if not sources:
        return {"status": "infeasible", "message": "Aucune source disponible",
                "conflicting_constraints": []}

    # Normaliser recovery en fraction [0,1]
    for s in sources:
        rec = s.get("predicted_recovery", DEFAULT_RECOVERY)
        if rec is None: rec = DEFAULT_RECOVERY
        if rec > 1.5: rec = rec / 100.0
        s["_recovery"] = float(rec)
        s["_id"] = str(s.get("id") or s.get("label") or f"src_{sources.index(s)}")

    # ── Problème LP ───────────────────────────────────────────────────────────
    prob = pulp.LpProblem("IMBO_blend", pulp.LpMaximize)

    # Variables de décision : tonnes par source
    x = {}
    for s in sources:
        sid = s["_id"]
        lb = float(s.get("tonnage_min") or 0)
        ub = float(s.get("tonnage_max") or s.get("tonnage_available") or 1e9)
        x[sid] = pulp.LpVariable(f"x_{sid}", lowBound=lb, upBound=ub)

    # Tonnage total (variable auxiliaire pour linéariser les ratios)
    T = pulp.lpSum(x[s["_id"]] for s in sources)

    # ── Fonction objectif ─────────────────────────────────────────────────────
    # max Σ x[i] * grade[i] * recovery[i] * TROY_OZ_PER_GRAM * gold_price
    # (- slack pénalités pour contraintes soft)

    slack_vars: dict[str, pulp.LpVariable] = {}
    soft_penalty = 0

    for c in constraints:
        if c.get("severity") == "soft":
            cid = str(c.get("id") or c.get("name"))
            s_var = pulp.LpVariable(f"slack_{cid}", lowBound=0)
            slack_vars[cid] = s_var
            penalty = float(c.get("penalty_per_unit") or 100)
            soft_penalty += penalty * s_var

    obj_terms = pulp.lpSum(
        x[s["_id"]] * float(s.get("au_grade") or 0) * s["_recovery"] * TROY_OZ_PER_GRAM * gold_price
        for s in sources
    ) - soft_penalty

    prob += obj_terms, "Objective"

    # Contrainte : tonnage total > 0 (éviter solution vide)
    prob += T >= 1.0, "min_total_tonnage"

    # ── Contraintes blending (linéarisées) ───────────────────────────────────
    PARAM_MAP = {
        "bwi": "bwi", "bwi_kwh_t": "bwi",
        "s_sulphide": "s_sulphide", "s_sulphide_pct": "s_sulphide",
        "cu_ppm": "cu_ppm", "cu": "cu_ppm",
        "carbon_pct": "carbon_pct",
        "recovery": "_recovery", "predicted_recovery": "_recovery",
        "cn_kg_t": "predicted_cn_kg_t",
    }

    constraint_vars: list[tuple[dict, pulp.LpConstraint | None]] = []

    for c in constraints:
        param_key = PARAM_MAP.get(c.get("parameter") or "", c.get("parameter") or "")
        cval = float(c.get("value") or 0)
        op = c.get("operator") or "lte"
        cid = str(c.get("id") or c.get("name") or "c")
        is_soft = c.get("severity") == "soft"

        # Σ x[i]*prop[i] OP value * T  (linéarisé)
        prop_sum = pulp.lpSum(
            x[s["_id"]] * float(s.get(param_key) or 0) for s in sources
        )

        if op == "lte":
            lhs = prop_sum - cval * T
            if is_soft:
                lhs = lhs - slack_vars.get(cid, 0)
            constr = lhs <= 0
        elif op == "gte":
            lhs = cval * T - prop_sum
            if is_soft:
                lhs = lhs - slack_vars.get(cid, 0)
            constr = lhs <= 0
        elif op == "eq":
            constr = prop_sum == cval * T
        else:
            continue

        if param_key and any(s.get(param_key) for s in sources):
            prob += constr, f"c_{cid}"
            constraint_vars.append((c, constr))
        else:
            constraint_vars.append((c, None))  # contrainte ignorée (données manquantes)

    # ── Résolution ────────────────────────────────────────────────────────────
    solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=30)
    try:
        prob.solve(solver)
    except Exception as e:
        return {"status": "error", "message": str(e), "solve_time_ms": 0}

    solve_ms = round((time.perf_counter() - t0) * 1000, 1)
    status_str = pulp.LpStatus[prob.status].lower()

    if status_str != "optimal":
        # Identifier contraintes conflictuelles
        conflicting = [c.get("name") or c.get("id") for c, _ in constraint_vars if _ is not None]
        return {
            "status": "infeasible",
            "conflicting_constraints": conflicting,
            "solve_time_ms": solve_ms,
            "allocation": None,
        }

    # ── Extraire résultats ────────────────────────────────────────────────────
    total_t = sum(pulp.value(x[s["_id"]]) or 0 for s in sources)
    if total_t < 1e-6: total_t = 1.0

    allocation = []
    blend_au, blend_rec, blend_bwi, blend_cn = 0.0, 0.0, 0.0, 0.0
    total_allocated = 0.0

    for s in sources:
        sid = s["_id"]
        tonnes = float(pulp.value(x[sid]) or 0)
        if tonnes < 0.01: continue
        pct = round(tonnes / total_t * 100, 2)
        total_allocated += tonnes
        w = tonnes / total_t
        blend_au  += w * float(s.get("au_grade") or 0)
        blend_rec += w * s["_recovery"]
        blend_bwi += w * float(s.get("bwi") or 14)
        blend_cn  += w * float(s.get("predicted_cn_kg_t") or 0)
        allocation.append({
            "source_id": sid,
            "label": s.get("label") or sid,
            "tonnes": round(tonnes, 1),
            "pct_of_blend": pct,
            "au_grade_contribution": round(w * float(s.get("au_grade") or 0), 4),
        })

    pred_oz = total_t * blend_au * blend_rec * TROY_OZ_PER_GRAM

    # ── Shadow prices ─────────────────────────────────────────────────────────
    shadow_prices = []
    for c, constr in constraint_vars:
        if constr is None: continue
        cname = f"c_{c.get('id') or c.get('name')}"
        try:
            dual = prob.constraints.get(cname)
            sp = float(dual.pi) if dual and dual.pi is not None else 0.0
            slack = float(dual.slack) if dual and dual.slack is not None else 0.0
        except Exception:
            sp, slack = 0.0, 0.0
        shadow_prices.append({
            "constraint_id": str(c.get("id") or c.get("name")),
            "name": c.get("name") or str(c.get("id")),
            "shadow_price": round(sp, 4),
            "slack": round(slack, 4),
            "is_binding": abs(slack) < 1e-4,
        })

    binding = max(shadow_prices, key=lambda sp: abs(sp["shadow_price"]), default=None)

    return {
        "status": "optimal",
        "allocation": allocation,
        "blended_properties": {
            "au_grade_g_t": round(blend_au, 4),
            "recovery_pct": round(blend_rec * 100, 2),
            "bwi_kwh_t": round(blend_bwi, 2),
            "cn_kg_t": round(blend_cn, 3),
        },
        "predicted_recovery": round(blend_rec * 100, 2),
        "predicted_gold_oz": round(pred_oz, 0),
        "objective_value": round(float(pulp.value(prob.objective) or 0), 2),
        "shadow_prices": shadow_prices,
        "binding_constraint": binding["name"] if binding and binding["is_binding"] else None,
        "total_tonnage": round(total_allocated, 1),
        "vs_baseline": {},
        "solve_time_ms": solve_ms,
    }
