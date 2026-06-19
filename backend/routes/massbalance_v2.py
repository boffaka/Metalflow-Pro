"""
MPDPMS — Mass Balance v2 routes + Carbon Accounting endpoints.

Operates on the v2 schema (mass_balance_sections_v2 / mass_balance_streams_v2)
with circuit-template-driven generation, snapshots, and carbon footprint.
"""
from __future__ import annotations

import json
import logging
import uuid
import hashlib
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Depends
import psycopg2.extras

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release
    from ..engines.mass_balance_engine import generate_mass_balance, compute_carbon_footprint
    from ..audit import record_event
    from ..models import MbStreamPatch, MbSnapshotIn, CarbonFactorPatch
    from ..logging_config import log_user_action
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, conn, release
    from engines.mass_balance_engine import generate_mass_balance, compute_carbon_footprint
    from audit import record_event
    from models import MbStreamPatch, MbSnapshotIn, CarbonFactorPatch
    from logging_config import log_user_action

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["mass-balance-v2"])
logger = logging.getLogger("mpdpms.massbalance_v2")


_TEXT_PARAM_KEYS = frozenset({"recovery_snapshot_source"})


def _table_has_column(tbl: str, col: str) -> bool:
    """Runtime schema guard for mixed migration states across environments."""
    row = qone(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s AND column_name=%s",
        (tbl, col),
    )
    return bool(row)


def _sync_recovery_params_from_mb_summary(pid: str, summary: dict, cur) -> None:
    """Publish MB recovery breakdown to simulation_params for dashboard KPIs."""
    if not summary:
        return
    rows = [
        ("process", "overall_recovery_pct", summary.get("overall_recovery_pct")),
        ("process", "gravity_recovery_pct", summary.get("gravity_recovery_pct")),
        ("process", "leach_recovery_pct", summary.get("leach_recovery_pct")),
        ("process", "plant_formula_recovery_pct", summary.get("plant_formula_recovery_pct")),
        ("process", "recovery_snapshot_source", "mass_balance"),
    ]
    for cat, key, val in rows:
        if val is None:
            continue
        if key in _TEXT_PARAM_KEYS:
            cur.execute(
                """UPDATE simulation_params
                   SET param_value_text=%s, updated_at=NOW()
                   WHERE project_id=%s AND category=%s AND param_key=%s""",
                (str(val), pid, cat, key),
            )
            if cur.rowcount == 0:
                cur.execute(
                    """INSERT INTO simulation_params
                       (project_id, category, param_key, param_label, param_value_text)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (project_id, category, param_key)
                       DO UPDATE SET param_value_text=EXCLUDED.param_value_text,
                                     updated_at=NOW()""",
                    (pid, cat, key, key, str(val)),
                )
            continue

        num_val = float(val)
        cur.execute(
            """UPDATE simulation_params
               SET param_value=%s, updated_at=NOW()
               WHERE project_id=%s AND category=%s AND param_key=%s""",
            (num_val, pid, cat, key),
        )
        if cur.rowcount == 0:
            cur.execute(
                """INSERT INTO simulation_params
                   (project_id, category, param_key, param_label, param_value)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (project_id, category, param_key)
                   DO UPDATE SET param_value=EXCLUDED.param_value, updated_at=NOW()""",
                (pid, cat, key, key, num_val),
            )

# ---------------------------------------------------------------------------
# Allowed fields for stream PATCH (prevents arbitrary column injection)
# ---------------------------------------------------------------------------
STREAM_PATCH_FIELDS = {
    "solids_tph", "water_tph", "slurry_pct_w", "au_gt",
    "hours_per_day", "source",
}

# Derived-field helpers
WATER_SG = 1.0
DEFAULT_ORE_SG = 2.74


