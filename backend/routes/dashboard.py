"""Dashboard aggregation endpoint — single-call project summary."""
from __future__ import annotations
import logging
import os
import time

import psycopg2
from fastapi import APIRouter, HTTPException, Depends

logger = logging.getLogger("mpdpms.dashboard")

try:
    from ..auth import project_user
    from ..db import qone, qall
    from ..helpers import resolve_dashboard_project_fields, resolve_process_production, resolve_recovery_breakdown
    from ..routes.lims import _audit_lims_project
except ImportError:
    from auth import project_user
    from db import qone, qall
    from helpers import resolve_dashboard_project_fields, resolve_process_production, resolve_recovery_breakdown
    from routes.lims import _audit_lims_project

_USE_LEGACY_QUERIES = os.getenv("DASHBOARD_LEGACY_QUERIES", "0") == "1"

router = APIRouter(prefix="/api/v1/projects", tags=["dashboard"])

_DASHBOARD_CACHE: dict[str, tuple[float, dict]] = {}
_DASHBOARD_TTL = 120.0  # seconds — avoids hammering the DB on frontend auto-refresh


def _invalidate_dashboard(pid: str) -> None:
    """Call this after any write that affects dashboard counters."""
    _DASHBOARD_CACHE.pop(pid, None)


@router.get("/{pid}/dashboard")
def get_dashboard(pid: str, user=Depends(project_user)):
    try:
        now = time.monotonic()
        cached = _DASHBOARD_CACHE.get(pid)
        if cached and (now - cached[0]) < _DASHBOARD_TTL:
            return cached[1]
        result = _get_dashboard_impl(pid, user)
        _DASHBOARD_CACHE[pid] = (now, result)
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        logger.error("DB error in dashboard pid=%s", pid)
        raise HTTPException(503, detail="Base de données temporairement indisponible")


def _get_dashboard_impl(pid: str, user: dict) -> dict:
    if _USE_LEGACY_QUERIES:
        return _get_dashboard_legacy(pid, user)
    return _get_dashboard_optimized(pid, user)


