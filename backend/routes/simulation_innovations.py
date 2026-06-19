"""
MPDPMS — Innovations endpoints for the redesigned Simulation module.

Endpoints:
  GET  /api/v1/projects/{pid}/simulation/digital-twin-fidelity       → unified twin readiness score (0–100)
  GET  /api/v1/projects/{pid}/simulation/next-actions                 → 3-5 contextual cards
  POST /api/v1/projects/{pid}/flowsheet/ai-suggest                    → LLM flowsheet recommendation (#1)
  GET  /api/v1/projects/{pid}/simulation/runs/{run_id}/gradient       → gradients for what-if (mocked v1, #2)
  GET  /api/v1/projects/{pid}/simulation/runs/diff?a=...&b=...        → run-vs-run diff (#3)
  GET  /api/v1/projects/{pid}/simulation/runs/{run_id}/bottlenecks    → bottleneck explainer (#4)
  GET  /api/v1/projects/{pid}/simulation/runs/{run_id}/node-outputs   → calculated metrics per node
"""
from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field as _Field, UUID4

try:
    from ..auth import project_user
    from ..db import qone, qall, execute
    from ..flowsheet_templates import TEMPLATES, get_template_by_code
except ImportError:  # pragma: no cover
    from auth import project_user
    from db import qone, qall, execute
    from flowsheet_templates import TEMPLATES, get_template_by_code


router = APIRouter(prefix="/api/v1/projects", tags=["simulation-innovations"])
logger = logging.getLogger("mpdpms.simulation_innovations")


# ════════════════════════════════════════════════════════════════════════════
# Helper: fetch a snapshot of project state used by next-actions and AI suggest
# ════════════════════════════════════════════════════════════════════════════

