"""
MetalFlow Pro — AI Metallurgical Assistant.

Hybrid assistant:
- Local mode: answers project questions by querying modules directly
- LLM mode: proxies to Claude/Anthropic API with project context

Handles predefined intents (production, recovery, OPEX, risks, etc.)
and falls back to LLM for open-ended questions.
"""
from __future__ import annotations

import logging

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM

try:
    from ..settings import get_settings
except ImportError:
    from settings import get_settings

logger = logging.getLogger("mpdpms.assistant")


def build_assistant_metadata(pid: str, intent: str | None, source: str) -> dict:
    """Return provenance and safety metadata for assistant responses."""
    project_base = f"/projects/{pid}"
    intent_path = f"{project_base}/{intent}" if intent else project_base

    citations = [
        {
            "label": "Dossier projet",
            "path": project_base,
            "kind": "project",
        }
    ]
    if intent:
        citations.insert(
            0,
            {
                "label": f"Module {intent}",
                "path": intent_path,
                "kind": "module",
            },
        )

    if source == "llm":
        citations = [
            {"label": "Contexte projet", "path": project_base, "kind": "project"},
            {"label": "Statut et production", "path": f"{project_base}/dashboard", "kind": "module"},
            {"label": "Donnees LIMS", "path": f"{project_base}/lims", "kind": "module"},
        ]

    suggested_actions = [
        {"label": "Ouvrir le projet", "path": project_base},
    ]
    if intent:
        suggested_actions.insert(0, {"label": f"Verifier {intent}", "path": intent_path})

    limitations = (
        "Reponse locale fondee sur les donnees applicatives disponibles; validation QP / Qualified Person requise "
        "avant usage technique formel."
    )
    if source == "llm":
        limitations = (
            "Reponse assistee par IA a partir du contexte projet; les calculs et recommandations doivent etre "
            "revus par une personne qualifiee."
        )
    elif source == "fallback":
        limitations = (
            "Mode local/fallback: ANTHROPIC_API_KEY absent ou intention non reconnue; reponse limitee aux sujets "
            "preconfigures."
        )

    return {
        "citations": citations,
        "suggested_actions": suggested_actions,
        "limitations": limitations,
    }


def _assistant_result(pid: str, response: str, source: str, intent: str | None) -> dict:
    """Build the public assistant response contract."""
    result = {"response": response, "source": source, "intent": intent}
    result.update(build_assistant_metadata(pid, intent, source))
    return result


# ─── Predefined Intents ─────────────────────────────────────────────────────

INTENTS = {
    "production": {
        "keywords": ["production", "oz", "onces", "annuel", "annual", "gold output", "koz"],
        "query": "production",
    },
    "recovery": {
        "keywords": ["recovery", "recuperation", "récupération", "extraction", "rendement"],
        "query": "recovery",
    },
    "opex": {
        "keywords": ["opex", "operating cost", "cout operatoire", "coût opératoire", "$/t"],
        "query": "opex",
    },
    "capex": {
        "keywords": ["capex", "capital", "investissement", "equipment cost", "équipement"],
        "query": "capex",
    },
    "npv": {
        "keywords": ["npv", "van", "valeur actuelle", "irr", "tri", "rendement", "dcf"],
        "query": "npv",
    },
    "risks": {
        "keywords": ["risque", "risk", "critique", "bloquant", "gate"],
        "query": "risks",
    },
    "lims": {
        "keywords": ["lims", "échantillon", "echantillon", "sample", "assay", "test", "grade", "teneur"],
        "query": "lims",
    },
    "circuit": {
        "keywords": ["circuit", "flowsheet", "recommand", "optimis", "quel circuit"],
        "query": "circuit",
    },
    "water": {
        "keywords": ["eau", "water", "bilan eau", "water balance", "consommation eau"],
        "query": "water",
    },
    "status": {
        "keywords": ["status", "statut", "avancement", "progress", "overview", "resume", "résumé"],
        "query": "status",
    },
}


def detect_intent(message: str) -> str | None:
    """Detect the user's intent from their message."""
    try:
        msg_lower = message.lower()
        best_match = None
        best_score = 0
        for intent_id, intent in INTENTS.items():
            score = sum(1 for kw in intent["keywords"] if kw in msg_lower)
            if score > best_score:
                best_score = score
                best_match = intent_id
        return best_match if best_score > 0 else None
    except Exception as e:
        logger.error("detect_intent failed for message='%s': %s", message[:100], e)
        return None


