"""
Unified circuit strategy engine.

Combines:
- "Optimiser circuit IA" (4-candidate recommendation)
- "Trade-off circuits" (5+ scenario comparison)
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from .cip_cil_advisor import recommend_cip_cil_from_lims
    from .circuit_optimizer import (
        CIRCUITS,
        _CIRCUIT_BY_ID,
        _compatibility_notes,
        extract_ore_profile,
        filter_circuits,
        generate_comparison_summary,
        generate_justification,
        score_circuits,
        select_four_circuits,
    )
except ImportError:  # pragma: no cover
    from engines.cip_cil_advisor import recommend_cip_cil_from_lims
    from engines.circuit_optimizer import (
        CIRCUITS,
        _CIRCUIT_BY_ID,
        _compatibility_notes,
        extract_ore_profile,
        filter_circuits,
        generate_comparison_summary,
        generate_justification,
        score_circuits,
        select_four_circuits,
    )

logger = logging.getLogger("mpdpms.circuit_strategy")


_OP_NORMALIZATION = {
    "GRAVITE_KNELSON": "GRAVITY",
    "GRAVITE_FALCON": "GRAVITY",
    "GRAVITY": "GRAVITY",
    "FLOTATION_ROUGHER": "FLOTATION",
    "FLOTATION_SCAVENGER": "FLOTATION",
    "FLOTATION_CLEANER": "FLOTATION",
    "FLOTATION_COLONNE": "FLOTATION",
    "GIRATOIRE": "CRUSH",
    "CONE": "CRUSH",
}


def _normalize_ops(ops: list[str] | tuple[str, ...] | None) -> set[str]:
    if not ops:
        return set()
    out: set[str] = set()
    for op in ops:
        if not op:
            continue
        out.add(_OP_NORMALIZATION.get(str(op), str(op)))
    return out


def _load_active_template(pid: str, db_qall, db_qone) -> tuple[str | None, list[str], str | None]:
    tpl = db_qone(
        "SELECT id, name FROM circuit_templates "
        "WHERE project_id=%s AND is_active=true "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
        (pid,),
    )
    if not tpl:
        tpl = db_qone(
            "SELECT id, name FROM circuit_templates "
            "WHERE project_id=%s "
            "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
            (pid,),
        )
    if not tpl:
        return None, [], None

    tid = str(tpl["id"])
    rows = db_qall(
        "SELECT op_code FROM circuit_operations "
        "WHERE template_id=%s AND enabled=true "
        "ORDER BY sort_order",
        (tid,),
    )
    ops = [str(r.get("op_code")) for r in (rows or []) if r.get("op_code")]
    return tid, ops, tpl.get("name")


def _score_template_similarity(
    template_ops: set[str],
    circuit: dict[str, Any],
    leach_type: str | None,
) -> float:
    c_ops = _normalize_ops(circuit.get("ops") or [])
    if not c_ops:
        return 0.0

    inter = len(template_ops & c_ops)
    union = len(template_ops | c_ops) or 1
    jacc = inter / union

    score = jacc
    has_hpgr_tpl = "HPGR" in template_ops
    has_hpgr_c = bool(circuit.get("has_hpgr"))
    if has_hpgr_tpl == has_hpgr_c:
        score += 0.12

    has_flot_tpl = "FLOTATION" in template_ops
    has_flot_c = bool(circuit.get("has_flotation"))
    if has_flot_tpl == has_flot_c:
        score += 0.10

    if leach_type == "CIP" and circuit.get("is_cip"):
        score += 0.10
    if leach_type == "CIL" and not circuit.get("is_cip"):
        score += 0.06

    return score


def _build_tradeoff_ids(
    ore: dict[str, Any],
    template_ops: list[str],
    leach_type: str | None,
) -> list[str]:
    picked: list[str] = []
    seen: set[str] = set()

    norm_tpl = _normalize_ops(template_ops)
    if norm_tpl:
        ranked = sorted(
            CIRCUITS,
            key=lambda c: _score_template_similarity(norm_tpl, c, leach_type),
            reverse=True,
        )
        for c in ranked:
            cid = c["id"]
            if cid in seen:
                continue
            if _score_template_similarity(norm_tpl, c, leach_type) <= 0:
                continue
            picked.append(cid)
            seen.add(cid)
            if len(picked) >= 5:
                break

    four, _ = select_four_circuits(ore, leach_type=leach_type)
    for c in four:
        cid = c["id"]
        if cid not in seen:
            picked.append(cid)
            seen.add(cid)
        if len(picked) >= 5:
            break

    if len(picked) < 5:
        valid, _ = filter_circuits(ore)
        ranked_valid = sorted(valid, key=lambda c: c.get("capex_factor", 1.0), reverse=True)
        for c in ranked_valid:
            cid = c["id"]
            if cid in seen:
                continue
            picked.append(cid)
            seen.add(cid)
            if len(picked) >= 5:
                break

    if len(picked) < 5:
        for c in CIRCUITS:
            cid = c["id"]
            if cid in seen:
                continue
            picked.append(cid)
            seen.add(cid)
            if len(picked) >= 5:
                break

    return picked[:5]


def _met_note(circuit: dict[str, Any], template_ops: set[str]) -> str:
    ops = _normalize_ops(circuit.get("ops") or [])
    overlap = sorted(template_ops & ops)
    overlap_txt = ", ".join(overlap[:4]) if overlap else "aucune"
    hpgr = "HPGR" if circuit.get("has_hpgr") else "sans HPGR"
    flot = "avec flottation" if circuit.get("has_flotation") else "sans flottation"
    return f"Compatibilité template: {overlap_txt}. Variante {hpgr}, {flot}."


def _metallurgical_pick(
    scored: list[dict[str, Any]],
    ore: dict[str, Any],
    cip_cil: dict[str, Any] | None,
) -> dict[str, Any]:
    by_id = {s["id"]: s for s in scored}
    leach = (cip_cil or {}).get("circuit_type")
    s_pct = float(ore.get("s_total_pct") or 0)
    grg = float(ore.get("grg_pct") or 0)
    leach_rec = float(ore.get("leach_recovery_pct") or 0)
    flot_rec = float(ore.get("flot_recovery_pct") or 0)
    bwi = float(ore.get("bwi") or 14)
    rationale: list[str] = []

    target_ids = [s["id"] for s in scored]
    if flot_rec >= 70 or s_pct >= 2:
        target_ids = [i for i in target_ids if _CIRCUIT_BY_ID.get(i, {}).get("has_flotation")]
        rationale.append("Profil sulfuré/flottable: circuits avec flottation priorisés.")
    elif leach_rec >= 85 and s_pct < 1.5:
        target_ids = [i for i in target_ids if not _CIRCUIT_BY_ID.get(i, {}).get("has_flotation")]
        rationale.append("Lixiviation directe élevée: routes sans flottation favorisées.")

    if bwi > 16:
        hpgr_ids = [i for i in target_ids if _CIRCUIT_BY_ID.get(i, {}).get("has_hpgr")]
        if hpgr_ids:
            target_ids = hpgr_ids
            rationale.append(f"BWi {bwi:.1f} kWh/t: variantes HPGR favorisées.")

    if leach == "CIP":
        cip_ids = [i for i in target_ids if _CIRCUIT_BY_ID.get(i, {}).get("is_cip")]
        if cip_ids:
            target_ids = cip_ids
            rationale.append("LIMS/Stange: CIP recommandé.")

    if grg >= 10:
        rationale.append(f"GRG {grg:.1f}%: la gravité est à conserver en amont.")

    if not target_ids:
        target_ids = [s["id"] for s in scored]

    ranked = sorted((by_id[i] for i in target_ids if i in by_id), key=lambda x: x["npv_musd"], reverse=True)
    pick = ranked[0] if ranked else (scored[0] if scored else None)
    return {
        "circuit": pick,
        "circuit_id": pick["id"] if pick else None,
        "rationale": rationale,
    }


def _recommendation_payload(
    ore: dict[str, Any],
    cip_cil: dict[str, Any] | None,
) -> dict[str, Any]:
    leach_type = (cip_cil or {}).get("circuit_type")
    four, profile_label = select_four_circuits(ore, leach_type=leach_type)
    if len(four) < 4:
        four, profile_label = select_four_circuits(ore, leach_type=None)
    if len(four) < 4:
        four = CIRCUITS[:4]

    scored = score_circuits(four, ore)
    for s in scored:
        s["warnings"] = _compatibility_notes(_CIRCUIT_BY_ID.get(s["id"], {}), ore)

    viable = [s for s in scored if not s.get("warnings")]
    ranked = viable if viable else scored
    ranked.sort(key=lambda x: x["npv_musd"], reverse=True)
    recommended = ranked[0] if ranked else None
    if recommended:
        recommended["status"] = "Recommandé"
    for s in scored:
        if recommended and s["id"] == recommended["id"]:
            continue
        s["status"] = "Écarté (règles métallurgiques)" if s.get("warnings") else "Alternative"
        if s.get("is_pareto") and not s.get("warnings"):
            s["status"] = "Pareto"

    _, all_filtered = filter_circuits(ore)
    other_filtered = [
        {"id": f["id"], "name": f["name"], "reason": f["reasons"][0]}
        for f in all_filtered
        if f["id"] not in {c["id"] for c in four}
    ]

    return {
        "recommended": recommended,
        "candidates": scored,
        "evaluated_count": len(scored),
        "evaluation_profile": profile_label,
        "filtered_out": other_filtered,
        "ore_profile": ore,
        "justification": generate_justification(recommended, ore) if recommended else "",
        "comparison_summary": generate_comparison_summary(recommended, scored, ore) if recommended else "",
        "data_sources": {
            "lims": True,
            "block_model": bool(ore.get("bm_available")),
            "design_criteria": bool(ore.get("dc_available")),
            "economics": bool(ore.get("economics_available")),
        },
        "cip_cil": cip_cil,
    }


def _tradeoff_payload(
    ore: dict[str, Any],
    cip_cil: dict[str, Any] | None,
    template_ops: list[str],
) -> dict[str, Any]:
    leach_type = (cip_cil or {}).get("circuit_type")
    tradeoff_ids = _build_tradeoff_ids(ore, template_ops, leach_type)
    circuits = [_CIRCUIT_BY_ID[i] for i in tradeoff_ids if i in _CIRCUIT_BY_ID]
    scored = score_circuits(circuits, ore)
    template_norm = _normalize_ops(template_ops)

    for s in scored:
        cid = s["id"]
        raw = _CIRCUIT_BY_ID.get(cid, {})
        s["warnings"] = _compatibility_notes(raw, ore)
        s["metallurgical_note"] = _met_note(raw, template_norm)
        s["flowsheet_actuel"] = bool(template_norm and _normalize_ops(raw.get("ops") or []) == template_norm)
        s["tradeoff_group"] = "template_match" if s["flowsheet_actuel"] else ("hpgr" if raw.get("has_hpgr") else "baseline")

    npv_ranked = sorted(scored, key=lambda x: x["npv_musd"], reverse=True)
    recommended = npv_ranked[0] if npv_ranked else None
    if recommended:
        recommended["status"] = "NPV max (proxy)"
    for s in npv_ranked[1:]:
        s["status"] = "Alternative"

    meta = _metallurgical_pick(npv_ranked, ore, cip_cil)
    if meta.get("circuit"):
        meta["circuit"]["metallurgical_pick"] = True

    summary = generate_comparison_summary(recommended, npv_ranked, ore) if recommended else ""
    return {
        "kind": "circuit_tradeoff",
        "circuit_ids": tradeoff_ids,
        "recommended": recommended,
        "metallurgical_recommendation": meta,
        "candidates": npv_ranked,
        "ore_profile": ore,
        "cip_cil": cip_cil,
        "comparison_summary": summary,
        "evaluated_count": len(npv_ranked),
    }


def analyze_circuit_strategy(pid: str, db_qall, db_qone) -> dict[str, Any]:
    ore = extract_ore_profile(pid, db_qall, db_qone)
    try:
        cip_cil = recommend_cip_cil_from_lims(pid, db_qall, db_qone)
    except Exception:
        logger.warning("cip_cil failed in strategy for %s", pid, exc_info=True)
        cip_cil = None

    template_id, template_ops, template_name = _load_active_template(pid, db_qall, db_qone)
    if not template_ops:
        return {
            "kind": "circuit_strategy",
            "project_id": pid,
            "scenario_source": {
                "mode": "empty_project",
                "template_id": template_id,
                "template_name": template_name,
                "template_operations": [],
            },
            "ore_profile": ore,
            "cip_cil": cip_cil,
            "recommendation": {
                "project_id": pid,
                "recommended": None,
                "candidates": [],
                "evaluated_count": 0,
                "filtered_out": [],
                "ore_profile": ore,
                "justification": "",
                "comparison_summary": "",
                "data_sources": {
                    "lims": False,
                    "block_model": bool(ore.get("bm_available")),
                    "design_criteria": bool(ore.get("dc_available")),
                    "economics": bool(ore.get("economics_available")),
                },
                "cip_cil": cip_cil,
            },
            "tradeoff": {
                "kind": "circuit_tradeoff",
                "project_id": pid,
                "circuit_ids": [],
                "recommended": None,
                "metallurgical_recommendation": {"circuit": None, "circuit_id": None, "rationale": []},
                "candidates": [],
                "ore_profile": ore,
                "cip_cil": cip_cil,
                "comparison_summary": "",
                "evaluated_count": 0,
            },
        }

    source_mode = "template_active"

    recommendation = _recommendation_payload(ore, cip_cil)
    tradeoff = _tradeoff_payload(ore, cip_cil, template_ops)
    recommendation["project_id"] = pid
    tradeoff["project_id"] = pid

    return {
        "kind": "circuit_strategy",
        "project_id": pid,
        "scenario_source": {
            "mode": source_mode,
            "template_id": template_id,
            "template_name": template_name,
            "template_operations": template_ops,
        },
        "ore_profile": ore,
        "cip_cil": cip_cil,
        "recommendation": recommendation,
        "tradeoff": tradeoff,
    }
