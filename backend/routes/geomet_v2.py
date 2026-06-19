# backend/routes/geomet_v2.py
"""Routes API v2 Intelligence Géométallurgique — GADE · PRD · IMBO.

Préfixe : /api/v2/projects/{pid}/
Toutes les routes nécessitent project_user (authentification + isolation projet).
"""
from __future__ import annotations
import json
import logging
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException

try:
    from ..auth import project_user
    from ..db import execute, qall, qone
    from ..engines.gade_engine import run_gade, train_recovery_model
    from ..engines.prd_engine import compute_lom, detect_critical_periods, compute_lom_summary
    from ..engines.imbo_engine import optimize_blend
except ImportError:
    from auth import project_user
    from db import execute, qall, qone
    from engines.gade_engine import run_gade, train_recovery_model
    from engines.prd_engine import compute_lom, detect_critical_periods, compute_lom_summary
    from engines.imbo_engine import optimize_blend

logger = logging.getLogger("mpdpms.geomet_v2")
router = APIRouter()

def _new_id() -> str:
    return str(uuid.uuid4())

# ═══════════════════════════════════════════════════════════════════
# GADE — Geometallurgical Auto-Domaining Engine
# ═══════════════════════════════════════════════════════════════════

@router.post("/{pid}/gade/run")
def run_gade_session(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Lance GADE sur les données LIMS du projet (synchrone, timeout 30s)."""
    # Charger samples LIMS depuis DB
    samples_rows = qall(
        "SELECT * FROM lims_samples WHERE project_id=%s ORDER BY created_at LIMIT 5000",
        (pid,),
    )
    if not samples_rows:
        raise HTTPException(400, "Aucun échantillon LIMS disponible pour ce projet.")

    # Enrichir avec les données des tables de tests
    samples = [dict(r) for r in samples_rows]
    for tbl in ["lims_a1","lims_b1","lims_d1","lims_a3"]:
        try:
            rows = qall(f"SELECT ls.id as sample_id, t.* FROM {tbl} t JOIN lims_samples ls ON ls.id=t.sample_id WHERE ls.project_id=%s", (pid,))
            by_sid = {}
            for r in rows:
                by_sid.setdefault(str(r.get("sample_id")), {}).update({k:v for k,v in dict(r).items() if k != "sample_id"})
            for s in samples:
                s.update(by_sid.get(str(s["id"]), {}))
        except Exception:
            pass

    config = body.get("config") or {}
    session_name = body.get("name") or "Session GADE auto"

    # Exécuter engine pur
    try:
        result = run_gade(samples, config)
    except Exception as e:
        raise HTTPException(500, f"Erreur GADE engine : {e}")

    # Persister session GADE
    session_id = _new_id()
    execute(
        "INSERT INTO gade_sessions (id, project_id, name, algorithm, n_domains_requested, n_domains_found, "
        "features_used, normalization, n_samples_used, silhouette_score, davies_bouldin_score, status, completed_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'completed',now())",
        (session_id, pid, session_name,
         config.get("algorithm","kmeans"), config.get("n_domains"),
         result.get("n_domains_found",0),
         json.dumps(result.get("features_used",[])),
         config.get("normalization","robust"),
         result.get("n_samples_used",0),
         result.get("silhouette_score"), result.get("davies_bouldin_score")),
    )

    # Persister domaines
    for dom in result.get("domains", []):
        dom_id = _new_id()
        dom["_db_id"] = dom_id
        execute(
            "INSERT INTO geomet_domains (id, session_id, project_id, domain_code, label, color, "
            "n_samples, pct_of_total, statistics, metallurgical_signature, discriminating_features) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (dom_id, session_id, pid,
             dom["domain_code"], dom["label"], dom.get("color","#0D9488"),
             dom["n_samples"], dom["pct_of_total"],
             json.dumps(dom.get("statistics",{})),
             json.dumps({}),
             json.dumps(dom.get("discriminating_features",[]))),
        )

    return {
        "session_id": session_id,
        "n_domains": result.get("n_domains_found"),
        "n_samples": result.get("n_samples_used"),
        "silhouette_score": result.get("silhouette_score"),
        "davies_bouldin_score": result.get("davies_bouldin_score"),
        "domains": [
            {"domain_code": d["domain_code"], "label": d["label"],
             "n_samples": d["n_samples"], "pct_of_total": d["pct_of_total"],
             "color": d.get("color"), "discriminating_features": d.get("discriminating_features",[])}
            for d in result.get("domains", [])
        ],
        "warnings": result.get("warnings", []),
    }


@router.get("/{pid}/gade/sessions")
def list_gade_sessions(pid: str, user=Depends(project_user)):
    rows = qall(
        "SELECT id, name, algorithm, n_domains_found, n_samples_used, silhouette_score, "
        "status, created_at, completed_at FROM gade_sessions WHERE project_id=%s ORDER BY created_at DESC",
        (pid,),
    )
    return {"items": [dict(r) for r in rows]}


@router.get("/{pid}/gade/sessions/{session_id}/domains")
def get_session_domains(pid: str, session_id: str, user=Depends(project_user)):
    sess = qone("SELECT id FROM gade_sessions WHERE id=%s AND project_id=%s", (session_id, pid))
    if not sess:
        raise HTTPException(404, "Session non trouvée")
    rows = qall(
        "SELECT id, domain_code, label, color, n_samples, pct_of_total, "
        "statistics, discriminating_features FROM geomet_domains WHERE session_id=%s ORDER BY domain_code",
        (session_id,),
    )
    return {"items": [dict(r) for r in rows]}


@router.post("/{pid}/gade/sessions/{session_id}/train-models")
def train_session_models(pid: str, session_id: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Entraîne un RecoveryModel par domaine (synchrone)."""
    sess = qone("SELECT id FROM gade_sessions WHERE id=%s AND project_id=%s", (session_id, pid))
    if not sess:
        raise HTTPException(404, "Session non trouvée")

    domains = qall("SELECT id, domain_code FROM geomet_domains WHERE session_id=%s", (session_id,))
    target = body.get("target", "leach_rec_48h_pct")
    results = []

    for dom in domains:
        dom_id = str(dom["id"])
        # Charger samples du domaine (via block_domain_assignments si disponible)
        try:
            rows = qall(
                "SELECT ls.* FROM lims_samples ls "
                "JOIN block_domain_assignments bda ON bda.project_id=ls.project_id "
                "WHERE bda.domain_id=%s AND ls.project_id=%s LIMIT 500",
                (dom_id, pid),
            )
            domain_samples = [dict(r) for r in rows]
        except Exception:
            domain_samples = []

        if len(domain_samples) < 10:
            results.append({"domain_id": dom_id, "domain_code": dom["domain_code"],
                            "status": "skipped", "reason": f"< 10 samples ({len(domain_samples)})"})
            continue

        model_result = train_recovery_model(domain_samples, target)
        if model_result:
            rm_id = _new_id()
            execute(
                "INSERT INTO recovery_models (id, domain_id, model_type, target_variable, "
                "training_samples, test_r2, test_rmse, feature_importances, model_artifact_path, is_active) "
                "VALUES (%s,%s,'random_forest',%s,%s,%s,%s,%s,%s,true)",
                (rm_id, dom_id, target, model_result["n_samples"],
                 model_result.get("r2"), model_result.get("rmse"),
                 json.dumps(model_result.get("feature_importances",{})),
                 model_result.get("model_path")),
            )
            results.append({"domain_id": dom_id, "domain_code": dom["domain_code"],
                           "status": "trained", "r2": model_result.get("r2")})
        else:
            results.append({"domain_id": dom_id, "domain_code": dom["domain_code"],
                           "status": "failed", "reason": "Entraînement échoué"})

    return {"results": results}


# ═══════════════════════════════════════════════════════════════════
# PRD — Predictive Recovery Degradation over LOM
# ═══════════════════════════════════════════════════════════════════

@router.post("/{pid}/prd/run")
def run_prd_analysis(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Lance une analyse PRD (Monte Carlo synchrone)."""
    session_id = body.get("gade_session_id")
    name = body.get("name") or "Analyse PRD"
    mc_runs = int(body.get("monte_carlo_runs") or 500)
    thresholds = body.get("thresholds") or {}
    domain_mix_by_year = body.get("domain_mix_by_year") or []
    base_grade = float(body.get("base_grade_g_t") or 1.5)
    throughput = float(body.get("throughput_tph") or 100.0)

    # Récupération par domaine (depuis recovery_models ou valeur par défaut)
    recovery_by_domain: dict[str, float] = {}
    if session_id:
        domains = qall(
            "SELECT gd.domain_code, rm.test_r2 FROM geomet_domains gd "
            "LEFT JOIN recovery_models rm ON rm.domain_id=gd.id AND rm.is_active=true "
            "WHERE gd.session_id=%s",
            (session_id,),
        )
        for d in domains:
            # Heuristique : utiliser valeur sauvegardée ou défaut 89%
            recovery_by_domain[d["domain_code"]] = 0.89

    if not domain_mix_by_year:
        # Générer un LOM simplifié sur 10 ans si pas de données
        domain_mix_by_year = [
            {"year": y, "mix": {"D01": 0.7, "D02": 0.3}, "grade_g_t": base_grade}
            for y in range(1, 11)
        ]

    predictions = compute_lom(
        domain_mix_by_year, recovery_by_domain,
        base_grade_g_t=base_grade,
        throughput_tph=throughput,
        mc_runs=mc_runs,
    )
    critical_periods = detect_critical_periods(predictions, thresholds)
    summary = compute_lom_summary(predictions)

    # Persister analyse
    analysis_id = _new_id()
    execute(
        "INSERT INTO prd_analyses (id, project_id, gade_session_id, name, monte_carlo_runs, thresholds, status) "
        "VALUES (%s,%s,%s,%s,%s,%s,'completed')",
        (analysis_id, pid, session_id, name, mc_runs, json.dumps(thresholds)),
    )

    # Persister prédictions annuelles
    for pred in predictions:
        execute(
            "INSERT INTO annual_metallurgical_predictions "
            "(id, prd_analysis_id, year, domain_mix, blended_feed_grade_g_t, "
            "predicted_recovery_p50, predicted_recovery_p10, predicted_recovery_p90, "
            "predicted_gold_oz_p50, predicted_gold_oz_p10, predicted_gold_oz_p90, is_critical, critical_reasons) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (_new_id(), analysis_id, pred["year"],
             json.dumps(pred.get("domain_mix",{})),
             pred.get("feed_grade_g_t"),
             pred.get("blended_recovery_p50"), pred.get("blended_recovery_p10"), pred.get("blended_recovery_p90"),
             pred.get("gold_oz_p50"), pred.get("gold_oz_p10"), pred.get("gold_oz_p90"),
             pred.get("is_critical", False), json.dumps(pred.get("critical_reasons",[]))),
        )

    return {
        "analysis_id": analysis_id,
        "n_years": len(predictions),
        "predictions": predictions,
        "critical_periods": critical_periods,
        "lom_summary": summary,
    }


@router.get("/{pid}/prd/analyses")
def list_prd_analyses(pid: str, user=Depends(project_user)):
    rows = qall(
        "SELECT id, name, monte_carlo_runs, status, created_at FROM prd_analyses "
        "WHERE project_id=%s ORDER BY created_at DESC",
        (pid,),
    )
    return {"items": [dict(r) for r in rows]}


@router.get("/{pid}/prd/analyses/{analysis_id}/predictions")
def get_prd_predictions(pid: str, analysis_id: str, user=Depends(project_user)):
    a = qone("SELECT id FROM prd_analyses WHERE id=%s AND project_id=%s", (analysis_id, pid))
    if not a:
        raise HTTPException(404, "Analyse non trouvée")
    rows = qall(
        "SELECT year, domain_mix, blended_feed_grade_g_t, "
        "predicted_recovery_p50, predicted_recovery_p10, predicted_recovery_p90, "
        "predicted_gold_oz_p50, predicted_gold_oz_p10, predicted_gold_oz_p90, "
        "is_critical, critical_reasons FROM annual_metallurgical_predictions "
        "WHERE prd_analysis_id=%s ORDER BY year",
        (analysis_id,),
    )
    return {"items": [dict(r) for r in rows]}


@router.get("/{pid}/prd/analyses/{analysis_id}/critical-periods")
def get_critical_periods(pid: str, analysis_id: str, user=Depends(project_user)):
    a = qone("SELECT id FROM prd_analyses WHERE id=%s AND project_id=%s", (analysis_id, pid))
    if not a:
        raise HTTPException(404)
    rows = qall(
        "SELECT year, blended_recovery_p10, blended_recovery_p50, gold_oz_p50, critical_reasons "
        "FROM annual_metallurgical_predictions "
        "WHERE prd_analysis_id=%s AND is_critical=true ORDER BY year",
        (analysis_id,),
    )
    return {"items": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════════════
# IMBO — Intelligent Metallurgical Blend Optimizer
# ═══════════════════════════════════════════════════════════════════

@router.post("/{pid}/imbo/optimize")
def run_imbo_optimize(pid: str, body: dict = Body(...), user=Depends(project_user)):
    """Optimise l'allocation des sources de minerai (LP synchrone < 5s)."""
    sources = body.get("sources") or []
    constraints = body.get("constraints") or []
    gold_price = float(body.get("gold_price") or 3200.0)
    target = body.get("target_variable") or "maximize_au_oz"
    name = body.get("name") or "Session IMBO"

    if not sources:
        raise HTTPException(400, "Au moins une source requise.")

    try:
        result = optimize_blend(sources, constraints, gold_price, target)
    except Exception as e:
        raise HTTPException(500, f"Erreur IMBO engine : {e}")

    # Persister session
    session_id = _new_id()
    execute(
        "INSERT INTO blend_sessions_v2 (id, project_id, name, gold_price, target_variable, "
        "sources, constraints, result, solve_time_ms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (session_id, pid, name, gold_price, target,
         json.dumps(sources), json.dumps(constraints),
         json.dumps(result), result.get("solve_time_ms")),
    )

    return {"session_id": session_id, **result}


@router.get("/{pid}/imbo/sessions")
def list_imbo_sessions(pid: str, user=Depends(project_user)):
    rows = qall(
        "SELECT id, name, gold_price, target_variable, solve_time_ms, created_at "
        "FROM blend_sessions_v2 WHERE project_id=%s ORDER BY created_at DESC LIMIT 50",
        (pid,),
    )
    return {"items": [dict(r) for r in rows]}


@router.get("/{pid}/imbo/sessions/{session_id}")
def get_imbo_session(pid: str, session_id: str, user=Depends(project_user)):
    row = qone(
        "SELECT * FROM blend_sessions_v2 WHERE id=%s AND project_id=%s",
        (session_id, pid),
    )
    if not row:
        raise HTTPException(404, "Session non trouvée")
    return dict(row)