# ─── Local Query Handlers ────────────────────────────────────────────────────

def _query_production(pid: str, db_qone, db_qall) -> str:
    try:
        p = db_qone("SELECT * FROM projects WHERE id=%s", (pid,))
        if not p:
            return "Projet introuvable."
        tph = float(p.get("target_tph") or 0)
        grade = float(p.get("gold_grade_g_t") or 0)
        avail = float(p.get("availability_pct") or 92) / 100
        op_h = float(p.get("operating_hours_day") or 22)
        gold_price = float(p.get("gold_price_usd_oz") or 2340)

        rec_row = db_qone(
            "SELECT AVG(au_recovery_pct) as avg FROM lims_d1 WHERE project_id=%s AND au_recovery_pct IS NOT NULL",
            (pid,),
        )
        recovery = float(rec_row["avg"]) / 100 if rec_row and rec_row.get("avg") else 0.89

        annual_t = tph * op_h * 365 * avail
        annual_oz = annual_t * grade * recovery * TROY_OZ_PER_GRAM
        revenue = annual_oz * gold_price

        return (
            f"**Production estimée du projet**\n\n"
            f"- Débit : {tph:.0f} t/h ({annual_t:,.0f} t/an)\n"
            f"- Grade Au : {grade:.2f} g/t\n"
            f"- Récupération : {recovery*100:.1f}%\n"
            f"- **Production annuelle : {annual_oz:,.0f} oz ({annual_oz/1000:.1f} koz)**\n"
            f"- Revenus annuels : ${revenue/1e6:,.1f}M @ ${gold_price:.0f}/oz\n"
            f"- Vie mine : {p.get('mine_life_years', '—')} ans"
        )
    except Exception as e:
        logger.error("_query_production failed for project_id=%s: %s", pid, e)
        return "Erreur lors du calcul de la production."


def _query_recovery(pid: str, db_qone, db_qall) -> str:
    try:
        d1 = db_qall("SELECT au_recovery_pct FROM lims_d1 WHERE project_id=%s AND au_recovery_pct IS NOT NULL", (pid,))
        if not d1:
            return "Aucune donnée de récupération LIMS (D1) disponible. Ajoutez des tests de lixiviation."

        vals = [float(r["au_recovery_pct"]) for r in d1]
        avg = sum(vals) / len(vals)
        mn, mx = min(vals), max(vals)

        c2 = db_qall("SELECT gravity_au_recovery_pct FROM lims_c2 WHERE project_id=%s AND gravity_au_recovery_pct IS NOT NULL", (pid,))
        grg_text = ""
        if c2:
            grg_vals = [float(r["gravity_au_recovery_pct"]) for r in c2]
            grg_avg = sum(grg_vals) / len(grg_vals)
            grg_text = f"\n- GRG (gravité) : {grg_avg:.1f}% (n={len(grg_vals)})"

        return (
            f"**Récupération Au — Données LIMS**\n\n"
            f"- Moyenne lixiviation : **{avg:.1f}%** (n={len(vals)})\n"
            f"- Min : {mn:.1f}% — Max : {mx:.1f}%\n"
            f"- Écart : {mx-mn:.1f} points"
            f"{grg_text}\n\n"
            f"{'✅ Récupération excellente (>90%)' if avg > 90 else '⚠️ Récupération à améliorer (<90%)' if avg < 90 else '✅ Bonne récupération'}"
        )
    except Exception as e:
        logger.error("_query_recovery failed for project_id=%s: %s", pid, e)
        return "Erreur lors de la récupération des données LIMS."


