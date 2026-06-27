"""
MPDPMS — Flowsheet Engine: Energy balance, equipment sizing, sensitivity.
"""
from __future__ import annotations
import logging
import math
from typing import Any

from fastapi import APIRouter, HTTPException, Depends

try:
    from ..auth import project_user
    from ..db import qone, qall
except ImportError:  # pragma: no cover - supports direct script imports
    from auth import project_user
    from db import qone, qall

logger = logging.getLogger("mpdpms.flowsheet_engine")

router = APIRouter(tags=["flowsheet-engine"])


# ─── Helpers: get project phase ──────────────────────────────────────────────

PHASE_MAP = {"SCOPING": "SCOPING", "PFS": "PFS", "FS": "FS", "FEED": "FEED"}


def _get_phase(pid: str) -> str:
    row = qone("SELECT status FROM projects WHERE id=%s", (pid,))
    return PHASE_MAP.get(row["status"], "SCOPING") if row else "SCOPING"


def _get_default_template_id(pid: str) -> str | None:
    row = qone(
        "SELECT id FROM circuit_templates WHERE project_id=%s ORDER BY created_at LIMIT 1",
        (pid,),
    )
    return str(row["id"]) if row else None


# ─── Calculation helpers ─────────────────────────────────────────────────────

def _calc_mill_power(wi: float, f80: float, p80: float, throughput_tph: float) -> dict:
    """Bond formula: W = Wi * 10 * (1/√P80 - 1/√F80) [kWh/t], P = W * Q [kW].
    F80/P80 in microns, Wi in kWh/t."""
    if throughput_tph <= 0 or p80 <= 0 or f80 <= 0:
        return {"power_kw": 0, "specific_energy_kwh_t": 0, "formula": "bond", "inputs": {}}
    w = wi * 10 * (1 / math.sqrt(p80) - 1 / math.sqrt(f80))
    w = max(w, 0)
    p = w * throughput_tph
    return {
        "power_kw": round(p, 1),
        "specific_energy_kwh_t": round(w, 3),
        "formula": "bond",
        "inputs": {"Wi": wi, "F80": f80, "P80": p80, "Q": throughput_tph},
    }


def _calc_pump_power(flow_m3h: float, density_kg_m3: float, head_m: float,
                     efficiency: float = 0.65) -> dict:
    """Pump hydraulic power: P = Q * ρ * g * H / η [kW]."""
    if flow_m3h <= 0 or efficiency <= 0:
        return {"power_kw": 0, "formula": "pump_hydraulic", "inputs": {}}
    q_m3s = flow_m3h / 3600
    p = q_m3s * density_kg_m3 * 9.81 * head_m / efficiency / 1000
    return {
        "power_kw": round(p, 1),
        "formula": "pump_hydraulic",
        "inputs": {"Q_m3h": flow_m3h, "rho": density_kg_m3, "H": head_m, "eta": efficiency},
    }


def _calc_thickener_area(solids_tpd: float, unit_flux: float = 0.5) -> dict:
    """Thickener area from solids load and unit area flux [t/m²/d]."""
    if solids_tpd <= 0 or unit_flux <= 0:
        return {"area_m2": 0, "formula": "thickener_unit_area", "inputs": {}}
    area = solids_tpd / unit_flux
    diameter = math.sqrt(4 * area / math.pi)
    return {
        "area_m2": round(area, 1),
        "diameter_m": round(diameter, 1),
        "formula": "thickener_unit_area",
        "inputs": {"solids_tpd": solids_tpd, "unit_flux": unit_flux},
    }


def _calc_tank_volume(flow_m3h: float, srt_hours: float) -> dict:
    """Tank volume from flow rate and solids retention time."""
    if flow_m3h <= 0 or srt_hours <= 0:
        return {"volume_m3": 0, "formula": "tank_srt", "inputs": {}}
    vol = flow_m3h * srt_hours
    hd_ratio = 2.5
    diameter = (4 * vol / (math.pi * hd_ratio)) ** (1 / 3)
    height = hd_ratio * diameter
    return {
        "volume_m3": round(vol, 1),
        "diameter_m": round(diameter, 1),
        "height_m": round(height, 1),
        "formula": "tank_srt",
        "inputs": {"Q_m3h": flow_m3h, "SRT_h": srt_hours},
    }


