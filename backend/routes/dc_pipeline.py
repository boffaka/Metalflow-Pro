"""
MPDPMS — Smart DC Pipeline routes.

Adaptive step-by-step design criteria generation with LIMS-driven
recommendations, cascade recalculation, and snapshot versioning.
"""
from __future__ import annotations

import hashlib
import json
import logging
import psycopg2
from decimal import Decimal
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

try:
    from ..auth import project_user, require_role, require_project_role
    from ..db import qone, qall, execute, build_update_sets
    from ..audit import record_event
    from ..engines.dc_cascade import load_dag, cascade_recalculate
except ImportError:
    from auth import project_user, require_role, require_project_role
    from db import qone, qall, execute, build_update_sets
    from audit import record_event
    from engines.dc_cascade import load_dag, cascade_recalculate

# Write operations require engineer role (not Read-only or Reviewer)
_engineer_role = require_role("Process Engineer", "Metallurgist", "Project Manager")

router = APIRouter(prefix="/api/v1/projects", tags=["dc-pipeline"])
logger = logging.getLogger("mpdpms.dc_pipeline")

# Step definitions: id -> {name, section_filter, always_present}
_BASE_STEPS = [
    {"step_id": "data_input",    "name": "Données d'entrée",     "always": True},
    {"step_id": "topology",      "name": "Topologie du circuit", "always": True},
    {"step_id": "comminution",   "name": "Comminution",          "always": True},
    {"step_id": "leaching",      "name": "Lixiviation & CIP",    "always": True},
    {"step_id": "auxiliaries",   "name": "Auxiliaires & Réactifs","always": True},
    {"step_id": "freeze",        "name": "Figer la version",     "always": True},
]

_CONDITIONAL_STEPS = [
    {"step_id": "flotation",     "name": "Flotation",            "condition": "has_flotation",  "after": "topology"},
    {"step_id": "regrind",       "name": "Regrind",              "condition": "has_isamill",    "after": "flotation"},
    {"step_id": "gravity",       "name": "Gravity",              "condition": "has_gravity",    "after": "comminution"},
    {"step_id": "pretreatment",  "name": "Pre-treatment",        "condition": "has_pretreat",   "after": "gravity"},
]


def _build_pipeline_steps(flags: dict) -> list[dict]:
    """Build the ordered list of pipeline steps based on circuit flags."""
    steps = []
    for base in _BASE_STEPS:
        steps.append({"step_id": base["step_id"], "name": base["name"], "status": "pending"})
        # Insert conditional steps after their anchor
        for cond in _CONDITIONAL_STEPS:
            if cond["after"] == base["step_id"] and flags.get(cond["condition"], False):
                steps.append({"step_id": cond["step_id"], "name": cond["name"], "status": "pending"})
    # Assign order
    for i, s in enumerate(steps):
        s["order"] = i + 1
    return steps


# Step-to-section mapping for DC filtering
_STEP_SECTIONS = {
    "data_input":    ["General Plant Design", "General Project", "Ore Characteristics"],
    "comminution":   ["Crushing", "Comminution", "Classification", "HPGR"],
    "flotation":     ["Flotation"],
    "regrind":       ["Regrind", "IsaMill", "Vertimill"],
    "gravity":       ["Gravity"],
    "pretreatment":  ["Pre-treatment", "POX", "BIOX"],
    "leaching":      ["Leaching", "CIL", "CIP", "Desorption", "Electrowinning", "Detox", "Cyanide"],
    "auxiliaries":   ["Thickener", "Water", "Reagent", "Tailings"],
    "freeze":        [],  # special: summary of all sections
}

_STEP_OP_CODES = {
    "data_input": [],
    "comminution": [
        "GIRATOIRE", "CRIBLE", "CONE", "HPGR", "STOCKPILE",
        "SAG_MILL", "BALL_MILL", "ROD_MILL", "VERTIMILL", "HYDROCYCLONE",
        "CRIBLE_CLASS",
    ],
    "flotation": [
        "FLOTATION_ROUGHER", "FLOTATION_SCAVENGER", "FLOTATION_CLEANER",
        "FLOTATION_COLONNE",
    ],
    "regrind": ["ISAMILL", "VERTIMILL_REGRIND", "SMD", "UFG"],
    "gravity": ["GRAVITE_KNELSON", "GRAVITE_FALCON"],
    "pretreatment": ["BIOX", "POX", "ROASTING"],
    "leaching": [
        "PREAERATION", "LEACH_CUVES", "CIL", "CIP", "HEAP_LEACH", "VAT_LEACH",
        "ELUTION_AARL", "ELUTION_ZADRA", "ELECTROWINNING", "FONDERIE",
        "DETOX_INCO", "DETOX_CARO", "DETOX_PEROXIDE", "DETOX_BERLINER",
        "DETOX_OZONE", "DETOX_BIO", "DETOX_NEUTRALISATION",
    ],
    "auxiliaries": [
        "EPAISSISSEUR", "EPAISSISSEUR_HD", "EPAISSISSEUR_CONC",
        "TSF_CONVENTIONNEL", "TSF_DRY_STACK", "PASTE_THICKENING",
        "BASSIN_EAU", "TRAITEMENT_EFFLUENT",
        "REACTIF_PAX", "REACTIF_MIBC", "REACTIF_FLOCCULANT", "REACTIF_LIME",
        "REACTIF_NACN", "REACTIF_CUSO4", "REACTIF_NAOH", "REACTIF_ACID",
        "REACTIF_SO2", "REACTIF_OXYGEN", "REACTIF_CARBON",
    ],
}