def _query_opex(pid: str, db_qone, db_qall) -> str:
    try:
        mp = float((db_qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_manpower WHERE project_id=%s", (pid,)) or {}).get("t", 0))
        pw = float((db_qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_power WHERE project_id=%s", (pid,)) or {}).get("t", 0))
        rg = float((db_qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_reagents WHERE project_id=%s", (pid,)) or {}).get("t", 0))
        mb = float((db_qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_mobile WHERE project_id=%s", (pid,)) or {}).get("t", 0))
        total = mp + pw + rg + mb

        if total == 0:
            return "Aucune donnée OPEX générée. Allez dans Modèle Économique → OPEX → Générer OPEX."

        p = db_qone("SELECT target_tph, operating_hours_day, availability_pct FROM projects WHERE id=%s", (pid,))
        tph = float(p.get("target_tph") or 913) if p else 913
        avail = float(p.get("availability_pct") or 92) / 100 if p else 0.92
        op_h = float(p.get("operating_hours_day") or 22) if p else 22
        annual_t = tph * op_h * 365 * avail
        per_t = total / annual_t if annual_t > 0 else 0

        return (
            f"**OPEX — Répartition des coûts opératoires**\n\n"
            f"| Catégorie | CAD/an | % |\n|---|---|---|\n"
            f"| Main d'oeuvre | ${mp:,.0f} | {mp/total*100:.0f}% |\n"
            f"| Puissance électrique | ${pw:,.0f} | {pw/total*100:.0f}% |\n"
            f"| Réactifs & consommables | ${rg:,.0f} | {rg/total*100:.0f}% |\n"
            f"| Équipements mobiles | ${mb:,.0f} | {mb/total*100:.0f}% |\n"
            f"| **Total** | **${total:,.0f}** | **100%** |\n\n"
            f"- OPEX unitaire : **{per_t:.2f} $/t**\n"
            f"- Tonnage annuel : {annual_t:,.0f} t/an"
        )
    except Exception as e:
        logger.error("_query_opex failed for project_id=%s: %s", pid, e)
        return "Erreur lors du calcul de l'OPEX."


def _query_capex(pid: str, db_qone, db_qall) -> str:
    try:
        equip = db_qone("SELECT COUNT(*) AS n, COALESCE(SUM(price_cad),0) AS total FROM equipment_v2 WHERE project_id=%s AND enabled=true", (pid,))
        n = int(equip.get("n", 0)) if equip else 0
        total = float(equip.get("total", 0)) if equip else 0

        if n == 0:
            return "Aucun équipement dans le MER. Générez la liste d'équipements depuis le module Équipements."

        return (
            f"**CAPEX — Équipements (MER)**\n\n"
            f"- {n} équipements dans le registre\n"
            f"- **CAPEX total : ${total:,.0f} CAD (${total/1e6:.1f}M)**\n"
        )
    except Exception as e:
        logger.error("_query_capex failed for project_id=%s: %s", pid, e)
        return "Erreur lors du calcul du CAPEX."


def _query_risks(pid: str, db_qone, db_qall) -> str:
    try:
        risks = db_qall("SELECT * FROM risks WHERE project_id=%s ORDER BY criticality DESC", (pid,))
        if not risks:
            return "Aucun risque enregistré. Ajoutez des risques dans le Registre des risques."

        critical = [r for r in risks if (r.get("criticality") or 0) >= 15]
        high = [r for r in risks if 8 <= (r.get("criticality") or 0) < 15]
        blockers = [r for r in risks if r.get("is_gate_blocker")]

        lines = [f"**Registre des risques — {len(risks)} risques**\n"]
        if critical:
            lines.append(f"🔴 **{len(critical)} risques critiques (C≥15) :**")
            for r in critical[:3]:
                lines.append(f"  - {r.get('description', '—')} (P={r.get('probability')}, I={r.get('impact')})")
        if blockers:
            lines.append(f"\n⛔ **{len(blockers)} gate-bloquants**")
        lines.append(f"\n📊 Répartition : {len(critical)} critiques, {len(high)} élevés, {len(risks)-len(critical)-len(high)} autres")

        return "\n".join(lines)
    except Exception as e:
        logger.error("_query_risks failed for project_id=%s: %s", pid, e)
        return "Erreur lors de la récupération des risques."


def _query_lims(pid: str, db_qone, db_qall) -> str:
    try:
        samples = int((db_qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        a1 = int((db_qone("SELECT COUNT(*) AS n FROM lims_a1 WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        b1 = int((db_qone("SELECT COUNT(*) AS n FROM lims_b1 WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        d1 = int((db_qone("SELECT COUNT(*) AS n FROM lims_d1 WHERE project_id=%s", (pid,)) or {}).get("n", 0))

        avg_grade = db_qone("SELECT AVG(au_g_t) AS avg FROM lims_a1 WHERE project_id=%s AND au_g_t IS NOT NULL", (pid,))
        grade_text = f"{float(avg_grade['avg']):.2f} g/t" if avg_grade and avg_grade.get("avg") else "—"

        return (
            f"**Données LIMS**\n\n"
            f"- Échantillons : {samples}\n"
            f"- Tests chimiques (A1) : {a1}\n"
            f"- Tests comminution (B1) : {b1}\n"
            f"- Tests lixiviation (D1) : {d1}\n"
            f"- Grade Au moyen : {grade_text}\n\n"
            f"{'✅ Base de données LIMS complète' if a1 > 0 and b1 > 0 and d1 > 0 else '⚠️ Certains types de tests manquent'}"
        )
    except Exception as e:
        logger.error("_query_lims failed for project_id=%s: %s", pid, e)
        return "Erreur lors de la récupération des données LIMS."


def _query_circuit(pid: str, db_qone, db_qall) -> str:
    from .circuit_optimizer import recommend_circuit
    try:
        result = recommend_circuit(pid, db_qall, db_qone)
        rec = result.get("recommended")
        if not rec:
            return "Impossible de recommander un circuit. Vérifiez les données LIMS."
        return (
            f"{result['justification']}\n\n"
            f"📊 {len(result['candidates'])} circuits évalués, {len(result['filtered_out'])} éliminés.\n"
            f"Allez dans Critères de conception → **Optimiser circuit IA** pour voir le détail."
        )
    except Exception as e:
        return f"Erreur lors de l'analyse du circuit : {type(e).__name__}"


def _query_status(pid: str, db_qone, db_qall) -> str:
    try:
        p = db_qone("SELECT * FROM projects WHERE id=%s", (pid,))
        if not p:
            return "Projet introuvable."

        samples = int((db_qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        dc = int((db_qone("SELECT COUNT(*) AS n FROM design_criteria WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        equip = int((db_qone("SELECT COUNT(*) AS n FROM equipment_v2 WHERE project_id=%s AND enabled=true", (pid,)) or {}).get("n", 0))
        risks = int((db_qone("SELECT COUNT(*) AS n FROM risks WHERE project_id=%s", (pid,)) or {}).get("n", 0))
        ni = int((db_qone("SELECT COUNT(*) AS n FROM ni43101_sections WHERE project_id=%s", (pid,)) or {}).get("n", 0))

        return (
            f"**Résumé du projet — {p.get('project_name', '—')}**\n\n"
            f"- Phase : {p.get('status', 'SCOPING')}\n"
            f"- Débit design : {p.get('target_tph', '—')} t/h\n"
            f"- Grade Au : {p.get('gold_grade_g_t', '—')} g/t\n\n"
            f"**Modules :**\n"
            f"- LIMS : {samples} échantillons {'✅' if samples > 0 else '⬜'}\n"
            f"- Design Criteria : {dc} critères {'✅' if dc > 0 else '⬜'}\n"
            f"- Équipements : {equip} items {'✅' if equip > 0 else '⬜'}\n"
            f"- Risques : {risks} {'✅' if risks > 0 else '⬜'}\n"
            f"- NI 43-101 : {ni} sections {'✅' if ni > 0 else '⬜'}"
        )
    except Exception as e:
        logger.error("_query_status failed for project_id=%s: %s", pid, e)
        return "Erreur lors de la récupération du statut du projet."


_QUERY_HANDLERS = {
    "production": _query_production,
    "recovery": _query_recovery,
    "opex": _query_opex,
    "capex": _query_capex,
    "risks": _query_risks,
    "lims": _query_lims,
    "circuit": _query_circuit,
    "status": _query_status,
}


def _query_water(pid: str, db_qone, db_qall) -> str:
    return "Le bilan d'eau est disponible dans le module Bilan massique → onglet Bilan d'eau."


def _query_npv(pid: str, db_qone, db_qall) -> str:
    try:
        row = db_qone(
            "SELECT npv_usd, irr_pct, aisc_usd_oz FROM economic_indicators WHERE project_id=%s ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        if not row:
            return "Aucun modèle DCF calculé. Allez dans Modèle Économique → LOM & DCF pour lancer le calcul."

        npv = float(row.get("npv_usd") or 0)
        irr = row.get("irr_pct")
        aisc = row.get("aisc_usd_oz")

        return (
            f"**Indicateurs économiques (dernier DCF)**\n\n"
            f"- **NPV : ${npv/1e6:,.1f}M** {'✅ Positif' if npv > 0 else '🔴 Négatif'}\n"
            f"- IRR : {irr:.1f}%\n" if irr else ""
            f"- AISC : ${aisc:,.0f}/oz\n" if aisc else ""
        )
    except Exception as e:
        logger.error("_query_npv failed for project_id=%s: %s", pid, e)
        return "Erreur lors de la récupération des indicateurs économiques."


_QUERY_HANDLERS["water"] = _query_water
_QUERY_HANDLERS["npv"] = _query_npv


# ─── LLM Proxy ──────────────────────────────────────────────────────────────

# Long-lived client; the SDK reads ANTHROPIC_API_KEY from env.
_anthropic_client = None


def _get_anthropic_client(api_key: str):
    """Lazily build an Anthropic SDK client. Returns None if SDK not installed."""
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed; install `anthropic>=0.50.0`")
        return None
    _anthropic_client = anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=2)
    return _anthropic_client


_SYSTEM_INSTRUCTIONS = (
    "Tu es un assistant métallurgiste expert pour l'application MetalFlow Pro (MPDPMS). "
    "Tu réponds toujours en français. Tu es concis, précis, et tu raisonnes à partir des "
    "données du projet fournies en contexte. Quand tu cites une valeur numérique, indique "
    "son unité (g/t, kWh/t, $/t, etc.). Si l'utilisateur te demande une recommandation, "
    "appuie-toi sur les normes minières (NI 43-101, CIM, JORC) et les bonnes pratiques "
    "métallurgiques (Bond, Starkey, kinetics)."
)


def _call_llm(message: str, context: str, api_key: str) -> str | None:
    """Call Claude with the project context.

    Uses the Anthropic SDK with prompt caching on the project context (which is
    stable across a session — same project → same context block) and the system
    instructions (frozen). Repeat queries about the same project read the cache
    at ~10% cost. Returns None on any failure (caller falls back to local intents).
    """
    client = _get_anthropic_client(api_key)
    if client is None:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    try:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=2048,
            system=[
                {"type": "text", "text": _SYSTEM_INSTRUCTIONS},
                {
                    "type": "text",
                    "text": "Contexte du projet :\n\n" + context,
                    "cache_control": {"type": "ephemeral"},
                },
            ],
            messages=[{"role": "user", "content": message}],
        )
    except anthropic.APITimeoutError:
        logger.warning("anthropic: request timed out")
        return None
    except anthropic.RateLimitError as e:
        logger.warning("anthropic: rate limited (request_id=%s)", e.response.headers.get("request-id"))
        return None
    except anthropic.AuthenticationError:
        logger.error("anthropic: invalid API key")
        return None
    except anthropic.APIStatusError as e:
        logger.warning("anthropic: status %d (request_id=%s)", e.status_code, e._request_id)
        return None
    except Exception:
        logger.exception("anthropic: unexpected error")
        return None

    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    if text:
        logger.info(
            "anthropic: response received",
            extra={
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0),
                "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
            },
        )
    return text


# ─── Main Entry Point ────────────────────────────────────────────────────────

def chat(pid: str, message: str, db_qone, db_qall) -> dict:
    """Process a chat message. Returns {response, source, intent}."""

    # 1. Detect intent
    intent = detect_intent(message)

    # 2. Try local handler first
    if intent and intent in _QUERY_HANDLERS:
        try:
            response = _QUERY_HANDLERS[intent](pid, db_qone, db_qall)
            return _assistant_result(pid, response, "local", intent)
        except Exception:
            logger.exception("Local handler failed for intent %s", intent)

    # 3. Try LLM if API key configured
    api_key = get_settings().anthropic_api_key
    if api_key:
        # Build context from local queries
        context_parts = []
        for q_name in ("status", "production", "lims"):
            try:
                ctx = _QUERY_HANDLERS[q_name](pid, db_qone, db_qall)
                context_parts.append(ctx)
            except Exception:
                pass
        context = "\n\n---\n\n".join(context_parts)

        llm_response = _call_llm(message, context, api_key)
        if llm_response:
            return _assistant_result(pid, llm_response, "llm", intent)

    # 4. Fallback — suggest available queries
    available = ", ".join(f"**{k}**" for k in _QUERY_HANDLERS.keys())
    return _assistant_result(
        pid,
        (
            f"Je n'ai pas compris votre question. Essayez de me demander :\n\n"
            f"- Quelle est la **production** estimée ?\n"
            f"- Quel est le taux de **récupération** ?\n"
            f"- Détaillez l'**OPEX** du projet\n"
            f"- Quels sont les **risques** critiques ?\n"
            f"- Résumez le **statut** du projet\n"
            f"- Quel **circuit** recommandez-vous ?\n"
            f"- Quelles sont les données **LIMS** ?\n\n"
            f"_Sujets disponibles : {available}_"
        ),
        "fallback",
        None,
    )
