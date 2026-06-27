"""
Simulation et Optimisation — API.

Wraps simulation-v2 and project defaults; does not duplicate unit-operation physics.
Surrogate v1: linear sensitivities around the latest rigorous run `results.overall`.
Modes: simulation (steady-state), optimisation (NSGA-II), suivi opérations.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Query

try:
    from ..auth import project_user
    from ..db import conn, execute, get_conn, qall, qone, release
    from ..engines.metallurgical_levers import (
        discover_project_levers,
        normalize_lever_dict,
        nsga_job_variables,
        voi_for_circuit,
    )
    from ..routes.lims import LIMS_TABLES, safe_table_name
    from ..routes.pipeline import mark_stale_cascade, set_status
    from ..routes.simulation_defaults import flat_simulation_defaults
except ImportError:
    from auth import project_user

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM
    from db import conn, execute, get_conn, qall, qone, release
    from engines.metallurgical_levers import (
        discover_project_levers,
        normalize_lever_dict,
        nsga_job_variables,
        voi_for_circuit,
    )
    from routes.lims import LIMS_TABLES, safe_table_name
    from routes.pipeline import mark_stale_cascade, set_status
    from routes.simulation_defaults import flat_simulation_defaults

logger = logging.getLogger("mpdpms.metallurgical_decision")

router = APIRouter(
    prefix="/api/v1/projects/{pid}/metallurgical-decision",
    tags=["simulation-optimisation", "metallurgical-decision"],
)

def _project_lever_pack(pid: str) -> dict[str, Any]:
    """Per-project dynamic levers from active circuit template."""
    return discover_project_levers(pid)
_METRIC_KEYS = (
    "total_recovery_pct",
    "opex_per_t",
    "total_energy_kwh_t",
    "annual_gold_oz",
)

def _template_ops(pid: str) -> list[str]:
    tpl = qone(
        "SELECT id FROM circuit_templates WHERE project_id=%s "
        "ORDER BY is_active DESC NULLS LAST, updated_at DESC LIMIT 1",
        (pid,),
    )
    if not tpl:
        return []
    rows = qone(
        "SELECT array_agg(DISTINCT op_code) AS codes FROM circuit_template_operations "
        "WHERE template_id=%s",
        (tpl["id"],),
    )
    codes = rows.get("codes") if rows else None
    return list(codes or [])


def _build_lever_values(pid: str) -> dict[str, Any]:
    return _project_lever_pack(pid)["levers"]


def _levers_meta(pid: str) -> list[dict]:
    return _project_lever_pack(pid)["levers_meta"]


def _normalize_levers(pid: str, raw: dict[str, Any]) -> dict[str, Any]:
    pack = _project_lever_pack(pid)
    valid = set(pack["levers"].keys())
    merged = {**pack["levers"], **normalize_lever_dict(raw or {}, valid)}
    return {k: merged[k] for k in valid if k in merged}


def _lims_test_counts(pid: str) -> dict[str, int]:
    try:
        union = " UNION ALL ".join(
            f"SELECT '{code}' AS code, COUNT(*) AS n FROM {safe_table_name(tbl)} WHERE project_id=%s"
            for code, tbl in LIMS_TABLES.items()
        )
        params = tuple(pid for _ in LIMS_TABLES)
        rows = qall(f"SELECT code, n FROM ({union}) AS t", params)
        return {r["code"]: int(r["n"]) for r in rows}
    except Exception as exc:
        logger.warning("lims counts failed for %s: %s", pid, exc)
        return {}


def _baseline_table_ready() -> bool:
    row = qone(
        "SELECT 1 AS ok FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='project_metallurgical_baseline' LIMIT 1"
    )
    return bool(row)


def _get_active_baseline(pid: str) -> Optional[dict]:
    if not _baseline_table_ready():
        return None
    row = qone(
        "SELECT id, project_id, source_run_id, mode, levers_json, kpis_p50_json, "
        "locked_by, locked_at, notes "
        "FROM project_metallurgical_baseline "
        "WHERE project_id=%s AND is_active=TRUE ORDER BY locked_at DESC LIMIT 1",
        (pid,),
    )
    if not row:
        return None
    for key in ("levers_json", "kpis_p50_json"):
        val = row.get(key)
        if isinstance(val, str):
            try:
                row[key] = json.loads(val)
            except json.JSONDecodeError:
                row[key] = {}
    row["id"] = str(row["id"])
    if row.get("source_run_id"):
        row["source_run_id"] = str(row["source_run_id"])
    if row.get("locked_at"):
        row["locked_at"] = row["locked_at"].isoformat()
    return row


def _lever_economics_rank(
    pid: str,
    base_levers: dict,
    overall: dict[str, float],
) -> list[dict[str, Any]]:
    """Rank levers by Δ metal (koz/y proxy) vs baseline positions."""
    if not overall:
        return []
    meta_list = _levers_meta(pid)
    ranked: list[dict[str, Any]] = []
    for meta in meta_list:
        lid = meta["id"]
        if meta.get("unit") == "bool":
            continue
        bump = max((float(meta["max"]) - float(meta["min"])) * 0.05, 0.5)
        trial = {**base_levers, lid: float(base_levers.get(lid) or meta["min"]) + bump}
        adj = _apply_surrogate(dict(overall), base_levers, trial, meta_list)
        oz0 = overall.get("annual_gold_oz") or 0
        oz1 = adj.get("annual_gold_oz") or oz0
        opex0 = overall.get("opex_per_t") or 0
        opex1 = adj.get("opex_per_t") or opex0
        delta_koz = (oz1 - oz0) / 1000.0
        delta_opex = opex1 - opex0
        ranked.append({
            "lever_id": lid,
            "label": meta["label"],
            "delta_gold_koz_y": round(delta_koz, 2),
            "delta_metal_koz_y": round(delta_koz, 2),
            "delta_opex_per_t": round(delta_opex, 2),
            "score": round(delta_koz * 2.0 - delta_opex * 0.15, 2),
        })
    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def _compute_voi(pid: str) -> dict[str, Any]:
    pack = _project_lever_pack(pid)
    return voi_for_circuit(pid, _lims_test_counts(pid), pack["circuit_profile"])


def _kpis_p50_from_overall(overall: dict[str, float]) -> dict[str, float]:
    rec = overall.get("total_recovery_pct")
    oz = overall.get("annual_gold_oz")
    return {
        "recovery_pct": rec,
        "gold_koz_y": (oz / 1000.0) if oz else None,
        "opex_per_t": overall.get("opex_per_t"),
        "energy_kwh_t": overall.get("total_energy_kwh_t"),
        "production_oz_h": overall.get("production_oz_h"),
    }


def _last_run_row(pid: str) -> Optional[dict]:
    return qone(
        "SELECT id, results, created_at, run_type FROM simulation_runs_v2 "
        "WHERE project_id=%s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )


def _parse_overall(run_row: Optional[dict], pid: str) -> dict[str, float]:
    if not run_row or not run_row.get("results"):
        return {}
    raw = run_row["results"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    overall = raw.get("overall") if isinstance(raw, dict) else {}
    if not isinstance(overall, dict):
        return {}
    out: dict[str, float] = {}
    for k in (
        "feed_tph", "total_recovery_pct", "total_energy_kwh_t",
        "annual_gold_oz", "feed_grade_au",
    ):
        v = overall.get(k)
        if v is not None:
            out[k] = float(v)
    tph = out.get("feed_tph") or 0
    oz_y = out.get("annual_gold_oz") or 0
    if tph > 0 and oz_y > 0:
        out["production_oz_h"] = oz_y / 8760.0
    flat_costs = flat_simulation_defaults(pid)
    if flat_costs.get("opex_per_tonne"):
        out["opex_per_t"] = float(flat_costs["opex_per_tonne"])
    return out


def _cone_from_p50(metrics: dict[str, float], spread: float = 0.04) -> dict[str, dict]:
    """Build P10/P50/P90 bands (v1: fixed spread until geomet blend wired)."""
    keys = {
        "recovery_pct": "total_recovery_pct",
        "gold_koz_y": "annual_gold_oz",
        "metal_koz_y": "annual_gold_oz",
        "opex_per_t": "opex_per_t",
        "energy_kwh_t": "total_energy_kwh_t",
        "production_oz_h": "production_oz_h",
    }
    bands: dict[str, dict] = {}
    for label, src in keys.items():
        p50 = metrics.get(src)
        if p50 is None:
            continue
        if label in ("gold_koz_y", "metal_koz_y"):
            p50 = p50 / 1000.0
        bands[label] = {
            "p10": round(p50 * (1 - spread), 3),
            "p50": round(p50, 3),
            "p90": round(p50 * (1 + spread), 3),
        }
    return bands


def _finalize_surrogate_metrics(raw: dict[str, float]) -> dict[str, float]:
    """Clamp and derive oz/h from recovery after surrogate adjustment."""
    out = dict(raw)
    rec = max(50.0, min(99.5, float(out.get("total_recovery_pct") or 89.0)))
    out["total_recovery_pct"] = round(rec, 2)
    out["opex_per_t"] = round(max(5.0, float(out.get("opex_per_t") or 11.8)), 2)
    out["total_energy_kwh_t"] = round(max(8.0, float(out.get("total_energy_kwh_t") or 15.0)), 2)
    tph = float(out.get("feed_tph") or 1517.0)
    grade = float(out.get("feed_grade_au") or 1.5)
    oz_h = tph * grade * (rec / 100.0) * TROY_OZ_PER_GRAM
    out["production_oz_h"] = round(oz_h, 2)
    out["annual_gold_oz"] = round(oz_h * 8760.0, 0)
    return out


def _lever_step(lever_id: str, levers_meta: list[dict]) -> float:
    meta = next((m for m in levers_meta if m["id"] == lever_id), None)
    if not meta:
        return 1.0
    return max((float(meta["max"]) - float(meta["min"])) * 0.05, 0.5)


def _surrogate_v2_coefficients(
    pid: str,
    overall: dict[str, float],
    base_levers: dict[str, Any],
) -> Optional[dict[str, dict[str, float]]]:
    """Central-difference slopes on a local lever grid (v2)."""
    if not overall:
        return None
    pack = _project_lever_pack(pid)
    meta_list = pack["levers_meta"]
    active = pack.get("active_lever_ids") or [
        m["id"] for m in meta_list if m.get("unit") != "bool"
    ][:6]
    coeffs: dict[str, dict[str, float]] = {}
    for lid in active:
        step = _lever_step(lid, meta_list)
        base_v = float(base_levers.get(lid) or 0)
        up = {**base_levers, lid: base_v + step}
        down = {**base_levers, lid: base_v - step}
        m_up = _finalize_surrogate_metrics(
            _apply_surrogate(dict(overall), base_levers, up, meta_list)
        )
        m_dn = _finalize_surrogate_metrics(
            _apply_surrogate(dict(overall), base_levers, down, meta_list)
        )
        coeffs[lid] = {
            k: (float(m_up.get(k) or 0) - float(m_dn.get(k) or 0)) / (2.0 * step)
            for k in _METRIC_KEYS
        }
    return coeffs


def _apply_surrogate_v2(
    overall: dict[str, float],
    base_levers: dict[str, Any],
    new_levers: dict[str, Any],
    coeffs: Optional[dict[str, dict[str, float]]],
    levers_meta: list[dict],
) -> dict[str, float]:
    if not coeffs:
        return _finalize_surrogate_metrics(
            _apply_surrogate(overall, base_levers, new_levers, levers_meta)
        )
    adjusted = {k: float(overall.get(k) or 0) for k in _METRIC_KEYS if overall.get(k) is not None}
    for lid, slopes in coeffs.items():
        delta = float(new_levers.get(lid) or base_levers.get(lid) or 0) - float(
            base_levers.get(lid) or 0
        )
        for key, slope in slopes.items():
            adjusted[key] = adjusted.get(key, float(overall.get(key) or 0)) + delta * slope
    merged = dict(overall)
    merged.update(adjusted)
    return _finalize_surrogate_metrics(merged)


def _cone_from_surrogate_v2(
    metrics: dict[str, float],
    coeffs: Optional[dict[str, dict[str, float]]],
    uncertainty_by_lever: Optional[dict[str, float]] = None,
) -> dict[str, dict]:
    """P50 from adjusted metrics; P10/P90 from lever uncertainty propagation (v2)."""
    if not coeffs:
        return _cone_from_p50(metrics)
    label_map = {
        "recovery_pct": "total_recovery_pct",
        "gold_koz_y": "annual_gold_oz",
        "metal_koz_y": "annual_gold_oz",
        "opex_per_t": "opex_per_t",
        "energy_kwh_t": "total_energy_kwh_t",
        "production_oz_h": "production_oz_h",
    }
    unc = uncertainty_by_lever or {}
    bands: dict[str, dict] = {}
    for label, src in label_map.items():
        p50 = metrics.get(src)
        if p50 is None:
            continue
        if label in ("gold_koz_y", "metal_koz_y"):
            p50 = float(p50) / 1000.0
        else:
            p50 = float(p50)
        spread = 0.0
        for lid in coeffs:
            slope = abs((coeffs.get(lid) or {}).get(src) or 0.0)
            spread += slope * unc.get(lid, 1.0)
        if label in ("gold_koz_y", "metal_koz_y"):
            spread = spread / 1000.0
        spread = max(spread, p50 * 0.02)
        bands[label] = {
            "p10": round(p50 - spread, 3),
            "p50": round(p50, 3),
            "p90": round(p50 + spread, 3),
        }
    return bands


def _recovery_at_risk(cone: dict[str, dict]) -> Optional[dict[str, Any]]:
    """NI 43-101 prudent recovery = P10 of plant recovery band."""
    rec = cone.get("recovery_pct")
    if not rec:
        return None
    p10 = float(rec.get("p10") or 0)
    p50 = float(rec.get("p50") or 0)
    gap = round(p50 - p10, 2)
    if gap >= 4.0:
        level = "critical"
    elif gap >= 2.0:
        level = "warn"
    else:
        level = "ok"
    return {
        "recovery_p10_pct": round(p10, 2),
        "recovery_p50_pct": round(p50, 2),
        "gap_pct": gap,
        "level": level,
        "label_fr": f"Recovery-at-risk P10 : {p10:.1f}% (−{gap:.1f} pts vs P50)",
        "label_en": f"Recovery-at-risk P10: {p10:.1f}% (−{gap:.1f} pts vs P50)",
    }


def _impact_payload(
    pid: str,
    new_levers: dict[str, Any],
    overall: dict[str, float],
    base_levers: dict[str, Any],
) -> dict[str, Any]:
    pack = _project_lever_pack(pid)
    meta_list = pack["levers_meta"]
    base_n = _normalize_levers(pid, base_levers)
    new_n = _normalize_levers(pid, {**base_n, **(new_levers or {})})
    coeffs = _surrogate_v2_coefficients(pid, overall, base_n)
    adjusted = _apply_surrogate_v2(overall, base_n, new_n, coeffs, meta_list)
    cone = _cone_from_surrogate_v2(
        adjusted, coeffs, pack.get("uncertainty_by_lever")
    )
    rar = _recovery_at_risk(cone)
    return {
        "cone": cone,
        "metrics": adjusted,
        "is_surrogate": True,
        "surrogate_version": 2 if coeffs else 1,
        "recovery_at_risk": rar,
        "active_lever_ids": pack.get("active_lever_ids"),
    }


def _apply_surrogate(
    baseline: dict[str, float],
    base_levers: dict,
    new_levers: dict,
    levers_meta: list[dict],
) -> dict[str, float]:
    out = dict(baseline)
    rec = float(out.get("total_recovery_pct") or 89.0)
    opex = float(out.get("opex_per_t") or 11.8)
    energy = float(out.get("total_energy_kwh_t") or 15.0)
    oz = float(out.get("annual_gold_oz") or 0)

    for meta in levers_meta:
        if meta.get("unit") == "bool":
            continue
        lid = meta["id"]
        b0 = float(base_levers.get(lid) or meta.get("min") or 0)
        b1 = float(new_levers.get(lid) or b0)
        delta = b1 - b0
        if abs(delta) < 1e-9:
            continue
        for metric, coef in (meta.get("sensitivities") or {}).items():
            c = float(coef)
            if metric == "recovery_pct":
                rec += delta * c
            elif metric == "opex_per_t":
                opex += delta * c
            elif metric == "energy_kwh_t":
                energy += abs(delta) * c
            elif metric == "metal_koz_y" and oz:
                oz += delta * c * 1000.0

    out["total_recovery_pct"] = rec
    out["opex_per_t"] = opex
    out["total_energy_kwh_t"] = energy
    if oz:
        out["annual_gold_oz"] = oz
    return _finalize_surrogate_metrics(out)


@router.get("/context")
def get_context(pid: str, user=Depends(project_user)):
    """Levers, last run reference, cone bands, baseline if locked."""
    proj = qone(
        "SELECT project_name, project_code, commodity, target_tph, gold_grade_g_t, status "
        "FROM projects WHERE id=%s",
        (pid,),
    )
    if not proj:
        raise HTTPException(404, "Projet introuvable")
    pack = _project_lever_pack(pid)
    levers = pack["levers"]
    levers_meta = pack["levers_meta"]
    circuit_profile = pack["circuit_profile"]
    run = _last_run_row(pid)
    overall = _parse_overall(run, pid)
    baseline = _get_active_baseline(pid)
    if baseline and isinstance(baseline.get("levers_json"), dict):
        baseline["levers_json"] = _normalize_levers(pid, baseline["levers_json"])
    impact = _impact_payload(pid, levers, overall, levers) if overall else None
    cone = (impact or {}).get("cone") or _cone_from_p50(overall)
    n_active = len(pack.get("active_lever_ids") or [])
    family = circuit_profile.get("flowsheet_family", "generic")
    return {
        "project": proj,
        "levers_meta": levers_meta,
        "levers": levers,
        "circuit_profile": circuit_profile,
        "primary_metal": circuit_profile.get("primary_metal", "Au"),
        "last_run_id": str(run["id"]) if run else None,
        "last_run_at": run.get("created_at").isoformat() if run and run.get("created_at") else None,
        "cone": cone,
        "baseline": baseline,
        "baseline_locked": baseline is not None,
        "lever_ranking": _lever_economics_rank(pid, levers, overall),
        "recovery_at_risk": (impact or {}).get("recovery_at_risk") or _recovery_at_risk(cone),
        "surrogate_version": (impact or {}).get("surrogate_version", 1),
        "domains": [],
        "default_mode": "study",
        "surrogate_note": (
            f"Surrogate v2 — {n_active} leviers ({family}); bandes depuis le dernier run rigoureux."
            if overall
            else f"Circuit {family} — lancez un run rigoureux pour calibrer le surrogate."
        ),
    }


@router.get("/baseline")
def get_baseline(pid: str, user=Depends(project_user)):
    """Return active locked metallurgical plan, if any."""
    baseline = _get_active_baseline(pid)
    if not baseline:
        return {"baseline": None, "locked": False}
    return {"baseline": baseline, "locked": True}


@router.get("/voi")
def get_voi(pid: str, user=Depends(project_user)):
    """Value of information — rank missing LIMS tests by NPV band narrowing."""
    return _compute_voi(pid)


@router.post("/lock-baseline")
def post_lock_baseline(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Lock current levers + P50 KPIs as the PFS reference plan; cascade pipeline staleness."""
    if not _baseline_table_ready():
        raise HTTPException(
            503,
            "Table project_metallurgical_baseline absente — exécuter les migrations Alembic.",
        )
    levers = _normalize_levers(pid, body.get("levers") or _build_lever_values(pid))
    run = _last_run_row(pid)
    run_id = body.get("source_run_id") or (str(run["id"]) if run else None)
    if not run_id:
        raise HTTPException(
            400,
            "Aucun run de simulation — lancez « Recalculer référence » avant de verrouiller.",
        )
    overall = _parse_overall(run, pid)
    if body.get("levers"):
        overall = _apply_surrogate(
            overall or {},
            _build_lever_values(pid),
            levers,
            _levers_meta(pid),
        )
    if not overall:
        raise HTTPException(400, "Impossible de dériver les KPI P50 pour le verrouillage.")
    kpis = _kpis_p50_from_overall(overall)
    mode = body.get("mode") or "study_lock"
    if mode not in ("study_lock", "feasibility_adopted"):
        raise HTTPException(400, "mode invalide")
    notes = body.get("notes") or ""
    execute(
        "UPDATE project_metallurgical_baseline SET is_active=FALSE WHERE project_id=%s AND is_active=TRUE",
        (pid,),
    )
    execute(
        """
        INSERT INTO project_metallurgical_baseline
            (project_id, source_run_id, mode, levers_json, kpis_p50_json, locked_by, notes)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        RETURNING id, locked_at
        """,
        (
            pid,
            run_id,
            mode,
            json.dumps(levers),
            json.dumps(kpis),
            str(user["id"]),
            notes,
        ),
    )
    snap = {
        "source": "metallurgical_decision",
        "run_id": run_id,
        "levers": levers,
        "kpis_p50": kpis,
        "mode": mode,
    }
    set_status(
        pid,
        "simulation",
        "complete",
        user_id=str(user["id"]),
        triggered_by="metallurgical_decision_lock",
        input_snapshot=snap,
    )
    cascaded = mark_stale_cascade(pid, "simulation", user_id=str(user["id"]))
    baseline = _get_active_baseline(pid)
    return {
        "ok": True,
        "baseline": baseline,
        "cascaded_modules": cascaded,
        "message": "Plan PFS verrouillé — modules aval marqués obsolètes.",
    }