# ─── Op-code to section mapping (actual DB op_codes) ────────────────────────

OP_SECTION_MAP = {
    "GIRATOIRE": "CRUSHING", "CONE": "CRUSHING", "CRIBLE": "CRUSHING",
    "HPGR": "COMMINUTION", "SAG_MILL": "COMMINUTION", "BALL_MILL": "COMMINUTION",
    "HYDROCYCLONE": "CLASSIFICATION",
    "ISAMILL": "COMMINUTION", "VERTIMILL_REGRIND": "COMMINUTION",
    "GRAVITE_KNELSON": "GRAVITY",
    "FLOTATION_ROUGHER": "FLOTATION", "FLOTATION_CLEANER": "FLOTATION",
    "FLOTATION_SCAVENGER": "FLOTATION",
    "PREAERATION": "LEACHING",
    "LEACH_CUVES": "LEACHING", "CIP": "LEACHING",
    "DETOX_INCO": "CN_DESTRUCT",
    "ELUTION_AARL": "ADR", "ELUTION_ZADRA": "ADR",
    "ELECTROWINNING": "ADR", "FONDERIE": "ADR",
    "EPAISSISSEUR": "THICKENING", "EPAISSISSEUR_CONC": "THICKENING",
    "EPAISSISSEUR_HD": "THICKENING",
    "TSF_CONVENTIONNEL": "TAILINGS",
    "BASSIN_EAU": "WATER", "STOCKPILE": "RECEPTION",
}


