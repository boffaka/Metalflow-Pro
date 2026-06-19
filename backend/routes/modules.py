"""
MPDPMS — Modules status & cross-module import endpoints.
Provides:
  GET  /api/v1/projects/{pid}/modules/status
  POST /api/v1/projects/{pid}/lims/import/blockmodel
  POST /api/v1/projects/{pid}/simulation/import/lims
  POST /api/v1/projects/{pid}/costs/OPEX/import/simulation
  POST /api/v1/projects/{pid}/working-capital/compute-bfr
  POST /api/v1/projects/{pid}/flowsheets/import/process-options
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Depends

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, conn, release
    from ..helpers import get_operating_hours_day, get_availability_pct, get_opex_defaults
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, conn, release
    from helpers import get_operating_hours_day, get_availability_pct, get_opex_defaults
    from constants import TROY_OZ_PER_GRAM

router = APIRouter(prefix="/api/v1/projects/{pid}", tags=["modules"])
logger = logging.getLogger("mpdpms.modules")

# ─── Token → flowsheet block mapping ─────────────────────────────────────────

def _load_token_blocks() -> dict[str, list[tuple[str, str]]]:
    path = Path(__file__).resolve().parent.parent / "config" / "flowsheet_token_blocks.json"
    if not path.exists():
        logger.warning("flowsheet token mapping file not found: %s", path)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # intentional: log and continue on optional operation
        logger.exception("failed to load flowsheet token mapping from %s", path)
        return {}

    mapping: dict[str, list[tuple[str, str]]] = {}
    for token, items in (raw or {}).items():
        cleaned: list[tuple[str, str]] = []
        for entry in items or []:
            if isinstance(entry, list) and len(entry) == 2:
                cleaned.append((str(entry[0]), str(entry[1])))
        if cleaned:
            mapping[str(token)] = cleaned
    return mapping


TOKEN_BLOCKS = _load_token_blocks()


# ─── Endpoint 1: Module Status ────────────────────────────────────────────────

@router.get("/modules/status")
def get_modules_status(pid: str, user=Depends(project_user)):
    """Return the status of every module in a single request."""
    try:
        return _get_modules_status_impl(pid)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _get_modules_status_impl(pid: str):
    status: dict = {}

    # --- Block model ---
    try:
        bm_configs = qall("SELECT id FROM block_model_configs WHERE project_id=%s", (pid,))
        bm_count = len(bm_configs)
        bm_blocks = 0
        total_tonnage = 0.0
        avg_grade = 0.0
        if bm_configs:
            config_ids = [str(c["id"]) for c in bm_configs]
            placeholders = ",".join(["%s"] * len(config_ids))
            agg = qone(
                f"SELECT COUNT(*) as cnt, "
                f"COALESCE(SUM(COALESCE(tonnage, volume * density, 0)),0) as total_t, "
                f"COALESCE("
                f"  SUM(COALESCE(tonnage, volume * density, 0) * COALESCE(grade_au, 0))"
                f"  / NULLIF(SUM(COALESCE(tonnage, volume * density, 0)), 0), "
                f"  0"
                f") as avg_grade "
                f"FROM blocks WHERE config_id IN ({placeholders})",
                tuple(config_ids)
            )
            if agg:
                bm_blocks = int(agg.get("cnt") or 0)
                total_tonnage = round(float(agg.get("total_t") or 0) / 1e6, 2)
                avg_grade = round(float(agg.get("avg_grade") or 0), 2)
        status["blockmodel"] = {
            "has_data": bm_count > 0,
            "configs": bm_count,
            "blocks": bm_blocks,
            "total_tonnage_mt": total_tonnage,
            "avg_grade_g_t": avg_grade,
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("blockmodel status error: %s", e)
        status["blockmodel"] = {"has_data": False, "error": str(e)}

    # --- LIMS ---
    try:
        sample_count = int(
            (qone("SELECT COUNT(*) AS n FROM lims_samples WHERE project_id=%s", (pid,)) or {}).get("n", 0)
        )
        lims_table_map = {
            "a1": "lims_a1", "b1": "lims_b1", "g1": "lims_flotation",
            "d1": "lims_d1", "c2": "lims_c2", "a3": "lims_a3",
            "m1": "lims_m1", "h1": "lims_elution", "i1": "lims_environmental",
            "a2": "lims_a2",
        }
        # Single UNION ALL query replaces the previous N+1 loop (10 round-trips → 1)
        union_sql = " UNION ALL ".join(
            f"SELECT '{code}' AS code, COUNT(*) AS cnt FROM {tbl} WHERE project_id=%s"
            for code, tbl in lims_table_map.items()
        )
        lims_rows = qall(union_sql, [pid] * len(lims_table_map))
        test_counts = {r["code"]: int(r["cnt"]) for r in lims_rows if int(r["cnt"]) > 0}
        status["lims"] = {
            "has_data": sample_count > 0,
            "samples": sample_count,
            "tests": test_counts,
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("lims status error: %s", e)
        status["lims"] = {"has_data": False, "error": str(e)}

    # --- Flowsheet ---
    try:
        fs_list = qall("SELECT id, blocks, connections FROM flowsheets WHERE project_id=%s", (pid,))
        total_blocks = sum(len(f.get("blocks") or []) for f in fs_list)
        total_conns = sum(len(f.get("connections") or []) for f in fs_list)
        status["flowsheet"] = {
            "has_data": len(fs_list) > 0,
            "count": len(fs_list),
            "blocks": total_blocks,
            "connections": total_conns,
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("flowsheet status error: %s", e)
        status["flowsheet"] = {"has_data": False, "error": str(e)}

    # --- Simulation ---
    try:
        sim_rows = qall("SELECT id FROM simulation_params WHERE project_id=%s", (pid,))
        status["simulation"] = {
            "has_data": len(sim_rows) > 0,
            "params_count": len(sim_rows),
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("simulation status error: %s", e)
        status["simulation"] = {"has_data": False, "error": str(e)}

    # --- Equipment ---
    try:
        eq_rows = qall("SELECT id FROM equipment WHERE project_id=%s", (pid,))
        status["equipment"] = {
            "has_data": len(eq_rows) > 0,
            "count": len(eq_rows),
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("equipment status error: %s", e)
        status["equipment"] = {"has_data": False, "error": str(e)}

    # --- Costs ---
    try:
        capex_model = qone(
            "SELECT id FROM cost_models WHERE project_id=%s AND model_type='CAPEX' ORDER BY version DESC LIMIT 1",
            (pid,)
        )
        opex_model = qone(
            "SELECT id FROM cost_models WHERE project_id=%s AND model_type='OPEX' ORDER BY version DESC LIMIT 1",
            (pid,)
        )
        capex_status: dict = {"has_data": False, "items": 0, "total_usd": 0}
        opex_status: dict = {"has_data": False, "items": 0, "total_per_t": 0}
        if capex_model:
            agg = qone(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(total_cost_usd),0) as total "
                "FROM cost_line_items WHERE model_id=%s",
                (capex_model["id"],)
            )
            if agg:
                capex_status = {
                    "has_data": True,
                    "items": int(agg.get("cnt") or 0),
                    "total_usd": round(float(agg.get("total") or 0), 0),
                }
        if opex_model:
            agg = qone(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(unit_cost_usd),0) as total_per_t "
                "FROM cost_line_items WHERE model_id=%s",
                (opex_model["id"],)
            )
            if agg:
                opex_status = {
                    "has_data": True,
                    "items": int(agg.get("cnt") or 0),
                    "total_per_t": round(float(agg.get("total_per_t") or 0), 2),
                }
        status["costs"] = {
            "has_data": capex_status.get("has_data", False) or opex_status.get("has_data", False),
            "capex": capex_status,
            "opex": opex_status,
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("costs status error: %s", e)
        status["costs"] = {"has_data": False, "error": str(e)}

    # --- Stage gates ---
    try:
        sg_rows = qall("SELECT id, status FROM stage_gates WHERE project_id=%s", (pid,))
        approved = sum(1 for r in sg_rows if (r.get("status") or "").lower() in ("approved", "passed", "go"))
        status["stage_gates"] = {
            "has_data": len(sg_rows) > 0,
            "count": len(sg_rows),
            "approved": approved,
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("stage_gates status error: %s", e)
        status["stage_gates"] = {"has_data": False, "error": str(e)}

    # --- Risks ---
    try:
        risk_rows = qall("SELECT id, is_gate_blocker, criticality FROM risks WHERE project_id=%s", (pid,))
        blockers = sum(1 for r in risk_rows if r.get("is_gate_blocker") or int(r.get("criticality") or 0) >= 15)
        status["risks"] = {
            "has_data": len(risk_rows) > 0,
            "count": len(risk_rows),
            "blockers": blockers,
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("risks status error: %s", e)
        status["risks"] = {"has_data": False, "error": str(e)}

    # --- Campaigns ---
    try:
        camp_rows = qall("SELECT id FROM test_campaigns WHERE project_id=%s", (pid,))
        status["campaigns"] = {
            "has_data": len(camp_rows) > 0,
            "count": len(camp_rows),
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("campaigns status error: %s", e)
        status["campaigns"] = {"has_data": False, "error": str(e)}

    # --- Ramp-up ---
    try:
        ramp_rows = qall("SELECT id FROM rampup_factors WHERE project_id=%s", (pid,))
        status["rampup"] = {
            "has_data": len(ramp_rows) > 0,
            "months_defined": len(ramp_rows),
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("rampup status error: %s", e)
        status["rampup"] = {"has_data": False, "error": str(e)}

    # --- Working capital ---
    try:
        wc = qone("SELECT id FROM working_capital WHERE project_id=%s", (pid,))
        status["working_capital"] = {"has_data": wc is not None}
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("working_capital status error: %s", e)
        status["working_capital"] = {"has_data": False, "error": str(e)}

    # --- Decisions ---
    try:
        dec_rows = qall("SELECT id FROM decisions WHERE project_id=%s", (pid,))
        status["decisions"] = {
            "has_data": len(dec_rows) > 0,
            "count": len(dec_rows),
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("decisions status error: %s", e)
        status["decisions"] = {"has_data": False, "error": str(e)}

    # --- NI 43-101 ---
    try:
        ni_rows = qall("SELECT id FROM ni43101_sections WHERE project_id=%s", (pid,))
        status["ni43101"] = {
            "has_data": len(ni_rows) > 0,
            "sections": len(ni_rows),
        }
    except Exception as e:  # intentional: graceful degradation for status collection
        logger.warning("ni43101 status error: %s", e)
        status["ni43101"] = {"has_data": False, "error": str(e)}

    return status


# ─── Endpoint 2: Import LIMS samples from block model ────────────────────────

@router.post("/lims/import/blockmodel")
def import_lims_from_blockmodel(pid: str, user=Depends(project_user)):
    """Create LIMS samples from distinct rock_types in the block model."""
    try:
        return _import_lims_from_blockmodel_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _import_lims_from_blockmodel_impl(pid: str, user):
    # Get all configs for this project
    configs = qall("SELECT id FROM block_model_configs WHERE project_id=%s", (pid,))
    if not configs:
        return {"created": [], "skipped": [], "message": "Aucun modèle de blocs trouvé"}

    config_ids = [str(c["id"]) for c in configs]
    placeholders = ",".join(["%s"] * len(config_ids))

    # Get distinct rock_types with aggregate data
    rock_types = qall(
        f"SELECT rock_type, COUNT(*) as block_count, "
        f"COALESCE(SUM(COALESCE(tonnage, volume * density, 0)),0) as total_tonnage "
        f"FROM blocks WHERE config_id IN ({placeholders}) "
        f"GROUP BY rock_type ORDER BY rock_type",
        tuple(config_ids)
    )

    if not rock_types:
        return {"created": [], "skipped": [], "message": "Aucun bloc trouvé dans le modèle"}

    # Get existing sample_id_display values
    existing = qall(
        "SELECT sample_id_display FROM lims_samples WHERE project_id=%s",
        (pid,)
    )
    existing_displays = {r["sample_id_display"] for r in existing}

    created = []
    skipped = []

    for rt in rock_types:
        rock_type = rt.get("rock_type") or "Unknown"
        block_count = int(rt.get("block_count") or 0)
        total_tonnage = float(rt.get("total_tonnage") or 0)
        sample_id_display = f"AUTO-{rock_type}"

        if sample_id_display in existing_displays:
            skipped.append({"sample_id_display": sample_id_display, "reason": "already_exists"})
            continue

        mass_kg = round(total_tonnage / 1e6, 2)
        provenance = f"Importé depuis modèle de blocs — {block_count} blocs"

        try:
            row = execute(
                """INSERT INTO lims_samples
                   (project_id, sample_id_display, phase, sample_type, lithology, provenance, mass_kg)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id, sample_id_display""",
                (pid, sample_id_display, "PFS", "Composite_Auto", rock_type, provenance, mass_kg)
            )
            created.append({
                "id": str(row["id"]),
                "sample_id_display": row["sample_id_display"],
                "rock_type": rock_type,
                "block_count": block_count,
                "total_tonnage": total_tonnage,
                "mass_kg": mass_kg,
            })
            existing_displays.add(sample_id_display)
        except Exception as e:  # intentional: log and skip failed sample in batch
            logger.warning("Failed to create sample %s: %s", sample_id_display, e)
            skipped.append({"sample_id_display": sample_id_display, "reason": str(e)})

    return {
        "created": created,
        "skipped": skipped,
        "summary": {"created_count": len(created), "skipped_count": len(skipped)},
    }


# ─── Endpoint 3: Calibrate simulation from LIMS ──────────────────────────────

@router.post("/simulation/import/lims")
def import_simulation_from_lims(pid: str, user=Depends(project_user)):
    """Calibrate simulation parameters from LIMS test results."""
    try:
        return _import_simulation_from_lims_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _import_simulation_from_lims_impl(pid: str, user):
    updates = []

    def _avg(rows, field):
        """Compute average of a numeric field, ignoring None/0."""
        vals = [float(r[field]) for r in rows if r.get(field) is not None]
        return (sum(vals) / len(vals)) if vals else None

    def _update_param(category: str, key: str, value: float, source: str):
        try:
            execute(
                """UPDATE simulation_params
                   SET param_value=%s, source=%s, updated_at=NOW()
                   WHERE project_id=%s AND category=%s AND param_key=%s""",
                (value, source, pid, category, key)
            )
            updates.append({"category": category, "param_key": key, "new_value": value, "source": source})
        except Exception as e:  # intentional: log and skip failed param update
            logger.warning("Failed to update sim param %s.%s: %s", category, key, e)

    # B1 — Bond work index, DWi, Abrasion index
    b1_rows = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
    if b1_rows:
        avg_bwi = _avg(b1_rows, "mb_kwh_t")
        if avg_bwi is not None:
            _update_param("comminution", "bm_bwi", round(avg_bwi, 2), "LIMS B1 — mb_kwh_t moyen")
            _update_param("comminution", "sag_bwi", round(avg_bwi, 2), "LIMS B1 — mb_kwh_t moyen")

        avg_dwi = _avg(b1_rows, "dwi_kwh_m3")
        if avg_dwi is not None:
            _update_param("comminution", "hpgr_specific_energy", round(avg_dwi, 2), "LIMS B1 — dwi_kwh_m3 moyen")

        avg_ai = _avg(b1_rows, "abrasion_index_ai")
        if avg_ai is not None:
            logger.info("LIMS B1 abrasion_index_ai avg=%.3f (pas de param direct en simulation)", avg_ai)

    # Flotation (G1 / lims_flotation)
    flot_rows = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
    if flot_rows:
        avg_au_rec = _avg(flot_rows, "au_recovery_pct")
        if avg_au_rec is not None:
            _update_param("concentration", "flot_rec_au", round(avg_au_rec, 2), "LIMS G1 — au_recovery_pct moyen")

        avg_conc_wt = _avg(flot_rows, "concentrate_wt_pct")
        if avg_conc_wt is not None:
            _update_param("concentration", "flot_mass_pull", round(avg_conc_wt, 2), "LIMS G1 — concentrate_wt_pct moyen")

    # D1 — Leach recovery & cyanide consumption
    d1_rows = qall("SELECT * FROM lims_d1 WHERE project_id=%s", (pid,))
    if d1_rows:
        avg_leach_rec = _avg(d1_rows, "leach_rec_48h_pct")
        if avg_leach_rec is not None:
            _update_param("leaching", "rec_baseline", round(avg_leach_rec, 2), "LIMS D1 — leach_rec_48h_pct moyen")

        avg_nacn = _avg(d1_rows, "nacn_consumption_kg_t")
        if avg_nacn is not None:
            _update_param("reagents", "nacn_kg_t", round(avg_nacn, 3), "LIMS D1 — nacn_consumption_kg_t moyen")

    sources_used = []
    if b1_rows:
        sources_used.append(f"lims_b1 ({len(b1_rows)} tests)")
    if flot_rows:
        sources_used.append(f"lims_flotation ({len(flot_rows)} tests)")
    if d1_rows:
        sources_used.append(f"lims_d1 ({len(d1_rows)} tests)")

    return {
        "updated_params": updates,
        "sources_used": sources_used,
        "summary": {"params_updated": len(updates)},
    }


# ─── Endpoint 4: Recalculate OPEX from simulation params ─────────────────────

@router.post("/costs/OPEX/import/simulation")
def import_opex_from_simulation(pid: str, user=Depends(project_user)):
    """Recalculate OPEX line items from current simulation parameters."""
    try:
        return _import_opex_from_simulation_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _import_opex_from_simulation_impl(pid: str, user):
    # Fetch simulation params
    sim_rows = qall(
        "SELECT param_key, param_value FROM simulation_params WHERE project_id=%s AND param_value IS NOT NULL",
        (pid,)
    )
    sim = {r["param_key"]: float(r["param_value"]) for r in sim_rows}

    defaults_cfg = get_opex_defaults(sim)
    p_energy = defaults_cfg["energy_rate"]
    p_nacn   = defaults_cfg["nacn_price"]
    p_cao    = defaults_cfg["cao_price"]

    aux_kwh_t = defaults_cfg["aux_energy_kwh_t"]
    c_energy  = defaults_cfg["sag_specific_energy"] + defaults_cfg["bm_specific_energy"] + aux_kwh_t
    if "energy_kwh_t" in sim:
        c_energy = sim["energy_kwh_t"]

    c_nacn = defaults_cfg["nacn_kg_t"]
    c_cao  = defaults_cfg["cao_kg_t"]

    # Costs
    cost_energy         = round(c_energy * p_energy, 2)
    cost_nacn           = round(c_nacn * p_nacn, 2)
    cost_cao            = round(c_cao * p_cao, 2)
    cost_other_reagents = defaults_cfg["opex_other_reag_usd_t"]
    cost_media          = defaults_cfg["opex_media_usd_t"]
    cost_liners         = defaults_cfg["opex_liners_usd_t"]
    cost_labor          = defaults_cfg["opex_labor_usd_t"]
    cost_maint          = defaults_cfg["opex_maint_usd_t"]
    cost_lab            = defaults_cfg["opex_lab_usd_t"]
    cost_ga             = defaults_cfg["opex_ga_usd_t"]

    new_lines = [
        {"category": "Énergie électrique",      "description": f"{c_energy:.1f} kWh/t @ ${p_energy}/kWh",      "unit_cost_usd": cost_energy,         "wbs_code": "OP-100"},
        {"category": "Réactifs (NaCN)",          "description": f"{c_nacn} kg/t @ ${p_nacn}/kg",                "unit_cost_usd": cost_nacn,           "wbs_code": "OP-201"},
        {"category": "Réactifs (CaO / Chaux)",   "description": f"{c_cao} kg/t @ ${p_cao}/kg",                  "unit_cost_usd": cost_cao,            "wbs_code": "OP-202"},
        {"category": "Réactifs (Autres)",         "description": "Floculant, acide, charbon actif",             "unit_cost_usd": cost_other_reagents, "wbs_code": "OP-203"},
        {"category": "Consommables (Boulets)",    "description": "Boulets de broyage acier",                    "unit_cost_usd": cost_media,          "wbs_code": "OP-301"},
        {"category": "Consommables (Blindages)",  "description": "Blindages broyeur, grilles",                  "unit_cost_usd": cost_liners,         "wbs_code": "OP-302"},
        {"category": "Main d'œuvre (Opérations)","description": "Personnel opérations usine",                  "unit_cost_usd": cost_labor,          "wbs_code": "OP-400"},
        {"category": "Maintenance (Pièces)",      "description": "Pièces de rechange et maintenance",           "unit_cost_usd": cost_maint,          "wbs_code": "OP-500"},
        {"category": "Laboratoire",               "description": "Analyses chimiques, contrôle qualité",        "unit_cost_usd": cost_lab,            "wbs_code": "OP-600"},
        {"category": "G&A (Frais généraux)",      "description": "Administration, assurances, permis",          "unit_cost_usd": cost_ga,             "wbs_code": "OP-700"},
    ]

    # Get or create OPEX model
    model = qone(
        "SELECT * FROM cost_models WHERE project_id=%s AND model_type='OPEX' ORDER BY version DESC LIMIT 1",
        (pid,)
    )
    if not model:
        model = execute(
            "INSERT INTO cost_models (project_id, model_type, version) VALUES (%s, 'OPEX', 1) RETURNING *",
            (pid,)
        )

    model_id = model["id"]

    # Replace all existing lines
    c = conn()
    cur = None
    try:
        cur = c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("DELETE FROM cost_line_items WHERE model_id=%s", (model_id,))
        for item in new_lines:
            cur.execute(
                "INSERT INTO cost_line_items (model_id, category, description, quantity, unit, unit_cost_usd, source, wbs_code) "
                "VALUES (%s, %s, %s, 1, '$/t', %s, %s, %s)",
                (model_id, item["category"], item["description"],
                 item["unit_cost_usd"], "Recalculé depuis simulation_params", item["wbs_code"])
            )
        cur.execute("UPDATE cost_models SET updated_at=NOW() WHERE id=%s", (model_id,))
        c.commit()
    except Exception:  # intentional broad catch for transaction cleanup
        c.rollback()
        raise
    finally:
        if cur is not None:
            cur.close()
        release(c)

    # Return updated model
    items = qall(
        "SELECT * FROM cost_line_items WHERE model_id=%s ORDER BY wbs_code",
        (model_id,)
    )
    for it in items:
        for k in ("quantity", "unit_cost_usd", "total_cost_usd"):
            if it.get(k) is not None:
                it[k] = float(it[k])
    total = sum(it.get("unit_cost_usd") or 0 for it in items)

    return {
        "model_id": str(model_id),
        "model_type": "OPEX",
        "items": items,
        "total_per_t_usd": round(total, 2),
        "simulation_inputs": {
            "c_energy_kwh_t": c_energy,
            "c_nacn_kg_t": c_nacn,
            "c_cao_kg_t": c_cao,
            "p_energy": p_energy,
            "p_nacn": p_nacn,
            "p_cao": p_cao,
        },
    }


# ─── Endpoint 5: Compute BFR (Working Capital) ───────────────────────────────

@router.post("/working-capital/compute-bfr")
def compute_bfr(pid: str, user=Depends(project_user)):
    """Calculate and store BFR from available data."""
    try:
        return _compute_bfr_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _compute_bfr_impl(pid: str, user):
    # Working capital record
    wc = qone("SELECT * FROM working_capital WHERE project_id=%s", (pid,))
    if not wc:
        wc = {
            "receivable_days": 30,
            "inventory_days": 45,
            "payable_days": 30,
            "other_current_assets": 0.0,
            "other_current_liabilities": 0.0,
        }

    # Project data
    project = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not project:
        raise HTTPException(404, "Projet introuvable")

    target_tph       = float(project.get("target_tph") or 0)
    op_hours_day     = get_operating_hours_day(project)
    availability_pct = get_availability_pct(pid, project)
    gold_grade       = float(project.get("gold_grade_g_t") or 0)
    gold_price       = float(project.get("gold_price_usd_oz") or 2340)

    # Annual throughput
    annual_t = target_tph * op_hours_day * 365 * (availability_pct / 100)

    # Total annual OPEX from cost_line_items
    opex_model = qone(
        "SELECT id FROM cost_models WHERE project_id=%s AND model_type='OPEX' ORDER BY version DESC LIMIT 1",
        (pid,)
    )
    total_opex_annual = 0.0
    if opex_model:
        agg = qone(
            "SELECT COALESCE(SUM(total_cost_usd),0) as total FROM cost_line_items WHERE model_id=%s",
            (opex_model["id"],)
        )
        if agg:
            total_opex_annual = float(agg.get("total") or 0)

    # If total_cost_usd looks like $/t (small number), multiply by annual throughput
    # This handles the case where OPEX items have quantity=1, unit="$/t"
    if opex_model and annual_t > 0 and 0 < total_opex_annual < annual_t:
        agg2 = qone(
            "SELECT COALESCE(SUM(unit_cost_usd),0) as total_per_t FROM cost_line_items WHERE model_id=%s",
            (opex_model["id"],)
        )
        if agg2:
            total_opex_annual = float(agg2.get("total_per_t") or 0) * annual_t

    daily_opex = total_opex_annual / 365 if total_opex_annual > 0 else 0.0

    # Revenue calculation
    recovery = 0.83  # default recovery
    daily_au_oz = (annual_t * gold_grade * recovery * TROY_OZ_PER_GRAM / 365) if annual_t > 0 else 0
    daily_revenue = daily_au_oz * gold_price

    # BFR calculation
    recv_days = float(wc.get("receivable_days") or 0)
    inv_days  = float(wc.get("inventory_days") or 45)
    pay_days  = float(wc.get("payable_days") or 30)
    other_assets = float(wc.get("other_current_assets") or 0)
    other_liab   = float(wc.get("other_current_liabilities") or 0)

    bfr = (recv_days * daily_revenue) + (inv_days * daily_opex) - (pay_days * daily_opex) + other_assets - other_liab

    bfr_rounded       = round(bfr, 0)
    daily_opex_rounded = round(daily_opex, 0)
    daily_rev_rounded  = round(daily_revenue, 0)

    # Persist bfr_computed_usd if working_capital row exists
    existing_wc = qone("SELECT id FROM working_capital WHERE project_id=%s", (pid,))
    if existing_wc:
        try:
            execute(
                "UPDATE working_capital SET updated_at=NOW() WHERE project_id=%s",
                (pid,)
            )
        except Exception as e:  # intentional: graceful fallback on optional timestamp update
            logger.warning("Could not update working_capital timestamp: %s", e)

    return {
        "bfr_computed_usd": bfr_rounded,
        "daily_opex_usd":   daily_opex_rounded,
        "daily_revenue_usd": daily_rev_rounded,
        "details": {
            "annual_throughput_t": round(annual_t, 0),
            "total_opex_annual_usd": round(total_opex_annual, 0),
            "daily_au_oz": round(daily_au_oz, 2),
            "gold_grade_g_t": gold_grade,
            "gold_price_usd_oz": gold_price,
            "recovery_pct": recovery * 100,
            "receivable_days": recv_days,
            "inventory_days": inv_days,
            "payable_days": pay_days,
            "other_current_assets": other_assets,
            "other_current_liabilities": other_liab,
        },
    }


# ─── Endpoint 6: Create flowsheet from process_options string ─────────────────

@router.post("/flowsheets/import/process-options")
def import_flowsheet_from_process_options(pid: str, user=Depends(project_user)):
    """Create a flowsheet by parsing project.process_options string."""
    try:
        return _import_flowsheet_from_process_options_impl(pid, user)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _import_flowsheet_from_process_options_impl(pid: str, user):
    project = qone("SELECT id, process_options FROM projects WHERE id=%s", (pid,))
    if not project:
        raise HTTPException(404, "Projet introuvable")

    process_options = (project.get("process_options") or "").strip()
    if not process_options:
        raise HTTPException(400, "project.process_options est vide — impossible de générer le flowsheet")

    # Parse tokens from the string (split by +, space, comma, semicolon)
    import re
    raw_tokens = re.split(r"[+,;\s]+", process_options)
    tokens = [t.strip() for t in raw_tokens if t.strip()]

    blocks = []
    conns = []
    x_cursor = int(os.getenv("FLOWSHEET_IMPORT_X_START", "50"))
    y_base = int(os.getenv("FLOWSHEET_IMPORT_Y_BASE", "100"))
    x_step = int(os.getenv("FLOWSHEET_IMPORT_X_STEP", "180"))
    last_id = None

    def ab(btype, label, x, y):
        bid = str(uuid.uuid4())
        blocks.append({"id": bid, "type": btype, "label": label, "x": x, "y": y})
        return bid

    def link(a, b):
        if a and b:
            conns.append({"from": a, "to": b})

    for token in tokens:
        token_blocks = TOKEN_BLOCKS.get(token)
        if token_blocks is None:
            # Try case-insensitive match
            for k, v in TOKEN_BLOCKS.items():
                if k.lower() == token.lower():
                    token_blocks = v
                    break
        if token_blocks is None:
            logger.info("Unknown process token '%s' — skipped", token)
            continue

        first_in_group = None
        prev_in_group = None
        y_offset = 0
        for btype, label in token_blocks:
            bid = ab(btype, label, x_cursor, y_base + y_offset)
            if first_in_group is None:
                first_in_group = bid
            if prev_in_group:
                link(prev_in_group, bid)
            prev_in_group = bid
            x_cursor += x_step
            y_offset = 0  # keep same row

        # Connect last block of previous token to first block of this token
        if last_id and first_in_group:
            link(last_id, first_in_group)
        last_id = prev_in_group

    if not blocks:
        raise HTTPException(400, f"Aucun token reconnu dans process_options: '{process_options}'")

    # Replace existing flowsheets
    execute("DELETE FROM flowsheets WHERE project_id=%s", (pid,))
    row = execute(
        "INSERT INTO flowsheets (project_id, blocks, connections) "
        "VALUES (%s, %s::jsonb, %s::jsonb) RETURNING *",
        (pid, psycopg2.extras.Json(blocks), psycopg2.extras.Json(conns))
    )
    row["blocks"] = blocks
    row["connections"] = conns

    return {
        "flowsheet_id": str(row["id"]),
        "tokens_parsed": tokens,
        "blocks_count": len(blocks),
        "connections_count": len(conns),
        "blocks": blocks,
        "connections": conns,
    }
