"""
Metallurgical plant design & process simulation QA.

References:
- MetPlant 2008 — *Metallurgical Plant Design and Operating Strategies*:
  comminution testwork hierarchy (pilot → SMC/BWi → variability), flotation
  variability, scale-up discipline.
- SLA (2008) — *Process Simulation Best Practices* (MetPlant 2008):
  study-level testwork requirements (O'Callaghan 2001 Table 1), iterative model
  development, GIGO / calibration against testwork, mass & energy balance audit.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("mpdpms.plant_design")

REFERENCE_METPLANT = (
    "MetPlant 2008 — Metallurgical Plant Design and Operating Strategies "
    "(comminution test hierarchy, variability, scale-up)."
)
REFERENCE_SLA = (
    "SLA (2008) — Process Simulation Best Practices, MetPlant 2008 "
    "(study-level testwork, model lifecycle, validation vs pilot/lab)."
)

WEIGHTS_VERSION = "2026-05-16-metplant-sla-v1"

# SLA / O'Callaghan 2001 — typical testwork by engineering study stage
STUDY_TESTWORK: dict[str, dict[str, Any]] = {
    "desktop": {
        "label": "Revue technologique / desktop",
        "lims_min": {},
        "samples_min": 0,
        "sla_note": "Aucun essai requis — revue bibliographique / analogues.",
    },
    "scoping": {
        "label": "Étude de cadrage (scoping)",
        "lims_min": {"a1": 3, "b1": 1, "d1": 1},
        "samples_min": 5,
        "sla_note": "Bench scale — preuve de concept (SLA Table 1).",
    },
    "pfs": {
        "label": "Pré-faisabilité (PFS)",
        "lims_min": {"a1": 8, "b1": 4, "d1": 2, "g1": 2},
        "samples_min": 12,
        "lithology_min": 2,
        "sla_note": "Bench scale optimisé ; mini-pilote si unité critique à risque.",
    },
    "fs": {
        "label": "Faisabilité (FS)",
        "lims_min": {"a1": 15, "b1": 8, "d1": 4, "g1": 4, "a3": 2},
        "samples_min": 25,
        "lithology_min": 3,
        "pilot_recommended": True,
        "sla_note": "Pilote à recycles fermés ; démonstration si scale-up incertain.",
    },
}

# MetPlant — comminution hierarchy indicators (minimum rows for design confidence)
COMMINUTION_DESIGN_MIN = {
    "sag_ball": {"b1": 5, "note": "SAG exige hiérarchie BWi / Ai / variabilité (MetPlant Table 1–2)."},
    "hpgr_ball": {"b1": 4, "note": "HPGR : BWi + essais de pression recommandés."},
    "ball_only": {"b1": 3, "note": "Broyage boulets : BWi obligatoire."},
}

# SLA — simulation model development stages (checklist)
SLA_MODEL_STAGES = [
    ("data_collection", "Collecte données & PFD", "flowsheet + critères DC"),
    ("testwork_adequacy", "Essais vs niveau d'étude", "LIMS couvrant le flowsheet"),
    ("feed_characterisation", "Minéralogie / chimie alimentation", "MIN-01a + MIN-03 si preg-robbing"),
    ("model_build", "Construction modèle", "Paramètres simulation peuplés"),
    ("validation", "Validation & calage", "Bilan massique + runs convergés"),
    ("scenarios", "Scénarios & sensibilité", "Scénarios / historique simulation"),
]

try:
    from ..db import qall, qone
except ImportError:  # pragma: no cover
    from db import qall, qone


def _regclass_exists(qualified: str) -> bool:
    row = qone("SELECT to_regclass(%s) AS t", (qualified,))
    return bool(row and row.get("t"))


def _safe_int(row: dict | None, key: str) -> int:
    if not row or row.get(key) is None:
        return 0
    try:
        return int(row[key])
    except (TypeError, ValueError):
        return 0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def normalize_study_level(status: Optional[str]) -> str:
    """Map project.status → desktop | scoping | pfs | fs."""
    s = (status or "SCOPING").upper().replace("-", "_").replace(" ", "_")
    if s in ("DESKTOP", "TECH_REVIEW", "TECHNOLOGY_REVIEW", "CONCEPT"):
        return "desktop"
    if s in ("SCOPING", "PEA", "ORDER_OF_MAGNITUDE"):
        return "scoping"
    if s in ("PFS", "PRE_FEASIBILITY", "PREFEASIBILITY", "PRE_FEAS"):
        return "pfs"
    if s in ("FS", "FEASIBILITY", "BFS", "DFS", "ENGINEERING", "DETAILED", "DEFINITIVE"):
        return "fs"
    return "scoping"


def _count_lims_tables(pid: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    mapping = {
        "a1": "lims_a1",
        "a2": "lims_a2",
        "a3": "lims_a3",
        "b1": "lims_b1",
        "d1": "lims_d1",
        "g1": "lims_g1",
        "m1": "lims_m1",
        "c2": "lims_c2",
        "c2b": "lims_c2b",
        "c2c": "lims_c2c",
    }
    for key, table in mapping.items():
        if not _regclass_exists(f"public.{table}"):
            counts[key] = 0
            continue
        try:
            counts[key] = _safe_int(
                qone(f"SELECT COUNT(*)::int AS n FROM {table} WHERE project_id=%s", (pid,)),
                "n",
            )
        except Exception:
            logger.debug("lims count failed for %s", table, exc_info=True)
            counts[key] = 0
    return counts


def _count_samples(pid: str) -> tuple[int, int]:
    """Return (total samples, distinct lithology/domain tags if available)."""
    if not _regclass_exists("public.lims_samples"):
        return 0, 0
    row = qone(
        "SELECT COUNT(*)::int AS n FROM lims_samples WHERE project_id=%s",
        (pid,),
    ) or {}
    samples_n = _safe_int(row, "n")
    lith_n = samples_n
    try:
        lit_row = qone(
            "SELECT COUNT(DISTINCT COALESCE("
            "NULLIF(TRIM(lithology),''), NULLIF(TRIM(sample_type),''), id::text"
            "))::int AS n_lit FROM lims_samples WHERE project_id=%s",
            (pid,),
        ) or {}
        lith_n = max(_safe_int(lit_row, "n_lit"), 1 if samples_n else 0)
    except Exception:
        logger.debug("lithology diversity count skipped for %s", pid, exc_info=True)
    return samples_n, lith_n


def _count_scenarios(pid: str) -> int:
    """Count project + simulation scenarios (no legacy `scenarios` table)."""
    total = 0
    for table in ("project_scenarios", "simulation_scenarios"):
        if not _regclass_exists(f"public.{table}"):
            continue
        try:
            total += _safe_int(
                qone(f"SELECT COUNT(*)::int AS n FROM {table} WHERE project_id=%s", (pid,)),
                "n",
            )
        except Exception:
            logger.debug("scenario count failed for %s", table, exc_info=True)
    return total


def _bwi_variability(pid: str) -> dict[str, Any]:
    if not _regclass_exists("public.lims_b1"):
        return {"n": 0, "cv": None, "avg": None}
    rows = qall(
        "SELECT bwi_kwh_t, mb_kwh_t FROM lims_b1 WHERE project_id=%s",
        (pid,),
    ) or []
    vals = []
    for r in rows:
        v = r.get("bwi_kwh_t") if r.get("bwi_kwh_t") not in (None, "") else r.get("mb_kwh_t")
        if v is not None:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                pass
    if len(vals) < 2:
        avg = vals[0] if vals else None
        return {"n": len(vals), "cv": None, "avg": avg}
    mean = sum(vals) / len(vals)
    var = sum((x - mean) ** 2 for x in vals) / (len(vals) - 1)
    std = math.sqrt(var) if var > 0 else 0.0
    cv = (std / mean) if mean else None
    return {"n": len(vals), "cv": cv, "avg": mean}


def _active_template_id(pid: str) -> Optional[str]:
    """Resolve active circuit template (tolerates schema without is_active)."""
    if not _regclass_exists("public.circuit_templates"):
        return None
    queries = [
        "SELECT id::text AS id FROM circuit_templates "
        "WHERE project_id=%s AND COALESCE(is_active, FALSE)=TRUE "
        "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
        "SELECT id::text AS id FROM circuit_templates "
        "WHERE project_id=%s ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
    ]
    for sql in queries:
        try:
            row = qone(sql, (pid,))
            if row and row.get("id"):
                return str(row["id"])
        except Exception:
            logger.debug("template query failed: %s", sql[:60], exc_info=True)
    return None


def _template_op_codes(template_id: str) -> list[str]:
    """Read enabled op codes from circuit_operations or legacy circuit_template_operations."""
    if _regclass_exists("public.circuit_operations"):
        try:
            rows = qall(
                "SELECT op_code FROM circuit_operations "
                "WHERE template_id=%s AND COALESCE(enabled, TRUE)",
                (template_id,),
            ) or []
            return [str(r.get("op_code") or "").upper() for r in rows if r.get("op_code")]
        except Exception:
            logger.debug("circuit_operations read failed", exc_info=True)
    if _regclass_exists("public.circuit_template_operations"):
        try:
            rows = qall(
                "SELECT op_code FROM circuit_template_operations WHERE template_id=%s",
                (template_id,),
            ) or []
            return [str(r.get("op_code") or "").upper() for r in rows if r.get("op_code")]
        except Exception:
            logger.debug("circuit_template_operations read failed", exc_info=True)
    return []


def _active_grinding_route(pid: str) -> str:
    tpl_id = _active_template_id(pid)
    if not tpl_id:
        return "ball_only"
    ops = _template_op_codes(tpl_id)
    if any("SAG" in o for o in ops):
        return "sag_ball"
    if any("HPGR" in o for o in ops):
        return "hpgr_ball"
    return "ball_only"


def assess_testwork_program(
    pid: str,
    study_level: Optional[str] = None,
    project_status: Optional[str] = None,
) -> dict[str, Any]:
    """Score testwork coverage vs study stage (SLA Table 1 + MetPlant hierarchy)."""
    level = study_level or normalize_study_level(project_status)
    spec = STUDY_TESTWORK.get(level, STUDY_TESTWORK["scoping"])
    lims = _count_lims_tables(pid)
    samples_n, lith_n = _count_samples(pid)
    bwi = _bwi_variability(pid)
    grinding = _active_grinding_route(pid)
    comm_req = COMMINUTION_DESIGN_MIN.get(grinding, COMMINUTION_DESIGN_MIN["ball_only"])

    gaps: list[dict[str, Any]] = []
    metplant_notes: list[str] = []
    earned = 0.0
    possible = 0.0

    lims_min: dict[str, int] = spec.get("lims_min") or {}
    for table, target in lims_min.items():
        possible += 1.0
        have = lims.get(table, 0)
        frac = _clamp01(have / target) if target else 1.0
        earned += frac
        if frac < 0.95:
            gaps.append({
                "code": f"LIMS_{table.upper()}_LOW",
                "severity": "high" if frac < 0.5 else "warning",
                "message": (
                    f"{table.upper()} : {have}/{target} essais pour {spec['label']} "
                    f"({spec.get('sla_note', '')})"
                ),
                "have": have,
                "required": target,
            })

    samples_min = int(spec.get("samples_min") or 0)
    if samples_min:
        possible += 1.0
        s_frac = _clamp01(samples_n / samples_min)
        earned += s_frac
        if s_frac < 0.95:
            gaps.append({
                "code": "SAMPLES_LOW",
                "severity": "warning",
                "message": (
                    f"Échantillons LIMS : {samples_n}/{samples_min} — variabilité géologique "
                    f"insuffisante pour calibrer le modèle (MetPlant / SLA)."
                ),
                "have": samples_n,
                "required": samples_min,
            })

    lith_min = int(spec.get("lithology_min") or 0)
    if lith_min:
        possible += 1.0
        l_frac = _clamp01(lith_n / lith_min)
        earned += l_frac
        if l_frac < 0.95:
            gaps.append({
                "code": "LITHOLOGY_VARIABILITY_LOW",
                "severity": "warning",
                "message": (
                    f"Domaines / lithologies distincts : {lith_n}/{lith_min} — "
                    f"programme de variabilité incomplet (MetPlant)."
                ),
                "have": lith_n,
                "required": lith_min,
            })

    b1_need = int(comm_req.get("b1") or 3)
    if lims.get("b1", 0) < b1_need:
        metplant_notes.append(comm_req.get("note", ""))
        gaps.append({
            "code": "COMMINUTION_HIERARCHY_GAP",
            "severity": "high" if grinding == "sag_ball" else "warning",
            "message": (
                f"Comminution ({grinding}) : {lims.get('b1', 0)}/{b1_need} essais BWi — "
                f"{comm_req.get('note', '')}"
            ),
            "have": lims.get("b1", 0),
            "required": b1_need,
        })

    bwi_cv_thresh = 0.30
    if bwi.get("cv") is not None and bwi["cv"] > bwi_cv_thresh and lith_n < max(lith_min, 2):
        gaps.append({
            "code": "BWI_VARIABILITY_UNMAPPED",
            "severity": "warning",
            "message": (
                f"CV(BWi)={bwi['cv']:.0%} > {bwi_cv_thresh:.0%} sans couverture lithologique "
                f"suffisante — risque sous-dimensionnement broyage (MetPlant)."
            ),
        })
        metplant_notes.append("Variabilité BWi élevée : essais par domaine géométallurgique requis.")

    if spec.get("pilot_recommended") and lims.get("d1", 0) < 8:
        gaps.append({
            "code": "PILOT_SCALE_RECOMMENDED",
            "severity": "info",
            "message": (
                "Niveau FS : pilote à recycles fermés recommandé avant gel du modèle "
                "de simulation (SLA §1.3, Table 1)."
            ),
        })

    score = round(100.0 * earned / possible) if possible else 100
    return {
        "study_level": level,
        "study_label": spec["label"],
        "score": score,
        "lims_counts": lims,
        "samples_total": samples_n,
        "lithology_domains": lith_n,
        "bwi_stats": bwi,
        "grinding_route": grinding,
        "gaps": gaps,
        "metplant_notes": metplant_notes,
        "references": [REFERENCE_METPLANT, REFERENCE_SLA],
        "sla_note": spec.get("sla_note"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def assess_simulation_qa(pid: str, project_status: Optional[str] = None) -> dict[str, Any]:
    """SLA iterative model checklist + pre-run readiness."""
    level = normalize_study_level(project_status)
    try:
        testwork = assess_testwork_program(pid, study_level=level)
    except Exception:
        logger.exception("assess_testwork_program failed for %s", pid)
        testwork = {
            "study_level": level,
            "study_label": STUDY_TESTWORK.get(level, STUDY_TESTWORK["scoping"])["label"],
            "score": 0,
            "lims_counts": {},
            "gaps": [],
            "sla_note": "",
        }
    stages: list[dict[str, Any]] = []
    blockers: list[str] = []

    has_flowsheet = False
    for table in ("flowsheets", "flowshheets"):
        if _regclass_exists(f"public.{table}"):
            try:
                has_flowsheet = bool(
                    qone(f"SELECT 1 AS ok FROM {table} WHERE project_id=%s LIMIT 1", (pid,))
                )
                if has_flowsheet:
                    break
            except Exception:
                logger.debug("flowsheet check failed for %s", table, exc_info=True)
    stages.append({
        "id": "data_collection",
        "label": "Collecte données & PFD",
        "fraction": 1.0 if has_flowsheet else 0.0,
        "ok": has_flowsheet,
        "hint": "Générez le flowsheet procédé (PFD) avant simulation rigoureuse.",
    })
    if not has_flowsheet:
        blockers.append("flowsheet_missing")

    tw_frac = testwork["score"] / 100.0
    stages.append({
        "id": "testwork_adequacy",
        "label": "Essais vs niveau d'étude",
        "fraction": tw_frac,
        "ok": tw_frac >= 0.75,
        "hint": testwork.get("sla_note") or "Complétez le programme LIMS.",
        "detail": {"testwork_score": testwork["score"], "gaps": len(testwork["gaps"])},
    })
    if tw_frac < 0.5:
        blockers.append("testwork_critical_gap")

    feed_ok = testwork["lims_counts"].get("a1", 0) >= 1
    if level in ("pfs", "fs"):
        feed_ok = feed_ok and (
            testwork["lims_counts"].get("a3", 0) >= 1
            or testwork["lims_counts"].get("a1", 0) >= 5
        )
    stages.append({
        "id": "feed_characterisation",
        "label": "Caractérisation alimentation",
        "fraction": 1.0 if feed_ok else 0.3 if testwork["lims_counts"].get("a1") else 0.0,
        "ok": feed_ok,
        "hint": "MIN-01a ; MIN-03 (a3) pour preg-robbing / minéralogie (SLA §1.4).",
    })

    sim_n = 0
    if _regclass_exists("public.simulation_params"):
        sim_n = _safe_int(
            qone("SELECT COUNT(*)::int AS n FROM simulation_params WHERE project_id=%s", (pid,)),
            "n",
        )
    sim_frac = _clamp01(sim_n / 20.0)
    stages.append({
        "id": "model_build",
        "label": "Construction modèle",
        "fraction": sim_frac,
        "ok": sim_frac >= 0.5,
        "hint": "Paramètres procédé / financiers pour calage (SLA §2).",
        "detail": {"params": sim_n},
    })

    mb_streams = 0
    if _regclass_exists("public.mass_balance_streams_v2"):
        mb_streams = _safe_int(
            qone("SELECT COUNT(*)::int AS n FROM mass_balance_streams_v2 WHERE project_id=%s", (pid,)),
            "n",
        )
    recent_ok = False
    if _regclass_exists("public.simulation_runs_v2"):
        for sql in (
            "SELECT 1 AS ok FROM simulation_runs_v2 "
            "WHERE project_id=%s ORDER BY created_at DESC NULLS LAST LIMIT 1",
            "SELECT 1 AS ok FROM simulation_runs_v2 "
            "WHERE project_id=%s ORDER BY id DESC LIMIT 1",
        ):
            try:
                recent_ok = bool(qone(sql, (pid,)))
                if recent_ok:
                    break
            except Exception:
                logger.debug("simulation_runs_v2 recent check failed", exc_info=True)
    val_frac = 0.5 * _clamp01(mb_streams / 8.0) + 0.5 * (1.0 if recent_ok else 0.0)
    stages.append({
        "id": "validation",
        "label": "Validation & calage",
        "fraction": val_frac,
        "ok": val_frac >= 0.6,
        "hint": "Bilan massique v2 + au moins un run simulation pour calage (SLA §3–4).",
        "detail": {"mb_streams": mb_streams, "has_sim_run": recent_ok},
    })

    scen_n = _count_scenarios(pid)
    scen_frac = _clamp01(scen_n / 3.0)
    stages.append({
        "id": "scenarios",
        "label": "Scénarios & sensibilité",
        "fraction": scen_frac,
        "ok": scen_frac >= 0.66,
        "hint": "Base case + options / sensibilité (SLA §5–6).",
        "detail": {"scenarios": scen_n},
    })

    overall = round(sum(s["fraction"] for s in stages) / len(stages) * 100) if stages else 0
    can_run = "testwork_critical_gap" not in blockers and has_flowsheet
    warnings = [g["message"] for g in testwork["gaps"] if g.get("severity") in ("high", "warning")]

    return {
        "kind": "simulation_qa",
        "weights_version": WEIGHTS_VERSION,
        "study_level": level,
        "score": overall,
        "can_run_rigorous": can_run,
        "blockers": blockers,
        "stages": stages,
        "testwork": testwork,
        "warnings": warnings[:12],
        "references": [REFERENCE_SLA, REFERENCE_METPLANT],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def validate_before_simulation(
    pid: str,
    project_status: Optional[str] = None,
    op_codes: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Structured warnings to append to simulate_circuit (GIGO guard)."""
    proj = qone("SELECT status FROM projects WHERE id=%s", (pid,)) or {}
    status = project_status or proj.get("status")
    qa = assess_simulation_qa(pid, project_status=status)
    out: list[dict[str, Any]] = []

    for w in qa.get("warnings") or []:
        out.append({"code": "TESTWORK_GAP", "message": w, "severity": "warning"})

    if not qa.get("can_run_rigorous"):
        for b in qa.get("blockers") or []:
            out.append({
                "code": b.upper(),
                "message": f"Simulation rigoureuse : bloqueur « {b} » (SLA best practices).",
                "severity": "warning",
            })

    ops = {str(o).upper() for o in (op_codes or [])}
    lims = qa["testwork"]["lims_counts"]
    if ops & {"FLOTATION", "FLOT_BANK", "FLOT_CLEANER"} and lims.get("g1", 0) < 2:
        out.append({
            "code": "FLOTATION_TESTWORK_LOW",
            "message": (
                "Flottation au flowsheet mais < 2 essais FLT-04 — "
                "variabilité flottation non caractérisée (MetPlant)."
            ),
            "severity": "warning",
        })
    if ops & {"SAG_MILL"} and lims.get("b1", 0) < 5:
        out.append({
            "code": "SAG_COMMINUTION_TESTWORK",
            "message": (
                "SAG actif : hiérarchie essais comminution incomplète "
                "(BWi / variabilité — MetPlant Table 1)."
            ),
            "severity": "warning",
        })

    tw_score = qa["testwork"]["score"]
    if tw_score < 50:
        out.append({
            "code": "GIGO_TESTWORK",
            "message": (
                f"Score programme essais {tw_score}/100 — modèle à haut risque "
                f"(garbage-in / garbage-out, SLA §1)."
            ),
            "severity": "warning",
        })

    return out