def _energy_for_operation(op_code: str, installed_power_kw: float | None,
                          feed_tph: float, slurry_m3h: float) -> dict:
    """Calculate energy for an operation. Uses installed power from design criteria when available,
    otherwise estimates from engineering formulas based on op_code and throughput."""

    if installed_power_kw and installed_power_kw > 0:
        se = round(installed_power_kw / feed_tph, 3) if feed_tph > 0 else 0
        return {"power_kw": round(installed_power_kw, 1), "specific_energy_kwh_t": se,
                "formula": "design_criteria", "inputs": {"installed_kw": installed_power_kw, "Q": feed_tph}}

    # ── Grinding: Bond-based estimate ──
    if op_code in ("SAG_MILL", "BALL_MILL"):
        se = 12.0 if op_code == "SAG_MILL" else 8.0
        power_kw = round(se * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": se,
                "formula": "bond_estimate", "inputs": {"Q": feed_tph, "se": se}}

    if op_code in ("ISAMILL", "VERTIMILL_REGRIND"):
        se = 15.0 if op_code == "ISAMILL" else 10.0
        power_kw = round(se * feed_tph * 0.1, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": round(se * 0.1, 3),
                "formula": "regrind_estimate", "inputs": {"Q": feed_tph, "se": se}}

    # ── Crushing ──
    if op_code == "GIRATOIRE":
        se = 0.4
    elif op_code == "CONE":
        se = 0.55
    elif op_code == "HPGR":
        se = 2.5
    elif op_code == "CRIBLE":
        se = 0.3
    else:
        se = None

    if se is not None:
        power_kw = round(se * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": se,
                "formula": "crusher_estimate", "inputs": {"Q": feed_tph, "se": se}}

    # ── Classification ──
    if op_code == "HYDROCYCLONE":
        if slurry_m3h > 0:
            return _calc_pump_power(slurry_m3h, 1400, 20, 0.65)
        power_kw = round(0.5 * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": 0.5,
                "formula": "cyclone_pump", "inputs": {"Q": feed_tph}}

    # ── Gravity ──
    if op_code == "GRAVITE_KNELSON":
        grav_feed = feed_tph * 0.10
        se = 0.4
        power_kw = round(se * grav_feed, 1)
        actual_se = round(power_kw / grav_feed, 3) if grav_feed > 0 else 0
        return {"power_kw": power_kw, "specific_energy_kwh_t": actual_se,
                "formula": "gravity_se", "inputs": {"Q_grav": grav_feed, "se": se}}

    # ── Flotation ──
    if op_code.startswith("FLOTATION_"):
        se_map = {"FLOTATION_ROUGHER": 1.5, "FLOTATION_CLEANER": 1.2, "FLOTATION_SCAVENGER": 1.5}
        se = se_map.get(op_code, 1.5)
        power_kw = round(se * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": se,
                "formula": "flotation_agitator", "inputs": {"Q": feed_tph, "se": se}}

    # ── Leaching / CIP ──
    if op_code in ("LEACH_CUVES", "CIP", "PREAERATION"):
        se = 2.25
        power_kw = round(se * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": se,
                "formula": "agitator_estimate", "inputs": {"Q": feed_tph, "se": se}}

    # ── Detox ──
    if op_code == "DETOX_INCO":
        se = 0.75
        power_kw = round(se * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": se,
                "formula": "detox_agitator", "inputs": {"Q": feed_tph, "se": se}}

    # ── ADR / Elution / EW / Refining — fixed power (small-scale operations) ──
    if op_code in ("ELUTION_AARL", "ELUTION_ZADRA"):
        power_kw = 150
        se = round(power_kw / feed_tph, 3) if feed_tph > 0 else 0
        return {"power_kw": power_kw, "specific_energy_kwh_t": se,
                "formula": "elution_fixed", "inputs": {"Q": feed_tph}}
    if op_code == "ELECTROWINNING":
        power_kw = 120
        se = round(power_kw / feed_tph, 3) if feed_tph > 0 else 0
        return {"power_kw": power_kw, "specific_energy_kwh_t": se,
                "formula": "electrowinning_fixed", "inputs": {"Q": feed_tph}}
    if op_code == "FONDERIE":
        power_kw = 250
        return {"power_kw": power_kw, "specific_energy_kwh_t": round(250 / feed_tph, 3) if feed_tph > 0 else 0,
                "formula": "furnace_fixed", "inputs": {"Q": feed_tph}}

    # ── Thickening ──
    if op_code.startswith("EPAISSISSEUR"):
        solids_tpd = feed_tph * 24
        area = solids_tpd / 0.5
        rake_kw = 0.005 * area
        total_kw = round(rake_kw + 15, 1)
        se = round(total_kw / feed_tph, 3) if feed_tph > 0 else 0.35
        return {"power_kw": total_kw, "specific_energy_kwh_t": se,
                "formula": "thickener_drive", "inputs": {"area_m2": round(area, 1), "Q": feed_tph}}

    # ── Tailings ──
    if op_code == "TSF_CONVENTIONNEL":
        if slurry_m3h > 0:
            return _calc_pump_power(slurry_m3h, 1500, 30, 0.60)
        power_kw = round(2.0 * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": 2.0,
                "formula": "tailings_pump", "inputs": {"Q": feed_tph}}

    # ── Water ──
    if op_code == "BASSIN_EAU":
        power_kw = round(0.5 * feed_tph, 1)
        return {"power_kw": power_kw, "specific_energy_kwh_t": 0.5,
                "formula": "water_pump", "inputs": {"Q": feed_tph}}

    # ── Reagents ──
    if op_code.startswith("REACTIF_"):
        return {"power_kw": 5, "specific_energy_kwh_t": round(5 / feed_tph, 3) if feed_tph > 0 else 0,
                "formula": "reagent_dosing", "inputs": {"Q": feed_tph}}

    # ── Stockpile ──
    if op_code == "STOCKPILE":
        power_kw = round(0.15 * feed_tph, 1)
        return {"power_kw": max(power_kw, 10), "specific_energy_kwh_t": 0.15,
                "formula": "conveyor_drive", "inputs": {"Q": feed_tph}}

    # ── Fallback ──
    power_kw = round(0.5 * feed_tph, 1)
    return {"power_kw": power_kw, "specific_energy_kwh_t": 0.5,
            "formula": "estimated", "inputs": {"Q": feed_tph}}


# ─── API endpoints ───────────────────────────────────────────────────────────