_CATALOG_OP_MAP = {
    "BM": "BALL_MILL",
    "CRUSH": "GIRATOIRE",
    "FLOTATION": "FLOTATION_ROUGHER",
    "GRAVITY": "GRAVITE_KNELSON",
    "HEAP": "HEAP_LEACH",
    "PEBBLE_CRUSHER": "CONE",
    "REGRIND": "ISAMILL",
    "SAG": "SAG_MILL",
}


def _selected_op_codes(tid: str) -> set[str]:
    rows = qall(
        "SELECT op_code FROM circuit_operations WHERE template_id = %s AND enabled = TRUE",
        (tid,),
    )
    return {r["op_code"] for r in rows or []}


def _apply_operation_flags(flags: dict, op_codes: set[str]) -> dict:
    if {"FLOTATION_ROUGHER", "FLOTATION_SCAVENGER", "FLOTATION_CLEANER", "FLOTATION_COLONNE"} & op_codes:
        flags["has_flotation"] = True
    if {"GRAVITE_KNELSON", "GRAVITE_FALCON"} & op_codes:
        flags["has_gravity"] = True
    if "HPGR" in op_codes:
        flags["has_hpgr"] = True
    if {"ISAMILL", "VERTIMILL_REGRIND", "SMD", "UFG"} & op_codes:
        flags["has_isamill"] = True
    if {"BIOX", "POX", "ROASTING", "UFG"} & op_codes:
        flags["has_pretreat"] = True
    return flags


def _generate_and_enrich_criteria(pid: str, tid: str, op_codes: list[str], user_id: str) -> int:
    try:
        from .circuit import _generate_default_criteria
    except ImportError:
        from circuit import _generate_default_criteria

    total = 0
    for i, op_code in enumerate(op_codes):
        total += _generate_default_criteria(pid, tid, op_code, user_id, (i + 1) * 10)

    try:
        try:
            from ..engines.dc_generator import enrich_criteria_with_lims
            from ..db import conn, release
        except ImportError:  # pragma: no cover
            from engines.dc_generator import enrich_criteria_with_lims
            from db import conn, release

        c = conn()
        cur = None
        try:
            import psycopg2.extras
            cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            enrich_criteria_with_lims(pid, tid, cur)
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            if cur is not None:
                cur.close()
            release(c)
    except Exception as e:
        logger.warning("dc_pipeline criteria enrichment failed for template %s: %s", tid, e)

    return total