def _build_memo_html(
    *,
    lang: str,
    proj: dict,
    pid: str,
    levers: dict,
    levers_meta: list[dict],
    circuit_profile: dict,
    cone: dict,
    baseline: Optional[dict],
    voi: dict,
    ranking: list,
    run_id: str,
    recovery_at_risk: Optional[dict],
) -> str:
    en = lang.lower().startswith("en")
    t = {
        "title": (
            f"PFS Metallurgical Memo — {proj.get('project_name', pid)}"
            if en
            else f"Mémo PFS — {proj.get('project_name', pid)}"
        ),
        "h1": (
            "PFS Metallurgical Memo — Metallurgical Decision Module"
            if en
            else "Mémo métallurgique PFS — Simulation et Optimisation"
        ),
        "levers": (
            f"Process levers ({len(levers_meta)})"
            if en
            else f"Leviers procédé ({len(levers_meta)})"
        ),
        "cone": "Geomet cone P10 – P50 – P90" if en else "Cône géomét P10 – P50 – P90",
        "rank": "Top economic lever" if en else "Levier économique #1",
        "voi": "Priority test (VOI)" if en else "Essai prioritaire (VOI)",
        "locked_yes": "Yes" if en else "Oui",
        "locked_no": "No" if en else "Non",
        "footer": (
            "Generated by MetalFlow Pro — simulation_run + LIMS trace. "
            "Not a substitute for a QP NI 43-101 report."
            if en
            else "Document généré par MetalFlow Pro — trace simulation_run + LIMS. "
            "Non substitut au rapport QP NI 43-101."
        ),
        "no_run": "No run — launch simulation" if en else "Aucun run — lancer simulation",
        "insufficient": "Insufficient data" if en else "Données insuffisantes",
    }
    metal = (circuit_profile.get("primary_metal") or "Au").strip()
    metal_lbl = (
        f"Net {metal} (koz/y)"
        if en
        else f"{metal} net (koz/an)"
    )
    cone_labels = {
        "recovery_pct": "Plant recovery (%)" if en else "Récupération usine (%)",
        "gold_koz_y": metal_lbl,
        "metal_koz_y": metal_lbl,
        "opex_per_t": "OPEX ($/t)",
        "energy_kwh_t": "Energy (kWh/t)" if en else "Énergie (kWh/t)",
    }
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sample_n = voi.get("sample_count", 0)
    locked = t["locked_yes"] if baseline else t["locked_no"]

    lev_rows = []
    for m in levers_meta:
        lbl = m.get("label_en" if en else "label", m.get("label", m["id"]))
        v = levers.get(m["id"], "—")
        unit = m.get("unit", "")
        if unit == "bool":
            v = ("Yes" if en else "Oui") if float(v or 0) >= 0.5 else ("No" if en else "Non")
        lev_rows.append(f"<tr><td>{lbl}</td><td>{v} {unit if unit != 'bool' else ''}</td></tr>")

    cone_parts = []
    for key, label in cone_labels.items():
        b = cone.get(key)
        if b:
            cone_parts.append(
                f"<tr><td>{label}</td><td>{b['p10']}</td><td><b>{b['p50']}</b></td><td>{b['p90']}</td></tr>"
            )
    cone_html = "\n".join(cone_parts) or f"<tr><td colspan='4'>{t['no_run']}</td></tr>"

    top_voi = voi.get("top")
    if top_voi:
        dom = "domain" if en else "domaine"
        voi_html = (
            f"<p><b>{top_voi['label']}</b> ({dom} {top_voi['domain_hint']}) — "
            f"NPV band ~${top_voi['expected_npv_band_m_usd']:.1f} M. {top_voi['rationale']}</p>"
        )
    else:
        voi_html = f"<p>{voi.get('message', '')}</p>"

    rank_html = "".join(
        f"<li>{r['label']}: Δ gold {r['delta_gold_koz_y']:+.1f} koz/y, Δ OPEX {r['delta_opex_per_t']:+.2f} $/t</li>"
        for r in (ranking or [])[:5]
    )
    rar_html = ""
    if recovery_at_risk:
        rar_label = recovery_at_risk.get("label_en" if en else "label_fr", "")
        rar_html = f"<h2>Recovery-at-risk</h2><p><b>{rar_label}</b></p>"

    return f"""<!DOCTYPE html>
<html lang="{'en' if en else 'fr'}"><head><meta charset="utf-8"/>
<title>{t['title']}</title>
<style>
body {{ font-family: Georgia, serif; max-width: 800px; margin: 24px auto; color: #111; line-height: 1.45; }}
h1 {{ font-size: 1.35rem; border-bottom: 2px solid #b8860b; padding-bottom: 8px; }}
h2 {{ font-size: 1.05rem; margin-top: 1.4rem; color: #333; }}
table {{ width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 0.9rem; }}
th, td {{ border: 1px solid #ccc; padding: 6px 8px; text-align: left; }}
th {{ background: #f5f5f0; }}
.meta {{ font-size: 0.85rem; color: #555; }}
@media print {{ body {{ margin: 12mm; }} }}
</style></head><body>
<h1>{t['h1']}</h1>
<p class="meta"><b>{proj.get('project_name')}</b> ({proj.get('project_code')}) · {proj.get('status', '')} · "
f"{proj.get('target_tph', '—')} t/h · {proj.get('gold_grade_g_t', '—')} g/t Au<br/>
Generated {now} · run_id <code>{run_id}</code> · Locked plan: {locked} · LIMS samples: {sample_n}</p>
{rar_html}
<h2>1. {t['levers']}</h2>
<table><thead><tr><th>{'Lever' if en else 'Levier'}</th><th>{'Value' if en else 'Valeur'}</th></tr></thead>
<tbody>{''.join(lev_rows)}</tbody></table>
<h2>2. {t['cone']}</h2>
<table><thead><tr><th>{'Metric' if en else 'Métrique'}</th><th>P10</th><th>P50</th><th>P90</th></tr></thead>
<tbody>{cone_html}</tbody></table>
<h2>3. {t['rank']}</h2>
<ol>{rank_html or f'<li>{t["insufficient"]}</li>'}</ol>
<h2>4. {t['voi']}</h2>
{voi_html}
<p class="meta">{t['footer']}</p>
</body></html>"""