@router.post("/api/v1/projects/{pid}/flowsheet-engine/energy-balance")
def compute_energy_balance(pid: str, user=Depends(project_user)):
    """Calculate energy balance for all operations in the active circuit template."""
    tid = _get_default_template_id(pid)
    if not tid:
        raise HTTPException(400, "Aucun circuit template actif.")

    phase = _get_phase(pid)

    ops = qall(
        "SELECT id, op_code, sort_order FROM circuit_operations "
        "WHERE template_id=%s AND enabled=TRUE ORDER BY sort_order",
        (tid,),
    )
    if not ops:
        raise HTTPException(400, "Le circuit template n'a aucune opération.")

    installed_power_map: dict[str, float] = {}
    dc_rows = qall(
        "SELECT op_code, design_value FROM design_criteria_v2 "
        "WHERE template_id=%s AND enabled=TRUE AND unit='kW' "
        "AND item ILIKE '%%power%%'",
        (tid,),
    )
    for r in dc_rows:
        oc = r.get("op_code") or ""
        val = r.get("design_value")
        if oc and val is not None:
            try:
                installed_power_map[oc] = max(installed_power_map.get(oc, 0), float(val))
            except (ValueError, TypeError):
                pass

    mb_feed: dict[str, dict] = {}
    mb_rows = qall(
        "SELECT sec.op_code, s.solids_tph, s.slurry_m3h "
        "FROM mass_balance_streams_v2 s "
        "JOIN mass_balance_sections_v2 sec ON sec.id = s.section_id "
        "WHERE sec.template_id=%s AND s.sort_order = 1",
        (tid,),
    )
    for r in mb_rows:
        oc = r.get("op_code") or ""
        if oc and oc not in mb_feed:
            mb_feed[oc] = r

    p = qone("SELECT target_tph FROM projects WHERE id=%s", (pid,))
    throughput = float(p["target_tph"]) if p and p.get("target_tph") else 0

    results = []
    total_installed = 0.0
    total_operating = 0.0

    for op in ops:
        oc = op["op_code"] or ""
        if not oc:
            continue

        feed_data = mb_feed.get(oc, {})
        feed_tph = float(feed_data.get("solids_tph") or 0) or throughput
        slurry_m3h = float(feed_data.get("slurry_m3h") or 0)

        calc = _energy_for_operation(oc, installed_power_map.get(oc), feed_tph, slurry_m3h)

        power_kw = calc.get("power_kw", 0)
        operating_factor = 0.85
        power_op = power_kw * operating_factor
        se = calc.get("specific_energy_kwh_t", 0)
        section = OP_SECTION_MAP.get(oc, "MISC")

        results.append({
            "op_code": oc,
            "section": section,
            "power_installed_kw": round(power_kw, 1),
            "power_operating_kw": round(power_op, 1),
            "specific_energy_kwh_t": round(se, 3),
            "formula_used": calc.get("formula", ""),
            "inputs": calc.get("inputs", {}),
        })
        total_installed += power_kw
        total_operating += power_op

    return {
        "items": results,
        "total_installed_kw": round(total_installed, 1),
        "total_operating_kw": round(total_operating, 1),
        "total_installed_mw": round(total_installed / 1000, 2),
        "kwh_per_tonne": round(total_installed / throughput, 2) if throughput > 0 else 0,
        "phase": phase,
    }


@router.get("/api/v1/projects/{pid}/flowsheet-engine/energy-balance")
def get_energy_balance(pid: str, user=Depends(project_user)):
    """Retrieve the latest energy balance by computing it on-the-fly."""
    return compute_energy_balance(pid, user)


# ─── Equipment Sizing ────────────────────────────────────────────────────────

DESIGN_FACTORS = {"SCOPING": 1.30, "PFS": 1.25, "FS": 1.15, "FEED": 1.10}


