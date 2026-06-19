"""
MPDPMS — Pipeline de génération des modules (traçabilité & dépendances)

Implémente la chaîne de flux de données selon les normes de l'industrie minière :

  BlockModel ──┐
               ├──► LIMS ──► Critères de Conception ──► Bilan Massique
                                        │                      │
                                        └──────────────────────┴──► Schéma de Procédé
                                                                          │
                                                                          ▼
                                                               Liste d'Équipements (MER)
                                                                          │
                                                       ┌──────────────────┤
                                                       ▼                  ▼
                                                     OPEX               CAPEX
                                                       │                  │
                                                       └──────────────────┴──► Économie de Projet

Règles :
  - Un module ne peut être généré que si tous ses prédécesseurs sont à l'état «complete»
  - Toute modification d'un module source marque automatiquement ses dépendants comme «stale»
  - Chaque génération enregistre un snapshot des données sources (traçabilité NI 43-101)
  - Le flux est unidirectionnel (DAG sans cycle)

Endpoints :
  GET  /api/v1/projects/{pid}/pipeline/graph     — Graphe de dépendances complet
  GET  /api/v1/projects/{pid}/pipeline/status    — État de tous les modules
  POST /api/v1/projects/{pid}/pipeline/regenerate — Régénération complète en cascade
  POST /api/v1/projects/{pid}/pipeline/modules/{module_code}/mark-stale
  POST /api/v1/projects/{pid}/pipeline/modules/{module_code}/mark-complete
"""
from __future__ import annotations

import hashlib
import json
import logging
import psycopg2
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks

try:
    from ..auth import project_user
    from ..db import qone, qall, execute
    from ..audit import record_event
except ImportError:
    from auth import project_user
    from db import qone, qall, execute
    from audit import record_event

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["pipeline"])
logger = logging.getLogger("mpdpms.pipeline")

# ─────────────────────────────────────────────────────────────────────────────
# MODULE DEPENDENCY GRAPH
# Norme industrielle : ordre de génération conforme à la pratique FS/PFS
# ─────────────────────────────────────────────────────────────────────────────