@router.get("/export/memo")
def export_memo(
    pid: str,
    lang: str = Query("fr", pattern="^(fr|en)$"),
    user=Depends(project_user),
):
    """HTML mémo PFS (FR/EN) for browser print / PDF — NI 43-101 internal use."""
    proj = qone(
        "SELECT project_name, project_code, target_tph, gold_grade_g_t, status FROM projects WHERE id=%s",
        (pid,),
    )
    if not proj:
        raise HTTPException(404, "Projet introuvable")
    pack = _project_lever_pack(pid)
    levers = pack["levers"]
    run = _last_run_row(pid)
    overall = _parse_overall(run, pid)
    base_levers = levers
    impact = _impact_payload(pid, levers, overall, base_levers) if overall else None
    cone = (impact or {}).get("cone") or _cone_from_p50(overall)
    baseline = _get_active_baseline(pid)
    voi = _compute_voi(pid)
    ranking = _lever_economics_rank(pid, levers, overall)
    run_id = str(run["id"]) if run else "—"
    rar = (impact or {}).get("recovery_at_risk") or _recovery_at_risk(cone)
    html = _build_memo_html(
        lang=lang,
        proj=proj,
        pid=pid,
        levers=levers,
        levers_meta=pack["levers_meta"],
        circuit_profile=pack["circuit_profile"],
        cone=cone,
        baseline=baseline,
        voi=voi,
        ranking=ranking,
        run_id=run_id,
        recovery_at_risk=rar,
    )
    title = (
        f"PFS Memo — {proj.get('project_name', '')}"
        if lang.startswith("en")
        else f"Mémo PFS — {proj.get('project_name', '')}"
    )
    return {"html": html, "title": title, "lang": lang}