def _recalc_derived(solids_tph: float, water_tph: float, pct_solids: float,
                    h_per_d: float, ore_sg: float = DEFAULT_ORE_SG) -> dict:
    """Recalculate derived columns after a manual stream edit."""
    solids_m3h = solids_tph / ore_sg if ore_sg > 0 else 0.0
    water_m3h = water_tph / WATER_SG
    slurry_tph = solids_tph + water_tph
    slurry_m3h = solids_m3h + water_m3h
    slurry_sg = slurry_tph / slurry_m3h if slurry_m3h > 0 else 1.0
    solids_tpd = solids_tph * h_per_d
    water_tpd = water_tph * h_per_d
    slurry_tpd = slurry_tph * h_per_d
    return {
        "solids_m3h": round(solids_m3h, 3),
        "water_m3h": round(water_m3h, 3),
        "slurry_tph": round(slurry_tph, 3),
        "slurry_m3h": round(slurry_m3h, 3),
        "slurry_sg": round(slurry_sg, 4),
        "solids_tpd": round(solids_tpd, 2),
        "water_tpd": round(water_tpd, 2),
        "slurry_tpd": round(slurry_tpd, 2),
    }


# ============================================================================
# 1. GET /mass-balance-v2 — full mass balance grouped by section
# ============================================================================