def _get_dashboard_legacy(pid: str, user):
    # Stage gates
    gates = qall("SELECT status FROM stage_gates WHERE project_id=%s", (pid,)) or []
    total_gates = len(gates)
    completed = sum(1 for g in gates if g["status"] in ("approved", "complete"))
    current_gate = qone(
        "SELECT stage_name FROM stage_gates WHERE project_id=%s AND status NOT IN ('approved','complete') "
        "ORDER BY stage_order LIMIT 1", (pid,)
    )
    blocked_count = sum(1 for g in gates if g["status"] == "blocked")
    completion_pct = round((completed / total_gates * 100), 1) if total_gates else 0.0

    # LIMS
    sample_count = (qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0)
    lims_complete = (qone("SELECT COUNT(*) AS n FROM lims_a1 WHERE project_id=%s", (pid,)) or {}).get("n", 0)
    lims_qaqc = _audit_lims_project(pid)
    geomet_domains = int((qone("SELECT COUNT(*) AS n FROM geomet_domains WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    geomet_composites = int((qone("SELECT COUNT(*) AS n FROM geomet_composites WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    control_variables = int((qone("SELECT COUNT(*) AS n FROM control_variables WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    control_alarms = int((qone("SELECT COUNT(*) AS n FROM control_alarms WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    control_interlocks = int((qone("SELECT COUNT(*) AS n FROM control_interlocks WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    geomet_confidence = round(
        (min(100.0, (geomet_domains * 20.0)) * 0.25)
        + (min(100.0, (geomet_composites * 25.0)) * 0.25)
        + (lims_qaqc["quality_score"] * 0.50),
        1,
    )
    automation_readiness = round(min(100.0, (control_variables * 10.0) + (control_alarms * 7.5) + (control_interlocks * 12.5)), 1)

    # Costs
    capex_row = qone(
        "SELECT COALESCE(SUM(cli.total_cost_usd), 0) AS total "
        "FROM cost_line_items cli "
        "JOIN cost_models cm ON cm.id = cli.model_id "
        "WHERE cm.project_id=%s AND cm.model_type='CAPEX'", (pid,)
    ) or {}
    opex_row = qone(
        "SELECT COALESCE(SUM(cli.total_cost_usd), 0) AS total "
        "FROM cost_line_items cli "
        "JOIN cost_models cm ON cm.id = cli.model_id "
        "WHERE cm.project_id=%s AND cm.model_type='OPEX'", (pid,)
    ) or {}

    # Risks (criticality = probability × impact, stored as integer)
    risks = qall("SELECT criticality FROM risks WHERE project_id=%s", (pid,)) or []
    def count_by_level(rows, low, high):
        return sum(1 for r in rows if low <= (r.get("criticality") or 0) <= high)
    risk_summary = {
        "critical": count_by_level(risks, 20, 25),
        "high": count_by_level(risks, 12, 19),
        "medium": count_by_level(risks, 6, 11),
        "low": count_by_level(risks, 1, 5),
    }

    # Recent decisions (last 5) — table may not exist yet if migrations not run
    try:
        recent_decisions = qall(
            "SELECT id, title, status, decided_at FROM decisions "
            "WHERE project_id=%s ORDER BY created_at DESC LIMIT 5", (pid,)
        ) or []
        for d in recent_decisions:
            d["id"] = str(d["id"]) if d.get("id") else None
    except Exception:
        recent_decisions = []

    project = qone("SELECT * FROM projects WHERE id=%s", (pid,)) or {}
    proj_view = resolve_dashboard_project_fields(pid, project)
    float(proj_view.get("target_tph") or 0)
    float(proj_view.get("gold_grade") or 0)
    float(proj_view.get("availability_pct") or 92) / 100
    int(proj_view.get("mine_life_years") or 0)
    gold_price = float(proj_view.get("gold_price") or 0)

    prod = resolve_process_production(pid, project)
    recovery_detail = resolve_recovery_breakdown(pid, project)
    recovery_pct = float(recovery_detail["plant_recovery_pct"])
    annual_tonnes = float(prod["annual_tonnes"])
    annual_gold_oz = float(prod["annual_gold_oz"])
    annual_revenue = annual_gold_oz * gold_price if gold_price > 0 else 0

    # Equipment count
    equip_count = int((qone("SELECT COUNT(*) AS n FROM equipment_v2 WHERE project_id=%s AND enabled=true", (pid,)) or {}).get("n", 0))
    equip_capex = float((qone("SELECT COALESCE(SUM(price_cad),0) AS total FROM equipment_v2 WHERE project_id=%s AND enabled=true", (pid,)) or {}).get("total", 0))

    # OPEX breakdown
    opex_manpower = float((qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_manpower WHERE project_id=%s", (pid,)) or {}).get("t", 0))
    opex_power = float((qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_power WHERE project_id=%s", (pid,)) or {}).get("t", 0))
    opex_reagents = float((qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_reagents WHERE project_id=%s", (pid,)) or {}).get("t", 0))
    opex_mobile = float((qone("SELECT COALESCE(SUM(total_cost),0) AS t FROM opex_mobile WHERE project_id=%s", (pid,)) or {}).get("t", 0))
    opex_total_v2 = opex_manpower + opex_power + opex_reagents + opex_mobile

    # Design criteria count
    dc_count = int((qone("SELECT COUNT(*) AS n FROM design_criteria WHERE project_id=%s", (pid,)) or {}).get("n", 0))
    dc_v2_count = int((qone(
        "SELECT COUNT(*) AS n FROM design_criteria_v2 WHERE template_id IN "
        "(SELECT id FROM circuit_templates WHERE project_id=%s) AND enabled=true", (pid,)
    ) or {}).get("n", 0))

    # Mass balance streams
    mb_streams = int((qone(
        "SELECT COUNT(*) AS n FROM mass_balance_streams_v2 WHERE section_id IN "
        "(SELECT id FROM mass_balance_sections_v2 WHERE project_id=%s)", (pid,)
    ) or {}).get("n", 0))

    # Failed tasks
    failed_tasks = int((qone(
        "SELECT COUNT(*) AS n FROM failed_tasks WHERE project_id=%s AND resolved_at IS NULL", (pid,)
    ) or {}).get("n", 0))

    # NI 43-101 sections
    ni_sections = int((qone("SELECT COUNT(*) AS n FROM ni43101_sections WHERE project_id=%s", (pid,)) or {}).get("n", 0))

    # Audit events count (last 7 days)
    recent_audit = int((qone(
        "SELECT COUNT(*) AS n FROM audit_events WHERE project_id=%s AND timestamp > NOW() - INTERVAL '7 days'",
        (pid,),
    ) or {}).get("n", 0))

    return {
        "project": proj_view,
        "production": {
            "annual_tonnes": round(annual_tonnes, 0),
            "annual_gold_oz": round(annual_gold_oz, 0),
            "annual_gold_koz": round(annual_gold_oz / 1000, 1),
            "annual_revenue_musd": round(annual_revenue / 1e6, 1),
            "recovery_pct": round(recovery_pct, 1),
            "recovery": recovery_detail,
        },
        "stage_gates": {
            "current_phase": current_gate["stage_name"] if current_gate else None,
            "total": total_gates,
            "completed": completed,
            "completion_pct": completion_pct,
            "blocked_count": blocked_count,
        },
        "lims": {
            "sample_count": int(sample_count),
            "tests_complete": int(lims_complete),
            "tests_pending": max(0, int(sample_count) - int(lims_complete)),
            "quality_score": lims_qaqc["quality_score"],
            "high_issues": lims_qaqc["issue_counts"]["high"],
            "medium_issues": lims_qaqc["issue_counts"]["medium"],
        },
        "geomet": {
            "domains": geomet_domains,
            "composites": geomet_composites,
            "confidence_score": geomet_confidence,
        },
        "automation": {
            "variables": control_variables,
            "alarms": control_alarms,
            "interlocks": control_interlocks,
            "readiness_score": automation_readiness,
        },
        "modules": {
            "design_criteria": dc_count + dc_v2_count,
            "mass_balance_streams": mb_streams,
            "equipment": equip_count,
            "equipment_capex": equip_capex,
            "ni43101_sections": ni_sections,
        },
        "costs": {
            "capex_total": float(capex_row.get("total") or 0),
            "opex_total": float(opex_row.get("total") or 0),
            "opex_v2_total": opex_total_v2,
            "opex_breakdown": {
                "manpower": opex_manpower,
                "power": opex_power,
                "reagents": opex_reagents,
                "mobile": opex_mobile,
            },
            "currency": "CAD",
        },
        "risks": risk_summary,
        "alerts": {
            "failed_tasks": failed_tasks,
            "critical_risks": risk_summary.get("critical", 0),
            "high_risks": risk_summary.get("high", 0),
            "lims_high_issues": lims_qaqc["issue_counts"]["high"],
            "blocked_gates": blocked_count,
        },
        "activity": {
            "recent_audit_events": recent_audit,
            "recent_decisions": recent_decisions,
        },
    }


def _get_dashboard_optimized(pid: str, user) -> dict:
    """Consolidated dashboard query — ~5 queries instead of 25+."""

    # ── Query 1: Stage gates — single SELECT for all gate rows ──────────
    gates = qall(
        "SELECT stage_name, status, stage_order FROM stage_gates "
        "WHERE project_id=%s ORDER BY stage_order",
        (pid,),
    ) or []
    total_gates = len(gates)
    completed = sum(1 for g in gates if g["status"] in ("approved", "complete"))
    blocked_count = sum(1 for g in gates if g["status"] == "blocked")
    completion_pct = round((completed / total_gates * 100), 1) if total_gates else 0.0
    # First non-complete gate is the current phase
    current_phase = None
    for g in gates:
        if g["status"] not in ("approved", "complete"):
            current_phase = g["stage_name"]
            break

    # ── Query 2: All entity counts — single query with scalar subqueries ─
    counts_row = qone(
        "SELECT "
        "  (SELECT COUNT(*) FROM lims_samples WHERE project_id=%s) AS sample_count, "
        "  (SELECT COUNT(*) FROM lims_a1 WHERE project_id=%s) AS lims_complete, "
        "  (SELECT COUNT(*) FROM geomet_domains WHERE project_id=%s) AS geomet_domains, "
        "  (SELECT COUNT(*) FROM geomet_composites WHERE project_id=%s) AS geomet_composites, "
        "  (SELECT COUNT(*) FROM control_variables WHERE project_id=%s) AS control_variables, "
        "  (SELECT COUNT(*) FROM control_alarms WHERE project_id=%s) AS control_alarms, "
        "  (SELECT COUNT(*) FROM control_interlocks WHERE project_id=%s) AS control_interlocks, "
        "  (SELECT COUNT(*) FROM equipment_v2 WHERE project_id=%s AND enabled=true) AS equip_count, "
        "  (SELECT COALESCE(SUM(price_cad),0) FROM equipment_v2 WHERE project_id=%s AND enabled=true) AS equip_capex, "
        "  (SELECT COUNT(*) FROM design_criteria WHERE project_id=%s) AS dc_count, "
        "  (SELECT COUNT(*) FROM design_criteria_v2 WHERE template_id IN "
        "    (SELECT id FROM circuit_templates WHERE project_id=%s) AND enabled=true) AS dc_v2_count, "
        "  (SELECT COUNT(*) FROM mass_balance_streams_v2 WHERE section_id IN "
        "    (SELECT id FROM mass_balance_sections_v2 WHERE project_id=%s)) AS mb_streams, "
        "  (SELECT COUNT(*) FROM failed_tasks WHERE project_id=%s AND resolved_at IS NULL) AS failed_tasks, "
        "  (SELECT COUNT(*) FROM ni43101_sections WHERE project_id=%s) AS ni_sections, "
        "  (SELECT COUNT(*) FROM audit_events WHERE project_id=%s AND timestamp > NOW() - INTERVAL '7 days') AS recent_audit",
        (pid, pid, pid, pid, pid, pid, pid, pid, pid, pid, pid, pid, pid, pid, pid),
    ) or {}

    sample_count = int(counts_row.get("sample_count") or 0)
    lims_complete = int(counts_row.get("lims_complete") or 0)
    geomet_domains = int(counts_row.get("geomet_domains") or 0)
    geomet_composites = int(counts_row.get("geomet_composites") or 0)
    control_variables = int(counts_row.get("control_variables") or 0)
    control_alarms = int(counts_row.get("control_alarms") or 0)
    control_interlocks = int(counts_row.get("control_interlocks") or 0)
    equip_count = int(counts_row.get("equip_count") or 0)
    equip_capex = float(counts_row.get("equip_capex") or 0)
    dc_count = int(counts_row.get("dc_count") or 0)
    dc_v2_count = int(counts_row.get("dc_v2_count") or 0)
    mb_streams = int(counts_row.get("mb_streams") or 0)
    failed_tasks = int(counts_row.get("failed_tasks") or 0)
    ni_sections = int(counts_row.get("ni_sections") or 0)
    recent_audit = int(counts_row.get("recent_audit") or 0)

    # ── Query 3: Cost sums — CAPEX/OPEX totals + OPEX v2 breakdown ──────
    cost_row = qone(
        "SELECT "
        "  (SELECT COALESCE(SUM(cli.total_cost_usd), 0) "
        "   FROM cost_line_items cli JOIN cost_models cm ON cm.id = cli.model_id "
        "   WHERE cm.project_id=%s AND cm.model_type='CAPEX') AS capex_total, "
        "  (SELECT COALESCE(SUM(cli.total_cost_usd), 0) "
        "   FROM cost_line_items cli JOIN cost_models cm ON cm.id = cli.model_id "
        "   WHERE cm.project_id=%s AND cm.model_type='OPEX') AS opex_total, "
        "  (SELECT COALESCE(SUM(total_cost),0) FROM opex_manpower WHERE project_id=%s) AS opex_manpower, "
        "  (SELECT COALESCE(SUM(total_cost),0) FROM opex_power WHERE project_id=%s) AS opex_power, "
        "  (SELECT COALESCE(SUM(total_cost),0) FROM opex_reagents WHERE project_id=%s) AS opex_reagents, "
        "  (SELECT COALESCE(SUM(total_cost),0) FROM opex_mobile WHERE project_id=%s) AS opex_mobile",
        (pid, pid, pid, pid, pid, pid),
    ) or {}

    capex_total = float(cost_row.get("capex_total") or 0)
    opex_total = float(cost_row.get("opex_total") or 0)
    opex_manpower = float(cost_row.get("opex_manpower") or 0)
    opex_power = float(cost_row.get("opex_power") or 0)
    opex_reagents = float(cost_row.get("opex_reagents") or 0)
    opex_mobile = float(cost_row.get("opex_mobile") or 0)
    opex_total_v2 = opex_manpower + opex_power + opex_reagents + opex_mobile

    # ── Query 4: Risks + decisions + recovery sim param ─────────────────
    risks = qall("SELECT criticality FROM risks WHERE project_id=%s", (pid,)) or []

    def count_by_level(rows, low, high):
        return sum(1 for r in rows if low <= (r.get("criticality") or 0) <= high)

    risk_summary = {
        "critical": count_by_level(risks, 20, 25),
        "high": count_by_level(risks, 12, 19),
        "medium": count_by_level(risks, 6, 11),
        "low": count_by_level(risks, 1, 5),
    }

    # Recent decisions (last 5) — table may not exist yet if migrations not run
    try:
        recent_decisions = qall(
            "SELECT id, title, status, decided_at FROM decisions "
            "WHERE project_id=%s ORDER BY created_at DESC LIMIT 5", (pid,)
        ) or []
        for d in recent_decisions:
            d["id"] = str(d["id"]) if d.get("id") else None
    except Exception:
        recent_decisions = []

    # ── Query 5: Project row + LIMS quality audit ───────────────────────
    project = qone("SELECT * FROM projects WHERE id=%s", (pid,)) or {}
    lims_qaqc = _audit_lims_project(pid)

    # ── Derive production estimates (shared with economics / DCF) ───────
    proj_view = resolve_dashboard_project_fields(pid, project)
    gold_price = float(proj_view.get("gold_price") or 0)

    prod = resolve_process_production(pid, project)
    recovery_detail = resolve_recovery_breakdown(pid, project)
    recovery_pct = float(recovery_detail["plant_recovery_pct"])
    annual_tonnes = float(prod["annual_tonnes"])
    annual_gold_oz = float(prod["annual_gold_oz"])
    annual_revenue = annual_gold_oz * gold_price if gold_price > 0 else 0

    # ── Derived scores ──────────────────────────────────────────────────
    geomet_confidence = round(
        (min(100.0, (geomet_domains * 20.0)) * 0.25)
        + (min(100.0, (geomet_composites * 25.0)) * 0.25)
        + (lims_qaqc["quality_score"] * 0.50),
        1,
    )
    automation_readiness = round(
        min(100.0, (control_variables * 10.0) + (control_alarms * 7.5) + (control_interlocks * 12.5)),
        1,
    )

    # ── Build response (same structure as legacy) ───────────────────────
    return {
        "project": proj_view,
        "production": {
            "annual_tonnes": round(annual_tonnes, 0),
            "annual_gold_oz": round(annual_gold_oz, 0),
            "annual_gold_koz": round(annual_gold_oz / 1000, 1),
            "annual_revenue_musd": round(annual_revenue / 1e6, 1),
            "recovery_pct": round(recovery_pct, 1),
            "recovery": recovery_detail,
        },
        "stage_gates": {
            "current_phase": current_phase,
            "total": total_gates,
            "completed": completed,
            "completion_pct": completion_pct,
            "blocked_count": blocked_count,
        },
        "lims": {
            "sample_count": sample_count,
            "tests_complete": lims_complete,
            "tests_pending": max(0, sample_count - lims_complete),
            "quality_score": lims_qaqc["quality_score"],
            "high_issues": lims_qaqc["issue_counts"]["high"],
            "medium_issues": lims_qaqc["issue_counts"]["medium"],
        },
        "geomet": {
            "domains": geomet_domains,
            "composites": geomet_composites,
            "confidence_score": geomet_confidence,
        },
        "automation": {
            "variables": control_variables,
            "alarms": control_alarms,
            "interlocks": control_interlocks,
            "readiness_score": automation_readiness,
        },
        "modules": {
            "design_criteria": dc_count + dc_v2_count,
            "mass_balance_streams": mb_streams,
            "equipment": equip_count,
            "equipment_capex": equip_capex,
            "ni43101_sections": ni_sections,
        },
        "costs": {
            "capex_total": capex_total,
            "opex_total": opex_total,
            "opex_v2_total": opex_total_v2,
            "opex_breakdown": {
                "manpower": opex_manpower,
                "power": opex_power,
                "reagents": opex_reagents,
                "mobile": opex_mobile,
            },
            "currency": "CAD",
        },
        "risks": risk_summary,
        "alerts": {
            "failed_tasks": failed_tasks,
            "critical_risks": risk_summary.get("critical", 0),
            "high_risks": risk_summary.get("high", 0),
            "lims_high_issues": lims_qaqc["issue_counts"]["high"],
            "blocked_gates": blocked_count,
        },
        "activity": {
            "recent_audit_events": recent_audit,
            "recent_decisions": recent_decisions,
        },
    }