@router.post("/impact")
def post_impact(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Recalculate P10/P50/P90 from lever positions (surrogate v2 grid)."""
    new_levers = _normalize_levers(pid, body.get("levers") or {})
    base_levers = _build_lever_values(pid)
    run = _last_run_row(pid)
    overall = _parse_overall(run, pid)
    if not overall:
        defaults = flat_simulation_defaults(pid)
        overall = {
            "feed_tph": defaults.get("feed_tph") or 1517,
            "feed_grade_au": defaults.get("head_grade_au") or 1.5,
            "total_recovery_pct": defaults.get("flot_rec_au") or 88,
            "total_energy_kwh_t": 15.0,
            "opex_per_t": defaults.get("opex_per_tonne") or 11.8,
        }
    payload = _impact_payload(pid, new_levers, overall, base_levers)
    return payload


@router.post("/run-full")
def post_run_full(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Trigger rigorous simulation on the dynamic gold process route."""
    try:
        from ..routes.simulation_v2 import run_gold_process_simulation
    except ImportError:
        from routes.simulation_v2 import run_gold_process_simulation
    return run_gold_process_simulation(pid, body, user)


@router.post("/simulate")
def post_simulate(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Legacy alias used by the monolith frontend."""
    return post_run_full(pid, body, user)


# ─── Mode Faisabilité (Tranche 2) ───────────────────────────────────────────


def _active_template(pid: str) -> dict:
    tpl = qone(
        "SELECT id, name FROM circuit_templates WHERE project_id=%s "
        "ORDER BY is_active DESC NULLS LAST, updated_at DESC LIMIT 1",
        (pid,),
    )
    if not tpl:
        raise HTTPException(404, "Aucun circuit actif — créez un template circuit d'abord.")
    return tpl


def _equipment_capex_musd(pid: str) -> float:
    row = qone(
        "SELECT COALESCE(SUM(price_cad), 0) AS total FROM equipment_v2 "
        "WHERE project_id=%s AND enabled=TRUE",
        (pid,),
    )
    total_cad = float((row or {}).get("total") or 0)
    return round(total_cad / 1_000_000.0, 2)


def _feasibility_constraints(pid: str, body: dict) -> dict[str, Any]:
    equip_capex = _equipment_capex_musd(pid)
    default_max = max(equip_capex * 1.15, 80.0) if equip_capex > 0 else 120.0
    return {
        "max_capex_musd": float(body.get("max_capex_musd") or default_max),
        "min_recovery_pct": float(body.get("min_recovery_pct") or 85.0),
        "max_opex_per_t": float(body.get("max_opex_per_t") or 13.0),
        "mass_pull_min_pct": float(body.get("mass_pull_min_pct") or 5.0),
        "mass_pull_max_pct": float(body.get("mass_pull_max_pct") or 10.0),
        "max_energy_kwh_t": float(body.get("max_energy_kwh_t") or 18.0),
        "equipment_capex_musd": equip_capex,
        "engine": {
            "min_recovery": float(body.get("min_recovery_pct") or 85.0),
            "max_aisc": float(body.get("max_aisc_usd_oz") or 1500.0),
        },
    }


def _feasibility_job_variables(pid: str, constraints: dict[str, Any]) -> list[dict]:
    pack = _project_lever_pack(pid)
    family = pack["circuit_profile"].get("flowsheet_family", "generic")
    dyn = nsga_job_variables(pack["levers_meta"], family)
    if dyn:
        for item in dyn:
            if item["param"] == "mass_pull_pct":
                item["min"] = max(item["min"], constraints["mass_pull_min_pct"])
                item["max"] = min(item["max"], constraints["mass_pull_max_pct"])
        return dyn
    return [
        {"param": "p80_um", "min": 53, "max": 150},
        {
            "param": "mass_pull_pct",
            "min": constraints["mass_pull_min_pct"],
            "max": constraints["mass_pull_max_pct"],
        },
        {"param": "srt_h", "min": 18, "max": 42},
        {"param": "nacn_kg_t", "min": 0.4, "max": 1.8},
    ]


_NSGA_PARAM_TO_LEVER = {
    "p80_um": "grind_p80",
    "mass_pull_pct": "flot_mass_pull",
    "srt_h": "leach_recovery",
}


def _vars_to_levers(
    variables: dict[str, Any],
    base_levers: dict,
    levers_meta: Optional[list[dict]] = None,
) -> dict[str, Any]:
    out = dict(base_levers)
    valid = {m["id"] for m in (levers_meta or [])} if levers_meta else set(out.keys())
    for param, lid in _NSGA_PARAM_TO_LEVER.items():
        if variables.get(param) is None:
            continue
        if lid not in valid and lid not in out:
            continue
        if param == "srt_h" and lid == "leach_recovery":
            base_rec = float(out.get(lid) or 88)
            out[lid] = min(98.0, max(50.0, base_rec + (float(variables["srt_h"]) - 24) * 0.15))
        else:
            out[lid] = float(variables[param])
    return out


def _format_pareto_point(
    sol: dict,
    idx: int,
    base_levers: dict,
    levers_meta: Optional[list[dict]] = None,
) -> dict[str, Any]:
    obj = sol.get("objectives") or {}
    met = sol.get("metrics") or {}
    variables = sol.get("variables") or {}
    npv = obj.get("npv_musd") if obj.get("npv_musd") is not None else met.get("npv_musd")
    capex = obj.get("capex_musd") if obj.get("capex_musd") is not None else met.get("capex_musd")
    oz_y = met.get("annual_gold_oz") or 0
    return {
        "index": idx,
        "npv_musd": npv,
        "capex_musd": capex,
        "recovery_pct": met.get("recovery_pct"),
        "aisc_usd_oz": met.get("aisc_usd_oz"),
        "gold_koz_y": round(float(oz_y) / 1000.0, 1) if oz_y else None,
        "co2_per_oz": obj.get("co2_per_oz") or met.get("co2_per_oz"),
        "energy_kwh_t": met.get("energy_kwh_t"),
        "feasible": sol.get("feasible", True),
        "levers": _vars_to_levers(variables, base_levers, levers_meta),
        "variables": variables,
    }


def _filter_pareto_front(front: list[dict], constraints: dict[str, Any]) -> list[dict]:
    out: list[dict] = []
    for sol in front:
        met = sol.get("metrics") or {}
        obj = sol.get("objectives") or {}
        capex = obj.get("capex_musd") or met.get("capex_musd") or 0
        rec = met.get("recovery_pct") or 0
        mp = (sol.get("variables") or {}).get("mass_pull_pct") or 0
        if capex > constraints["max_capex_musd"]:
            continue
        if rec < constraints["min_recovery_pct"]:
            continue
        if mp and (mp < constraints["mass_pull_min_pct"] or mp > constraints["mass_pull_max_pct"]):
            continue
        out.append(sol)
    return out if out else front[:8]


def _pareto_payload_from_results(
    results: dict,
    pid: str,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    pack = _project_lever_pack(pid)
    base_levers = pack["levers"]
    meta = pack["levers_meta"]
    raw_front = results.get("pareto_front") or []
    filtered = _filter_pareto_front(raw_front, constraints)
    points = [
        _format_pareto_point(s, i, base_levers, meta) for i, s in enumerate(filtered[:12])
    ]
    ov = _parse_overall(_last_run_row(pid), pid)
    current = _format_pareto_point(
        {
            "objectives": {
                "npv_musd": ov.get("npv_musd"),
                "capex_musd": ov.get("capex_musd"),
            },
            "metrics": {
                "recovery_pct": ov.get("total_recovery_pct"),
                "annual_gold_oz": ov.get("annual_gold_oz"),
                "npv_musd": ov.get("npv_musd"),
                "capex_musd": ov.get("capex_musd"),
                "aisc_usd_oz": ov.get("aisc_usd_oz"),
            },
            "variables": {
                "p80_um": base_levers.get("grind_p80"),
                "mass_pull_pct": base_levers.get("flot_mass_pull"),
            },
            "feasible": True,
        },
        -1,
        base_levers,
        meta,
    )
    current["is_current"] = True
    knee = results.get("best_balanced")
    best_npv = results.get("best_npv")
    return {
        "points": points,
        "n_solutions": len(points),
        "circuit_profile": pack["circuit_profile"],
        "best_balanced": _format_pareto_point(knee, 0, base_levers, meta) if knee else None,
        "best_npv": _format_pareto_point(best_npv, 0, base_levers, meta) if best_npv else None,
        "current": current,
        "constraints": constraints,
    }


def _run_nsga2_sync(
    pid: str,
    template_id: str,
    population_size: int,
    n_generations: int,
    constraints: dict[str, Any],
) -> dict[str, Any]:
    try:
        from ..engines.nsga2_optimizer import nsga2_optimize
    except ImportError:
        from engines.nsga2_optimizer import nsga2_optimize

    import psycopg2.extras

    with get_conn() as db:
        cur = db.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            return nsga2_optimize(
                pid,
                template_id,
                cur,
                population_size=population_size,
                n_generations=n_generations,
                constraints=constraints.get("engine"),
                job_variables=_feasibility_job_variables(pid, constraints),
            )
        finally:
            cur.close()


@router.post("/feasibility/optimize")
def post_feasibility_optimize(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """NSGA-II Pareto (NPV vs CAPEX) with equipment-v2 CAPEX ceiling."""
    tpl = _active_template(pid)
    template_id = str(tpl["id"])
    constraints = _feasibility_constraints(pid, body)
    pop_size = min(max(int(body.get("population_size", 20)), 8), 50)
    n_gen = min(max(int(body.get("n_generations", 20)), 5), 100)
    params = {
        "population_size": pop_size,
        "n_generations": n_gen,
        "constraints": constraints,
        "source": "metallurgical_decision",
    }

    async_queued = False
    run_id = str(uuid.uuid4())
    try:
        from tasks.simulation_tasks import run_nsga2_optimization

        db = conn()
        try:
            with db.cursor() as cur:
                cur.execute(
                    "INSERT INTO simulation_runs_v2 "
                    "(id, project_id, template_id, run_type, params, status, created_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        run_id,
                        pid,
                        template_id,
                        "optimization",
                        json.dumps(params),
                        "queued",
                        user.get("id"),
                    ),
                )
            db.commit()
        finally:
            release(db)

        run_nsga2_optimization.delay(
            pid,
            run_id,
            template_id,
            pop_size,
            n_gen,
            ["npv_musd", "capex_musd", "co2_per_oz"],
            constraints.get("engine"),
        )
        async_queued = True
    except Exception as exc:
        logger.info("Celery indisponible, NSGA-II synchrone: %s", exc)

    if async_queued:
        return {
            "run_id": run_id,
            "status": "queued",
            "poll_url": f"/api/v1/projects/{pid}/metallurgical-decision/feasibility/pareto/{run_id}",
            "constraints": constraints,
        }

    try:
        engine_result = _run_nsga2_sync(pid, template_id, pop_size, n_gen, constraints)
        actual_run_id = str(engine_result.get("run_id") or run_id)
        payload = _pareto_payload_from_results(engine_result, pid, constraints)
        return {
            "run_id": actual_run_id,
            "status": "completed",
            "constraints": constraints,
            **payload,
        }
    except Exception as exc:
        raise HTTPException(500, f"Optimisation échouée: {exc}") from exc


@router.get("/feasibility/pareto/{run_id}")
def get_feasibility_pareto(pid: str, run_id: str, user=Depends(project_user)):
    """Pareto solutions for a feasibility optimization run."""
    row = qone(
        "SELECT id, status, results, params, run_type FROM simulation_runs_v2 "
        "WHERE id=%s AND project_id=%s",
        (run_id, pid),
    )
    if not row:
        raise HTTPException(404, "Run d'optimisation introuvable")
    if row.get("run_type") not in ("optimization", "nsga2_optimization"):
        raise HTTPException(400, "Ce run n'est pas une optimisation Pareto")
    status = row.get("status") or "pending"
    raw = row.get("results")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if status in ("failed", "error"):
        return {"run_id": run_id, "status": "failed", "error": raw.get("error", "Échec optimisation")}
    if status in ("queued", "running", "pending") or not raw.get("pareto_front"):
        return {"run_id": run_id, "status": status, "points": []}
    params_col = row.get("params")
    if isinstance(params_col, str):
        try:
            params_col = json.loads(params_col)
        except json.JSONDecodeError:
            params_col = {}
    constraints = (params_col or {}).get("constraints") if isinstance(params_col, dict) else {}
    if not constraints:
        constraints = _feasibility_constraints(pid, {})
    payload = _pareto_payload_from_results(raw, pid, constraints)
    return {"run_id": run_id, "status": "completed", **payload}


@router.post("/feasibility/adopt")
def post_feasibility_adopt(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Adopt a Pareto solution as the locked baseline (feasibility_adopted)."""
    solution = body.get("solution") or {}
    levers = _normalize_levers(pid, solution.get("levers") or body.get("levers") or {})
    if not levers:
        raise HTTPException(400, "solution.levers requis")
    run_id = body.get("source_run_id") or body.get("run_id")
    if not run_id:
        run = _last_run_row(pid)
        run_id = str(run["id"]) if run else None
    if not run_id:
        raise HTTPException(400, "source_run_id requis")
    if not _baseline_table_ready():
        raise HTTPException(503, "Migration baseline absente")
    run = qone(
        "SELECT results FROM simulation_runs_v2 WHERE id=%s AND project_id=%s",
        (run_id, pid),
    )
    overall = _parse_overall(run, pid) if run else {}
    if solution.get("metrics"):
        for k, v in solution["metrics"].items():
            if v is not None:
                overall[k if k != "recovery_pct" else "total_recovery_pct"] = v
    if not overall:
        overall = _apply_surrogate(
            {}, _build_lever_values(pid), levers, _levers_meta(pid)
        )
    kpis = _kpis_p50_from_overall(overall)
    execute(
        "UPDATE project_metallurgical_baseline SET is_active=FALSE WHERE project_id=%s AND is_active=TRUE",
        (pid,),
    )
    execute(
        """
        INSERT INTO project_metallurgical_baseline
            (project_id, source_run_id, mode, levers_json, kpis_p50_json, locked_by, notes)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        """,
        (
            pid,
            run_id,
            "feasibility_adopted",
            json.dumps(levers),
            json.dumps(kpis),
            str(user["id"]),
            body.get("notes") or "Adopté depuis front Pareto faisabilité",
        ),
    )
    set_status(
        pid,
        "simulation",
        "complete",
        user_id=str(user["id"]),
        triggered_by="feasibility_adopt",
        input_snapshot={"run_id": run_id, "levers": levers},
    )
    cascaded = mark_stale_cascade(pid, "simulation", user_id=str(user["id"]))
    return {
        "ok": True,
        "baseline": _get_active_baseline(pid),
        "cascaded_modules": cascaded,
        "levers": levers,
    }


# ─── Mode Opérations (Tranche 3) ──────────────────────────────────────────────

_OPS_SPREAD = 0.04
_OPS_METRICS = (
    ("recovery_pct", "total_recovery_pct"),
    ("gold_koz_y", "annual_gold_oz"),
    ("opex_per_t", "opex_per_t"),
    ("energy_kwh_t", "total_energy_kwh_t"),
)


def _ops_table_ready() -> bool:
    row = qone(
        "SELECT 1 AS ok FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name='metallurgical_operations_monthly' LIMIT 1"
    )
    return bool(row)


def _parse_json_field(val: Any) -> dict:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _corridor_from_kpis(kpis: dict[str, float], spread: float = _OPS_SPREAD) -> dict[str, dict]:
    bands: dict[str, dict] = {}
    for label, _src in _OPS_METRICS:
        p50 = kpis.get(label)
        if p50 is None and label == "gold_koz_y":
            oz = kpis.get("annual_gold_oz")
            p50 = (float(oz) / 1000.0) if oz else None
        if p50 is None:
            continue
        p50f = float(p50)
        if label == "gold_koz_y":
            p50f = p50f if p50f < 5000 else p50f / 1000.0
        bands[label] = {
            "p10": round(p50f * (1 - spread), 3),
            "p50": round(p50f, 3),
            "p90": round(p50f * (1 + spread), 3),
        }
    return bands


def _actuals_to_kpis(actuals: dict[str, Any]) -> dict[str, float]:
    tph = float(actuals.get("feed_tph") or 0)
    grade = float(actuals.get("head_grade_g_t") or actuals.get("grade_g_t") or 0)
    rec = float(actuals.get("recovery_pct") or 0)
    hours = float(actuals.get("hours_operated") or 720.0)
    kpis: dict[str, float] = {
        "recovery_pct": round(rec, 2),
        "opex_per_t": round(float(actuals.get("opex_per_t") or 0), 2),
        "energy_kwh_t": round(float(actuals.get("energy_kwh_t") or 0), 2),
    }
    if tph > 0 and grade > 0 and rec > 0 and hours > 0:
        oz_month = tph * hours * grade * (rec / 100.0) * TROY_OZ_PER_GRAM
        kpis["gold_koz_y"] = round(oz_month * (8760.0 / hours) / 1000.0, 1)
    elif actuals.get("gold_koz_y") is not None:
        kpis["gold_koz_y"] = round(float(actuals["gold_koz_y"]), 1)
    return kpis


def _metric_status(label: str, actual: float, band: dict[str, float]) -> str:
    p10, p50, p90 = float(band["p10"]), float(band["p50"]), float(band["p90"])
    if p10 <= actual <= p90:
        return "ok"
    higher_is_better = label in ("recovery_pct", "gold_koz_y")
    if higher_is_better:
        if actual < p10:
            return "red"
        if actual < p50:
            return "amber"
        return "amber"
    # opex / energy — lower is better
    if actual > p90:
        return "red"
    if actual > p50:
        return "amber"
    return "amber"


def _compute_variance(actuals: dict[str, Any], baseline_kpis: dict[str, Any]) -> dict[str, Any]:
    kpis = _parse_json_field(baseline_kpis)
    actual_kpis = _actuals_to_kpis(actuals)
    corridors = _corridor_from_kpis(kpis)
    deltas: dict[str, Any] = {}
    statuses: dict[str, str] = {}
    for label, _ in _OPS_METRICS:
        actual = actual_kpis.get(label)
        band = corridors.get(label)
        p50 = (band or {}).get("p50")
        if actual is None or p50 is None:
            statuses[label] = "unknown"
            continue
        deltas[label] = round(float(actual) - float(p50), 3)
        statuses[label] = _metric_status(label, float(actual), band) if band else "unknown"
    below_p10_recovery = (
        actual_kpis.get("recovery_pct") is not None
        and corridors.get("recovery_pct")
        and float(actual_kpis["recovery_pct"]) < float(corridors["recovery_pct"]["p10"])
    )
    return {
        "actual_kpis": actual_kpis,
        "baseline_kpis": kpis,
        "corridors": corridors,
        "deltas": deltas,
        "statuses": statuses,
        "below_p10_recovery": below_p10_recovery,
    }


def _list_ops_months(pid: str, limit: int = 12) -> list[dict]:
    if not _ops_table_ready():
        return []
    rows = qall(
        "SELECT id, period_yyyy_mm, actuals_json, variance_json, created_at, updated_at "
        "FROM metallurgical_operations_monthly WHERE project_id=%s "
        "ORDER BY period_yyyy_mm DESC LIMIT %s",
        (pid, limit),
    )
    out = []
    for row in rows or []:
        out.append({
            "id": str(row["id"]),
            "period_yyyy_mm": row["period_yyyy_mm"],
            "actuals": _parse_json_field(row.get("actuals_json")),
            "variance": _parse_json_field(row.get("variance_json")),
            "updated_at": row.get("updated_at").isoformat() if row.get("updated_at") else None,
        })
    return out


def _drift_alert(months: list[dict]) -> dict[str, Any]:
    """Alert if 3 latest months consecutive below P10 recovery."""
    if len(months) < 3:
        return {"active": False, "message": None}
    recent = sorted(months, key=lambda m: m["period_yyyy_mm"], reverse=True)[:3]
    if all((m.get("variance") or {}).get("below_p10_recovery") for m in recent):
        periods = ", ".join(m["period_yyyy_mm"] for m in recent)
        return {
            "active": True,
            "message": f"Dérive métallurgique : récupération sous P10 pendant {periods}",
            "periods": [m["period_yyyy_mm"] for m in recent],
        }
    return {"active": False, "message": None}


def _lims_month_hints(pid: str) -> dict[str, Any]:
    """Prefill monthly actuals from recent LIMS (D1 + A1 averages)."""
    hints: dict[str, Any] = {"source": "lims"}
    d1 = qone(
        "SELECT AVG(au_recovery_pct) AS rec, AVG(nacn_consumption_kg_t) AS cn "
        "FROM lims_d1 WHERE project_id=%s AND au_recovery_pct IS NOT NULL",
        (pid,),
    )
    a1 = qone(
        "SELECT AVG(au_g_t) AS grade FROM lims_a1 WHERE project_id=%s AND au_g_t IS NOT NULL",
        (pid,),
    )
    proj = qone("SELECT target_tph, gold_grade_g_t FROM projects WHERE id=%s", (pid,))
    if d1 and d1.get("rec") is not None:
        hints["recovery_pct"] = round(float(d1["rec"]), 2)
    if d1 and d1.get("cn") is not None:
        hints["cn_kg_t"] = round(float(d1["cn"]), 2)
    if a1 and a1.get("grade") is not None:
        hints["head_grade_g_t"] = round(float(a1["grade"]), 3)
    if proj:
        if proj.get("target_tph"):
            hints["feed_tph"] = float(proj["target_tph"])
        if proj.get("gold_grade_g_t") and "head_grade_g_t" not in hints:
            hints["head_grade_g_t"] = float(proj["gold_grade_g_t"])
    flat = flat_simulation_defaults(pid)
    if flat.get("opex_per_tonne"):
        hints["opex_per_t"] = float(flat["opex_per_tonne"])
    hints["energy_kwh_t"] = 15.0
    return hints


@router.get("/operations/variance")
def get_operations_variance(pid: str, user=Depends(project_user)):
    """Variance vs locked baseline, 12-month history, drift alert."""
    if not _ops_table_ready():
        raise HTTPException(503, "Table metallurgical_operations_monthly absente — migrations requises.")
    baseline = _get_active_baseline(pid)
    if not baseline:
        return {
            "locked": False,
            "message": "Aucun plan verrouillé — utilisez le mode Étude ou Faisabilité.",
            "months": [],
            "series": {},
        }
    months = _list_ops_months(pid, 12)
    latest = months[0] if months else None
    corridors = _corridor_from_kpis(_parse_json_field(baseline.get("kpis_p50_json")))
    series: dict[str, list] = {label: [] for label, _ in _OPS_METRICS}
    for m in reversed(sorted(months, key=lambda x: x["period_yyyy_mm"])):
        ak = (m.get("variance") or {}).get("actual_kpis") or _actuals_to_kpis(m.get("actuals") or {})
        for label, _ in _OPS_METRICS:
            v = ak.get(label)
            if v is not None:
                series[label].append({"period": m["period_yyyy_mm"], "value": v})
    return {
        "locked": True,
        "baseline": baseline,
        "corridors": corridors,
        "latest": latest,
        "months": months,
        "series": series,
        "drift_alert": _drift_alert(months),
    }


@router.get("/operations/lims-hints")
def get_operations_lims_hints(pid: str, user=Depends(project_user)):
    """Suggested monthly actuals from LIMS aggregates."""
    return _lims_month_hints(pid)


@router.delete("/simulation-runs")
def delete_simulation_runs(pid: str, user=Depends(project_user)):
    """Delete metallurgical-decision simulation runs for the current project."""
    row = qone("SELECT COUNT(*) AS n FROM simulation_runs_v2 WHERE project_id=%s", (pid,)) or {}
    deleted_runs = int(row.get("n") or 0)
    execute("DELETE FROM simulation_runs_v2 WHERE project_id=%s", (pid,))
    execute("DELETE FROM metallurgical_decision_runs WHERE project_id=%s", (pid,))
    return {"ok": True, "deleted_runs": deleted_runs}


@router.post("/operations/month")
def post_operations_month(pid: str, body: dict = Body(default={}), user=Depends(project_user)):
    """Record or update monthly plant actuals vs locked baseline."""
    if not _ops_table_ready():
        raise HTTPException(503, "Table metallurgical_operations_monthly absente — migrations requises.")
    baseline = _get_active_baseline(pid)
    if not baseline:
        raise HTTPException(400, "Verrouillez un plan PFS avant la saisie opérationnelle.")
    period = str(body.get("period_yyyy_mm") or datetime.now(timezone.utc).strftime("%Y-%m"))
    if len(period) != 7 or period[4] != "-":
        raise HTTPException(400, "period_yyyy_mm invalide (format YYYY-MM)")
    actuals = body.get("actuals") if isinstance(body.get("actuals"), dict) else {
        k: body[k]
        for k in (
            "feed_tph", "head_grade_g_t", "grade_g_t", "recovery_pct",
            "opex_per_t", "energy_kwh_t", "cn_kg_t", "hours_operated", "gold_koz_y",
        )
        if k in body
    }
    if not actuals:
        raise HTTPException(400, "actuals requis (feed_tph, recovery_pct, …)")
    variance = _compute_variance(actuals, baseline.get("kpis_p50_json") or {})
    row = execute(
        """
        INSERT INTO metallurgical_operations_monthly
            (project_id, period_yyyy_mm, actuals_json, variance_json, updated_at)
        VALUES (%s, %s, %s::jsonb, %s::jsonb, NOW())
        ON CONFLICT (project_id, period_yyyy_mm) DO UPDATE SET
            actuals_json = EXCLUDED.actuals_json,
            variance_json = EXCLUDED.variance_json,
            updated_at = NOW()
        RETURNING id, period_yyyy_mm
        """,
        (pid, period, json.dumps(actuals), json.dumps(variance)),
    )
    months = _list_ops_months(pid, 12)
    return {
        "ok": True,
        "period_yyyy_mm": period,
        "id": str(row.get("id", "")),
        "variance": variance,
        "drift_alert": _drift_alert(months),
        "months": months,
    }
