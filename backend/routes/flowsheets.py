"""
MPDPMS — Flowsheet routes.
Handles flowsheet listing, updating, and auto-generation from LIMS data.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import psycopg2.extras
from fastapi import APIRouter, HTTPException, Depends

try:
    from ..auth import project_user
    from ..db import qone, qall, execute
    from ..helpers import select_leach_circuit
    from ..models import FlowsheetUpdate
    from orm_models.database import get_db
    from orm_models.models import Flowsheet
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute
    from helpers import select_leach_circuit
    from models import FlowsheetUpdate
    from orm_models.database import get_db
    from orm_models.models import Flowsheet
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/v1/projects", tags=["flowsheets"])
logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _flowsheets_table_name() -> str:
    row = qone("SELECT to_regclass('public.flowsheets') AS t")
    if row and row.get("t"):
        return "flowsheets"
    row = qone("SELECT to_regclass('public.flowshheets') AS t")
    if row and row.get("t"):
        return "flowshheets"
    return "flowsheets"


def _flowsheets_has_metadata_column(table: str) -> bool:
    row = qone(
        "SELECT 1 AS ok FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s AND column_name = 'metadata' LIMIT 1",
        (table,),
    )
    return bool(row)


def _regclass_exists(qualified: str) -> bool:
    row = qone("SELECT to_regclass(%s) AS t", (qualified,))
    return bool(row and row.get("t"))


def _sim_params_index(pid: str) -> dict[tuple[str, str], float]:
    if not _regclass_exists("public.simulation_params"):
        return {}
    rows = qall(
        "SELECT category, param_key, param_value FROM simulation_params WHERE project_id = %s",
        (pid,),
    )
    idx: dict[tuple[str, str], float] = {}
    for r in rows:
        key = (str(r.get("category") or ""), str(r.get("param_key") or ""))
        v = r.get("param_value")
        if v is None:
            continue
        try:
            idx[key] = float(v)
        except (TypeError, ValueError):
            continue
    return idx


def _mass_balance_lightweight(pid: str) -> dict:
    if not _regclass_exists("public.mass_balance_sections_v2"):
        return {"sections": 0, "streams": 0, "feed_solids_tph": None, "feed_au_gt": None, "total_abs_water_m3h": None}
    sec_n = int(
        (qone("SELECT COUNT(*)::int AS n FROM mass_balance_sections_v2 WHERE project_id=%s", (pid,)) or {}).get("n")
        or 0
    )
    if sec_n == 0:
        return {"sections": 0, "streams": 0, "feed_solids_tph": None, "feed_au_gt": None, "total_abs_water_m3h": None}
    st_n = int(
        (qone("SELECT COUNT(*)::int AS n FROM mass_balance_streams_v2 WHERE project_id=%s", (pid,)) or {}).get("n") or 0
    )
    feed = qone(
        "SELECT solids_tph, au_gt FROM mass_balance_streams_v2 "
        "WHERE project_id=%s AND (LOWER(COALESCE(stream_name,'')) LIKE %s OR LOWER(COALESCE(stream_name,'')) LIKE %s) "
        "ORDER BY solids_tph DESC NULLS LAST LIMIT 1",
        (pid, "%feed%", "%rom%"),
    ) or {}
    water = qone(
        "SELECT SUM(ABS(COALESCE(water_m3h,0)))::float AS w FROM mass_balance_streams_v2 "
        "WHERE project_id=%s AND COALESCE(is_balance_check, FALSE) = FALSE",
        (pid,),
    ) or {}
    wval = water.get("w")
    return {
        "sections": sec_n,
        "streams": st_n,
        "feed_solids_tph": float(feed["solids_tph"]) if feed.get("solids_tph") is not None else None,
        "feed_au_gt": float(feed["au_gt"]) if feed.get("au_gt") is not None else None,
        "total_abs_water_m3h": float(wval) if wval is not None else None,
    }


def _equipment_v2_count(pid: str) -> int:
    if not _regclass_exists("public.equipment_v2"):
        return 0
    return int(
        (qone(
            "SELECT COUNT(*)::int AS n FROM equipment_v2 WHERE project_id=%s AND COALESCE(enabled, TRUE)",
            (pid,),
        ) or {}).get("n")
        or 0
    )


def _active_template_id(pid: str) -> str | None:
    if not _regclass_exists("public.circuit_templates"):
        return None
    row = qone(
        "SELECT id::text AS id FROM circuit_templates "
        "WHERE project_id=%s AND COALESCE(is_active, FALSE) = TRUE ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    return row["id"] if row else None


def _circuit_criteria_count_active(pid: str) -> int:
    if not _regclass_exists("public.circuit_criteria"):
        return 0
    row = qone(
        "SELECT COUNT(*)::int AS n FROM circuit_criteria cc "
        "JOIN circuit_templates ct ON cc.template_id = ct.id "
        "WHERE ct.project_id=%s AND COALESCE(ct.is_active, FALSE) = TRUE",
        (pid,),
    )
    return int((row or {}).get("n") or 0)


def _compute_flowsheet_source_signature(pid: str) -> str:
    tpl = _active_template_id(pid) or "none"
    mb = _mass_balance_lightweight(pid)
    raw = "|".join(
        [
            tpl,
            str(mb["sections"]),
            str(mb["streams"]),
            str(_equipment_v2_count(pid)),
            str(len(_sim_params_index(pid))),
            str(_circuit_criteria_count_active(pid)),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _annotate_blocks_engineering(pid: str, blocks: list[dict], sim: dict[tuple[str, str], float]) -> None:
    bm_p80 = sim.get(("comminution", "bm_p80"))
    bm_f80 = sim.get(("comminution", "bm_f80"))
    sag_p80 = sim.get(("comminution", "sag_p80"))
    sag_f80 = sim.get(("comminution", "sag_f80"))
    mb = _mass_balance_lightweight(pid)
    mb_note = ""
    if mb.get("feed_solids_tph"):
        mb_note = f"MB alim. ≈{float(mb['feed_solids_tph']):.0f} t/h sol."
    for b in blocks:
        t = str(b.get("type") or "").upper()
        bits: list[str] = []
        if "BALL_MILL" in t:
            if bm_p80 is not None:
                bits.append(f"P80≈{bm_p80:g} µm (sim.)")
            if bm_f80 is not None:
                bits.append(f"F80≈{bm_f80:g} µm (sim.)")
        elif "SAG_MILL" in t:
            if sag_p80 is not None:
                bits.append(f"P80 SAG≈{sag_p80:g} mm (sim.)")
            if sag_f80 is not None:
                bits.append(f"F80 SAG≈{sag_f80:g} mm (sim.)")
        elif "CYCLONE" in t:
            if bm_p80 is not None:
                bits.append(f"Cible overflow P80≈{bm_p80:g} µm")
        if mb_note and any(x in t for x in ("BALL_MILL", "SAG_MILL", "CYCLONE", "STOCKPILE", "ROM_BIN")):
            bits.append(mb_note)
        if bits:
            b["engineering_notes"] = bits


def _build_v2_metadata(pid: str, blocks: list[dict]) -> dict:
    sim = _sim_params_index(pid)
    sig = _compute_flowsheet_source_signature(pid)
    psd = {
        "bm_p80_um": sim.get(("comminution", "bm_p80")),
        "bm_f80_um": sim.get(("comminution", "bm_f80")),
        "sag_p80_mm": sim.get(("comminution", "sag_p80")),
        "sag_f80_mm": sim.get(("comminution", "sag_f80")),
    }
    return {
        "version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_signature": sig,
        "sources": {
            "circuit_template_id": _active_template_id(pid),
            "mass_balance": _mass_balance_lightweight(pid),
            "equipment_v2_items": _equipment_v2_count(pid),
            "simulation_param_pairs": len(sim),
            "psd_simulation": {k: v for k, v in psd.items() if v is not None},
            "block_count": len(blocks),
        },
    }


@router.get("/{pid}/flowsheets/coherence")
def flowsheet_coherence(pid: str, user=Depends(project_user)):
    if not qone("SELECT 1 AS ok FROM projects WHERE id=%s LIMIT 1", (pid,)):
        raise HTTPException(404, "Projet introuvable")
    table = _flowsheets_table_name()
    fs = qone(
        f"SELECT metadata FROM {table} WHERE project_id=%s ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    meta = fs.get("metadata") if fs else None
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except json.JSONDecodeError:
            meta = {}
    if not isinstance(meta, dict):
        meta = {}
    cur_sig = _compute_flowsheet_source_signature(pid)
    prev_sig = meta.get("source_signature")
    stale = bool(prev_sig and cur_sig != prev_sig)
    changes_summary: list[str] = []
    if stale:
        changes_summary.append(
            "Les données ingénierie (circuit DC, bilan massique, MER ou simulation) ont évolué — régénérez le flowsheet."
        )
    warnings: list[str] = []
    mb = _mass_balance_lightweight(pid)
    if mb["sections"] == 0:
        warnings.append(
            "Bilan massique v2 absent : générez-le pour aligner débits et eau sur le schéma."
        )
    if not _active_template_id(pid):
        warnings.append(
            "Aucun gabarit de circuit actif : activez un template dans Critères de conception."
        )
    if _equipment_v2_count(pid) == 0:
        warnings.append(
            "Aucun équipement MER (v2) : exécutez l'auto-génération équipement pour enrichir le registre."
        )
    return {
        "ok": True,
        "is_stale": stale,
        "changes_summary": changes_summary,
        "warnings": warnings,
        "source_signature": cur_sig,
    }


@router.post("/{pid}/flowsheets/auto-generate-v2")
def auto_generate_flowsheet_v2(pid: str, user=Depends(project_user)):
    """Topologie procédé (v1) + annotations issues du DC, bilan massique, MER, paramètres simulation (PSD)."""
    row = auto_generate_flowsheet(pid, user=user)
    blocks = list(row.get("blocks") or [])
    conns = list(row.get("connections") or [])
    table = _flowsheets_table_name()
    fs_id = row.get("id")
    if not fs_id:
        raise HTTPException(500, "Flowsheet sans identifiant après insertion")
    try:
        sim = _sim_params_index(pid)
        _annotate_blocks_engineering(pid, blocks, sim)
        meta = _build_v2_metadata(pid, blocks)
    except Exception as exc:  # noqa: BLE001 — enrichment must not drop generation
        logger.exception("flowsheet v2 enrichment failed project=%s", pid)
        meta = {
            "version": 2,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "enrichment_error": str(exc),
            "source_signature": _compute_flowsheet_source_signature(pid),
        }
    if _flowsheets_has_metadata_column(table):
        execute(
            f"UPDATE {table} SET blocks=%s::jsonb, connections=%s::jsonb, metadata=%s::jsonb "
            f"WHERE id=%s AND project_id=%s",
            (
                psycopg2.extras.Json(blocks),
                psycopg2.extras.Json(conns),
                psycopg2.extras.Json(meta),
                str(fs_id),
                pid,
            ),
        )
    else:
        execute(
            f"UPDATE {table} SET blocks=%s::jsonb, connections=%s::jsonb WHERE id=%s AND project_id=%s",
            (psycopg2.extras.Json(blocks), psycopg2.extras.Json(conns), str(fs_id), pid),
        )
    row["blocks"] = blocks
    row["connections"] = conns
    row["metadata"] = meta
    return row


@router.delete("/{pid}/flowsheets/all")
def purge_all_flowsheets(pid: str, user=Depends(project_user), db: Session = Depends(get_db)):
    """Delete all flowsheets for this project."""
    count = db.query(Flowsheet).filter(Flowsheet.project_id == pid).count()
    db.query(Flowsheet).filter(Flowsheet.project_id == pid).delete()
    db.commit()
    return {"ok": True, "deleted": count}


@router.get("/{pid}/flowsheets")
def list_flowsheets(pid: str, user=Depends(project_user), db: Session = Depends(get_db)):
    flowsheets = db.query(Flowsheet).filter(Flowsheet.project_id == pid).order_by(Flowsheet.created_at.desc()).all()
    return [
        {
            "id": str(f.id),
            "project_id": str(f.project_id),
            "blocks": f.blocks,
            "connections": f.connections,
            "created_at": f.created_at
        } for f in flowsheets
    ]


@router.put("/{pid}/flowsheets/{fs_id}")
def update_flowsheet(pid: str, fs_id: str, body: FlowsheetUpdate, user=Depends(project_user), db: Session = Depends(get_db)):
    flowsheet = db.query(Flowsheet).filter(Flowsheet.id == fs_id, Flowsheet.project_id == pid).first()
    if not flowsheet:
        raise HTTPException(404, "Flowsheet not found")

    blocks_data = [b.model_dump(by_alias=True) for b in body.blocks]
    conns_data = [c.model_dump(by_alias=True) for c in body.connections]

    try:
        flowsheet.blocks = blocks_data
        flowsheet.connections = conns_data
        db.commit()
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")

    return {"ok": True}


@router.post("/{pid}/flowsheets/auto-generate")
def auto_generate_flowsheet(pid: str, user=Depends(project_user)):
    table = _flowsheets_table_name()
    p = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not p: raise HTTPException(404)
    phase = (p.get("status") or "SCOPING").upper()

    # Pull LIMS data to decide flowsheet topology
    a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
    b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
    c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
    d1 = qall("SELECT * FROM lims_d1 WHERE project_id=%s", (pid,))
    g1 = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))

    avg_grg = float(sum(float(r.get("au_recovery_pct") or 0) for r in c2) / max(len(c2), 1)) if c2 else 0
    avg_s   = float(sum(float(r.get("s_sulfide_pct") or 0) for r in a1) / max(len(a1), 1)) if a1 else 0
    avg_c_org = float(sum(float(r.get("c_organic_pct") or 0) for r in a1) / max(len(a1), 1)) if a1 else 0
    avg_bwi = float(sum(float(r.get("mb_kwh_t") or r.get("bwi_kwh_t") or 14) for r in b1) / max(len(b1), 1)) if b1 else 14
    avg_rec = float(sum(float(r.get("au_recovery_pct") or 0) for r in d1) / max(len(d1), 1)) if d1 else 88
    avg_flot = float(sum(float(r.get("au_recovery_pct") or 0) for r in g1) / max(len(g1), 1)) if g1 else 0

    # Determine CIL vs CIP from LIMS mineralogy and leach kinetics
    leach_decision = select_leach_circuit(pid, a1_rows=a1, d1_rows=d1, project=p)
    circuit_type   = leach_decision["circuit_type"]  # "CIL" or "CIP"

    lims_empty = not (a1 or b1 or c2 or d1 or g1)
    process_options = (p.get("process_options") or "").upper()

    # Topology from LIMS data (thresholds configurable via environment variables)
    grg_threshold = _env_float("FLOWSHEET_GRG_THRESHOLD", 10.0)
    s_flot_threshold = _env_float("FLOWSHEET_S_FLOT_THRESHOLD", 2.5)
    flot_rec_threshold = _env_float("FLOWSHEET_FLOT_REC_THRESHOLD", 50.0)
    c_org_flot_max = _env_float("FLOWSHEET_CORG_FLOT_MAX", 0.3)
    hpgr_bwi_threshold = _env_float("FLOWSHEET_HPGR_BWI_THRESHOLD", 16.0)
    pox_corg_threshold = _env_float("FLOWSHEET_POX_CORG_THRESHOLD", 0.3)
    pox_s_threshold = _env_float("FLOWSHEET_POX_S_THRESHOLD", 5.0)
    pox_rec_threshold = _env_float("FLOWSHEET_POX_REC_THRESHOLD", 80.0)
    include_sag_default = _env_bool("FLOWSHEET_INCLUDE_SAG_DEFAULT", True)
    include_isamill_default = _env_bool("FLOWSHEET_INCLUDE_ISAMILL_DEFAULT", False)

    has_gravity   = avg_grg >= grg_threshold
    has_flotation = (avg_s > s_flot_threshold or avg_flot > flot_rec_threshold) and avg_c_org < c_org_flot_max
    has_hpgr      = avg_bwi > hpgr_bwi_threshold
    _has_pox       = avg_c_org > pox_corg_threshold or (avg_s > pox_s_threshold and avg_rec < pox_rec_threshold)
    _has_sag       = include_sag_default
    has_isamill   = include_isamill_default
    has_heap_leach = False
    has_vat_leach = False

    # Fallback to process_options when LIMS is empty
    if lims_empty and process_options:
        has_flotation = "FLOTATION" in process_options
        has_gravity   = "GRAVITY" in process_options
        has_hpgr      = "HPGR" in process_options
        _has_sag       = ("SAG" in process_options) or include_sag_default
        has_isamill   = "ISAMILL" in process_options
        _has_pox       = "POX" in process_options
        has_heap_leach = "HEAP_LEACH" in process_options
        has_vat_leach = "VAT_LEACH" in process_options

    # ── Circuit template override (highest priority) ───────────────────────
    # The active circuit template is the single source of truth for topology,
    # shared with the Mass Balance engine. Override LIMS-derived flags so the
    # flowsheet is always consistent with DC / MB.
    _GRAVITY_OPS = {"GRAVITE_KNELSON", "GRAVITE_FALCON", "GRAVITY", "GRAVITY_CONC", "KNELSON_FALCON"}
    tpl_row = qone(
        "SELECT id FROM circuit_templates "
        "WHERE project_id=%s AND is_active=TRUE "
        "ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if tpl_row:
        ops_rows = qall(
            "SELECT op_code FROM circuit_operations "
            "WHERE template_id=%s AND enabled=TRUE",
            (str(tpl_row["id"]),),
        )
        template_ops = {r["op_code"] for r in ops_rows}
        has_hpgr      = "HPGR" in template_ops
        has_flotation = "FLOTATION_ROUGHER" in template_ops
        has_isamill   = bool(template_ops & {"ISAMILL", "VERTIMILL_REGRIND", "SMD"})
        _has_sag       = "SAG_MILL" in template_ops
        has_gravity   = bool(template_ops & _GRAVITY_OPS)
        _has_pox       = bool(template_ops & {"POX", "BIOX"})
        has_heap_leach = "HEAP_LEACH" in template_ops
        has_vat_leach = "VAT_LEACH" in template_ops
        if "CIP" in template_ops:
            circuit_type = "CIP"
        elif "CIL" in template_ops:
            circuit_type = "CIL"

    detail_level = 1
    if phase in ["PFS", "FS"]: detail_level = 2
    elif phase in ["ENGINEERING", "COMMISSIONING"]: detail_level = 3

    blocks = []
    conns  = []

    def ab(t, lbl, x, y):
        bid = str(uuid.uuid4())
        blocks.append({"id": bid, "type": t, "label": lbl, "x": x, "y": y})
        return bid

    def link(a, b):
        conns.append({"from": a, "to": b})

    # ── SECTION 1: RÉCEPTION & MANUTENTION (y=50) ─────────────────────────────
    b_rom   = ab("rom_bin",   "Trémie ROM",          50, 60)
    b_conv1 = ab("belt_conveyor",  "Convoyeur Alimentation", 190, 60)
    link(b_rom, b_conv1)

    # ── SECTION 2: CONCASSAGE (y=60) ──────────────────────────────────────────
    b_cru1  = ab("jaw_crusher",   "Concasseur Primaire",  330, 60)
    b_scr1  = ab("vibrating_screen",    "Crible Vibrant #1",    480, 60)
    b_cru2  = ab("cone_crusher",   "Concasseur Secondaire", 480, 190)
    b_sp    = ab("stockpile", "Stockpile Concassé",   630, 60)
    b_conv2 = ab("belt_conveyor",  "Convoyeur SAG",        780, 60)
    link(b_conv1, b_cru1); link(b_cru1, b_scr1)
    link(b_scr1, b_cru2);  link(b_cru2, b_scr1)
    link(b_scr1, b_sp);    link(b_sp,   b_conv2)

    # ── SECTION 3: BROYAGE (y=280) ────────────────────────────────────────────
    if has_hpgr and detail_level >= 2:
        b_hpgr = ab("hpgr",    f"HPGR (BWi={avg_bwi:.0f} kWh/t)", 930, 60)
        link(b_conv2, b_hpgr)
        prev_grind = b_hpgr
    else:
        prev_grind = b_conv2

    b_cyc = ab("hydrocyclone", "Batterie Hydrocyclones", 1530, 290)
    if _has_sag:
        b_sag   = ab("sag_mill",  "Broyeur SAG",          930, 290)
        b_peb_cr= ab("roll_crusher",   "Concasseur Galets SAG", 930, 440) if detail_level >= 2 else None
        b_sump1 = ab("water_tank",      "Puisard Décharge SAG", 1080, 290)
        b_pump1 = ab("slurry_pump",      "Pompe SAG Décharge",   1080, 440)
        b_ball  = ab("ball_mill", "Broyeur à Boulets",    1230, 290)
        b_sump2 = ab("water_tank",      "Puisard Décharge BB",  1380, 290)
        b_pump2 = ab("slurry_pump",      "Pompe BB Décharge",    1380, 440)

        link(prev_grind, b_sag); link(b_sag, b_sump1); link(b_sump1, b_pump1)
        link(b_pump1, b_cyc);    link(b_cyc, b_ball)
        link(b_ball, b_sump2);   link(b_sump2, b_pump2); link(b_pump2, b_cyc)
        if b_peb_cr:
            link(b_sag, b_peb_cr); link(b_peb_cr, b_sump1)
    else:
        # Some project templates intentionally omit SAG (e.g., HPGR + BM route)
        b_ball  = ab("ball_mill", "Broyeur à Boulets",    1180, 290)
        b_sump2 = ab("water_tank",      "Puisard Décharge BB",  1330, 290)
        b_pump2 = ab("slurry_pump",      "Pompe BB Décharge",    1330, 440)
        link(prev_grind, b_ball)
        link(b_ball, b_sump2)
        link(b_sump2, b_pump2)
        link(b_pump2, b_cyc)

    # Classification overflow → next section
    prev_cyc_of = b_cyc

    # ── SECTION 4: GRAVIMÉTRIE (y=60-200) ─────────────────────────────────────
    if has_gravity:
        b_knels = ab("knelson_concentrator",  f"Concentrateur Knelson (GRG≈{avg_grg:.0f}%)", 1680, 100)
        b_icr   = ab("icr_reactor",      "Réacteur Lixiv. Intense (ICR)", 1830, 100)
        b_pump_g= ab("centrifugal_pump",     "Pompe Concentré Grav.",  1980, 100)
        b_ew_g  = ab("ew_cell","EW Gravité",            2130, 100)
        link(b_cyc, b_knels); link(b_knels, b_icr)
        link(b_icr, b_pump_g); link(b_pump_g, b_ew_g)
        link(b_knels, b_cyc)   # tails recirculate

    # ── SECTION 5: FLOTTATION (y=490) ─────────────────────────────────────────
    prev_to_cil = prev_cyc_of
    if has_flotation:
        b_cond  = ab("leach_tank",  "Conditionneur Réactifs", 1680, 490)
        b_fl_r  = ab("flotation_cell", "Flottation Ébauchage (Rougher)", 1830, 490)
        b_fl_sc = ab("flotation_cell", "Flottation Scavenger",  1980, 490)
        b_fl_cl = ab("flotation_cell", "Flottation Nettoyage (Cleaner)", 1830, 640)
        b_col   = ab("column_cell",    "Colonne Flotation (Recleaner)", 1980, 640)
        b_thk_fl= ab("thickener", "Épaississeur Concentré Flot.", 2130, 490)
        link(prev_cyc_of, b_cond); link(b_cond, b_fl_r)
        link(b_fl_r, b_fl_sc);    link(b_fl_r, b_fl_cl)
        link(b_fl_sc, b_fl_cl);   link(b_fl_cl, b_col)
        link(b_col, b_thk_fl)
        link(b_fl_sc, prev_cyc_of)  # scavenger tails → cyclones
        prev_to_cil = b_thk_fl

    # ── SECTION 5b: ISAMILL (rebroyage ultrafin) ──────────────────────────────
    if has_isamill:
        b_isa = ab("isamill", "IsaMill (rebroyage ultrafin)", 2280, 490)
        link(prev_to_cil, b_isa)
        prev_to_cil = b_isa

    # ── SECTION 6: PRÉ-LIXIVIATION & RÉACTIFS (y=800) ─────────────────────────
    b_floc  = ab("leach_tank",  "Cuve Floculant",         330, 790)
    b_lime  = ab("leach_tank",  "Lait de Chaux (CaO)",    480, 790)
    b_nacn  = ab("leach_tank",  "Solution NaCN",           630, 790)
    b_thk_l = ab("thickener", "Épaississeur Pré-Lixiv.", 930, 820)
    b_pump3 = ab("slurry_pump",      f"Pompe Alimentation {circuit_type}",  1080, 820)
    b_aer   = ab("leach_tank","Cuve Pré-Aération",       1230, 820)

    # Refractory pre-treatment from active design template (POX/BIOX)
    if _has_pox:
        b_pox = ab("leach_tank", "Autoclave POX / Prétraitement", 2430, 490)
        b_pox_neut = ab("neutralization", "Neutralisation post-POX", 2580, 490)
        b_pox_thk = ab("thickener", "Épaississeur post-POX", 2730, 490)
        link(prev_to_cil, b_pox)
        link(b_pox, b_pox_neut)
        link(b_pox_neut, b_pox_thk)
        prev_to_cil = b_pox_thk

    link(prev_to_cil, b_thk_l)
    link(b_floc, b_thk_l); link(b_lime, b_thk_l)
    link(b_thk_l, b_pump3); link(b_pump3, b_aer)
    link(b_nacn, b_aer)

    # ── SECTION 7: CIRCUIT DE LIXIVIATION (CIL ou CIP) ────────────────────────
    if has_heap_leach:
        b_heap = ab("heap_leach_pad", "Aire de lixiviation en tas", 330, 970)
        b_preg_heap = ab("pregnant_tank", "Bassin solution enceinte", 520, 970)
        b_scr_cil = ab("carbon_screen", "Crible récupération carbone", 700, 970)
        link(b_aer, b_heap)
        link(b_heap, b_preg_heap)
        link(b_preg_heap, b_scr_cil)
    elif has_vat_leach:
        b_vat1 = ab("vat_leach", "Cuve lixiviation VAT #1", 330, 970)
        b_vat2 = ab("vat_leach", "Cuve lixiviation VAT #2", 500, 970)
        b_scr_cil = ab("carbon_screen", "Crible lavage carbone", 690, 970)
        link(b_aer, b_vat1)
        link(b_vat1, b_vat2)
        link(b_vat2, b_scr_cil)
    elif circuit_type == "CIP":
        # CIP: cuves de lixiviation dédiées (sans charbon) suivi de cuves d'adsorption
        n_leach = _env_int("FLOWSHEET_CIP_LEACH_TANKS_DETAILED", 5) if detail_level >= 2 else _env_int("FLOWSHEET_CIP_LEACH_TANKS_BASIC", 4)
        n_ads   = _env_int("FLOWSHEET_CIP_ADS_TANKS_DETAILED", 4) if detail_level >= 2 else _env_int("FLOWSHEET_CIP_ADS_TANKS_BASIC", 3)
        spacing = _env_int("FLOWSHEET_CIP_SPACING", 130)
        leach_ids = []
        for i in range(n_leach):
            bx = 330 + i * spacing
            lid = ab("leach_tank", f"Lixiviation {i+1} (CIP)", bx, 970)
            leach_ids.append(lid)
        link(b_aer, leach_ids[0])
        for i in range(len(leach_ids) - 1):
            link(leach_ids[i], leach_ids[i + 1])

        # Adsorption CIP tanks (with carbon, separate from leach)
        ads_ids = []
        for i in range(n_ads):
            bx = 330 + (n_leach + i) * spacing
            aid = ab("cip_tank", f"Cuve CIP {i+1} (Adsorption)", bx, 970)
            ads_ids.append(aid)
        link(leach_ids[-1], ads_ids[0])
        for i in range(len(ads_ids) - 1):
            link(ads_ids[i], ads_ids[i + 1])

        b_scr_cil = ab("carbon_screen", "Crible Lavage Charbon", 330 + (n_leach + n_ads) * spacing, 970)
        link(ads_ids[-1], b_scr_cil)
        link(b_scr_cil, ads_ids[0])   # charbon counter-courant dans adsorption seulement

    else:
        # CIL: lixiviation et adsorption simultanées dans chaque cuve
        n_cil = _env_int("FLOWSHEET_CIL_TANKS_DETAILED", 6) if detail_level >= 2 else _env_int("FLOWSHEET_CIL_TANKS_BASIC", 4)
        cil_spacing = _env_int("FLOWSHEET_CIL_SPACING", 160)
        cil_ids = []
        for i in range(n_cil):
            bx = 330 + i * cil_spacing
            cid = ab("cil_tank", f"Cuve CIL {i+1}", bx, 970)
            cil_ids.append(cid)
        link(b_aer, cil_ids[0])
        for i in range(len(cil_ids) - 1):
            link(cil_ids[i], cil_ids[i + 1])

        b_scr_cil = ab("carbon_screen", "Crible Lavage Charbon", 330 + n_cil * cil_spacing, 970)
        link(cil_ids[-1], b_scr_cil)
        link(b_scr_cil, cil_ids[0])   # charbon counter-courant

    # ── SECTION 8: ADR CIRCUIT (y=1150) ───────────────────────────────────────
    b_str_tnk = ab("pregnant_tank","Cuve Solution d'Élution",  330, 1150)
    b_heater  = ab("water_tank",  "Préchauffeur Solution",     480, 1150)
    b_elut    = ab("elution_column", "Colonne d'Élution (Strip)", 630, 1150)
    b_preg    = ab("pregnant_tank","Solution Dorée (Pregnant)", 780, 1150)
    b_ew1     = ab("ew_cell","Cellule EW #1",              930, 1150)
    b_ew2     = ab("ew_cell","Cellule EW #2",             1080, 1150)
    b_smelt   = ab("induction_furnace",     "Four à Induction",          1230, 1150)
    b_dore    = ab("dore_casting",  "Coulée Lingots Doré",       1380, 1150)

    link(b_scr_cil, b_str_tnk)
    link(b_str_tnk, b_heater); link(b_heater, b_elut)
    link(b_elut, b_preg);      link(b_preg, b_ew1)
    link(b_ew1, b_ew2);        link(b_ew2, b_smelt)
    link(b_smelt, b_dore)
    link(b_elut, b_str_tnk)   # spent solution recycle

    # ── SECTION 9: TRAITEMENT RÉSIDUS & EAU (y=1150 right) ───────────────────
    b_cn1   = ab("cn_destruct","Destruction Cyanure (INCO/SO₂)", 1530, 970)
    b_cn2   = ab("neutralization","Neutralisation pH / Lime",        1680, 970)
    b_thk_t = ab("thickener", "Épaississeur Résidus",             1830, 970)
    b_filt  = ab("pressure_filter",    "Filtre-Presse Résidus",            1980, 970)
    b_tsf   = ab("tailings_dam",  "Parc à Résidus (TSF)",             2130, 970)
    b_wtr   = ab("water_treatment","Bassin Eau de Procédé (Recyclée)", 1980, 1150)
    b_lab   = ab("sampler",       "Laboratoire / Contrôle Qualité",   2130, 820)

    link(b_scr_cil, b_cn1);  link(b_cn1, b_cn2)
    link(b_cn2, b_thk_t);    link(b_thk_t, b_filt)
    link(b_filt, b_tsf);     link(b_thk_t, b_wtr)
    link(b_wtr, b_thk_l)     # reclaim water back to process
    link(b_lab, b_cn1)       # lab monitors effluent

    # Optional global transform for generated layout
    x_scale = _env_float("FLOWSHEET_AUTOGEN_X_SCALE", 1.0)
    y_scale = _env_float("FLOWSHEET_AUTOGEN_Y_SCALE", 1.0)
    x_offset = _env_int("FLOWSHEET_AUTOGEN_X_OFFSET", 0)
    y_offset = _env_int("FLOWSHEET_AUTOGEN_Y_OFFSET", 0)
    if x_scale != 1.0 or y_scale != 1.0 or x_offset != 0 or y_offset != 0:
        for b in blocks:
            b["x"] = int(float(b.get("x", 0)) * x_scale) + x_offset
            b["y"] = int(float(b.get("y", 0)) * y_scale) + y_offset

    # Save (replace existing)
    execute(f"DELETE FROM {table} WHERE project_id=%s", (pid,))
    row = execute(
        f"INSERT INTO {table} (project_id, blocks, connections) VALUES (%s,%s::jsonb,%s::jsonb) RETURNING *",
        (pid, psycopg2.extras.Json(blocks), psycopg2.extras.Json(conns))
    )
    row["blocks"]      = blocks
    row["connections"] = conns
    return row