def _get_template(pid: str) -> dict:
    """Get the latest circuit template for a project, or raise 404."""
    t = qone(
        "SELECT id, pipeline_state FROM circuit_templates WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if not t:
        raise HTTPException(404, "Aucun circuit template. Créez-en un via l'étape Topologie.")
    return t


def _lims_recommendation(pid: str, step_id: str) -> dict:
    """Build a LIMS-driven recommendation summary for a given step."""
    rec = {"summary": "", "confidence": "low", "lims_count": 0}

    if step_id == "data_input":
        # Count LIMS tests by type
        counts = {}
        from .lims import LIMS_TABLES, safe_table_name
        for code, table in LIMS_TABLES.items():
            tbl = safe_table_name(table)
            row = qone(f"SELECT COUNT(*) as cnt FROM {tbl} WHERE project_id = %s", (pid,))
            counts[code] = row["cnt"] if row else 0
        total = sum(counts.values())
        rec["summary"] = f"{total} tests LIMS disponibles"
        rec["confidence"] = "high" if total > 30 else "medium" if total > 10 else "low"
        rec["lims_count"] = total
        rec["detail"] = counts

    elif step_id == "comminution":
        b1 = qall("SELECT bwi_kwh_t, p80_target_um FROM lims_b1 WHERE project_id=%s", (pid,))
        if b1:
            vals = [float(r["bwi_kwh_t"]) for r in b1 if r.get("bwi_kwh_t")]
            if vals:
                avg_bwi = sum(vals) / len(vals)
                rec["summary"] = f"BWi moyen: {avg_bwi:.1f} kWh/t ({len(vals)} tests B1)"
                rec["confidence"] = "high" if len(vals) >= 10 else "medium" if len(vals) >= 5 else "low"
                rec["lims_count"] = len(vals)

    elif step_id == "flotation":
        try:
            g1 = qall("SELECT recovery_pct, mass_pull_pct FROM lims_flotation WHERE project_id=%s", (pid,))
        except Exception:  # intentional: fallback to empty/default on optional data
            g1 = []
        if g1:
            recs = [float(r["recovery_pct"]) for r in g1 if r.get("recovery_pct")]
            pulls = [float(r["mass_pull_pct"]) for r in g1 if r.get("mass_pull_pct")]
            if recs:
                rec["summary"] = f"Recovery moy: {sum(recs)/len(recs):.1f}%, Mass pull: {sum(pulls)/len(pulls):.1f}% ({len(recs)} tests G1)"
                rec["confidence"] = "high" if len(recs) >= 5 else "medium"
                rec["lims_count"] = len(recs)

    elif step_id == "leaching":
        d1 = qall("SELECT au_recovery_pct, nacn_consumption_kg_t FROM lims_d1 WHERE project_id=%s", (pid,))
        if d1:
            recs = [float(r["au_recovery_pct"]) for r in d1 if r.get("au_recovery_pct")]
            if recs:
                avg_rec = sum(recs) / len(recs)
                rec["summary"] = f"Recovery lixiviation moy: {avg_rec:.1f}% ({len(recs)} tests D1)"
                rec["confidence"] = "high" if len(recs) >= 15 else "medium" if len(recs) >= 5 else "low"
                rec["lims_count"] = len(recs)

    elif step_id == "gravity":
        c2 = qall("SELECT au_recovery_pct FROM lims_c2 WHERE project_id=%s", (pid,))
        if c2:
            vals = [float(r["au_recovery_pct"]) for r in c2 if r.get("au_recovery_pct")]
            if vals:
                rec["summary"] = f"GRG moyen: {sum(vals)/len(vals):.1f}% ({len(vals)} tests C2)"
                rec["confidence"] = "high" if len(vals) >= 5 else "medium"
                rec["lims_count"] = len(vals)

    elif step_id == "auxiliaries":
        e1 = qall("SELECT unit_area_m2_t_d FROM lims_e1 WHERE project_id=%s", (pid,))
        if e1:
            vals = [float(r["unit_area_m2_t_d"]) for r in e1 if r.get("unit_area_m2_t_d")]
            if vals:
                rec["summary"] = f"Unit area moy: {sum(vals)/len(vals):.3f} m²·t/d ({len(vals)} tests E1)"
                rec["lims_count"] = len(vals)

    elif step_id == "freeze":
        # Summary: count total DC rows
        t = _get_template(pid)
        count = qone("SELECT COUNT(*) as cnt FROM design_criteria_v2 WHERE template_id=%s AND enabled=TRUE", (t["id"],))
        rec["summary"] = f"{count['cnt'] if count else 0} critères de conception"

    return rec


class TopologyRequest(BaseModel):
    mode: str  # "recommended", "custom", "catalog"
    operations: Optional[list[str]] = None  # for mode "custom"
    circuit_id: Optional[str] = None  # for mode "catalog"


@router.post("/{pid}/dc-pipeline/topology")
def set_topology(pid: str, body: TopologyRequest, user=Depends(_engineer_role)):
    """Set the circuit topology. Three modes: recommended (LIMS-driven), custom, catalog."""
    try:
        from ..helpers import get_circuit_flags
    except ImportError:
        from helpers import get_circuit_flags

    try:
        # Get or create circuit template
        template = qone(
            "SELECT id FROM circuit_templates WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        if not template:
            # Create a new template
            template = execute(
                "INSERT INTO circuit_templates (project_id, name, created_by) VALUES (%s, %s, %s) RETURNING *",
                (pid, "Pipeline Auto", user["id"]),
            )
        tid = template["id"]

        operations = []
        alerts = []
        compatibility_score = None

        if body.mode == "recommended":
            # Use circuit.py suggest logic
            a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
            b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
            c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
            _d1 = qall("SELECT * FROM lims_d1 WHERE project_id=%s", (pid,))
            try:
                g1 = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
            except Exception:  # intentional: fallback to empty/default on optional data
                g1 = []
            try:
                _e1 = qall("SELECT * FROM lims_e1 WHERE project_id=%s", (pid,))
            except Exception:  # intentional: fallback to empty/default on optional data
                _e1 = []

            def _avg(rows, field, default=None):
                vals = [float(r[field]) for r in rows if r.get(field) not in (None, "", 0)]
                return sum(vals) / len(vals) if vals else default

            # Build suggestions from LIMS (same logic as circuit.py suggest)
            suggestions = []

            # Always include basic comminution
            suggestions.append({"op_code": "GIRATOIRE", "reason": "Concassage primaire standard", "confidence": "high"})

            avg_bwi = _avg(b1, "bwi_kwh_t")
            if avg_bwi and avg_bwi > 16:
                suggestions.append({"op_code": "HPGR", "reason": f"BWi={avg_bwi:.1f} > 16 kWh/t", "confidence": "high"})

            suggestions.append({"op_code": "SAG_MILL", "reason": "Broyage SAG standard", "confidence": "high"})
            suggestions.append({"op_code": "BALL_MILL", "reason": "Broyage secondaire", "confidence": "high"})

            avg_grg = _avg(c2, "au_recovery_pct", 0)
            if avg_grg and avg_grg >= 10:
                suggestions.append({"op_code": "GRAVITE_KNELSON", "reason": f"GRG={avg_grg:.1f}% >= 10%", "confidence": "high" if avg_grg > 30 else "medium"})

            avg_s = _avg(a1, "s_total_pct", 0)
            avg_flot = _avg(g1, "recovery_pct", 0) if g1 else 0
            avg_c_org = _avg(a1, "c_organic_pct", 0)
            if (avg_s and avg_s > 2.5 or avg_flot > 50) and (not avg_c_org or avg_c_org < 0.3):
                suggestions.append({"op_code": "FLOTATION_ROUGHER", "reason": f"S={avg_s:.1f}%, Flot rec={avg_flot:.0f}%", "confidence": "high"})
                suggestions.append({"op_code": "ISAMILL", "reason": "Rebroyage concentré flotation", "confidence": "medium"})

            # Leach circuit selection
            if avg_c_org and avg_c_org > 0.3:
                suggestions.append({"op_code": "CIP", "reason": f"C_org={avg_c_org:.2f}% > 0.3% (preg-robbing)", "confidence": "high"})
            else:
                suggestions.append({"op_code": "CIL", "reason": "CIL standard (pas de preg-robbing)", "confidence": "high"})

            suggestions.append({"op_code": "ELUTION_AARL", "reason": "Élution AARL standard", "confidence": "medium"})
            suggestions.append({"op_code": "ELECTROWINNING", "reason": "Électrolyse standard", "confidence": "high"})
            suggestions.append({"op_code": "DETOX_INCO", "reason": "Détoxification SO₂/Air", "confidence": "medium"})
            suggestions.append({"op_code": "EPAISSISSEUR", "reason": "Épaississeur résidus", "confidence": "high"})

            # Clear existing operations and insert suggestions
            execute("DELETE FROM design_criteria_v2 WHERE template_id = %s", (tid,))
            execute("DELETE FROM circuit_operations WHERE template_id = %s", (tid,))
            for i, sug in enumerate(suggestions):
                try:
                    execute(
                        "INSERT INTO circuit_operations (template_id, op_code, enabled, sort_order, created_by) "
                        "VALUES (%s, %s, TRUE, %s, %s) ON CONFLICT (template_id, op_code) DO UPDATE SET enabled=TRUE, sort_order=%s",
                        (tid, sug["op_code"], i * 10, user["id"], i * 10),
                    )
                except Exception as e:  # intentional: graceful fallback on optional operation
                    logger.warning("Skip op %s: %s", sug["op_code"], e)
                    continue

            operations = suggestions
            _generate_and_enrich_criteria(pid, tid, [o["op_code"] for o in operations], user["id"])

        elif body.mode == "custom":
            if not body.operations:
                raise HTTPException(400, "operations requises pour le mode custom")

            execute("DELETE FROM design_criteria_v2 WHERE template_id = %s", (tid,))
            execute("DELETE FROM circuit_operations WHERE template_id = %s", (tid,))
            for i, op_code in enumerate(body.operations):
                cat = qone("SELECT op_code, label FROM unit_operations_catalog WHERE op_code=%s", (op_code,))
                if not cat:
                    alerts.append({"severity": "warning", "message": f"Opération {op_code} inconnue dans le catalogue et ignorée."})
                    continue
                try:
                    execute(
                        "INSERT INTO circuit_operations (template_id, op_code, enabled, sort_order, created_by) "
                        "VALUES (%s, %s, TRUE, %s, %s) ON CONFLICT (template_id, op_code) DO UPDATE SET enabled=TRUE, sort_order=%s",
                        (tid, op_code, i * 10, user["id"], i * 10),
                    )
                    operations.append({"op_code": op_code, "reason": "Sélection manuelle", "confidence": "manual"})
                except Exception as e:  # intentional: collect error and continue processing
                    alerts.append({"severity": "warning", "message": f"Opération {op_code} ignorée: {str(e)}"})
            _generate_and_enrich_criteria(pid, tid, [o["op_code"] for o in operations], user["id"])

        elif body.mode == "catalog":
            if not body.circuit_id:
                raise HTTPException(400, "circuit_id requis pour le mode catalog")
            try:
                from ..engines.circuit_optimizer import CIRCUITS
            except ImportError:
                from engines.circuit_optimizer import CIRCUITS

            circuit = next((c for c in CIRCUITS if c["id"] == body.circuit_id), None)
            if not circuit:
                raise HTTPException(404, f"Circuit {body.circuit_id} non trouvé dans le catalogue")

            # Map circuit ops to op_codes
            op_map = _CATALOG_OP_MAP
            execute("DELETE FROM design_criteria_v2 WHERE template_id = %s", (tid,))
            execute("DELETE FROM circuit_operations WHERE template_id = %s", (tid,))
            for i, op in enumerate(circuit.get("ops", [])):
                mapped = op_map.get(op, op)
                cat = qone("SELECT op_code FROM unit_operations_catalog WHERE op_code=%s", (mapped,))
                if not cat:
                    alerts.append({"severity": "warning", "message": f"Opération catalogue {op} ({mapped}) ignorée: absente du catalogue d'équipements."})
                    continue
                try:
                    execute(
                        "INSERT INTO circuit_operations (template_id, op_code, enabled, sort_order, created_by) "
                        "VALUES (%s, %s, TRUE, %s, %s) ON CONFLICT (template_id, op_code) DO UPDATE SET enabled=TRUE",
                        (tid, mapped, i * 10, user["id"]),
                    )
                    operations.append({"op_code": mapped, "reason": f"Catalogue {body.circuit_id}: {circuit['name']}", "confidence": "catalog"})
                except Exception as e:  # intentional: collect optional lookup failure
                    alerts.append({"severity": "warning", "message": f"Opération {mapped} ignorée: {str(e)}"})
            _generate_and_enrich_criteria(pid, tid, [o["op_code"] for o in operations], user["id"])

            compatibility_score = round(circuit.get("base_recovery", 0) * 100, 0)
        else:
            raise HTTPException(400, f"Mode '{body.mode}' non supporté. Options: recommended, custom, catalog")

        # Update pipeline_state: mark topology as validated
        execute(
            "UPDATE circuit_templates SET pipeline_state = pipeline_state || %s::jsonb WHERE id = %s",
            (json.dumps({"topology": "validated", "data_input": "validated"}), tid),
        )

        # Rebuild pipeline steps based on new operations
        a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
        b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
        c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
        try:
            g1 = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
        except Exception:  # intentional: fallback to empty/default on optional data
            g1 = []
        flags = get_circuit_flags(pid, a1, b1, c2, g1)

        # Override flags based on actual operations selected
        op_codes = {o["op_code"] for o in operations}
        flags = _apply_operation_flags(flags, op_codes)

        pipeline_steps = _build_pipeline_steps(flags)

        result = {
            "template_id": str(tid),
            "mode": body.mode,
            "operations": operations,
            "pipeline_steps": pipeline_steps,
            "alerts": alerts,
        }
        if compatibility_score is not None:
            result["compatibility_score"] = compatibility_score
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


class StepRequest(BaseModel):
    step_id: str
    action: str  # "load" or "validate"


@router.post("/{pid}/dc-pipeline/step")
def handle_step(pid: str, body: StepRequest, user=Depends(project_user)):
    """Load or validate a pipeline step."""
    try:
        if body.step_id not in _STEP_SECTIONS and body.step_id != "topology":
            raise HTTPException(400, f"step_id '{body.step_id}' inconnu")

        t = _get_template(pid)
        tid = t["id"]
        pipeline_state = t.get("pipeline_state") or {}

        if body.action == "load":
            sections = _STEP_SECTIONS.get(body.step_id, [])

            if body.step_id == "freeze":
                # Special: return all DC rows as summary.
                # NOTE: include `id` (UUID) and `dag_key` so the frontend
                # can PATCH by primary key (audit final review §1) and the
                # cascade flow can use the canonical DAG node id directly
                # without reconstructing it from `ref_number`.
                rows = qall(
                    "SELECT id, dag_key, op_code, ref_number, section_title, item, unit, design_value, nominal_value, "
                    "min_value, max_value, source_code, revision, author, comments, version "
                    "FROM design_criteria_v2 WHERE template_id = %s AND enabled = TRUE "
                    "ORDER BY sort_order, ref_number",
                    (tid,),
                )
            elif sections:
                # Match by selected operation codes first, with section-title
                # keywords as a fallback for older rows/templates.
                step_ops = _STEP_OP_CODES.get(body.step_id, [])
                like_clauses = " OR ".join(["section_title ILIKE %s"] * len(sections))
                op_clauses = " OR ".join(["op_code = %s"] * len(step_ops))
                filters = []
                params = [tid]
                if like_clauses:
                    filters.append(f"({like_clauses})")
                    params.extend([f"%{s}%" for s in sections])
                if op_clauses:
                    filters.append(f"({op_clauses})")
                    params.extend(step_ops)
                # Same UUID + dag_key plumbing as the freeze branch above.
                rows = qall(
                    f"SELECT id, dag_key, op_code, ref_number, section_title, item, unit, design_value, nominal_value, "
                    f"min_value, max_value, source_code, revision, author, comments, version "
                    f"FROM design_criteria_v2 WHERE template_id = %s AND enabled = TRUE "
                    f"AND ({' OR '.join(filters)}) ORDER BY sort_order, ref_number",
                    tuple(params),
                )
            else:
                rows = []

            recommendation = _lims_recommendation(pid, body.step_id)

            # Get full pipeline state
            try:
                from ..helpers import get_circuit_flags
            except ImportError:
                from helpers import get_circuit_flags
            a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
            b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
            c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
            try:
                g1 = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
            except Exception:  # intentional: fallback to empty/default on optional data
                g1 = []
            flags = _apply_operation_flags(get_circuit_flags(pid, a1, b1, c2, g1), _selected_op_codes(tid))
            steps = _build_pipeline_steps(flags)
            # Apply saved statuses
            for s in steps:
                s["status"] = pipeline_state.get(s["step_id"], "pending")

            return {
                "step_id": body.step_id,
                "status": pipeline_state.get(body.step_id, "pending"),
                "recommendation": recommendation,
                "dc_rows": rows or [],
                "pipeline": steps,
            }

        elif body.action == "validate":
            # Mark this step as validated
            pipeline_state[body.step_id] = "validated"
            execute(
                "UPDATE circuit_templates SET pipeline_state = %s::jsonb WHERE id = %s",
                (json.dumps(pipeline_state), tid),
            )

            # Rebuild pipeline with updated statuses
            try:
                from ..helpers import get_circuit_flags
            except ImportError:
                from helpers import get_circuit_flags
            a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
            b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
            c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
            try:
                g1 = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
            except Exception:  # intentional: fallback to empty/default on optional data
                g1 = []
            flags = _apply_operation_flags(get_circuit_flags(pid, a1, b1, c2, g1), _selected_op_codes(tid))
            steps = _build_pipeline_steps(flags)
            for s in steps:
                s["status"] = pipeline_state.get(s["step_id"], "pending")

            return {
                "step_id": body.step_id,
                "status": "validated",
                "pipeline": steps,
            }

        else:
            raise HTTPException(400, f"action '{body.action}' non supportée. Options: load, validate")
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/dc-pipeline/steps")
def get_pipeline_steps(pid: str, user=Depends(project_user)):
    """Get the current pipeline steps for this project based on circuit flags."""
    try:
        from ..helpers import get_circuit_flags
    except ImportError:
        from helpers import get_circuit_flags

    try:
        template = qone(
            "SELECT id FROM circuit_templates WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        if not template:
            raise HTTPException(404, "Aucun circuit template. Configurez la topologie du circuit.")

        a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
        b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
        c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
        try:
            g1 = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
        except Exception:  # intentional: fallback to empty/default on optional data
            g1 = []

        flags = get_circuit_flags(pid, a1, b1, c2, g1)
        flags = _apply_operation_flags(flags, _selected_op_codes(template["id"]))
        steps = _build_pipeline_steps(flags)
        return {"steps": steps, "flags": flags}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


class DCChange(BaseModel):
    key: str
    value: float


class CascadeRequest(BaseModel):
    changes: list[DCChange]


@router.post("/{pid}/dc-pipeline/cascade")
def run_cascade(pid: str, body: CascadeRequest, user=Depends(_engineer_role)):
    """Run cascade recalculation for DC changes."""
    try:
        dag = load_dag()

        # Load current DC values from design_criteria_v2
        template = qone(
            "SELECT id FROM circuit_templates WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        if not template:
            raise HTTPException(404, "Aucun circuit template. Créez-en un à l'étape Topologie.")

        tid = template["id"]
        # Only rows that carry an explicit `dag_key` participate in the cascade
        # snapshot — that key is the canonical DAG node id, populated by the
        # catalog seeding (`_generate_default_criteria`) and by the LIMS /
        # calculator writers. Rows without a `dag_key` are descriptive
        # parameters (model, motor type, dimensions) that don't drive the DAG.
        rows = qall(
            "SELECT id, ref_number, dag_key, item, design_value, source_code "
            "FROM design_criteria_v2 "
            "WHERE template_id = %s AND enabled = TRUE AND dag_key IS NOT NULL",
            (tid,),
        )

        # Build current values and source map from DB + project settings
        project = qone("SELECT * FROM projects WHERE id = %s", (pid,))
        current_values = {
            "target_tph": float(project.get("target_tph") or 1596),
            "gold_grade_g_t": float(project.get("gold_grade_g_t") or 1.5),
            "operating_hours_day": float(project.get("operating_hours_day") or 24),
            "availability_pct": float(project.get("availability_pct") or 92),
        }
        source_map = {}

        # Override project-table seed values with the v2 row for any DAG input that has a corresponding catalog row. The v2 row is the source of truth for the engineer's current PDC; the project-table seed only serves as a safety default for keys with no v2 row.
        for r in rows:
            key = r["dag_key"]
            if not key:
                continue
            if r.get("design_value") is None:
                continue
            current_values[key] = float(r["design_value"])
            source_map[key] = r.get("source_code", "X")

        updates, alerts = cascade_recalculate(
            dag=dag,
            current_values=current_values,
            source_map=source_map,
            changes=[{"key": c.key, "value": c.value} for c in body.changes],
        )

        persisted_updates = []
        for update in updates:
            key = update.get("key")
            if not key:
                continue
            new_value = update.get("new")
            if new_value is None:
                continue
            existing_source = source_map.get(key)
            if existing_source in ("M", "O", "Manual"):
                continue
            row = execute(
                "UPDATE design_criteria_v2 "
                "SET design_value = %s, source_code = 'C', "
                "    version = COALESCE(version, 1) + 1, updated_at = NOW(), updated_by = %s "
                "WHERE template_id = %s AND dag_key = %s "
                "  AND enabled = TRUE "
                "  AND COALESCE(source_code, 'X') NOT IN ('M', 'O') "
                "RETURNING id, version, dag_key, design_value, source_code",
                (new_value, user["id"], tid, key),
            )
            if row:
                persisted_updates.append(dict(row))

        if persisted_updates:
            record_event(
                user_id=user["id"],
                project_id=pid,
                entity_type="design_criteria_v2",
                entity_id=None,
                action="cascade_recalculate",
                old_value={"changes": [c.model_dump() for c in body.changes]},
                new_value={"updates": persisted_updates},
                source="web",
            )

        return {"updates": updates, "persisted": persisted_updates, "alerts": alerts}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# Allowed fields for PATCH /dc-pipeline/rows/{rid}. Kept narrow because the
# row carries provenance metadata (source_code, version, updated_by) that we
# don't want clients to forge directly.
_DCV2_PATCH_ALLOWED = frozenset(
    [
        "design_value", "nominal_value", "min_value", "max_value",
        "source_code", "revision", "author", "comments",
    ]
)


class V2PatchBody(BaseModel):
    design_value: Optional[float] = None
    nominal_value: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    source_code: Optional[str] = None
    revision: Optional[str] = None
    author: Optional[str] = None
    comments: Optional[str] = None
    # Optimistic-locking opt-in (audit final review §3). Clients that know
    # the row's last-seen version pass it here; the UPDATE then includes
    # `AND version = %s` and a mismatch surfaces as 409. Omitted → legacy
    # last-write-wins behaviour preserved (backwards-compat for older
    # clients that haven't adopted the field yet).
    expected_version: Optional[int] = None


@router.patch("/{pid}/dc-pipeline/rows/{rid}")
def patch_v2_row(
    pid: str,
    rid: str,
    body: V2PatchBody,
    user=Depends(require_project_role("Process Engineer", "Metallurgist", "Project Manager")),
):
    """Update a `design_criteria_v2` row keyed by its UUID, scoped to project `pid`.

    Living Circuit edit flow lands here. The row's `template_id` must belong
    to a `circuit_template` whose `project_id` is `pid`; otherwise 404.

    Replaces the legacy `PATCH /design-criteria/rows/{rid}` for cascade
    purposes — the legacy endpoint still exists (and is deprecated) but
    writes to the legacy `design_criteria` table that the cascade engine
    never reads.

    Auth: `require_project_role` combines role-check (engineer-class) with
    project-membership-check, so a user who is not a member of `pid` is
    rejected before we even reach the JOIN check below.
    """
    try:
        # Pop the optimistic-locking sentinel before building the SET list
        # so it doesn't leak into the UPDATE (it's a guard in WHERE, not a
        # column to write).
        expected_version = body.expected_version
        changes = body.model_dump(exclude_none=True)
        changes.pop("expected_version", None)
        if not changes:
            raise HTTPException(400, "Rien à mettre à jour")

        # Verify the row's template belongs to this project — defense in depth
        # so a hostile caller can't patch another project's row by knowing its
        # UUID. We also fetch the full pre-update row here so we can capture
        # `old_value` for the audit replay (bonus #5).
        existing = qone(
            "SELECT v.* FROM design_criteria_v2 v "
            "JOIN circuit_templates t ON v.template_id = t.id "
            "WHERE v.id = %s AND t.project_id = %s",
            (rid, pid),
        )
        if not existing:
            raise HTTPException(404, "Row non trouvé pour ce projet")

        # Snapshot only the fields we are about to mutate, so the audit
        # `old_value` mirrors the shape of `new_value` (the `changes` dict).
        # Numeric DB columns surface as Decimal — coerce to float so
        # `psycopg2.extras.Json` (used by `record_event`) can serialize
        # without a TypeError.
        old_value = {
            k: (float(existing[k]) if isinstance(existing.get(k), Decimal) else existing.get(k))
            for k in changes.keys()
        }

        fields, vals = build_update_sets(changes, allowed=_DCV2_PATCH_ALLOWED)
        if not fields:
            raise HTTPException(400, "Rien à mettre à jour")
        # Always bump updated_at, version, and updated_by on a write so the
        # row carries a fresh optimistic-locking signal and provenance.
        # SQL: fields is a list of "col=%s" fragments built by
        # build_update_sets — safe to interpolate.
        set_clause = ", ".join(
            fields
            + ["updated_at = NOW()", "version = COALESCE(version, 1) + 1", "updated_by = %s"]
        )
        # Build the WHERE clause. When the client supplied
        # `expected_version`, append `AND version = %s` so the UPDATE
        # only touches the row when its current version matches what the
        # client last saw (audit final review §3 — optimistic locking).
        # No-rows-updated → 409, distinguishing a conflict from a 404
        # caused by a missing row.
        where_clause = "id = %s"
        vals = vals + [user["id"], rid]
        if expected_version is not None:
            where_clause += " AND version = %s"
            vals = vals + [expected_version]
        row = execute(
            f"UPDATE design_criteria_v2 SET {set_clause} WHERE {where_clause} RETURNING *",  # noqa: S608
            vals,
        )
        if row is None or row == {}:
            # When expected_version was supplied, an empty update result
            # most likely means the row's version moved on (another writer
            # got there first). Surface as 409 so clients can distinguish
            # this from a vanished row.
            if expected_version is not None:
                raise HTTPException(
                    409,
                    detail="Version conflict — row was modified by another user",
                )
            # Row vanished between the project-scope check and the update —
            # surface as a 404 rather than a confusing 500.
            raise HTTPException(404, "Row non trouvé pour ce projet")

        record_event(
            user_id=user["id"], project_id=pid,
            entity_type="design_criteria_v2", entity_id=str(rid),
            action="update", old_value=old_value, new_value=changes, source="web",
        )
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


class FreezeRequest(BaseModel):
    version_label: str
    ni43101_stage: Optional[str] = None
    notes: Optional[str] = None


@router.post("/{pid}/dc-pipeline/freeze")
def freeze_snapshot(pid: str, body: FreezeRequest, user=Depends(_engineer_role)):
    """Freeze current DC state as an immutable snapshot."""
    try:
        template = qone(
            "SELECT id FROM circuit_templates WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (pid,),
        )
        if not template:
            raise HTTPException(404, "Aucun circuit template.")

        tid = template["id"]
        # Include `id` and `dag_key` in the snapshot payload so post-freeze
        # diffs/cascades can resolve a row by its UUID — same plumbing as
        # the load step (audit final review §1).
        rows = qall(
            "SELECT id, dag_key, op_code, ref_number, section_title, item, unit, design_value, nominal_value, "
            "min_value, max_value, source_code, revision, author, comments "
            "FROM design_criteria_v2 WHERE template_id = %s AND enabled = TRUE "
            "ORDER BY sort_order, ref_number",
            (tid,),
        )
        if not rows:
            raise HTTPException(400, "Aucun critère de conception à figer.")

        snapshot_data = rows
        canonical = json.dumps(snapshot_data, sort_keys=True, default=str)
        checksum = hashlib.sha256(canonical.encode()).hexdigest()

        # Get previous snapshot
        prev = qone(
            "SELECT id FROM dc_snapshots WHERE project_id = %s ORDER BY frozen_at DESC LIMIT 1",
            (pid,),
        )

        row = execute(
            "INSERT INTO dc_snapshots "
            "(project_id, previous_snapshot_id, version_label, ni43101_stage, notes, "
            "snapshot_data, frozen_by, checksum_sha256) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s) RETURNING *",
            (pid, prev["id"] if prev else None, body.version_label,
             body.ni43101_stage, body.notes, canonical, user["id"], checksum),
        )

        # Emit audit event into the chained audit trail
        record_event(
            user_id=user["id"],
            project_id=pid,
            entity_type="dc_snapshot",
            entity_id=str(row["id"]),
            action="freeze",
            field_name="version",
            new_value={"version_label": body.version_label, "checksum": checksum},
            source="web",
        )

        # Mark downstream modules as stale
        try:
            from .pipeline import mark_stale_cascade
            mark_stale_cascade(pid, "design_criteria", user_id=user["id"])
        except Exception:  # intentional: ignore optional lookup failure
            pass

        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/dc-pipeline/snapshots")
def list_snapshots(pid: str, user=Depends(project_user)):
    try:
        rows = qall(
            "SELECT id, version_label, ni43101_stage, notes, frozen_by, frozen_at, checksum_sha256 "
            "FROM dc_snapshots WHERE project_id = %s ORDER BY frozen_at DESC",
            (pid,),
        )
        return rows or []
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/dc-pipeline/snapshots/{sid}")
def get_snapshot(pid: str, sid: str, user=Depends(project_user)):
    try:
        row = qone(
            "SELECT * FROM dc_snapshots WHERE id = %s AND project_id = %s",
            (sid, pid),
        )
        if not row:
            raise HTTPException(404, "Snapshot non trouvé")
        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/dc-pipeline/snapshots/{sid}/diff/{target}")
def diff_snapshots(pid: str, sid: str, target: str, user=Depends(project_user)):
    """Compare two snapshots, or a snapshot vs current draft (target='draft')."""
    try:
        snap1 = qone("SELECT snapshot_data FROM dc_snapshots WHERE id=%s AND project_id=%s", (sid, pid))
        if not snap1:
            raise HTTPException(404, "Snapshot source non trouvé")

        if target == "draft":
            template = qone(
                "SELECT id FROM circuit_templates WHERE project_id=%s ORDER BY created_at DESC LIMIT 1",
                (pid,),
            )
            if not template:
                raise HTTPException(404, "Aucun circuit template")
            data2 = qall(
                "SELECT op_code, ref_number, section_title, item, unit, design_value, nominal_value, "
                "min_value, max_value, source_code, revision, author, comments "
                "FROM design_criteria_v2 WHERE template_id=%s AND enabled=TRUE "
                "ORDER BY sort_order, ref_number",
                (template["id"],),
            )
        else:
            snap2 = qone("SELECT snapshot_data FROM dc_snapshots WHERE id=%s AND project_id=%s", (target, pid))
            if not snap2:
                raise HTTPException(404, "Snapshot cible non trouvé")
            data2 = snap2["snapshot_data"]

        data1 = snap1["snapshot_data"]
        # Build diff by ref_number
        map1 = {r["ref_number"]: r for r in data1}
        map2 = {r["ref_number"]: r for r in (data2 or [])}

        changes = []
        for ref in set(list(map1.keys()) + list(map2.keys())):
            r1 = map1.get(ref, {})
            r2 = map2.get(ref, {})
            if r1.get("design_value") != r2.get("design_value"):
                changes.append({
                    "ref_number": ref,
                    "item": r1.get("item") or r2.get("item"),
                    "old_value": r1.get("design_value"),
                    "new_value": r2.get("design_value"),
                    "unit": r1.get("unit") or r2.get("unit"),
                })

        return {"source": sid, "target": target, "changes": changes, "total_changes": len(changes)}
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@lru_cache(maxsize=1)
def _cached_dag():
    """Process-lifetime cache around the YAML DAG.

    `load_dag()` parses `dc_dag_registry.yaml` from disk on every call.
    The DAG is read-only at runtime, so the YAML can be parsed once per
    process. We wrap the loader here (rather than modifying
    `dc_cascade.py`) so the uncached path stays available for
    `run_cascade` and other callers that may want the latest copy.
    """
    return load_dag()


@router.get("/{pid}/dc-pipeline/dependencies")
def get_dependencies(pid: str, user=Depends(project_user)):
    """Expose the cascade DAG (inputs + computed nodes) for the Living Circuit.

    The DAG is loaded from dc_dag_registry.yaml. Project-scoped at the URL
    so future per-project enrichment is non-breaking; v1.0 returns the same
    payload for any pid the user can access.
    """
    try:
        dag = _cached_dag()
        return {
            "inputs": dag.get("inputs", []),
            "nodes": dag.get("nodes", {}),
        }
    except FileNotFoundError:
        raise HTTPException(503, detail="DAG registry unavailable")