MODULE_GRAPH: Dict[str, Dict[str, Any]] = {
    # ── Niveau 0 : Données sources (alimentation manuelle) ──────────────────
    "lims": {
        "label": "Données LIMS",
        "label_en": "LIMS Test Data",
        "order": 0,
        "depends_on": [],
        "auto_generate": False,   # alimentation manuelle uniquement
        "tables": [
            "lims_samples", "lims_a1", "lims_b1", "lims_c2",
            "lims_d1", "lims_e1", "lims_flotation", "lims_elution",
        ],
        "description": (
            "Données de laboratoire (LIMS) : essais d'assay, de broyabilité, "
            "de lixiviation, de flottation et de gravité. "
            "Source primaire de données métallurgiques."
        ),
        "readiness_check": "lims_ready",
    },
    "blockmodel": {
        "label": "Modèle de Blocs",
        "label_en": "Block Model",
        "order": 0,
        "depends_on": [],
        "auto_generate": False,
        "tables": ["block_model_configs", "blocks"],
        "description": "Modèle de blocs géologiques : tonnages, teneurs, types de minerai.",
        "readiness_check": None,
    },

    # ── Niveau 1 : Critères de conception ───────────────────────────────────
    "design_criteria": {
        "label": "Critères de Conception",
        "label_en": "Design Criteria",
        "order": 1,
        "depends_on": ["lims"],
        "auto_generate": True,
        "endpoint": "/{pid}/design-criteria/auto-generate",
        "tables": ["design_criteria"],
        "description": (
            "Critères de conception de l'usine : débit, circuit (CIL/CIP), "
            "paramètres de broyage, flottation, lixiviation. "
            "Générés à partir des données LIMS."
        ),
        "readiness_check": "dc_ready",
        "min_lims_tables": ["lims_a1", "lims_d1"],
    },

    # ── Niveau 2 : Bilan massique ────────────────────────────────────────────
    "mass_balance": {
        "label": "Bilan Massique et Hydrique",
        "label_en": "Mass & Water Balance",
        "order": 2,
        "depends_on": ["design_criteria"],
        "auto_generate": True,
        "endpoint": "/{pid}/mass-balance/auto-generate",
        "tables": ["mass_balance_streams", "water_balance_nodes", "equipment"],
        "description": (
            "Bilan massique et hydrique de l'usine : débits solides, liquides, "
            "densités de pulpe, teneur en or par flux. "
            "Généré à partir des critères de conception."
        ),
        "readiness_check": "mb_ready",
    },

    # ── Niveau 3 : Schéma de procédé ─────────────────────────────────────────
    "flowsheet": {
        "label": "Schéma de Procédé",
        "label_en": "Process Flowsheet",
        "order": 3,
        "depends_on": ["design_criteria", "mass_balance"],
        "auto_generate": True,
        "endpoint": "/{pid}/flowsheets/auto-generate",
        "tables": ["flowsheets"],
        "description": (
            "Schéma de procédé (PFD) : blocs d'équipements et connexions. "
            "Généré à partir des critères de conception et du bilan massique."
        ),
        "readiness_check": "flowsheet_ready",
    },

    # ── Niveau 4 : Liste d'équipements (MER) ─────────────────────────────────
    "equipment": {
        "label": "Liste d'Équipements (MER)",
        "label_en": "Equipment List (MER)",
        "order": 4,
        "depends_on": ["flowsheet", "design_criteria"],
        "auto_generate": True,
        "endpoint": "/{pid}/mass-balance/auto-generate",  # equipment embedded in MB
        "tables": ["equipment"],
        "description": (
            "Liste des équipements principaux de l'usine (MER) : puissance installée, "
            "capacité de conception, articles à long délai d'approvisionnement. "
            "Générée à partir du schéma de procédé et des critères de conception."
        ),
        "readiness_check": "equipment_ready",
    },

    # ── Niveau 5 : OPEX ──────────────────────────────────────────────────────
    "opex": {
        "label": "Coûts d'Exploitation (OPEX)",
        "label_en": "Operating Costs (OPEX)",
        "order": 5,
        "depends_on": ["equipment", "design_criteria"],
        "auto_generate": True,
        "endpoint": "/{pid}/opex-v2/auto-generate",
        "tables": ["opex_manpower", "opex_power", "opex_reagents", "opex_mobile"],
        "description": (
            "Coûts d'exploitation : main d'œuvre, énergie, réactifs, consommables. "
            "Générés à partir de la liste d'équipements et des critères de conception."
        ),
        "readiness_check": "opex_ready",
    },

    # ── Niveau 5 : Simulation ────────────────────────────────────────────────
    "simulation": {
        "label": "Simulation Métallurgique",
        "label_en": "Metallurgical Simulation",
        "order": 5,
        "depends_on": ["design_criteria", "mass_balance"],
        "auto_generate": True,
        "endpoint": "/{pid}/simulation/import/lims",
        "tables": ["simulation_params"],
        "description": (
            "Paramètres de simulation calibrés depuis les données LIMS "
            "et les critères de conception."
        ),
        "readiness_check": None,
    },

    # ── Niveau 6 : Économie du projet ────────────────────────────────────────
    "economics": {
        "label": "Économie du Projet",
        "label_en": "Project Economics",
        "order": 6,
        "depends_on": ["opex"],
        "auto_generate": False,
        "tables": ["cost_models", "cost_line_items"],
        "description": (
            "Analyse économique : VAN, TRI, AISC. "
            "Calculée à partir de l'OPEX et des paramètres économiques du projet."
        ),
        "readiness_check": None,
    },

    # ── Niveau 7 : Registre des risques ───────────────────────────────────────
    "risks": {
        "label": "Registre des Risques",
        "label_en": "Risk Register",
        "order": 7,
        "depends_on": ["design_criteria", "mass_balance", "equipment", "flowsheet"],
        "auto_generate": True,
        "endpoint": "/{pid}/risks/auto-generate",
        "tables": ["risks"],
        "description": (
            "Registre des risques EPCM : techniques, HSE, financiers, planning. "
            "Généré à partir des critères de conception, bilan massique, équipements et schéma de procédé."
        ),
        "readiness_check": None,
    },
}