def _project_state_snapshot(pid: str) -> dict:
    proj = qone("SELECT * FROM projects WHERE id=%s", (pid,)) or {}
    tpl = qone(
        "SELECT id, updated_at FROM circuit_templates WHERE project_id=%s "
        "ORDER BY updated_at DESC NULLS LAST LIMIT 1",
        (pid,),
    )
    has_flowsheet = tpl is not None
    nodes = qall(
        "SELECT id, op_code, parent_op_id, product_kind, recovery_pct, throughput_tph, "
        "       water_m3h, grade_au_gt, values_source, equipment_id "
        "FROM circuit_template_operations WHERE template_id=%s",
        (tpl["id"],) if tpl else (None,),
    ) if has_flowsheet else []

    has_bullion = any(n.get("product_kind") == "bullion" for n in nodes)
    nodes_missing_lims = sum(
        1 for n in nodes
        if n.get("recovery_pct") is None and n.get("values_source") in (None, "lims_auto")
    )
    nodes_unlinked = sum(1 for n in nodes if n.get("equipment_id") is None)

    last_run = qone(
        "SELECT id, status, results, created_at FROM simulation_runs_v2 WHERE project_id=%s "
        "ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )

    lims_count = (qone(
        "SELECT COUNT(*) AS n FROM lims_flotation WHERE project_id=%s",
        (pid,),
    ) or {}).get("n", 0)

    lims_samples = 0
    try:
        lims_samples = int((qone(
            "SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s",
            (pid,),
        ) or {}).get("n", 0))
    except Exception:
        lims_samples = 0

    node_outputs_count = 0
    try:
        if last_run and last_run.get("id"):
            node_outputs_count = int((qone(
                "SELECT COUNT(*) AS n FROM simulation_node_outputs WHERE run_id=%s",
                (last_run["id"],),
            ) or {}).get("n", 0))
    except Exception:
        node_outputs_count = 0

    return {
        "project": proj,
        "template_id": tpl["id"] if tpl else None,
        "has_flowsheet": has_flowsheet,
        "node_count": len(nodes),
        "has_bullion": has_bullion,
        "nodes_missing_lims": nodes_missing_lims,
        "nodes_unlinked_equipment": nodes_unlinked,
        "last_run_id": last_run["id"] if last_run else None,
        "last_run_at": last_run.get("created_at") if last_run else None,
        "last_run_status": (str(last_run.get("status") or "").lower()) if last_run else None,
        "last_run_has_results": bool(last_run.get("results")) if last_run else False,
        "lims_flotation_count": lims_count,
        "lims_sample_count": lims_samples,
        "node_outputs_count": node_outputs_count,
    }


def _clamp_score(x: float) -> float:
    return max(0.0, min(100.0, x))


def compute_twin_fidelity_from_snapshot(s: dict) -> dict:
    """Compute the « fidélité du jumeau » index (0–100) from `_project_state_snapshot` output.

    Five pillars — circuit topology, LIMS anchoring, engine runs, equipment linkage,
    and per-node observability — are weighted and aggregated. Intended as a single
    cross-cutting readiness signal for the simulation hub (not a plant historian).
    """
    has_fs = bool(s.get("has_flowsheet")) and int(s.get("node_count") or 0) > 0
    nc = int(s.get("node_count") or 0)
    bull = bool(s.get("has_bullion"))
    if not has_fs:
        topo = 0.0
        topo_detail = "Aucun gabarit de circuit actif ou aucun nœud."
    else:
        topo = _clamp_score(38.0 + (28.0 if bull else 0.0) + min(34.0, nc * 3.4))
        topo_detail = f"{nc} nœud(s), produit final lingot={'oui' if bull else 'non'}."

    miss = int(s.get("nodes_missing_lims") or 0)
    lsm = int(s.get("lims_sample_count") or 0)
    lims_binding = _clamp_score(100.0 - miss * 6.0 + min(12.0, lsm * 0.4))
    lims_detail = f"{miss} nœud(s) sans recovery LIMS déclarée · {lsm} échantillon(s) LIMS."

    st = (s.get("last_run_status") or "").lower()
    has_res = bool(s.get("last_run_has_results"))
    if not s.get("last_run_id"):
        exec_s = 12.0
        exec_detail = "Aucune exécution du moteur enregistrée."
    elif st in ("completed", "done", "success") and has_res:
        exec_s = 100.0
        exec_detail = "Dernière simulation terminée avec résultats persistés."
    elif st in ("completed", "done", "success"):
        exec_s = 72.0
        exec_detail = "Simulation terminée mais résultats incomplets."
    elif st in ("failed", "error"):
        exec_s = 22.0
        exec_detail = "Dernière simulation en échec — revoir paramètres ou circuit."
    else:
        exec_s = 48.0
        exec_detail = f"Dernier statut moteur : {st or 'inconnu'}."

    nu = int(s.get("nodes_unlinked_equipment") or 0)
    if nc <= 0:
        equip = 55.0
        equip_detail = "Pas de nœuds — équipements non applicables."
    else:
        equip = _clamp_score(100.0 * (1.0 - min(1.0, nu / max(1, nc))))
        equip_detail = f"{nu} / {nc} nœud(s) sans équipement lié."

    noc = int(s.get("node_outputs_count") or 0)
    if not s.get("last_run_id"):
        obs = 10.0
        obs_detail = "Aucun run — pas de métriques nœud."
    elif has_res and noc > 0:
        obs = _clamp_score(28.0 + min(72.0, noc * 1.8))
        obs_detail = f"{noc} métrique(s) nœud matérialisée(s) sur le dernier run."
    elif has_res:
        obs = 38.0
        obs_detail = "Run avec résultats mais peu de métriques nœud détaillées."
    else:
        obs = 18.0
        obs_detail = "Exécution sans détail nœud exploitable pour diagnostics."

    w_topo, w_lims, w_exec, w_equip, w_obs = 0.22, 0.22, 0.26, 0.15, 0.15
    score = round(
        w_topo * topo + w_lims * lims_binding + w_exec * exec_s + w_equip * equip + w_obs * obs,
        1,
    )

    grade = "A" if score >= 85 else ("B" if score >= 70 else ("C" if score >= 50 else "D"))

    factors = [
        {
            "id": "topology",
            "label": "Circuit & produit final",
            "score": round(topo, 1),
            "weight": w_topo,
            "detail": topo_detail,
        },
        {
            "id": "lims_binding",
            "label": "Ancrage données LIMS",
            "score": round(lims_binding, 1),
            "weight": w_lims,
            "detail": lims_detail,
        },
        {
            "id": "execution",
            "label": "Exécutions moteur",
            "score": round(exec_s, 1),
            "weight": w_exec,
            "detail": exec_detail,
        },
        {
            "id": "equipment",
            "label": "Lien équipements",
            "score": round(equip, 1),
            "weight": w_equip,
            "detail": equip_detail,
        },
        {
            "id": "observability",
            "label": "Observabilité nœud",
            "score": round(obs, 1),
            "weight": w_obs,
            "detail": obs_detail,
        },
    ]

    hints: list[str] = []
    sorted_f = sorted(factors, key=lambda f: float(f["score"]))
    if sorted_f and float(sorted_f[0]["score"]) < 50:
        hints.append(f"Pilier prioritaire : {sorted_f[0]['label']} — {sorted_f[0]['detail']}")
    if not has_fs:
        hints.append("Définissez un gabarit actif avec un chemin jusqu'au lingot pour ancrer les KPIs.")
    elif not bull:
        hints.append("Marquez la feuille « Lingot » pour verrouiller la cohérence NPV / AISC sur le circuit.")
    if miss >= 4:
        hints.append("Importez LIMS (flottation, broyage) pour réduire l'écart modèle vs laboratoire.")
    if st in ("failed", "error"):
        hints.append("Relancez une simulation globale après correction du compile ou des paramètres d'alimentation.")
    hints = hints[:5]

    return {
        "score": score,
        "grade": grade,
        "factors": factors,
        "hints": hints,
        "meta": {
            "method": "weighted_pillars_v1",
            "description": (
                "Indice transverse : circuit, données LIMS, exécutions moteur, "
                "équipements et métriques nœud — même lecture sur tout le hub Simulation."
            ),
        },
    }


@router.get("/{pid}/simulation/digital-twin-fidelity")
def get_digital_twin_fidelity(pid: str, user=Depends(project_user)):
    """Unified digital-twin readiness score for the Simulation hub."""
    snap = _project_state_snapshot(pid)
    return compute_twin_fidelity_from_snapshot(snap)


# ════════════════════════════════════════════════════════════════════════════
# 1. GET /simulation/next-actions — adaptive panel (rules engine)
# ════════════════════════════════════════════════════════════════════════════

@router.get("/{pid}/simulation/next-actions")
def get_next_actions(pid: str, user=Depends(project_user)):
    s = _project_state_snapshot(pid)
    cards: list[dict] = []

    # Rule 1 — no flowsheet
    if not s["has_flowsheet"] or s["node_count"] == 0:
        cards.append({
            "id": "build_flowsheet",
            "priority": 1,
            "icon": "🌳",
            "title": "Construire le flowsheet du projet",
            "description": "Aucun cheminement défini pour ce projet. Choisissez un modèle parmi 28 cas industriels ou laissez l'IA suggérer.",
            "actions": [
                {"label": "Choisir un modèle", "type": "open_template_picker"},
                {"label": "✨ Suggérer (IA)",   "type": "open_ai_suggest"},
            ],
            "severity": "warning",
        })
        return {"cards": cards}

    # Rule 2 — no bullion leaf
    if not s["has_bullion"]:
        cards.append({
            "id": "mark_bullion",
            "priority": 2,
            "icon": "🟡",
            "title": "Marquer le nœud Lingot",
            "description": "Aucune feuille du flowsheet n'est marquée comme produit final « Lingot ». Sans cela, les KPIs ne peuvent pas être calculés.",
            "actions": [
                {"label": "Marquer maintenant", "type": "open_first_leaf_drawer"},
            ],
            "severity": "warning",
        })

    # Rule 3 — bottleneck (placeholder — innovation #4 logic)
    if s["last_run_id"]:
        cards.append({
            "id": "bottleneck",
            "priority": 3,
            "icon": "⚠️",
            "title": "Goulot identifié (analyse en cours)",
            "description": "Ouvrez le détail des bottlenecks pour voir l'explication en langage naturel et la recommandation chiffrée.",
            "actions": [
                {"label": "Voir l'analyse", "type": "open_bottlenecks", "run_id": str(s["last_run_id"])},
            ],
            "severity": "info",
        })

    # Rule 4 — no run
    if not s["last_run_id"]:
        cards.append({
            "id": "first_run",
            "priority": 4,
            "icon": "▶️",
            "title": "Lancer la première simulation",
            "description": "Un flowsheet est défini mais aucune simulation n'a été exécutée. Lancez le moteur pour calculer les KPIs.",
            "actions": [
                {"label": "Lancer maintenant", "type": "trigger_simulation"},
            ],
            "severity": "info",
        })

    # Rule 5 — LIMS missing on many nodes
    if s["nodes_missing_lims"] >= 4:
        cards.append({
            "id": "import_lims",
            "priority": 5,
            "icon": "🧪",
            "title": f"{s['nodes_missing_lims']} nœuds sans données LIMS",
            "description": "Importez des données LIMS (flottation, kinetics) pour affiner les recoveries et obtenir des résultats projet-spécifiques.",
            "actions": [
                {"label": "Aller au module LIMS", "type": "navigate", "section": "lims"},
            ],
            "severity": "info",
        })

    # Rule 6 — equipment unlinked
    if s["nodes_unlinked_equipment"] >= 1 and s["nodes_unlinked_equipment"] < s["node_count"]:
        cards.append({
            "id": "link_equipment",
            "priority": 6,
            "icon": "🔗",
            "title": f"{s['nodes_unlinked_equipment']} nœud(s) sans équipement lié",
            "description": "Liez chaque nœud à un équipement concret du module Équipement pour bénéficier des spécifications réelles (puissance, capacité).",
            "actions": [
                {"label": "Lier maintenant", "type": "open_first_unlinked"},
            ],
            "severity": "info",
        })

    # Rule 7 — recent run, suggest comparison
    if s["last_run_id"]:
        target_tph = s["project"].get("target_tph")
        suggested_tph = round(float(target_tph) * 1.1) if target_tph else None
        if suggested_tph:
            cards.append({
                "id": "compare_scenario",
                "priority": 7,
                "icon": "📊",
                "title": f"Comparer un scénario à {suggested_tph} t/h",
                "description": f"Tester la capacité à +10% ({suggested_tph} t/h vs {int(target_tph)} t/h actuel).",
                "actions": [
                    {"label": "Lancer la comparaison", "type": "open_diff_compose"},
                ],
                "severity": "info",
            })

    # Rule 9 — export available
    if s["last_run_id"]:
        cards.append({
            "id": "export_ni43101",
            "priority": 9,
            "icon": "📄",
            "title": "Exporter ce run vers le rapport NI 43-101",
            "description": "Le run actuel peut être attaché au rapport NI 43-101 (sections 17-18).",
            "actions": [
                {"label": "Exporter", "type": "trigger_export", "run_id": str(s["last_run_id"])},
            ],
            "severity": "success",
        })

    # Cap at 5 cards, by priority ascending
    cards.sort(key=lambda c: c["priority"])
    return {"cards": cards[:5]}


# ════════════════════════════════════════════════════════════════════════════
# 2. POST /flowsheet/ai-suggest — LLM-assisted flowsheet recommendation (#1)
# ════════════════════════════════════════════════════════════════════════════

def _ai_rule_based_fallback(s: dict) -> dict:
    """Deterministic fallback when Claude API is not available.

    Heuristic: pick a template based on project signals.
    """
    proj = s["project"]
    grade = float(proj.get("gold_grade_g_t") or 0)
    code = "AU_CIL_OXIDE"  # default

    # Rough rules — placeholder until Claude integration is fully tested.
    if grade < 0.5:
        code = "HEAP_OXIDE_STD"
    elif grade > 5:
        code = "AU_GRAVITY_CIL"
    elif (proj.get("commodity") or "").lower() in ("au-cu", "cu-au"):
        code = "AU_CU_PORPHYRY"

    tpl = get_template_by_code(code)
    return {
        "suggested_template": code,
        "rationale": (
            f"Heuristique : grade {grade} g/t et commodité '{proj.get('commodity')}' suggèrent "
            f"le template {tpl['name']}. (LLM indisponible — fallback déterministe.)"
        ),
        "modifications": [],
        "alternatives_considered": [t["code"] for t in TEMPLATES if t["code"] != code][:3],
        "alternatives_rationale": "Alternatives écartées par règle simple.",
        "source": "rule_based_fallback",
    }


@router.post("/{pid}/flowsheet/ai-suggest")
def ai_suggest_flowsheet(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    s = _project_state_snapshot(pid)

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback deterministic
        return _ai_rule_based_fallback(s)

    # Import lazily to avoid cost when LLM unused
    try:
        from anthropic import Anthropic
    except ImportError:
        return _ai_rule_based_fallback(s)

    proj = s["project"]
    catalog_summary = ", ".join(t["code"] for t in TEMPLATES)
    user_prompt = f"""Tu es un ingénieur métallurgiste senior. Pour ce projet aurifère, recommande le meilleur template de flowsheet parmi : {catalog_summary}.

Contexte projet :
- Commodité : {proj.get('commodity', 'Au')}
- Grade Au tête : {proj.get('gold_grade_g_t')} g/t
- Débit cible : {proj.get('target_tph')} t/h
- Statut : {proj.get('status')}
- Description : {proj.get('description') or '(aucune)'}

Réponds en JSON strict :
{{
  "suggested_template": "<CODE>",
  "rationale": "2 phrases max",
  "modifications": ["..."],
  "alternatives_considered": ["...", "..."]
}}"""

    try:
        client = Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = msg.content[0].text if msg.content else "{}"
        # Strip code fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        import json as _json
        data = _json.loads(text.strip())
        data["source"] = "claude"
        # Validate template exists
        if not get_template_by_code(data.get("suggested_template", "")):
            data["suggested_template"] = "AU_CIL_OXIDE"
        return data
    except Exception as e:  # intentional: graceful fallback on optional operation
        # Log the raw model output (truncated) so a future debugger can see why parsing
        # failed without leaking secrets or bloating logs.
        text_preview = (locals().get('text') or '')[:500]
        logger.warning("AI suggest failed: %s — body[:500]=%r — falling back to rules", e, text_preview)
        return _ai_rule_based_fallback(s)


# ════════════════════════════════════════════════════════════════════════════
# 3. GET /runs/{run_id}/gradient — what-if live (mocked v1, #2)
# ════════════════════════════════════════════════════════════════════════════

@router.get("/{pid}/simulation/runs/{run_id}/gradient")
def get_gradient(pid: str, run_id: str, user=Depends(project_user)):
    """Gradients ∂KPI/∂param for the what-if live mode. Mocked in v1."""
    # Verify the run belongs to the project
    run = qone(
        "SELECT id FROM simulation_runs_v2 WHERE id=%s AND project_id=%s",
        (run_id, pid),
    )
    if not run:
        raise HTTPException(404, "Run not found")

    # MOCK: realistic plausible gradients. Real engine produces these in v2.
    # Format: gradients[param_key][kpi_name] = ∂KPI/∂param
    return {
        "is_mock": True,
        "baseline": {
            "recovery_pct": 89.3, "production_oz_h": 24.5,
            "npv_musd": 342, "aisc_per_oz": 1050,
        },
        "gradients": {
            "feed_tph":      {"production_oz_h": 0.012, "npv_musd": 0.18},
            "head_grade_au": {"production_oz_h": 22.3,  "npv_musd": 312},
            "flot_rec_au":   {"recovery_pct": 0.85,    "production_oz_h": 0.22, "npv_musd": 4.2},
            "cil_rec_au":    {"recovery_pct": 0.78,    "production_oz_h": 0.21, "npv_musd": 4.0},
            "grav_rec_au":   {"recovery_pct": 0.10,    "production_oz_h": 0.03, "npv_musd": 0.5},
            "gold_price":    {"npv_musd": 0.92,        "aisc_per_oz": -0.43},
            "discount_rate": {"npv_musd": -42.5},
            "opex_per_tonne":{"aisc_per_oz": 49.2,     "npv_musd": -15.4},
        },
        "validity_range_pct": 25,  # linear approximation valid within ±25% of baseline
    }


# ════════════════════════════════════════════════════════════════════════════
# 4. GET /runs/diff?a=...&b=... — run vs run (#3)
# ════════════════════════════════════════════════════════════════════════════

@router.get("/{pid}/simulation/runs/diff")
def diff_runs(pid: str, a: str, b: str, user=Depends(project_user)):
    run_a = qone("SELECT id, params FROM simulation_runs_v2 WHERE id=%s AND project_id=%s", (a, pid))
    run_b = qone("SELECT id, params FROM simulation_runs_v2 WHERE id=%s AND project_id=%s", (b, pid))
    if not run_a or not run_b:
        raise HTTPException(404, "One of the runs not found")

    # Real implementation reads node_outputs and topology snapshots.
    # MOCK in v1 — returns plausible deltas.
    return {
        "is_mock": True,
        "run_a": str(a), "run_b": str(b),
        "param_diffs": [
            {"param": "flot_rec_au",   "from": 88.0, "to": 92.0, "delta": "+4.5%"},
            {"param": "cil_rec_au",    "from": 97.3, "to": 97.3, "delta": "—"},
            {"param": "feed_tph",      "from": 1595, "to": 1700, "delta": "+6.6%"},
        ],
        "topology_diffs": {
            "added":   [{"label": "Cleaner column"}],
            "removed": [],
            "moved":   [],
        },
        "kpi_diffs": {
            "recovery_pct":      {"from": 89.3,  "to": 91.5,  "delta": "+2.2"},
            "production_oz_y":   {"from": 215000,"to": 220000,"delta": "+5000"},
            "npv_musd":          {"from": 342,   "to": 358,   "delta": "+16"},
            "aisc_per_oz":       {"from": 1050,  "to": 1042,  "delta": "−8"},
        },
    }


# ════════════════════════════════════════════════════════════════════════════
# 5. GET /runs/{run_id}/bottlenecks — explainer (#4)
# ════════════════════════════════════════════════════════════════════════════

def _bottleneck_rule_based(pid: str, run_id: str) -> list[dict]:
    """Deterministic bottleneck detection without LLM."""
    # Read flowsheet nodes for this project
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE project_id=%s ORDER BY updated_at DESC LIMIT 1",
        (pid,),
    )
    if not tpl:
        return []
    nodes = qall(
        "SELECT id, op_code, node_label, recovery_pct, throughput_tph, product_kind "
        "FROM circuit_template_operations WHERE template_id=%s",
        (tpl["id"],),
    ) or []

    # Score = (target - actual) on Recovery (target = 92% by default)
    target = 92.0
    bottlenecks: list[dict] = []
    for n in nodes:
        r = n.get("recovery_pct")
        if r is None:
            continue
        delta = target - float(r)
        if delta > 2:
            label = n.get("node_label") or n.get("op_code")
            bottlenecks.append({
                "node_id": str(n["id"]),
                "label": label,
                "score": round(delta, 2),
                "severity": "high" if delta > 5 else "medium",
                "explanation": (
                    f"Le {label} est sous-performant : Recovery {r}% < cible {target}% "
                    f"(écart {delta:.1f} points). C'est un goulot potentiel."
                ),
                "recommendation": (
                    f"Augmenter la performance de cette opération ou ajouter un nœud aval "
                    f"pour récupérer le matériel perdu."
                ),
                "estimated_impact": {
                    "recovery_delta_pct": round(delta * 0.6, 2),
                    "npv_delta_musd": round(delta * 1.2, 1),
                },
            })
    bottlenecks.sort(key=lambda b: -b["score"])
    return bottlenecks[:3]


@router.get("/{pid}/simulation/runs/{run_id}/bottlenecks")
def get_bottlenecks(pid: str, run_id: str, user=Depends(project_user)):
    # Allow virtual run_id="latest" to use rule-based on current flowsheet
    # without hitting simulation_runs_v2 (id is a uuid, "latest" would error).
    if run_id != "latest":
        run = qone(
            "SELECT id FROM simulation_runs_v2 WHERE id=%s AND project_id=%s",
            (run_id, pid),
        )
        if not run:
            raise HTTPException(404, "Run not found")

    bottlenecks = _bottleneck_rule_based(pid, run_id)
    return {
        "bottlenecks": bottlenecks,
        "source": "rule_based",
        "note": "Explication enrichie par LLM disponible quand le moteur v2 sera connecté.",
    }


# ════════════════════════════════════════════════════════════════════════════
# 6. GET /runs/{run_id}/node-outputs — calculated metrics per node
# ════════════════════════════════════════════════════════════════════════════

@router.get("/{pid}/simulation/runs/{run_id}/node-outputs")
def get_node_outputs(pid: str, run_id: str, user=Depends(project_user)):
    """Return all simulation_node_outputs for the run, indexed by operation_id."""
    rows = qall(
        """
        SELECT sno.operation_id, sno.metric_key, sno.value_num, sno.value_unit
        FROM   simulation_node_outputs sno
        JOIN   simulation_runs_v2 r ON r.id = sno.run_id
        WHERE  sno.run_id=%s AND r.project_id=%s
        """,
        (run_id, pid),
    ) or []
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        op = str(r["operation_id"])
        out.setdefault(op, {})[r["metric_key"]] = {
            "value": float(r["value_num"]) if r["value_num"] is not None else None,
            "unit": r["value_unit"],
        }
    return {"by_operation_id": out}


# ════════════════════════════════════════════════════════════════════════════
# 7. POST /runs/{run_id}/node-outputs — write path for calculated metrics
# ════════════════════════════════════════════════════════════════════════════


class _NodeOutputIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID4
    metric_key:   str = _Field(..., min_length=1, max_length=64)
    value_num:    float | None = None
    value_unit:   str  | None = _Field(default=None, max_length=32)


class _NodeOutputsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[_NodeOutputIn] = _Field(default_factory=list)


@router.post("/{pid}/simulation/runs/{run_id}/node-outputs", status_code=201)
def post_node_outputs(pid: str, run_id: str, body: _NodeOutputsBody, user=Depends(project_user)):
    """Upsert calculated metrics for nodes of a given run.

    Engine convention (see plan pre-flight):
      - Per-node metrics (recovery_pct, power_kw, p80_um, …) are written under
        the actual operation_id of that node.
      - Plant-level metrics (npv_musd, aisc_per_oz, energy_kwh_t) are written
        under the operation_id of the BULLION leaf node (the terminal node
        whose product_kind='bullion'). The frontend resolves that id at
        render time by traversing the tree.
    """
    run = qone(
        "SELECT id FROM simulation_runs_v2 WHERE id=%s AND project_id=%s",
        (run_id, pid),
    )
    if not run:
        raise HTTPException(404, "Run not found")
    n = 0
    for m in body.metrics:
        # Defense-in-depth: the operation must belong to a circuit_template of this project
        op = qone(
            """
            SELECT cto.id FROM circuit_template_operations cto
            JOIN circuit_templates ct ON ct.id = cto.template_id
            WHERE cto.id = %s AND ct.project_id = %s
            """,
            (str(m.operation_id), pid),
        )
        if not op:
            raise HTTPException(400, f"operation_id {m.operation_id} not in project")
        execute(
            """
            INSERT INTO simulation_node_outputs (run_id, operation_id, metric_key, value_num, value_unit)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (run_id, operation_id, metric_key) DO UPDATE
              SET value_num = EXCLUDED.value_num,
                  value_unit = EXCLUDED.value_unit,
                  computed_at = NOW()
            """,
            (run_id, str(m.operation_id), m.metric_key, m.value_num, m.value_unit),
        )
        n += 1
    return {"inserted_or_updated": n}