def testwork_gap_suggestions(pid: str, project_status: Optional[str] = None) -> list[dict[str, Any]]:
    """Scenario-advisor hooks: actionable suggestions from testwork gaps."""
    assessment = assess_testwork_program(pid, project_status=project_status)
    suggestions: list[dict[str, Any]] = []
    for gap in assessment.get("gaps") or []:
        if gap.get("severity") not in ("high", "warning"):
            continue
        suggestions.append({
            "category": "testwork_program",
            "title": "Compléter programme essais",
            "rationale": gap["message"],
            "confidence": 0.85 if gap.get("severity") == "high" else 0.7,
            "source": "plant_design_advisor",
            "reference": REFERENCE_SLA,
        })
    bwi = assessment.get("bwi_stats") or {}
    if bwi.get("cv") is not None and bwi["cv"] > 0.30:
        suggestions.append({
            "category": "variability",
            "title": "Scénario variabilité BWi (HPGR / SABC)",
            "rationale": (
                f"CV(BWi)={bwi['cv']:.0%} — simuler P80 et énergie aux percentiles "
                f"P10/P90 (MetPlant variabilité comminution)."
            ),
            "confidence": 0.8,
            "source": "plant_design_advisor",
            "reference": REFERENCE_METPLANT,
        })
    return suggestions