# Ordre topologique de génération (respecte les dépendances)
GENERATION_ORDER = [
    "lims",
    "blockmodel",
    "design_criteria",
    "mass_balance",
    "flowsheet",
    "equipment",
    "simulation",
    "opex",
    "economics",
    "risks",
]

# Modules qui déclenchent le staleness en cascade vers l'aval
STALE_CASCADE: Dict[str, List[str]] = {
    "lims":            ["design_criteria", "mass_balance", "flowsheet",
                        "equipment", "simulation", "opex", "economics"],
    "blockmodel":      ["lims"],
    "design_criteria": ["mass_balance", "flowsheet", "equipment", "opex", "economics", "risks"],
    "mass_balance":    ["flowsheet", "equipment", "opex", "risks"],
    "flowsheet":       ["equipment", "opex", "risks"],
    "equipment":       ["opex", "economics", "risks"],
    "simulation":      ["opex"],
    "opex":            ["economics", "risks"],
    "economics":       ["risks"],
    "risks":           [],
}


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_status_row(pid: str, module_code: str) -> dict:
    """Get or create a status row for this (project, module) pair."""
    row = qone(
        "SELECT * FROM module_generation_status WHERE project_id=%s AND module_code=%s",
        (pid, module_code),
    )
    if not row:
        row = execute(
            "INSERT INTO module_generation_status "
            "(project_id, module_code, status) VALUES (%s,%s,'pending') "
            "ON CONFLICT (project_id, module_code) DO UPDATE SET updated_at=NOW() "
            "RETURNING *",
            (pid, module_code),
        )
    return row


def get_status(pid: str, module_code: str) -> str:
    """Return current status string for a module, defaulting to 'pending'."""
    row = qone(
        "SELECT status FROM module_generation_status WHERE project_id=%s AND module_code=%s",
        (pid, module_code),
    )
    return (row["status"] if row else "pending") or "pending"


def set_status(
    pid: str,
    module_code: str,
    status: str,
    *,
    user_id: Optional[str] = None,
    triggered_by: Optional[str] = None,
    warnings: Optional[List[str]] = None,
    errors: Optional[List[str]] = None,
    input_snapshot: Optional[dict] = None,
    input_count: Optional[int] = None,
) -> None:
    """Upsert the generation status for a module."""
    w = json.dumps(warnings or [])
    e = json.dumps(errors or [])
    snap = json.dumps(input_snapshot or {})

    generated_at_clause = ""
    if status == "complete":
        generated_at_clause = ", generated_at = NOW()"

    execute(
        f"""INSERT INTO module_generation_status
               (project_id, module_code, status, generated_by, triggered_by,
                warnings, errors, input_snapshot, input_count, updated_at
                {', generated_at' if status == 'complete' else ''})
            VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,NOW()
                    {', NOW()' if status == 'complete' else ''})
            ON CONFLICT (project_id, module_code) DO UPDATE SET
                status       = EXCLUDED.status,
                generated_by = EXCLUDED.generated_by,
                triggered_by = EXCLUDED.triggered_by,
                warnings     = EXCLUDED.warnings,
                errors       = EXCLUDED.errors,
                input_snapshot = EXCLUDED.input_snapshot,
                input_count  = EXCLUDED.input_count,
                updated_at   = NOW()
                {generated_at_clause}
        """,
        (pid, module_code, status, user_id, triggered_by, w, e, snap,
         input_count if input_count is not None else 0),
    )


