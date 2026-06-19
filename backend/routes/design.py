"""
MPDPMS — Design Criteria routes.
Handles design criteria listing, auto-generation, and row patching.
"""
from __future__ import annotations

import logging
import psycopg2
import math
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Depends, Query, Response

try:
    from ..auth import project_user
    from ..db import qone, qall, execute, build_update_sets, paginated_qall
    from ..helpers import get_ore_sg, compute_annual_t, get_recovery_pct, select_leach_circuit, get_circuit_flags
    from ..audit import record_event
    from .. import config as _app_config
    from ..settings import get_settings as _get_settings
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall, execute, build_update_sets, paginated_qall
    from helpers import get_ore_sg, compute_annual_t, get_recovery_pct, select_leach_circuit, get_circuit_flags
    from audit import record_event
    import config as _app_config
    from settings import get_settings as _get_settings

_SETTINGS = _get_settings()

router = APIRouter(prefix="/api/v1/projects", tags=["design"])
logger = logging.getLogger("mpdpms.design")


@router.get("/{pid}/design-criteria")
def list_dc(pid: str, limit: int = Query(500, ge=1, le=2000), offset: int = Query(0, ge=0), user=Depends(project_user)):
    try:
        rows = paginated_qall("SELECT * FROM design_criteria WHERE project_id=%s ORDER BY sort_order", (pid,), limit=limit, offset=offset)
        sections: Dict[str, list] = {}
        for r in rows:
            s = r.get("section", "General")
            sections.setdefault(s, []).append(r)
        return [{"name": k, "rows": v} for k, v in sections.items()]
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


@router.get("/{pid}/design-criteria/leach-circuit")
def get_leach_circuit_recommendation(pid: str, user=Depends(project_user)):
    """Return the CIL/CIP selection and its metallurgical justification for this project."""
    try:
        p = qone("SELECT * FROM projects WHERE id=%s", (pid,))
        if not p: raise HTTPException(404, "Projet introuvable")
        a1 = qall("SELECT c_organic_pct, s_sulfide_pct, s_total_pct, as_ppm, sb_ppm, cu_pct FROM lims_a1 WHERE project_id=%s", (pid,))
        d1 = qall("SELECT au_recovery_pct, nacn_consumption_kg_t FROM lims_d1 WHERE project_id=%s", (pid,))
        return select_leach_circuit(pid, a1_rows=a1, d1_rows=d1, project=p)
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _signal_pipeline(pid: str, module: str, status: str, user_id: str = None) -> None:
    """Signal pipeline status change — never blocks the route on failure."""
    try:
        from .pipeline import set_status, mark_stale_cascade
    except ImportError:
        from pipeline import set_status, mark_stale_cascade
    try:
        set_status(pid, module, status, user_id=user_id, triggered_by="auto_generate")
        if status == "complete":
            mark_stale_cascade(pid, module, user_id=user_id)
    except Exception:  # intentional: ignore optional lookup failure
        pass


@router.post("/{pid}/design-criteria/auto-generate")
def auto_generate_dc(pid: str, user=Depends(project_user)):
    p = qone("SELECT * FROM projects WHERE id=%s", (pid,))
    if not p: raise HTTPException(404, "Projet introuvable")
    _signal_pipeline(pid, "design_criteria", "generating", user_id=str(user["id"]))
    try:
        result = _do_generate_dc(pid, p, user)
        _signal_pipeline(pid, "design_criteria", "complete", user_id=str(user["id"]))
        return result
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")