def _size_operation(op_code: str, dc_params: dict, mb_streams: list,
                    throughput_tph: float, phase: str) -> dict | None:
    """Calculate equipment sizing based on op_code."""
    df = DESIGN_FACTORS.get(phase, 1.25)

    if op_code in ("SAG_MILL", "BALL_MILL", "GIRATOIRE", "CONE", "HPGR"):
        se_map = {"SAG_MILL": 12, "BALL_MILL": 8, "GIRATOIRE": 0.4, "CONE": 0.55, "HPGR": 2.5}
        se = se_map.get(op_code, 8)
        power = se * throughput_tph * df
        vol_power_density = 20
        volume = power / vol_power_density if vol_power_density > 0 else 0
        if op_code in ("GIRATOIRE", "CONE"):
            eq_type = "crusher"
        elif op_code == "HPGR":
            eq_type = "hpgr"
        else:
            eq_type = "mill"
        return {
            "sizing_inputs": {"Q": throughput_tph, "se": se},
            "sizing_results": {
                "power_kw": round(power, 1),
                "volume_m3": round(volume, 1),
                "design_factor": df,
            },
            "design_factor": df,
            "equipment_type": eq_type,
        }

    if op_code in ("LEACH_CUVES", "CIP", "PREAERATION"):
        srt = 24
        slurry_m3h = 0
        for s in mb_streams:
            if (s.get("op_code") or "") == op_code:
                slurry_m3h = max(slurry_m3h, float(s.get("slurry_m3h") or 0))
        if slurry_m3h <= 0:
            slurry_m3h = throughput_tph * 2.5
        calc = _calc_tank_volume(slurry_m3h, srt)
        return {
            "sizing_inputs": {"SRT_h": srt, "Q_m3h": slurry_m3h},
            "sizing_results": {
                "volume_m3": round(calc["volume_m3"] * df, 1),
                "diameter_m": round(calc.get("diameter_m", 0) * (df ** (1/3)), 1),
                "height_m": round(calc.get("height_m", 0) * (df ** (1/3)), 1),
                "design_factor": df,
            },
            "design_factor": df,
            "equipment_type": "tank",
        }

    if op_code.startswith("EPAISSISSEUR"):
        solids_tpd = 0
        for s in mb_streams:
            if (s.get("op_code") or "") == op_code:
                solids_tpd = max(solids_tpd, float(s.get("solids_tpd") or 0))
        if solids_tpd <= 0:
            solids_tpd = throughput_tph * 24
        calc = _calc_thickener_area(solids_tpd, 0.5)
        return {
            "sizing_inputs": {"solids_tpd": solids_tpd, "unit_flux": 0.5},
            "sizing_results": {
                "area_m2": round(calc["area_m2"] * df, 1),
                "diameter_m": round(calc.get("diameter_m", 0) * math.sqrt(df), 1),
                "design_factor": df,
            },
            "design_factor": df,
            "equipment_type": "thickener",
        }

    return None


@router.post("/api/v1/projects/{pid}/flowsheet-engine/sizing/all")
def compute_all_sizing(pid: str, user=Depends(project_user)):
    """Calculate equipment sizing for all operations in the active template."""
    tid = _get_default_template_id(pid)
    if not tid:
        raise HTTPException(400, "Aucun circuit template actif.")
    phase = _get_phase(pid)

    ops = qall(
        "SELECT id, op_code FROM circuit_operations "
        "WHERE template_id=%s AND enabled=TRUE ORDER BY sort_order",
        (tid,),
    )

    dc_rows = qall(
        "SELECT op_code, ref_number, item, design_value, unit FROM design_criteria_v2 "
        "WHERE template_id=%s AND enabled=TRUE",
        (tid,),
    )
    dc_params: dict[str, Any] = {}
    for r in dc_rows:
        key = f"{r.get('op_code','')}__{r.get('ref_number','')}"
        val = r.get("design_value")
        if val is not None:
            dc_params[key] = val

    mb_streams = qall(
        "SELECT sec.op_code, s.solids_tph, s.solids_tpd, s.slurry_m3h "
        "FROM mass_balance_streams_v2 s "
        "JOIN mass_balance_sections_v2 sec ON sec.id = s.section_id "
        "WHERE sec.template_id=%s AND s.sort_order = 1",
        (tid,),
    )

    p = qone("SELECT target_tph FROM projects WHERE id=%s", (pid,))
    throughput = float(p["target_tph"]) if p and p.get("target_tph") else 0

    results = []
    for op in ops:
        oc = op["op_code"] or ""
        if not oc:
            continue
        sizing = _size_operation(oc, dc_params, mb_streams, throughput, phase)
        if not sizing:
            continue
        sizing["op_code"] = oc
        sizing["status"] = "calculated"
        results.append(sizing)
    return {"items": results, "count": len(results), "phase": phase}


@router.get("/api/v1/projects/{pid}/flowsheet-engine/sizing")
def get_all_sizing(pid: str, user=Depends(project_user)):
    """Retrieve all sizing by computing on-the-fly."""
    return compute_all_sizing(pid, user)