def mark_stale_cascade(pid: str, source_module: str, user_id: Optional[str] = None) -> List[str]:
    """Mark all downstream modules as stale when a source module changes."""
    cascaded = STALE_CASCADE.get(source_module, [])
    marked = []
    for mod in cascaded:
        current = get_status(pid, mod)
        if current in ("complete", "error"):
            set_status(pid, mod, "stale",
                       user_id=user_id, triggered_by=source_module)
            marked.append(mod)
    if marked:
        logger.info("project %s: %s changed → marked stale: %s",
                    pid, source_module, marked)
    return marked


def _build_input_snapshot(pid: str, module_code: str) -> dict:
    """Build a minimal snapshot of key inputs used to generate this module."""
    snap: dict = {"project_id": pid, "module": module_code}
    try:
        p = qone("SELECT target_tph, gold_grade_g_t, availability_pct, "
                 "operating_hours_day FROM projects WHERE id=%s", (pid,))
        if p:
            snap["tph"] = float(p.get("target_tph") or 0)
            snap["grade"] = float(p.get("gold_grade_g_t") or 0)
            snap["avail_pct"] = float(p.get("availability_pct") or 92)
            snap["op_h"] = float(p.get("operating_hours_day") or 24)
    except Exception:  # intentional: ignore optional lookup failure
        pass

    if module_code in ("design_criteria", "mass_balance", "flowsheet", "equipment"):
        for tbl, field in [
            ("lims_a1", "s_total_pct"), ("lims_a1", "c_organic_pct"),
            ("lims_b1", "bwi_kwh_t"), ("lims_c2", "au_recovery_pct"),
            ("lims_d1", "au_recovery_pct"),
        ]:
            try:
                row = qone(
                    f"SELECT ROUND(AVG({field}::numeric),3) AS v "
                    f"FROM {tbl} WHERE project_id=%s", (pid,)
                )
                snap[f"avg_{tbl}_{field}"] = float(row["v"]) if row and row["v"] else None
            except Exception:  # intentional: ignore optional lookup failure
                pass

    if module_code == "design_criteria":
        dc_count = qone("SELECT COUNT(*) AS c FROM design_criteria WHERE project_id=%s", (pid,))
        snap["dc_rows"] = int(dc_count["c"]) if dc_count else 0

    if module_code in ("mass_balance", "flowsheet"):
        mb_count = qone(
            "SELECT COUNT(*) AS c FROM mass_balance_streams WHERE project_id=%s", (pid,)
        )
        snap["mb_streams"] = int(mb_count["c"]) if mb_count else 0

    return snap