def _do_generate_dc(pid: str, p: dict, user: dict):

    b1 = qall("SELECT * FROM lims_b1 WHERE project_id=%s", (pid,))
    d1 = qall("SELECT * FROM lims_d1 WHERE project_id=%s", (pid,))
    c2 = qall("SELECT * FROM lims_c2 WHERE project_id=%s", (pid,))
    e1 = qall("SELECT * FROM lims_e1 WHERE project_id=%s", (pid,))
    a1 = qall("SELECT * FROM lims_a1 WHERE project_id=%s", (pid,))
    try:
        g1 = qall("SELECT * FROM lims_flotation WHERE project_id=%s", (pid,))
    except Exception:  # intentional: fallback to empty/default on optional data
        g1 = []
    try:
        h1 = qall("SELECT * FROM lims_elution WHERE project_id=%s", (pid,))
    except Exception:  # intentional: fallback to empty/default on optional data
        h1 = []

    tph = p.get("target_tph")
    if not tph:
        logger.warning("project %s: target_tph not set, using default 100", pid)
        tph = 100
    tph = float(tph)

    grade = p.get("gold_grade_g_t")
    if not grade:
        logger.warning("project %s: gold_grade_g_t not set, using default 1.0", pid)
        grade = 1.0
    grade = float(grade)

    op_h = p.get("operating_hours_day")
    if not op_h:
        logger.warning("project %s: operating_hours_day not set, using default 24.0 (continuous operation)", pid)
        op_h = 24.0
    op_h = float(op_h)

    avail_pct = p.get("availability_pct")
    if not avail_pct:
        logger.warning("project %s: availability_pct not set, using default 92.0", pid)
        avail_pct = 92.0
    avail_pct = float(avail_pct)
    def _avg(rows, field, default=None):
        vals = [float(r[field]) for r in rows if r.get(field) not in (None, '', 0)]
        if vals:
            return float(sum(vals) / len(vals))
        return default

    avg_bwi  = _avg(b1, "bwi_kwh_t", 14.0)
    avg_rec  = _avg(d1, "au_recovery_pct", get_recovery_pct(pid, d1))
    avg_grg  = _avg(c2, "au_recovery_pct", 0.0)
    _avg(g1, "au_recovery_pct", None)

    # Circuit topology flags (single source of truth shared with flowsheet & mass balance)
    flags           = get_circuit_flags(pid, a1_rows=a1, b1_rows=b1, c2_rows=c2, g1_rows=g1)
    has_gravity     = flags["has_gravity"]
    has_flotation   = flags["has_flotation"]
    has_isamill     = flags["has_isamill"]
    avg_grg         = flags["avg_grg"]
    avg_flot_flags  = flags["avg_flot"]

    leach_decision     = select_leach_circuit(pid, a1_rows=a1, d1_rows=d1, project=p)
    circuit_type       = leach_decision["circuit_type"]   # "CIL" or "CIP"
    circuit_confidence = leach_decision["confidence"]
    circuit_score      = leach_decision["score"]
    avg_nacn = _avg(d1, "nacn_consumption_kg_t", _SETTINGS.default_nacn_consumption_kg_t)
    # Fallback aligned with industry_defaults.yaml (1.2 kg/t) via settings.
    # Previously hardcoded 0.8 — below the reference value for typical CIL circuits.
    avg_cao  = _avg(d1, "cao_consumption_kg_t", _SETTINGS.default_cao_consumption_kg_t)
    avg_ua   = _avg(e1, "unit_area_m2_t_d", 0.08)
    avg_ud   = _avg(e1, "underflow_density_pct_solids", 55.0)
    avg_floc = _avg(e1, "flocculant_dosage_g_t", 25.0)
    avg_p80  = _avg(b1, "p80_target_um", 75.0)
    avg_ai   = _avg(b1, "abrasion_index_ai", 0.3)

    # --- Connexion Modèle IA / Simulation (must be before cascade calcs) ---
    def _sp(row):
        try: return float(row["param_value"])
        except (TypeError, ValueError): return None
    sim_params = {r["param_key"]: v for r in qall("SELECT param_key, param_value FROM simulation_params WHERE project_id=%s", (pid,)) if (v := _sp(r)) is not None}

    # ── F80/P80 cascade (metallurgically correct chain) ──────────────────
    # ROM → Primary Crusher → Secondary Crusher → SAG → Ball Mill → Cyclone
    rom_f80_mm = float(sim_params.get("rom_f80_mm", 400.0))  # ROM F80 typically 300-600mm
    pc_css_mm = float(sim_params.get("pc_css_mm", 140.0))
    pc_p80_mm = round(pc_css_mm * 0.96, 0)  # P80 ≈ 0.96 × CSS for gyratory
    sc_css_mm = float(sim_params.get("sc_css_mm", 35.0))
    sc_p80_mm = round(sc_css_mm * 1.0, 0)  # P80 ≈ CSS for cone crusher
    # SAG F80 = secondary crusher product (or HPGR product if present)
    sag_f80_mm = sc_p80_mm
    # LIMS B1 f80_um is the lab test feed size — useful for Ball Mill reference
    lims_f80_um = _avg(b1, "f80_um", sag_f80_mm * 1000)  # µm
    lims_f80_um / 1000.0 if lims_f80_um else sag_f80_mm
    # SAG P80: calculated from Bond equation or default ~2mm
    sag_p80_mm = float(sim_params.get("sag_p80_mm", 2.0))
    bm_p80_um = avg_p80  # from LIMS B1 p80_target_um
    # Cyclone O/F P80 ≈ Ball Mill P80
    cyc_of_p80_um = bm_p80_um

    # ── Flotation-adjusted feed rates ─────────────────────────────────────
    # If flotation is present, leach/CIP/detox process concentrate, not full feed
    flot_mass_pull_pct = float(sim_params.get("flot_mass_pull_total", 17.0))  # % of feed
    leach_feed_tph = round(tph * flot_mass_pull_pct / 100, 1) if has_flotation else tph

    # ── Ball Mill power (Bond equation) ────────────────────────────────────
    safe_p80_um = max(bm_p80_um, 1.0)
    safe_f80_um = max(sag_p80_mm * 1000, safe_p80_um + 1.0)
    bm_energy_kwh_t = 10.0 * avg_bwi * (1.0/math.sqrt(safe_p80_um) - 1.0/math.sqrt(safe_f80_um))
    bm_power_kw = round(bm_energy_kwh_t * tph / 0.85, 0)  # 85% mechanical efficiency

    # ── Cyclone feed density (calculated) ──────────────────────────────────
    bm_circ_load_pct = float(sim_params.get("bm_circ_load", 250.0))
    tph * (1 + bm_circ_load_pct / 100)
    cyc_feed_pct_sol = 65.0  # typical cyclone feed density

    srt = sim_params.get("cil_srt", 24.0)
    pct_solids_cil = max(sim_params.get("cil_pct_solids", 45.0), 1.0) / 100.0  # Industry: 45% default (was 40%)
    sg_solids = get_ore_sg(pid, sim_params)  # reads sim_params['ore_sg'] → 2.75 default

    # Cuves CIL — dimensionnement (Marsden & House, SME Handbook)
    # SG_slurry = 1 / [(w/SG_s) + ((1-w)/SG_w)]  where w = pct_solids fraction, SG_w = 1.0
    sg_slurry = 1.0 / ((pct_solids_cil / sg_solids) + (1.0 - pct_solids_cil) / 1.0)
    # Volumetric slurry flow = solids_tph / (SG_slurry × pct_solids)
    # Use leach_feed_tph (concentrate if flotation, else full feed)
    vol_flow_m3h = leach_feed_tph / max(sg_slurry * pct_solids_cil, 0.01)
    vol_requis = vol_flow_m3h * srt

    max_vol_per_tank = float(sim_params.get("cil_max_vol_per_tank", 4000.0))
    min_tanks = int(sim_params.get("cil_min_tanks", 6))
    n_tanks = max(min_tanks, math.ceil(vol_requis / max_vol_per_tank))
    n_tanks = int(sim_params.get("cil_tanks", n_tanks))  # Surcharge possible par simulaire

    n_trains = 1
    if n_tanks > 10:
        n_trains = math.ceil(n_tanks / 6)
        n_tanks = n_trains * math.ceil(n_tanks / n_trains)

    round(vol_requis / max(n_tanks, 1), 0)

    # Énergie spécifique globale — Bond (W = Wi × 10 × (1/√P80 - 1/√F80))
    # Note: This uses LIMS B1 lab F80 (not SAG cascade F80). Used for DC reporting only.
    # The primary BM power calc (line 182-183) uses SAG P80→BM F80 cascade (physically correct).
    avg_f80 = _avg(b1, "f80_um", sim_params.get("sag_f80", 100.0) * 1000.0)  # sag_f80 in mm → µm
    safe_p80 = max(avg_p80, 1.0)
    safe_f80 = max(avg_f80, safe_p80 + 1.0)  # F80 must be coarser than P80
    round(10 * avg_bwi * (1/math.sqrt(safe_p80) - 1/math.sqrt(safe_f80)), 2)

    # Carbone actif — proportionnel au débit (tenant compte du facteur de sécu 1.15)
    carbon_makeup_rate = float(sim_params.get("carbon_makeup_kg_t", 0.04))  # kg/t (industrie 0.02-0.06)
    round((tph * 24.0) * carbon_makeup_rate / 1000, 2) # Nominal daily carbon makeup

    # === Élution (ADR) & Cinétique d'Adsorption ===
    # 1. Variables globales
    _avg(h1, "elution_efficiency_pct", 92.0)  # Industry: 90-95%, not 98% (Marsden & House)
    avg_el_temp   = _avg(h1, "temperature_c",          140.0)  # AARL typical: 140-160°C (was 135)
    avg_el_strip_h= _avg(h1, "cycle_time_h",            12.0)
    l_au_in       = _avg(h1, "carbon_loading_g_t",     float(_app_config.CARBON_LOADING_ELUTION_AVG_FALLBACK_G_T))
    l_au_out = float(sim_params.get("carbon_residual_loading_g_t", 60.0))  # Consistent with 92% eff: 1900×0.08=152→~60 g/t

    # 2. Bilan métal et Flux de charbon chargé (m_C_daily en t/j)
    # Use project.availability_pct (authoritative) — same source as all other modules
    daily_t = tph * 24.0 * avail_pct / 100.0  # Tonnes réelles / jour
    mod_daily_au_g = daily_t * grade * (avg_rec / 100.0)
    m_c_daily = mod_daily_au_g / (l_au_in - l_au_out) if (l_au_in - l_au_out) > 0 else 0

    # 3. Choix technologique et Temps de cycle global
    is_aarl = avg_el_temp >= 140  # AARL: ≥140°C (atmospheric); Zadra: 95-130°C (pressurized)
    t_aw = float(sim_params.get("elut_acid_wash_h", 2.0))
    t_rinse = float(sim_params.get("elut_rinse_h", 1.0))
    t_preheat = float(sim_params.get("elut_preheat_h", 1.5 if is_aarl else 1.0))
    t_strip = avg_el_strip_h
    t_drain = float(sim_params.get("elut_drain_h", 1.5))
    t_transf = float(sim_params.get("elut_transfer_h", 2.0))
    t_cycle = t_aw + t_rinse + t_preheat + t_strip + t_drain + t_transf

    # 4. Dimensionnement de la capacité d'élution
    n_columns = 1
    possible_batches = [2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0]
    batch_size_t = 2.0

    for b in possible_batches:
        n_cycles_per_day = 24.0 / t_cycle
        if b * n_cycles_per_day >= m_c_daily:
            batch_size_t = b
            break

    # Si redondance requise
    if (15.0 * (24.0/t_cycle)) < m_c_daily:
        n_columns = 2
        for b in possible_batches:
            if (b * (24.0/t_cycle) * n_columns) >= m_c_daily:
                batch_size_t = b
                break

    # Fréquence exacte: nombre de batches réels à effectuer par semaine
    math.ceil((m_c_daily * 7.0) / batch_size_t) if batch_size_t > 0 else 0

    # 5. Hydraulique & Thermique
    bv_m3 = batch_size_t * 2.0  # 1 Bed Volume d'apparence du carbone
    q_el = 2.0 * bv_m3  # Standard: 2 BV/h
    q_el * t_strip
    (q_el * 1000 / 3600) * 4.18 * (avg_el_temp - 25.0)

    # Épaississeur — dimensionnement réaliste
    MAX_THICKENER_DIAM = float(sim_params.get("thickener_max_diam_m", 45.0))
    design_safety_factor = float(sim_params.get("thickener_design_factor", 1.15))
    thk_area_total = (tph * 24.0) * avg_ua * design_safety_factor  # Area = tpd × UA (m²/tpd) × SF
    thk_area_max_unit = math.pi * (MAX_THICKENER_DIAM / 2) ** 2  # aire max par unité
    thk_count = max(1, math.ceil(thk_area_total / thk_area_max_unit))
    thk_area_per_unit = thk_area_total / thk_count
    thk_diam = round(math.sqrt(thk_area_per_unit / math.pi) * 2, 1)

    # Water balance (consistent with massbalance.py auto-generation)
    # All water flows in t/h (≈ m³/h since water density ≈ 1 t/m³)
    _cil_pct_s = float(sim_params.get("cil_pct_solids", 45.0))
    _cil_pct_frac = _cil_pct_s / 100.0
    _process_water_m3h = leach_feed_tph * (1.0 - _cil_pct_frac) / max(_cil_pct_frac, 0.01)
    _avg_ud = avg_ud if avg_ud else 55.0
    _ud_frac = _avg_ud / 100.0
    _tailings_loss_m3h = tph * (1.0 - _ud_frac) / max(_ud_frac, 0.01)
    _evap_loss_m3h = _process_water_m3h * 0.015
    _fresh_water_m3h = _tailings_loss_m3h + _evap_loss_m3h

    # ══════════════════════════════════════════════════════════════════
    #  TEMPLATE: PROCESS DESIGN CRITERIA (4706-500-100-DGC)
    #  Mirrors professional Excel template structure
    #  Each row: (section, item, unit, design, nominal, min_val, max_val, source, comments, is_header)
    # ══════════════════════════════════════════════════════════════════
    compute_annual_t(tph, op_h, avail_pct)
    tpcd = round(tph * op_h, 0)
    conc_design_factor_pct = float(sim_params.get("conc_design_factor_pct", 15))
    plant_design_tph = round(tph * (1 + conc_design_factor_pct / 100.0), 3)
    plant_design_tpcd = round(tpcd * (1 + conc_design_factor_pct / 100.0), 0)
    crushing_avail_pct = float(sim_params.get("crushing_availability_pct", 70.0))
    h_crush = round(365 * crushing_avail_pct / 100 * 24, 0)
    h_plant = round(365 * avail_pct / 100 * 24, 0)
    avg_au = _avg(a1, "au_g_t", grade)
    avg_ag = _avg(a1, "ag_g_t", 0.5)
    avg_as = _avg(a1, "as_ppm", 100) / 10000  # ppm → g/t
    avg_s  = _avg(a1, "s_total_pct", 1.0)
    avg_c_org = _avg(a1, "c_organic_pct", 0.3)

    # Detect which equipment is in the circuit
    circuit_ops = set()
    try:
        _tpl_rows = qall(
            "SELECT op_code FROM circuit_operations "
            "WHERE template_id IN (SELECT id FROM circuit_templates WHERE project_id=%s) AND enabled=true",
            (pid,),
        )
        circuit_ops = {r["op_code"].upper() for r in _tpl_rows if r.get("op_code")}
    except Exception:  # intentional: ignore optional lookup failure
        pass
    has_hpgr = "HPGR" in circuit_ops

    H = True   # sub-section header
    N = False  # data row
    # ══════════════════════════════════════════════════════════════════════
    #  PROCESS DESIGN CRITERIA — NI 43-101 / SME Handbook / Marsden & House
    #  Format: (section, item, unit, design, nominal, min, max, source, comments, is_header)
    #  NOTE: The section name is NOT repeated as the first header row —
    #        the UI already renders the section name as a group title.
    # ══════════════════════════════════════════════════════════════════════
    rows_def = [
        # ── General Plant Design Criteria ──────────────────────────────────
        ("General Plant Design Criteria", "Design basis", "", None, None, None, None, "", "", H),
        ("General Plant Design Criteria", "Design ore processing rate", "tpcd", plant_design_tpcd, tpcd, None, None, "Calc", f"Nominal × (1 + {conc_design_factor_pct:.0f}%)", N),
        ("General Plant Design Criteria", "Design ore processing rate - Plant", "tph", plant_design_tph, tph, round(tph*0.85), round(tph*1.15), "Calc", f"Nominal × (1 + {conc_design_factor_pct:.0f}%)", N),
        ("General Plant Design Criteria", "Operating hours per year - Crushing Circuit", "h/y", h_crush, h_crush, None, None, "Calc", f"{crushing_avail_pct}% availability", N),
        *([("General Plant Design Criteria", "Operating hours per year - HPGR", "h/y", h_plant, h_plant, None, None, "Calc", "", N)] if has_hpgr else []),
        ("General Plant Design Criteria", "Operating hours per year - Plant", "h/y", h_plant, h_plant, None, None, "Calc", f"{avail_pct}% availability", N),
        ("General Plant Design Criteria", "Operating hours per day - Crushing Circuit", "h/d", round(crushing_avail_pct/100*24, 1), round(crushing_avail_pct/100*24, 1), None, None, "Calc", "", N),
        *([("General Plant Design Criteria", "Operating hours per day - HPGR", "h/d", op_h, op_h, None, None, "Project", "", N)] if has_hpgr else []),
        ("General Plant Design Criteria", "Operating hours per day - Plant", "h/d", op_h, op_h, None, None, "Project", "", N),
        ("General Plant Design Criteria", "Operating Percentage - Crushing Circuit", "%", crushing_avail_pct, crushing_avail_pct, None, None, "Design", "", N),
        *([("General Plant Design Criteria", "Operating Percentage - HPGR", "%", avail_pct, avail_pct, None, None, "Design", "", N)] if has_hpgr else []),
        ("General Plant Design Criteria", "Operating Percentage - Plant", "%", avail_pct, avail_pct, None, None, "Project", "", N),
        ("General Plant Design Criteria", "Design Factors", "", None, None, None, None, "", "", H),
        ("General Plant Design Criteria", "Crushing plant equipment design factor", "%",
         float(sim_params.get("crush_design_factor_pct", 25)), float(sim_params.get("crush_design_factor_pct", 25)),
         None, None, "Design", "", N),
        ("General Plant Design Criteria", "Concentrator plant equipment design factor", "%",
         conc_design_factor_pct, conc_design_factor_pct,
         None, None, "Design", "", N),

        # ── General Project Information ──────────────────────────────────
        ("General Project Information", "Location", "", None, None, None, None, "", "", H),
        ("General Project Information", "Project location", "", None, None, None, None, p.get("location",""), p.get("location",""), N),
        ("General Project Information", "Site Elevation", "m MSL", None, None, None, None, "", "", N),
        ("General Project Information", "Location, latitude", "deg", None, None, None, None, "", "", N),
        ("General Project Information", "Location, longitude", "deg", None, None, None, None, "", "", N),
        ("General Project Information", "Climate", "", None, None, None, None, "", "", H),
        ("General Project Information", "Minimum design temperature", "oC", None, None, None, None, "", "", N),
        ("General Project Information", "Maximum design temperature", "oC", None, None, None, None, "", "", N),
        ("General Project Information", "Estimated process water temperature", "oC", None, None, None, None, "", "", N),

        # ── Ore Characteristics ───────────────────────────────────────────
        ("Ore Characteristics", "Mill feed head grades", "", None, None, None, None, "", "", H),
        ("Ore Characteristics", "Gold", "g/t", round(avg_au,2), round(avg_au,2), round(avg_au*0.7,2), round(avg_au*1.3,2), "LIMS A1", f"LOM avg: {grade} g/t", N),
        ("Ore Characteristics", "Silver", "g/t", round(avg_ag,2), round(avg_ag,2), None, None, "LIMS A1", "", N),
        ("Ore Characteristics", "Arsenic", "g/t", round(avg_as*10000,0), None, None, None, "LIMS A1", "ppm", N),
        ("Ore Characteristics", "Sulphur", "%", round(avg_s,2), round(avg_s,2), None, None, "LIMS A1", "", N),
        ("Ore Characteristics", "Carbon", "%", None, None, None, None, "LIMS A1", "", N),
        ("Ore Characteristics", "Organic carbon", "%", round(avg_c_org,2), round(avg_c_org,2), None, None, "LIMS A1", "", N),
        ("Ore Characteristics", "Mill feed characteristics", "", None, None, None, None, "", "", H),
        ("Ore Characteristics", "Domain 1", "%", None, None, None, None, "", "Define geomet domains", N),
        ("Ore Characteristics", "Domain 2", "%", None, None, None, None, "", "", N),
        ("Ore Characteristics", "Domain 3", "%", None, None, None, None, "", "", N),
        ("Ore Characteristics", "Domain 4", "%", None, None, None, None, "", "", N),
        ("Ore Characteristics", "Average density", "t/m3", round(sg_solids,2), round(sg_solids,2), None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "Average bulk density for volume calculations (dry)", "t/m3", None, None, None, None, "", "", N),
        ("Ore Characteristics", "Average bulk density for weight calculations (dry)", "t/m3", None, None, None, None, "", "", N),
        ("Ore Characteristics", "Humidity", "%", None, None, None, None, "", "", N),
        ("Ore Characteristics", "Angle of Repose", "deg", None, None, None, None, "", "", N),

        # ── Mill Feed Grindability and Crushability ──
        ("Ore Characteristics", "Mill Feed Grindability and Crushability", "", None, None, None, None, "", "", H),
        ("Ore Characteristics", "Crushing work index (CWi)", "kWh/t", None, None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "JK Drop Weight : Axb", "", _avg(b1,"a_x_b",None), None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "JK Drop Weight : ta", "", None, None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "Drop Weight Index (DWi)", "kWh/m3", _avg(b1,"dwi_kwh_m3",None), None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "Rod Mill Index (RWi)", "kWh/t", None, None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "Mia", "kWh/t", _avg(b1,"mia_kwh_t",None), None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "Mih", "kWh/t", _avg(b1,"mih_kwh_t",None), None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "Mic", "kWh/t", _avg(b1,"mic_kwh_t",None), None, None, None, "LIMS B1", "", N),
        ("Ore Characteristics", "Bond ball mill work index (BWi)", "kWh/t", round(avg_bwi,1), round(avg_bwi,1), round(avg_bwi*0.8,1), round(avg_bwi*1.2,1), "LIMS B1", "Range from testwork", N),
        ("Ore Characteristics", "Abrasion Index (Ai)", "g", round(avg_ai,2), round(avg_ai,2), None, None, "LIMS B1", "", N),

        # ── Crushing ──────────────────────────────────────────────────────
        ("Crushing", "Crushing Area", "", None, None, None, None, "", "", H),
        ("Crushing", "Crushing Area Availability", "%", crushing_avail_pct, crushing_avail_pct, None, None, "Design", "", N),
        ("Crushing", "Average hrs operating per day", "h/d", round(crushing_avail_pct/100*24, 1), None, None, None, "Calc", "", N),
        ("Crushing", "Processing rate", "t/h", round(tph / (crushing_avail_pct/100) * op_h / 24, 0) if op_h else tph, None, None, None, "Calc", "Crushing rate > plant rate", N),
        ("Crushing", "Rock Breaker", "", None, None, None, None, "", "", N),
        ("Crushing", "Stationary Grizzly Aperture", "mm", None, None, None, None, "", "", N),
        ("Crushing", "Dump Pocket (Residence time)", "Truck Loads", None, None, None, None, "", "", N),
        ("Crushing", "Coarse Ore Bin (Size - Live Volume)", "m³", None, None, None, None, "", "", N),
        ("Crushing", "Coarse Ore Bin (Size - Live Tonnage)", "t", None, None, None, None, "", "", N),
        ("Crushing", "Feed F100", "mm", round(rom_f80_mm * 1.35, 0), None, None, None, "Calc", "ROM top size", N),
        ("Crushing", "Feed F80", "mm", rom_f80_mm, rom_f80_mm, round(rom_f80_mm*0.7,0), round(rom_f80_mm*1.3,0), "Design", "ROM F80 alimentation", N),
        ("Crushing", "Target Product P100", "mm", round(pc_css_mm * 1.5, 0), None, None, None, "Calc", "", N),
        ("Crushing", "Target Product P80", "mm", pc_p80_mm, pc_p80_mm, None, None, "Calc", "P80 ≈ 0.96 × CSS", N),
        ("Crushing", "Primary Crushing", "", None, None, None, None, "", "", H),
        ("Crushing", "Feeder type", "", None, None, None, None, "", "", N),
        ("Crushing", "Haul truck capacity", "t", None, None, None, None, "", "", N),
        ("Crushing", "Crusher Type", "", None, None, None, None, "", "Gyratory / Jaw", N),
        ("Crushing", "Crusher Model (Example)", "", None, None, None, None, "", "", N),
        ("Crushing", "Quantity", "", 1, None, None, None, "Design", "", N),
        ("Crushing", "Crusher Feed size F80", "mm", rom_f80_mm, rom_f80_mm, None, None, "Design", "= ROM F80", N),
        ("Crushing", "Crusher Product Size (T80)", "mm", pc_p80_mm, pc_p80_mm, None, None, "Calc", "= P80 primary", N),
        ("Crushing", "Power draw (Installed)", "kW", None, None, None, None, "", "", N),
        ("Crushing", "Close side setting (CSS)", "mm", pc_css_mm, pc_css_mm, None, None, "Design", "", N),
        ("Crushing", "Coarse Ore Stockpile and Reclaim", "", None, None, None, None, "", "", H),
        ("Crushing", "Storage type", "", None, None, None, None, "", "", N),
        ("Crushing", "Capacity total", "t", None, None, None, None, "", "", N),
        ("Crushing", "Capacity (Live Tonnage)", "t", None, None, None, None, "", "", N),
        ("Crushing", "Live capacity residence time", "h", None, None, None, None, "", "", N),
        ("Crushing", "Reclaim method", "-", None, None, None, None, "", "", N),
        ("Crushing", "Number of feeder", "-", None, None, None, None, "", "", N),
        ("Crushing", "Reclaim rate per feeder", "t/h", None, None, None, None, "", "", N),

        # ── Comminution / Grinding ────────────────────────────────────────
        ("Comminution", "Grinding", "", None, None, None, None, "", "", H),
        ("Comminution", "Circuit processing rate - Fresh", "t/h", tph, tph, None, None, "Project", "", N),
        ("Comminution", "SAG Mill Feed F80", "mm", sag_f80_mm, sag_f80_mm, None, None, "Calc", "= produit concassage secondaire", N),
        ("Comminution", "SAG Mill Product P80", "mm", sag_p80_mm, sag_p80_mm, None, None, "Design", "", N),
        ("Comminution", "Mill type", "", None, None, None, None, "", "SAG / Ball / HPGR+Ball", N),
        ("Comminution", "Feed pulp density", "%",
         float(sim_params.get("sag_feed_pulp_density_pct", 75)), float(sim_params.get("sag_feed_pulp_density_pct", 75)),
         None, None, "Design", "Typical SAG 70-78%", N),
        ("Comminution", "Circulating load", "%", bm_circ_load_pct, bm_circ_load_pct, None, None, "Design", "", N),
        ("Comminution", "Discharge pulp density", "%",
         float(sim_params.get("sag_discharge_density_pct", 72)), float(sim_params.get("sag_discharge_density_pct", 72)),
         None, None, "Design", "", N),
        ("Comminution", "Mill diameter", "m", None, None, None, None, "", "", N),
        ("Comminution", "Ball mill effective grinding length, EGL", "m", None, None, None, None, "", "", N),
        ("Comminution", "Ball mill motor power, installed", "kW", bm_power_kw, bm_power_kw, None, None, "Calc", f"Bond: {round(bm_energy_kwh_t,1)} kWh/t × {tph} t/h / 0.85", N),
        ("Comminution", "Ball mill Feed F80", "µm", round(sag_p80_mm * 1000, 0), round(sag_p80_mm * 1000, 0), None, None, "Calc", "= SAG P80", N),
        ("Comminution", "Ball mill Product P80", "µm", round(bm_p80_um, 0), round(bm_p80_um, 0), None, None, "LIMS B1", "", N),
        ("Comminution", "Ball diameter", "mm",
         float(sim_params.get("ball_diameter_mm", 60)), None, None, None, "Design", "", N),
        ("Comminution", "Ball charge", "%",
         float(sim_params.get("ball_charge_pct", 32)), None, None, None, "Design", "", N),
        ("Comminution", "Ball consumption", "kg/t", round(avg_ai * 2, 1) if avg_ai else 0.6, None, None, None, "Calc", "≈ 2 × Ai", N),
        ("Comminution", "Number of cyclones", "", None, None, None, None, "", "", N),
        ("Comminution", "Cyclone Size", "mm", None, None, None, None, "", "", N),
        ("Comminution", "Cyclone feed density", "% w/w", cyc_feed_pct_sol, cyc_feed_pct_sol, None, None, "Design", "Typical 60-70%", N),
        ("Comminution", "Cyclone underflow density", "%",
         float(sim_params.get("cyc_uf_density_pct", 72)), float(sim_params.get("cyc_uf_density_pct", 72)),
         None, None, "Design", "", N),
        ("Comminution", "Cyclone overflow density", "%",
         float(sim_params.get("cyc_of_density_pct", 35)), float(sim_params.get("cyc_of_density_pct", 35)),
         None, None, "Design", "", N),
        ("Comminution", "Cyclone overflow P80", "µm", round(cyc_of_p80_um, 0), round(cyc_of_p80_um, 0), None, None, "Design", "= BM P80 target", N),
        ("Comminution", "Primary grinding - Product P80", "µm", round(avg_p80,0), round(avg_p80,0), None, None, "LIMS B1", "", N),

        # ── Flotation (conditionnel : S>2.5% ou récup. flot>50%) ──
        *([
        ("Flotation", "Rougher flotation", "", None, None, None, None, "", "", H),
        ("Flotation", "Flotation head grade, Au", "g/t", round(avg_au,2), None, None, None, "LIMS A1", "", N),
        ("Flotation", "Flotation feed density", "%w/w", None, None, None, None, "", "", N),
        ("Flotation", "Flotation cell type", "", None, None, None, None, "", "", N),
        ("Flotation", "Stages", "", None, None, None, None, "", "Rougher/Scavenger/Cleaner/Recleaner", N),
        ("Flotation", "Circuit mass pull", "%", None, None, None, None, "LIMS G1", "", N),
        ("Flotation", "Residence time, lab scale", "mins", None, None, None, None, "LIMS G1", "", N),
        ("Flotation", "Scale up factor", "", None, None, None, None, "", "", N),
        ("Flotation", "Residence time, design", "mins", None, None, None, None, "Calc", "", N),
        ("Flotation", "Sectorial recovery, Au", "%", round(avg_flot_flags,1) if avg_flot_flags else None, None, None, None, "LIMS G1", "", N),
        ("Flotation", "Concentrate grade, Au", "g/t", None, None, None, None, "LIMS G1", "", N),
        ("Flotation", "PAX addition", "g/t", None, None, None, None, "LIMS G1", "", N),
        ("Flotation", "MIBC addition", "g/t", None, None, None, None, "LIMS G1", "", N),
        ("Flotation", "Tailings grade, Au", "g/t", None, None, None, None, "", "", N),
        ] if has_flotation else []),

        # ── Regrind ──
        *([
        ("Flotation", "Rougher concentrate regrind", "", None, None, None, None, "", "", H),
        ("Flotation", "Regrind circuit feed", "t/h", None, None, None, None, "Calc", "", N),
        ("Flotation", "Mill type", "", None, None, None, None, "", "IsaMill", N),
        ("Flotation", "Specific Energy", "kWh/t", None, None, None, None, "", "", N),
        ("Flotation", "Feed size, 80", "µm", None, None, None, None, "", "", N),
        ("Flotation", "Product size, P80", "µm", None, None, None, None, "", "", N),
        ("Flotation", "Motor power, installed", "kW", None, None, None, None, "", "", N),
        ] if has_isamill else []),

        # ── Gravity Concentration (conditionnel : GRG ≥ 10 %) ──
        *([
        ("Gravity Concentration", "GRG (Gravity Recoverable Gold)", "%", round(avg_grg,1), round(avg_grg,1), None, None, "LIMS C2", "", N),
        ("Gravity Concentration", "Gravity feed rate", "t/h", round(tph*0.30,1), None, None, None, "Calc", "30% of mill discharge to gravity", N),
        ("Gravity Concentration", "Centrifuge type", "", None, None, None, None, "", "Falcon / Knelson", N),
        ("Gravity Concentration", "Intensive Cyanidation Reactor (ICR)", "", None, None, None, None, "", "", H),
        ("Gravity Concentration", "ICR feed rate", "t/h", None, None, None, None, "Calc", "", N),
        ("Gravity Concentration", "NaCN addition to ICR", "kg/t", None, None, None, None, "", "", N),
        ] if has_gravity else []),

        # ── Leaching ──────────────────────────────────────────────────────
        ("Leaching", "Concentrate Pre-aeration", "", None, None, None, None, "", "", H),
        ("Leaching", "Processing Circuit Rate (Solid)", "t/h", None, None, None, None, "Calc", "", N),
        ("Leaching", "Feed % solid", "%w/w", None, None, None, None, "", "", N),
        ("Leaching", "Number of tanks", "", None, None, None, None, "", "", N),
        ("Leaching", "Residence time", "h", None, None, None, None, "", "", N),
        ("Leaching", "Lime Addition", "kg/t con", round(avg_cao,2) if avg_cao else None, None, None, None, "LIMS D1", "", N),
        ("Leaching", "O2 Addition", "kg/t con", None, None, None, None, "", "", N),
        ("Leaching", "Leach system", "", None, None, None, None, "", "", H),
        ("Leaching", "Leach feed % solid", "%w/w", None, None, None, None, "", "", N),
        ("Leaching", "Number of tanks", "", None, None, None, None, "", "", N),
        ("Leaching", "Residence time", "h", srt, srt, None, None, "LIMS D1", "", N),
        ("Leaching", "NaCN Addition", "kg/t_con", round(avg_nacn,2) if avg_nacn else None, None, None, None, "LIMS D1", "", N),
        ("Leaching", "O2 Addition", "mg/L", None, None, None, None, "", "", N),
        ("Leaching", "Lime Addition", "kg/t_con", round(avg_cao,2) if avg_cao else None, None, None, None, "LIMS D1", "", N),
        ("Leaching", "Expected Leaching Extraction - Au", "%", round(avg_rec,1), None, None, None, "LIMS D1", "", N),
        ("Leaching", "Expected Extraction - Ag", "%", None, None, None, None, "", "", N),

        # ── Circuit CIL ou CIP (sélection LIMS) ──
        ("CIL/CIP", f"Circuit retenu : {circuit_type}", "", None, None, None, None, "LIMS/Auto", f"Confiance : {circuit_confidence} (score={circuit_score})", H),
        ("CIL/CIP", "Circuit selection basis", "", None, None, None, None, "Auto", "; ".join(leach_decision["reasons"][:3]), N),
        ("CIL/CIP", "Number of tanks", "", n_tanks, None, None, None, "Calc", "", N),
        ("CIL/CIP", "Residence time", "h", srt, None, None, None, "LIMS D1", "", N),
        ("CIL/CIP", "Activated Carbon", "g/L",
         float(sim_params.get("carbon_loading_gl", 20)), float(sim_params.get("carbon_loading_gl", 20)),
         15, 50, "Industry", "CIP: adsorption tanks only; CIL: all tanks" if circuit_type == "CIP" else "Adsorption simultaneous leach", N),
        ("CIL/CIP", "Expected leach extraction - Au", "%", round(avg_rec,1), None, None, None, "LIMS D1", "", N),
        ("CIL/CIP", "Number of strips per day", "", None, None, None, None, "", "", N),

        # ── Elution / ADR ─────────────────────────────────────────────────
        ("Desorption and Electrowinning", "Elution Type", "", None, None, None, None, "", "AARL / Zadra / Anglo", N),
        ("Desorption and Electrowinning", "Elution Frequency", "nb/d", None, None, None, None, "", "", N),
        ("Desorption and Electrowinning", "Elution Column Capacity", "t", None, None, None, None, "", "", N),
        ("Desorption and Electrowinning", "Elution Temperature", "°C", None, None, None, None, "LIMS H1", "", N),
        ("Desorption and Electrowinning", "Elution Duration", "h", None, None, None, None, "", "", N),
        ("Desorption and Electrowinning", "Elution Solution NaCN Concentration", "%", None, None, None, None, "", "", N),
        ("Desorption and Electrowinning", "Regeneration Kiln", "", None, None, None, None, "", "", H),
        ("Desorption and Electrowinning", "Carbon Quantity Proportion to Regeneration", "%", None, None, None, None, "", "", N),
        ("Desorption and Electrowinning", "Regeneration Capacity", "t/d", None, None, None, None, "", "", N),
        ("Desorption and Electrowinning", "Regeneration Duration", "h", None, None, None, None, "", "", N),

        # ── Cyanide Destruction ───────────────────────────────────────────
        ("Cyanide Destruction", "Circuit feed", "t/h", None, None, None, None, "Calc", "", N),
        ("Cyanide Destruction", "Pulp Density (w/w%)", "%", None, None, None, None, "", "", N),
        ("Cyanide Destruction", "Feed WAD concentration", "mg/L", None, None, None, None, "", "", N),
        ("Cyanide Destruction", "Number of tanks", "", None, None, None, None, "", "", N),
        ("Cyanide Destruction", "Residence time", "h", None, None, None, None, "", "", N),
        ("Cyanide Destruction", "SO2 dosage", "g SO2/g CNWAD", None, None, None, None, "", "", N),
        ("Cyanide Destruction", "CuSO4 Dosage", "mg Cu²+/L", None, None, None, None, "", "", N),
        ("Cyanide Destruction", "Lime Dosage", "gCa(OH)2/gSO2", None, None, None, None, "", "", N),
        ("Cyanide Destruction", "Oxygen Dosage", "kg O2/kg SO2", None, None, None, None, "", "", N),

        # ── Final Tailings Thickener ──────────────────────────────────────
        ("Final Tailings Thickener", "Thickener type", "", None, None, None, None, "", "", N),
        ("Final Tailings Thickener", "Feed rate", "t/h", tph, None, None, None, "Project", "", N),
        ("Final Tailings Thickener", "Feed pulp density", "%w/w", None, None, None, None, "", "", N),
        ("Final Tailings Thickener", "Underflow pulp density", "%w/w", None, None, None, None, "", "", N),
        ("Final Tailings Thickener", "Solids settling flux", "t/h/m²", round(avg_ua*24,2) if avg_ua else None, None, None, None, "LIMS E1", "", N),
        ("Final Tailings Thickener", "Thickening area", "m²", round(thk_area_total, 0), None, None, None, "Calc", f"{thk_count} unit(s)", N),
        ("Final Tailings Thickener", "Thickener diameter", "m", thk_diam, None, None, None, "Calc", f"{thk_count} × Ø{thk_diam}m", N),
        ("Final Tailings Thickener", "Flocculant addition", "g/t", round(avg_floc,0) if avg_floc else None, None, None, None, "LIMS E1", "", N),

        # ── Water Requirements ────────────────────────────────────────────
        ("Water Requirements", "Fresh water source", "", None, None, None, None, "", "", N),
        ("Water Requirements", "Average fresh water requirement", "m³/h", round(_fresh_water_m3h, 1), None, None, None, "Calc/BM", "", N),
        ("Water Requirements", "Fresh water consumption", "m³/t feed", round(_fresh_water_m3h / max(tph, 1), 3), None, None, None, "Calc/BM", "", N),
        ("Water Requirements", "Process water tank residence time", "h", None, None, None, None, "", "", N),
        ("Water Requirements", "Process water tank (live volume)", "m3", None, None, None, None, "", "", N),

        # ── Reagent Area Criteria ─────────────────────────────────────────
        ("Reagent Area Criteria", "Primary collector", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages (dry)", "g/t feed", None, None, None, None, "LIMS G1", "", N),
        ("Reagent Area Criteria", "Mixing Strength (%w/w)", "%", None, None, None, None, "", "", N),
        ("Reagent Area Criteria", "Frother", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages (dry)", "g/t feed", None, None, None, None, "LIMS G1", "", N),
        ("Reagent Area Criteria", "Flocculant", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages", "g/t feed", round(avg_floc,0) if avg_floc else None, None, None, None, "LIMS E1", "", N),
        ("Reagent Area Criteria", "Lime", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages (dry)", "kg/t feed", round(avg_cao,2) if avg_cao else None, None, None, None, "LIMS D1", "", N),
        ("Reagent Area Criteria", "Sodium Cyanide", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages (dry)", "kg/t feed", round(avg_nacn,2) if avg_nacn else None, None, None, None, "LIMS D1", "", N),
        ("Reagent Area Criteria", "Copper Sulphate", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages (dry)", "kg/t feed", None, None, None, None, "", "", N),
        ("Reagent Area Criteria", "Caustic Soda", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages (dry)", "kg/t feed", None, None, None, None, "", "", N),
        ("Reagent Area Criteria", "Liquid Sulphur Dioxide (SO2)", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosage", "kg/h", None, None, None, None, "", "", N),
        ("Reagent Area Criteria", "Oxygen", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Total dosages", "kg/t feed", None, None, None, None, "", "", N),
        ("Reagent Area Criteria", "Carbon", "", None, None, None, None, "", "", H),
        ("Reagent Area Criteria", "Carbon Consumption", "kg/t", None, None, None, None, "", "", N),
    ]

    execute("DELETE FROM design_criteria WHERE project_id=%s", (pid,))

    # Deduplicate: skip rows where (section, item) already seen
    # Also skip headers whose item text == section name (already shown as group title)
    seen: set = set()
    clean: list = []
    for row_tuple in rows_def:
        sec, item, _, _, _, _, _, _, _, is_hdr = row_tuple
        if is_hdr and item.strip() == sec.strip():
            continue
        key = (sec, item)
        if key in seen:
            continue
        seen.add(key)
        clean.append(row_tuple)

    sections_out: Dict[str, list] = {}
    for sort_idx, (section, item, unit, design, nominal, min_val, max_val, source, comments, is_header) in enumerate(clean):
        row = execute(
            "INSERT INTO design_criteria "
            "(project_id, section, item, unit, design, nominal, min_val, max_val, "
            " source, comments, is_header, sort_order) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (pid, section, item, unit, design, nominal, min_val, max_val,
             source, comments, is_header, sort_idx),
        )
        sections_out.setdefault(section, []).append(row)

    sections_list = [{"name": k, "rows": v} for k, v in sections_out.items()]

    record_event(
        user_id=user["id"], project_id=pid,
        entity_type="design_criteria", entity_id=None,
        action="auto_generate",
        new_value={"total_rows": len(clean), "tph": tph, "grade": grade},
        source="web",
    )

    return {
        "ok": True,
        "sections": sections_list,
        "total_rows": len(clean),
        "design_tph": tph,
        "design_grade": grade,
    }


@router.patch("/{pid}/design-criteria/rows/{rid}", deprecated=True)
# SQL SAFETY: field names checked against explicit allowlist ["design", "unit", "source"].
def patch_dc_row(pid: str, rid: str, body: Dict[str, Any], response: Response, user=Depends(project_user)):
    """DEPRECATED — write-through to legacy `design_criteria`.

    The cascade engine reads `design_criteria_v2`, not this table. Use
    `PATCH /api/v1/projects/{pid}/dc-pipeline/rows/{rid}` for any new
    write paths so the change is observable to `run_cascade` (Chunk 1.5
    relocation, audit U1). The legacy endpoint stays for backward
    compatibility — legacy report consumers (NI 43-101 export, dashboard,
    risks, mass-balance auto-gen) still read the legacy table.
    """
    logger.warning(
        "DEPRECATION: PATCH /design-criteria/rows/%s is deprecated. "
        "Use PATCH /dc-pipeline/rows/%s instead so writes participate in cascade.",
        rid, rid,
    )
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = (
        f'</api/v1/projects/{pid}/dc-pipeline/rows/{rid}>; rel="successor-version"'
    )
    try:
        _DC_ALLOWED = frozenset(["design", "nominal", "min_val", "max_val", "unit", "source", "revision", "author", "comments"])
        fields, vals = build_update_sets(
            {k: body[k] for k in _DC_ALLOWED if k in body and body[k] is not None},
            allowed=_DC_ALLOWED,
        )
        if not fields: raise HTTPException(400, "Rien à mettre à jour")
        vals += [rid, pid]
        row = execute(f"UPDATE design_criteria SET {', '.join(fields)} WHERE id=%s AND project_id=%s RETURNING *", vals)

        record_event(
            user_id=user["id"], project_id=pid,
            entity_type="design_criteria", entity_id=rid,
            action="update", new_value=body,
            source="web",
        )

        return row
    except HTTPException:
        raise
    except psycopg2.OperationalError:
        raise HTTPException(503, detail="Database temporarily unavailable")
    except psycopg2.IntegrityError as e:
        raise HTTPException(409, detail=f"Conflict: {e.diag.message_detail}")