@router.get("/mass-balance-v2")
def get_mass_balance(pid: str, user=Depends(project_user)):
    """Return all sections and streams, grouped by section, with summary."""
    try:
        return _get_mass_balance_impl(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _get_mass_balance_impl(pid: str):
    sections = qall(
        "SELECT id, section_name, op_code, sort_order "
        "FROM mass_balance_sections_v2 "
        "WHERE project_id = %s ORDER BY sort_order",
        (pid,),
    )
    if not sections:
        raise HTTPException(404, "No mass balance data — use auto-generate first")

    streams = qall(
        "SELECT s.* FROM mass_balance_streams_v2 s "
        "JOIN mass_balance_sections_v2 sec ON s.section_id = sec.id "
        "WHERE sec.project_id = %s "
        "ORDER BY sec.sort_order, s.sort_order",
        (pid,),
    )

    # Group streams by section
    stream_map: dict[str, list] = {}
    for st in streams:
        sid = str(st["section_id"])
        stream_map.setdefault(sid, []).append(st)

    result_sections = []
    for sec in sections:
        result_sections.append({
            "section_name": sec["section_name"],
            "op_code": sec["op_code"],
            "sort_order": sec["sort_order"],
            "streams": stream_map.get(str(sec["id"]), []),
        })

    # Summary: first section feed and overall recovery
    all_streams = streams
    feed_tph = 0.0
    feed_au = 0.0
    tails_au = 0.0
    for st in all_streams:
        name = (st.get("stream_name") or "").lower()
        sol = float(st.get("solids_tph") or 0)
        au = float(st.get("au_gt") or 0)
        # Feed: ROM feed or first non-zero stream
        if sol > 0 and au > 0 and feed_tph == 0:
            if "rom" in name or "feed" in name:
                feed_tph = sol
                feed_au = au
        # Tails: thickener U/F to TSF, or any stream with "tsf" / "tailings" + "u/f"
        if sol > 0 and ("tsf" in name or ("tailing" in name and ("u/f" in name or "underflow" in name or "final" in name))):
            tails_au = au

    recovery_pct = (1 - tails_au / feed_au) * 100 if feed_au > 0 and tails_au >= 0 else 0
    proj = qone(
        "SELECT operating_hours_day, availability_pct, gold_grade_g_t, target_tph, ore_sg FROM projects WHERE id=%s",
        (pid,),
    ) or {}
    op_h = float(proj.get("operating_hours_day") or 22.1)
    avail = float(proj.get("availability_pct") or 92.0)
    annual_hours = op_h * 365.25 * (avail / 100.0)
    plant_tph = float(proj.get("target_tph") or feed_tph)
    plant_grade = float(proj.get("gold_grade_g_t") or feed_au)
    try:
        from ..helpers import compute_annual_gold_oz
    except ImportError:
        from helpers import compute_annual_gold_oz
    annual_oz = compute_annual_gold_oz(plant_tph, op_h, avail, plant_grade, recovery_pct)

    # Reagent consumption totals from streams
    nacn_kgh = sum(float(st.get("water_tph") or 0) * 1000
                   for st in all_streams
                   if "nacn" in (st.get("stream_name") or "").lower() or "cyanide" in (st.get("stream_name") or "").lower())
    cao_kgh = sum(float(st.get("water_tph") or 0) * 1000
                  for st in all_streams
                  if "cao" in (st.get("stream_name") or "").lower() or "lime" in (st.get("stream_name") or "").lower())

    # Water balance totals
    total_water_in = sum(float(st.get("water_m3h") or 0) for st in all_streams
                         if not st.get("is_balance_check") and float(st.get("water_m3h") or 0) > 0)
    tailings_water = sum(float(st.get("water_m3h") or 0) for st in all_streams
                         if "tsf" in (st.get("stream_name") or "").lower() and not st.get("is_balance_check"))

    return {
        "sections": result_sections,
        "summary": {
            "total_feed_tph": round(feed_tph, 1),
            "nominal_tph": round(float(proj.get("target_tph") or feed_tph), 1),
            "recovery_pct": round(recovery_pct, 2),
            "overall_recovery_pct": round(recovery_pct, 2),
            "annual_gold_oz": round(annual_oz, 0),
            "gold_grade_g_t": round(feed_au or float(proj.get("gold_grade_g_t") or 0), 3),
            "ore_sg": round(float(proj.get("ore_sg") or 2.75), 2),
            "h_per_day": round(op_h, 2),
            "availability_pct": round(avail, 1),
            "annual_hours": round(annual_hours, 0),
            # Au production
            "au_production_kg_d": round(feed_tph * feed_au * (recovery_pct / 100) * op_h / 1000, 2),
            "au_production_oz_d": round(annual_oz * op_h / (annual_hours or 1) / 365.25, 1)
            if annual_hours > 0 else 0,
            # Reagents
            "nacn_kg_h": round(nacn_kgh, 1),
            "cao_kg_h": round(cao_kgh, 1),
            # Water
            "total_water_in_m3h": round(total_water_in, 1),
            "tailings_water_m3h": round(abs(tailings_water), 1),
        },
    }


# ============================================================================
# 2. POST /mass-balance-v2/auto-generate
# ============================================================================

@router.post("/mass-balance-v2/auto-generate")
def auto_generate(pid: str, template_id: str | None = None, user=Depends(project_user)):
    """Delete existing MB data and regenerate from the selected circuit template.

    Uses ``template_id`` when the UI has an explicit circuit selected. Otherwise
    it falls back to the latest active circuit template for this project. Calls
    generate_mass_balance() from the engine, and returns the summary.
    """
    if template_id:
        tpl = qone(
            "SELECT id FROM circuit_templates "
            "WHERE id = %s AND project_id = %s AND is_active = TRUE",
            (template_id, pid),
        )
    else:
        tpl = qone(
            "SELECT id FROM circuit_templates "
            "WHERE project_id = %s AND is_active = TRUE "
            "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
            (pid,),
        )
    if not tpl:
        raise HTTPException(
            404,
            "No matching active circuit template found — create or select a circuit first",
        )

    template_id = str(tpl["id"])

    # Pre-generation coherence check: warn about unjustified operations
    coherence_warnings = []
    try:
        from .circuit import validate_circuit as _vc
        _val = _vc(pid, template_id, user)
        coherence_warnings = _val.get("warnings", []) + _val.get("missing", [])
    except Exception:  # intentional: ignore optional lookup failure
        pass  # Validation is advisory, don't block MB generation

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        summary = generate_mass_balance(pid, template_id, cur)
        _sync_recovery_params_from_mb_summary(pid, summary, cur)
        try:
            from ..routes.dashboard import _invalidate_dashboard
        except ImportError:
            from routes.dashboard import _invalidate_dashboard
        _invalidate_dashboard(pid)

        record_event(
            user_id=user["id"], project_id=pid,
            entity_type="mass_balance", entity_id=None,
            action="auto_generate",
            new_value={"template_id": template_id},
            source="web",
        )
        log_user_action(
            "mass_balance.auto_generate",
            user_id=str(user["id"]),
            entity_type="mass_balance",
            details={"project_id": pid, "template_id": template_id},
        )

        if coherence_warnings:
            summary["coherence_warnings"] = coherence_warnings

        c.commit()

        return summary
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        logger.exception("auto-generate failed for project %s", pid)
        raise HTTPException(500, "Mass balance generation failed")
    finally:
        release(c)


# ============================================================================
# 3. PATCH /mass-balance-v2/streams/{sid}
# ============================================================================

@router.patch("/mass-balance-v2/streams/{sid}")
def patch_stream(pid: str, sid: str, body: MbStreamPatch, user=Depends(project_user)):
    """Update a single stream's editable fields with optimistic locking.

    Allowed fields: solids_tph, water_tph, pct_solids, au_gt, hours_per_day, source.
    Recalculates derived fields (tpd, m3h, slurry, SG).
    Requires `version` in body for optimistic locking.
    """
    try:
        return _patch_stream_impl(pid, sid, body.model_dump(exclude_none=False), user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


def _patch_stream_impl(pid: str, sid: str, body: dict, user):
    # Validate fields
    updates = {k: v for k, v in body.items() if k in STREAM_PATCH_FIELDS}
    if not updates:
        raise HTTPException(400, "No valid fields to update")

    expected_version = body.get("version")
    if expected_version is None:
        raise HTTPException(400, "version field required for optimistic locking")

    # Fetch current stream and verify ownership
    current = qone(
        "SELECT s.*, sec.project_id "
        "FROM mass_balance_streams_v2 s "
        "JOIN mass_balance_sections_v2 sec ON s.section_id = sec.id "
        "WHERE s.id = %s AND sec.project_id = %s",
        (sid, pid),
    )
    if not current:
        raise HTTPException(404, "Stream not found")

    if int(current.get("version", 1)) != int(expected_version):
        raise HTTPException(
            409,
            "Conflict — stream was modified by another user. Refresh and retry.",
        )

    # Merge updates into current values
    solids_tph = float(updates.get("solids_tph", current["solids_tph"]))
    water_tph = float(updates.get("water_tph", current.get("water_tph") or 0))
    pct_solids = float(updates.get("slurry_pct_w", current.get("slurry_pct_w") or 50))
    h_per_d = float(updates.get("hours_per_day", current.get("hours_per_day") or 22.1))
    au_gt = updates.get("au_gt", current.get("au_gt"))
    source = updates.get("source", current.get("source"))
    ore_sg = float(current.get("solids_sg") or DEFAULT_ORE_SG)

    # Recalculate derived fields
    derived = _recalc_derived(solids_tph, water_tph, pct_solids, h_per_d, ore_sg)

    row = execute(
        """UPDATE mass_balance_streams_v2
           SET solids_tph = %s,
               water_tph = %s,
               slurry_pct_w = %s,
               au_gt = %s,
               hours_per_day = %s,
               source = %s,
               solids_m3h = %s,
               water_m3h = %s,
               slurry_tph = %s,
               slurry_m3h = %s,
               slurry_sg = %s,
               solids_tpd = %s,
               water_tpd = %s,
               slurry_tpd = %s,
               version = version + 1,
               updated_at = NOW()
           WHERE id = %s AND version = %s
           RETURNING *""",
        (
            solids_tph, water_tph, pct_solids, au_gt, h_per_d, source,
            derived["solids_m3h"], derived["water_m3h"],
            derived["slurry_tph"], derived["slurry_m3h"], derived["slurry_sg"],
            derived["solids_tpd"], derived["water_tpd"], derived["slurry_tpd"],
            sid, int(expected_version),
        ),
    )
    if not row:
        raise HTTPException(409, "Conflict — stream was modified concurrently")

    return row


# ============================================================================
# 4. POST /mass-balance-v2/snapshot — save current state
# ============================================================================

@router.post("/mass-balance-v2/snapshot", status_code=201)
def save_snapshot(pid: str, body: MbSnapshotIn, user=Depends(project_user)):
    """Save current MB as a named snapshot.

    Body: {name: str}
    Reads all sections+streams, serializes to JSON, inserts into
    mass_balance_snapshots.
    """
    try:
        name = body.name

        # Read current MB data
        sections = qall(
            "SELECT * FROM mass_balance_sections_v2 WHERE project_id = %s ORDER BY sort_order",
            (pid,),
        )
        if not sections:
            raise HTTPException(404, "No mass balance data to snapshot")

        streams = qall(
            "SELECT s.* FROM mass_balance_streams_v2 s "
            "JOIN mass_balance_sections_v2 sec ON s.section_id = sec.id "
            "WHERE sec.project_id = %s ORDER BY sec.sort_order, s.sort_order",
            (pid,),
        )

        # Serialize — convert non-JSON-serializable types
        def _serialize(rows):
            result = []
            for row in rows:
                cleaned = {}
                for k, v in row.items():
                    if isinstance(v, datetime):
                        cleaned[k] = v.isoformat()
                    elif isinstance(v, uuid.UUID):
                        cleaned[k] = str(v)
                    elif isinstance(v, Decimal):
                        cleaned[k] = float(v)
                    else:
                        cleaned[k] = v
                result.append(cleaned)
            return result

        payload_obj = {
            "sections": _serialize(sections),
            "streams": _serialize(streams),
        }
        payload = json.dumps(payload_obj)

        # Compatibility for environments where `mass_balance_snapshots` exists in
        # either legacy shape (template_id/stream_data/...) or v2 shape
        # (snapshot_data/created_by). This removes 500s after partial migrations.
        has_snapshot_data = _table_has_column("mass_balance_snapshots", "snapshot_data")
        has_created_by = _table_has_column("mass_balance_snapshots", "created_by")
        if has_snapshot_data:
            sql_cols = ["id", "project_id", "name", "snapshot_data"]
            params = [str(uuid.uuid4()), pid, name, payload]
            if has_created_by:
                sql_cols.append("created_by")
                params.append(user.get("id"))
            sql = (
                f"INSERT INTO mass_balance_snapshots ({', '.join(sql_cols)}) "
                "VALUES (" + ", ".join(["%s"] * len(sql_cols)) + ") "
                "RETURNING id, name, created_at"
            )
            snapshot = execute(sql, tuple(params))
        else:
            # Legacy schema fallback
            tpl = qone(
                "SELECT s.template_id "
                "FROM mass_balance_streams_v2 s "
                "JOIN mass_balance_sections_v2 sec ON sec.id = s.section_id "
                "WHERE sec.project_id=%s AND s.template_id IS NOT NULL "
                "LIMIT 1",
                (pid,),
            ) or qone(
                "SELECT id AS template_id FROM circuit_templates "
                "WHERE project_id=%s AND is_active=TRUE "
                "ORDER BY updated_at DESC NULLS LAST, created_at DESC LIMIT 1",
                (pid,),
            )
            if not tpl or not tpl.get("template_id"):
                raise HTTPException(
                    422,
                    detail="Cannot snapshot: no active circuit template linked to this mass balance",
                )

            summary = _get_mass_balance_impl(pid).get("summary", {})
            water_summary = {
                k: v for k, v in summary.items()
                if ("water" in k.lower() or "m3h" in k.lower())
            }
            production_summary = {
                k: v for k, v in summary.items()
                if (
                    "recovery" in k.lower()
                    or "gold" in k.lower()
                    or k.lower().startswith("au_")
                    or "annual" in k.lower()
                    or "feed" in k.lower()
                )
            }
            checksum = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            snapshot = execute(
                "INSERT INTO mass_balance_snapshots "
                "(id, project_id, template_id, name, checksum, stream_data, water_summary, production_summary) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, name, created_at",
                (
                    str(uuid.uuid4()),
                    pid,
                    str(tpl.get("template_id")),
                    name,
                    checksum,
                    json.dumps(payload_obj),
                    json.dumps(water_summary),
                    json.dumps(production_summary),
                ),
            )
        return snapshot
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
    except psycopg2.Error as e:
        logger.exception("snapshot save failed for project %s", pid)
        raise HTTPException(500, detail=f"Snapshot save failed: {str(e)}")
    except ValueError as e:
        raise HTTPException(422, detail=str(e))


# ============================================================================
# 5. GET /mass-balance-v2/snapshots — list snapshots
# ============================================================================

@router.get("/mass-balance-v2/snapshots")
def list_snapshots(pid: str, user=Depends(project_user)):
    """List all named snapshots for this project (metadata only, no payload)."""
    try:
        has_created_by = _table_has_column("mass_balance_snapshots", "created_by")
        select_cols = "id, name, created_at"
        if has_created_by:
            select_cols = "id, name, created_by, created_at"
        rows = qall(
            f"SELECT {select_cols} "
            "FROM mass_balance_snapshots "
            "WHERE project_id = %s ORDER BY created_at DESC",
            (pid,),
        )
        if not has_created_by:
            for r in rows:
                r["created_by"] = None
        return rows
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 6. GET /mass-balance-v2/carbon — carbon accounting
# ============================================================================

@router.get("/mass-balance-v2/carbon")
def get_carbon(pid: str, user=Depends(project_user)):
    """Calculate and return carbon footprint from current MB streams.

    Calls compute_carbon_footprint() from the engine.
    Returns: {per_operation, total_kgh, co2_per_oz, wgc_benchmark, comparison}
    """
    try:
        return _get_carbon_impl(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _get_carbon_impl(pid: str):
    streams = qall(
        "SELECT s.* FROM mass_balance_streams_v2 s "
        "JOIN mass_balance_sections_v2 sec ON s.section_id = sec.id "
        "WHERE sec.project_id = %s",
        (pid,),
    )
    if not streams:
        raise HTTPException(404, "No mass balance data — generate first")

    c = conn()
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        result = compute_carbon_footprint(pid, streams, cur)
    finally:
        release(c)

    # Augment with WGC benchmark comparison
    try:
        from ..settings import get_settings
    except ImportError:
        from settings import get_settings
    WGC_BENCHMARK = get_settings().wgc_co2_benchmark
    co2_per_oz = result.get("co2_per_oz", 0)
    result["wgc_benchmark"] = WGC_BENCHMARK
    result["comparison"] = "below" if co2_per_oz < WGC_BENCHMARK else "above"

    return result


# ============================================================================
# 7. PATCH /carbon-factors/{fid} — update emission factor
# ============================================================================

@router.patch("/carbon-factors/{fid}")
def patch_carbon_factor(pid: str, fid: str, body: CarbonFactorPatch, user=Depends(project_user)):
    """Update a project-specific emission factor.

    If no project override exists for this factor, creates one.
    Body: {factor_value: float}
    """
    try:
        return _patch_carbon_factor_impl(pid, fid, body, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _patch_carbon_factor_impl(pid: str, fid: str, body: CarbonFactorPatch, user):
    factor_value = body.factor_value

    try:
        factor_value = float(factor_value)
    except (TypeError, ValueError):
        raise HTTPException(400, "factor_value must be numeric")

    # Check if factor exists
    existing = qone(
        "SELECT id, project_id, factor_key FROM carbon_emission_factors WHERE id = %s",
        (fid,),
    )

    if existing and existing.get("project_id") and str(existing["project_id"]) == pid:
        # Update existing project-specific factor
        row = execute(
            "UPDATE carbon_emission_factors SET factor_value = %s "
            "WHERE id = %s RETURNING *",
            (factor_value, fid),
        )
        return row

    if existing and existing.get("project_id") is None:
        # It's a global default — create project-specific override
        # Copy label/unit/source from the global default
        full = qone("SELECT * FROM carbon_emission_factors WHERE id = %s", (fid,))
        row = execute(
            "INSERT INTO carbon_emission_factors "
            "(id, project_id, factor_key, factor_label, factor_value, unit, source, is_default) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, false) RETURNING *",
            (str(uuid.uuid4()), pid, full["factor_key"],
             full.get("factor_label", ""), factor_value,
             full.get("unit", ""), full.get("source", "")),
        )
        return row

    if not existing:
        raise HTTPException(404, "Emission factor not found")

    # Factor belongs to another project
    raise HTTPException(403, "Cannot modify another project's emission factor")


# ============================================================================
# 8. GET /carbon-factors — list emission factors
# ============================================================================

@router.get("/carbon-factors")
def list_carbon_factors(pid: str, user=Depends(project_user)):
    """Return emission factors: project-specific overrides + global defaults
    for any factor_key not overridden at project level.
    """
    try:
        # Get project-specific overrides
        project_factors = qall(
            "SELECT * FROM carbon_emission_factors WHERE project_id = %s",
            (pid,),
        )
        overridden_keys = {f["factor_key"] for f in project_factors}

        # Get global defaults for non-overridden keys
        global_factors = qall(
            "SELECT * FROM carbon_emission_factors WHERE project_id IS NULL",
        )
        defaults = [f for f in global_factors if f["factor_key"] not in overridden_keys]

        # Mark origin (project override vs global default)
        for f in project_factors:
            f["origin"] = "project"
        for f in defaults:
            f["origin"] = "global_default"

        return project_factors + defaults
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


# ============================================================================
# 9. POST /mass-balance-v2/export — export to Excel (client-side generation)
# ============================================================================

@router.post("/mass-balance-v2/export")
def export_mb(pid: str, user=Depends(project_user)):
    """Generate structured JSON for client-side XLSX export.

    Returns the mass balance data formatted for XLSX.js generation on the frontend.
    """
    try:
        return _export_mb_impl(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _export_mb_impl(pid: str):
    sections = qall(
        "SELECT id, section_name, op_code, sort_order "
        "FROM mass_balance_sections_v2 "
        "WHERE project_id = %s ORDER BY sort_order",
        (pid,),
    )
    if not sections:
        raise HTTPException(404, "No mass balance data to export")

    streams = qall(
        "SELECT s.* FROM mass_balance_streams_v2 s "
        "JOIN mass_balance_sections_v2 sec ON s.section_id = sec.id "
        "WHERE sec.project_id = %s "
        "ORDER BY sec.sort_order, s.sort_order",
        (pid,),
    )

    proj = qone("SELECT project_name, target_tph, gold_grade_g_t FROM projects WHERE id=%s", (pid,))

    # Structure for XLSX generation
    sheets = []
    stream_map: dict[str, list] = {}
    for st in streams:
        sid = str(st["section_id"])
        stream_map.setdefault(sid, []).append(st)

    # Column headers for the export
    columns = [
        "stream_name", "solids_tph", "solids_tpd", "solids_m3h",
        "water_tph", "water_tpd", "water_m3h",
        "slurry_tph", "slurry_tpd", "slurry_m3h", "slurry_sg",
        "pct_solids", "au_gt", "s_pct",
    ]

    for sec in sections:
        sec_streams = stream_map.get(str(sec["id"]), [])
        rows = []
        for st in sec_streams:
            row = {}
            for col in columns:
                val = st.get(col)
                if val is not None:
                    try:
                        row[col] = float(val)
                    except (TypeError, ValueError):
                        row[col] = val
                else:
                    row[col] = None
            rows.append(row)

        sheets.append({
            "sheet_name": sec["section_name"],
            "columns": columns,
            "rows": rows,
        })

    return {
        "project_name": proj["project_name"] if proj else "Unknown",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "sheets": sheets,
    }