def _compute_hash(snap: dict) -> str:
    """SHA-256 of the snapshot dict (first 16 hex chars)."""
    raw = json.dumps(snap, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────────────────────────────────────
# READINESS CHECKS
# ─────────────────────────────────────────────────────────────────────────────

def check_readiness(pid: str, module_code: str) -> dict:
    """
    Returns {"ready": bool, "blockers": [...], "warnings": [...]}.

    A module is ready to generate when:
    1. All its dependencies are in status='complete'
    2. The minimum required source data is present
    """
    meta = MODULE_GRAPH.get(module_code, {})
    depends_on = meta.get("depends_on", [])
    blockers: List[str] = []
    warnings: List[str] = []

    for dep in depends_on:
        dep_status = get_status(pid, dep)
        dep_label  = MODULE_GRAPH.get(dep, {}).get("label", dep)
        if dep_status not in ("complete", "skipped"):
            blockers.append(
                f"«{dep_label}» doit être généré en premier "
                f"(statut actuel : {dep_status})"
            )
        elif dep_status == "stale":
            warnings.append(
                f"«{dep_label}» est marqué comme obsolète — "
                "une régénération est recommandée"
            )

    # Module-specific data checks
    if module_code == "design_criteria":
        for tbl in meta.get("min_lims_tables", []):
            try:
                row = qone(f"SELECT COUNT(*) AS c FROM {tbl} WHERE project_id=%s", (pid,))
                if not row or int(row.get("c") or 0) == 0:
                    warnings.append(f"Table {tbl.upper()} vide — les valeurs par défaut seront utilisées")
            except Exception:  # intentional: ignore optional lookup failure
                pass

    if module_code == "mass_balance":
        dc_count = qone(
            "SELECT COUNT(*) AS c FROM design_criteria WHERE project_id=%s", (pid,)
        )
        if not dc_count or int(dc_count.get("c") or 0) == 0:
            blockers.append(
                "Les critères de conception doivent être générés avant le bilan massique"
            )

    if module_code in ("flowsheet", "equipment"):
        mb_count = qone(
            "SELECT COUNT(*) AS c FROM mass_balance_streams WHERE project_id=%s", (pid,)
        )
        if not mb_count or int(mb_count.get("c") or 0) == 0:
            warnings.append(
                "Aucun flux de bilan massique trouvé — "
                "le schéma utilisera la topologie des critères de conception"
            )

    if module_code == "opex":
        eq_count = qone(
            "SELECT COUNT(*) AS c FROM equipment WHERE project_id=%s", (pid,)
        )
        if not eq_count or int(eq_count.get("c") or 0) == 0:
            warnings.append(
                "Liste d'équipements vide — "
                "l'OPEX électrique sera généré avec des valeurs par défaut"
            )

    return {
        "ready": len(blockers) == 0,
        "blockers": blockers,
        "warnings": warnings,
    }


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL GENERATE HELPER (appelle les routes existantes en interne)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_module(pid: str, module_code: str, user: dict) -> dict:
    """
    Trigger the auto-generate logic for one module.
    Calls the existing route functions directly (not via HTTP).
    Returns {"ok": True, "warnings": [], "errors": []}.
    """
    meta = MODULE_GRAPH.get(module_code, {})
    if not meta.get("auto_generate", False):
        return {"ok": True, "skipped": True, "warnings": [], "errors": []}

    warnings: List[str] = []
    errors: List[str] = []

    try:
        if module_code == "design_criteria":
            from .design import _do_generate_dc
            p = qone("SELECT * FROM projects WHERE id=%s", (pid,))
            _do_generate_dc(pid, p, user)

        elif module_code == "mass_balance":
            from .massbalance import auto_generate_mb
            auto_generate_mb(pid, user=user)

        elif module_code == "flowsheet":
            from .flowsheets import auto_generate_flowsheet
            auto_generate_flowsheet(pid, user=user)

        elif module_code == "equipment":
            # Equipment is generated as part of mass_balance auto-generate
            # It's embedded in auto_generate_mb — already done
            pass

        elif module_code == "simulation":
            from .modules import import_simulation_from_lims
            import_simulation_from_lims(pid, user=user)

        elif module_code == "opex":
            try:
                from .opex_v2 import auto_generate
                auto_generate(pid, user=user)
            except Exception as e:  # intentional: collect error and continue processing
                warnings.append(f"OPEX partiel : {e}")

        elif module_code == "risks":
            from .risks import _do_generate_risks
            _do_generate_risks(pid, user)

        else:
            return {"ok": False, "errors": [f"Module «{module_code}» non pris en charge"]}

    except Exception as exc:  # intentional: log and continue on optional operation
        logger.exception("pipeline generate failed: module=%s project=%s", module_code, pid)
        errors.append(str(exc))
        return {"ok": False, "warnings": warnings, "errors": errors}

    return {"ok": True, "warnings": warnings, "errors": errors}


# ─────────────────────────────────────────────────────────────────────────────
# API ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/pipeline/graph")
def get_pipeline_graph(pid: str, user=Depends(project_user)):
    """
    Return the full module dependency graph.
    Clients use this to render the pipeline visualization.
    """
    try:
        nodes = []
        edges = []
        for code, meta in MODULE_GRAPH.items():
            nodes.append({
                "code":         code,
                "label":        meta["label"],
                "label_en":     meta.get("label_en", ""),
                "order":        meta["order"],
                "auto_generate": meta.get("auto_generate", False),
                "description":  meta.get("description", ""),
                "tables":       meta.get("tables", []),
            })
            for dep in meta.get("depends_on", []):
                edges.append({"from": dep, "to": code})

        return {
            "nodes": nodes,
            "edges": edges,
            "generation_order": GENERATION_ORDER,
        }
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/pipeline/status")
def get_pipeline_status(pid: str, user=Depends(project_user)):
    """
    Return the full pipeline status for this project.
    """
    try:
        return _get_pipeline_status_impl(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _get_pipeline_status_impl(pid: str):
    p = qone("SELECT id FROM projects WHERE id=%s", (pid,))
    if not p:
        raise HTTPException(404, "Projet introuvable")

    # Load all status rows in one query
    rows = qall(
        "SELECT * FROM module_generation_status WHERE project_id=%s",
        (pid,),
    )
    status_map = {r["module_code"]: r for r in rows}

    modules = []
    next_to_generate: List[str] = []

    for code in GENERATION_ORDER:
        meta   = MODULE_GRAPH.get(code, {})
        row    = status_map.get(code)
        status = (row["status"] if row else "pending") or "pending"
        ready  = check_readiness(pid, code)

        # Data count from primary table
        data_count = 0
        primary_tbl = (meta.get("tables") or [None])[0]
        if primary_tbl:
            try:
                cnt = qone(
                    f"SELECT COUNT(*) AS c FROM {primary_tbl} WHERE project_id=%s",
                    (pid,),
                )
                data_count = int(cnt.get("c") or 0) if cnt else 0
            except Exception:  # intentional: ignore optional lookup failure
                pass

        modules.append({
            "code":            code,
            "label":           meta.get("label", code),
            "label_en":        meta.get("label_en", ""),
            "order":           meta.get("order", 99),
            "status":          status,
            "depends_on":      meta.get("depends_on", []),
            "auto_generate":   meta.get("auto_generate", False),
            "ready_to_generate": ready["ready"] and meta.get("auto_generate", False),
            "blockers":        ready["blockers"],
            "warnings":        ready["warnings"],
            "data_count":      data_count,
            "generated_at":    row["generated_at"].isoformat() if row and row.get("generated_at") else None,
            "generated_by":    str(row["generated_by"]) if row and row.get("generated_by") else None,
            "triggered_by":    row.get("triggered_by") if row else None,
            "input_count":     row.get("input_count", 0) if row else 0,
            "input_snapshot":  row.get("input_snapshot", {}) if row else {},
            "gen_warnings":    (row.get("warnings") or []) if row else [],
            "gen_errors":      (row.get("errors") or []) if row else [],
        })

        # Collect next actionable modules
        if (meta.get("auto_generate", False)
                and ready["ready"]
                and status in ("pending", "stale")):
            next_to_generate.append(code)

    # Overall pipeline health
    statuses = [m["status"] for m in modules if m["auto_generate"]]
    all_complete = all(s in ("complete", "skipped") for s in statuses)
    any_stale    = any(s == "stale" for s in statuses)
    any_error    = any(s == "error" for s in statuses)

    pipeline_health = "complete" if all_complete else (
        "error" if any_error else (
        "stale" if any_stale else "in_progress"
    ))

    return {
        "project_id": pid,
        "modules": modules,
        "pipeline_health": pipeline_health,
        "next_to_generate": next_to_generate[:3],  # top 3 ready actions
        "summary": {
            "total":    len([m for m in modules if m["auto_generate"]]),
            "complete": sum(1 for m in modules if m["status"] == "complete"),
            "stale":    sum(1 for m in modules if m["status"] == "stale"),
            "pending":  sum(1 for m in modules if m["status"] == "pending"),
            "error":    sum(1 for m in modules if m["status"] == "error"),
        },
    }


def _run_pipeline_background(pid: str, target_modules: list, force: bool, user: dict):
    """Exécute la régénération du pipeline en arrière-plan."""
    results = []
    pipeline_ok = True

    for module_code in GENERATION_ORDER:
        if module_code not in target_modules:
            continue
        meta = MODULE_GRAPH.get(module_code, {})
        if not meta.get("auto_generate", False):
            continue

        # Readiness check
        ready = check_readiness(pid, module_code)
        if not ready["ready"] and not force:
            results.append({
                "module":  module_code,
                "label":   meta.get("label", module_code),
                "status":  "skipped",
                "reason":  "Prérequis non satisfaits",
                "blockers": ready["blockers"],
            })
            continue

        # Mark as generating
        set_status(pid, module_code, "generating",
                   user_id=str(user["id"]),
                   triggered_by="pipeline/regenerate")

        # Build input snapshot BEFORE generation
        snap = _build_input_snapshot(pid, module_code)

        # Generate
        result = _generate_module(pid, module_code, user)

        if result.get("ok") and not result.get("skipped"):
            snap_after = _build_input_snapshot(pid, module_code)
            # Merge pre/post snap
            snap.update({k: v for k, v in snap_after.items() if k not in snap})

            set_status(
                pid, module_code, "complete",
                user_id=str(user["id"]),
                triggered_by="pipeline/regenerate",
                warnings=result.get("warnings", []) + ready["warnings"],
                errors=[],
                input_snapshot=snap,
                input_count=snap.get(
                    list({k: v for k, v in snap.items() if "_count" in k or "rows" in k or "streams" in k}.keys())[-1]
                    if any("_count" in k or "rows" in k or "streams" in k for k in snap) else "tph",
                    0,
                ),
            )
            record_event(
                user_id=str(user["id"]), project_id=pid,
                entity_type="pipeline", entity_id=None,
                action="generate",
                new_value={"module": module_code, "snapshot": snap},
                source="pipeline",
            )
            results.append({
                "module":   module_code,
                "label":    meta.get("label", module_code),
                "status":   "complete",
                "warnings": result.get("warnings", []),
            })
        else:
            set_status(
                pid, module_code, "error",
                user_id=str(user["id"]),
                triggered_by="pipeline/regenerate",
                errors=result.get("errors", []),
                input_snapshot=snap,
            )
            results.append({
                "module":  module_code,
                "label":   meta.get("label", module_code),
                "status":  "error",
                "errors":  result.get("errors", []),
            })
            if not force:
                pipeline_ok = False
                # Mark downstream as stale since this failed
                for downstream in STALE_CASCADE.get(module_code, []):
                    if downstream in target_modules:
                        set_status(pid, downstream, "stale",
                                   user_id=str(user["id"]),
                                   triggered_by=module_code)
                break

    completed = sum(1 for r in results if r["status"] == "complete")
    errors    = sum(1 for r in results if r["status"] == "error")
    logger.info(f"Pipeline background generation for {pid} finished. OK: {pipeline_ok}, Completed: {completed}, Errors: {errors}")


@router.post("/pipeline/regenerate")
def regenerate_pipeline(
    pid: str,
    background_tasks: BackgroundTasks,
    body: dict = None,
    user=Depends(project_user),
):
    """
    Régénération complète du pipeline en cascade.

    Génère chaque module dans l'ordre de dépendances (topologique).
    S'arrête si un module critique échoue (sauf si force=True).
    S'exécute en tâche de fond (BackgroundTasks) pour éviter les Timeouts.

    Body (optionnel):
    {
      "modules":  ["design_criteria", "mass_balance"],  // liste partielle, ou null pour tout
      "force":    false,   // ignorer les erreurs de dépendance
      "dry_run":  false    // vérifier seulement, ne pas générer
    }
    """
    p = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not p:
        raise HTTPException(404, "Projet introuvable")

    body = body or {}
    target_modules = body.get("modules") or [
        m for m in GENERATION_ORDER
        if MODULE_GRAPH.get(m, {}).get("auto_generate", False)
    ]
    force   = bool(body.get("force", False))
    dry_run = bool(body.get("dry_run", False))

    if dry_run:
        results = []
        for module_code in GENERATION_ORDER:
            if module_code not in target_modules:
                continue
            meta = MODULE_GRAPH.get(module_code, {})
            if not meta.get("auto_generate", False):
                continue

            ready = check_readiness(pid, module_code)
            results.append({
                "module":   module_code,
                "label":    meta.get("label", module_code),
                "status":   "would_generate",
                "blockers": ready["blockers"],
                "warnings": ready["warnings"],
            })
        return {
            "ok": True,
            "dry_run": True,
            "results": results
        }

    # Queue the background task
    background_tasks.add_task(_run_pipeline_background, pid, target_modules, force, user)

    return {
        "ok": True,
        "dry_run": False,
        "status": "queued",
        "message": "La régénération du pipeline a été lancée en arrière-plan. Vérifiez le statut pour suivre l'avancement."
    }


@router.post("/pipeline/modules/{module_code}/mark-stale")
def mark_module_stale(
    pid: str,
    module_code: str,
    cascade: bool = True,
    user=Depends(project_user),
):
    """
    Mark a module (and optionally all its downstream dependents) as stale.
    Called automatically when source data changes.
    """
    if module_code not in MODULE_GRAPH:
        raise HTTPException(404, f"Module inconnu : {module_code}")

    set_status(pid, module_code, "stale",
               user_id=str(user["id"]),
               triggered_by="manual")

    cascaded = []
    if cascade:
        cascaded = mark_stale_cascade(pid, module_code, user_id=str(user["id"]))

    return {
        "ok":       True,
        "module":   module_code,
        "cascaded": cascaded,
    }


@router.post("/pipeline/modules/{module_code}/mark-complete")
def mark_module_complete(
    pid: str,
    module_code: str,
    user=Depends(project_user),
):
    """
    Manually mark a module as complete (e.g. manually-loaded data like LIMS).
    """
    if module_code not in MODULE_GRAPH:
        raise HTTPException(404, f"Module inconnu : {module_code}")

    snap = _build_input_snapshot(pid, module_code)
    set_status(pid, module_code, "complete",
               user_id=str(user["id"]),
               triggered_by="manual",
               input_snapshot=snap)

    # Cascade staleness to dependents
    cascaded = mark_stale_cascade(pid, module_code, user_id=str(user["id"]))

    return {
        "ok":       True,
        "module":   module_code,
        "cascaded": cascaded,
    }


@router.get("/pipeline/trace/{module_code}")
def get_traceability(pid: str, module_code: str, user=Depends(project_user)):
    """
    Return the full traceability chain for a module:
    - What data was used when it was generated
    - All upstream modules and their generation timestamps
    - Data lineage from LIMS to final output
    """
    if module_code not in MODULE_GRAPH:
        raise HTTPException(404, f"Module inconnu : {module_code}")

    meta = MODULE_GRAPH[module_code]

    # Collect full upstream chain (BFS)
    chain = []
    visited = set()
    queue = [module_code]
    while queue:
        code = queue.pop(0)
        if code in visited:
            continue
        visited.add(code)
        m = MODULE_GRAPH.get(code, {})
        row = qone(
            "SELECT * FROM module_generation_status WHERE project_id=%s AND module_code=%s",
            (pid, code),
        )
        chain.append({
            "module":          code,
            "label":           m.get("label", code),
            "status":          (row["status"] if row else "pending") or "pending",
            "generated_at":    row["generated_at"].isoformat() if row and row.get("generated_at") else None,
            "input_snapshot":  row.get("input_snapshot", {}) if row else {},
            "warnings":        (row.get("warnings") or []) if row else [],
            "tables":          m.get("tables", []),
            "depends_on":      m.get("depends_on", []),
        })
        for dep in m.get("depends_on", []):
            if dep not in visited:
                queue.append(dep)

    # Order by generation order
    order_idx = {c: i for i, c in enumerate(GENERATION_ORDER)}
    chain.sort(key=lambda x: order_idx.get(x["module"], 99))

    return {
        "module":     module_code,
        "label":      meta.get("label", module_code),
        "trace":      chain,
        "data_flow": [
            f"{c['module']} → {module_code}"
            for c in chain if c["module"] != module_code
        ],
    }
