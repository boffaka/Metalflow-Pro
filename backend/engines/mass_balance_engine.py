"""
MetalFlow Pro — Mass Balance Calculation Engine.

Generates a complete circuit-by-circuit mass balance from design criteria
and LIMS data, matching the professional standard of PFS/FS mass balance
documents (14 numeric fields per stream, balance checks per section).

Reference basis: BMMC plant — 1517 t/h, 1.5 g/t Au, refractory ore.
"""
from __future__ import annotations

import logging
import json
import uuid
from typing import Any

logger = logging.getLogger("mpdpms.mass_balance")

# ============================================================================
# Constants
# ============================================================================

try:
    from ..constants import TROY_OZ_PER_GRAM, WATER_SG
    from ..settings import get_settings
    from .gravity_model import (
        gravity_concentrate_grade_g_t,
        plant_gravity_recovery_pct,
        resolve_gravity_params,
        blended_head_grade_g_t,
    )
except ImportError:
    from constants import TROY_OZ_PER_GRAM, WATER_SG
    from settings import get_settings
    from engines.gravity_model import (
        gravity_concentrate_grade_g_t,
        plant_gravity_recovery_pct,
        resolve_gravity_params,
        blended_head_grade_g_t,
    )

DEFAULT_ORE_SG = get_settings().default_ore_sg  # 2.75 — centralized in settings

# Section op_code mapping — determines which section generator to call
# and the order in which sections appear in the balance.
SECTION_REGISTRY: list[tuple[str, str, list[str]]] = [
    # (section_name, generator_function_suffix, required_op_codes_any)
    ("CRUSHING",            "crushing",           ["GIRATOIRE", "CONE", "CRIBLE", "STOCKPILE"]),
    ("COMMINUTION_HPGR",    "comminution_hpgr",   ["HPGR"]),
    ("COMMINUTION_SAG",     "comminution_sag",     ["SAG_MILL"]),
    ("COMMINUTION_BALL",    "comminution_ball",    ["BALL_MILL", "HYDROCYCLONE"]),
    ("GRAVITY_RECOVERY",    "gravity_recovery",    ["GRAVITE_KNELSON", "GRAVITE_FALCON", "GRAVITE_GEMENI"]),
    ("FLOTATION",           "flotation",           ["FLOTATION_ROUGHER", "FLOTATION_SCAVENGER"]),
    ("CONCENTRATE_REGRIND", "concentrate_regrind", ["ISAMILL", "VERTIMILL_REGRIND", "SMD", "VERTIMILL"]),
    ("CONCENTRATE_THICKENER", "concentrate_thickener", ["EPAISSISSEUR_CONC"]),
    ("LEACHING",            "leaching",            ["LEACH_CUVES", "PREAERATION"]),
    ("CIP",                 "cip",                 ["CIP"]),
    ("CIL",                 "cil",                 ["CIL"]),
    ("DETOX",               "detox",               ["DETOX_INCO", "DETOX_CARO", "DETOX_PEROXIDE"]),
    ("TAILINGS_THICKENER",  "tailings_thickener",  ["EPAISSISSEUR", "EPAISSISSEUR_HD"]),
    ("ADR",                 "adr",                 ["ELUTION_AARL", "ELUTION_ZADRA", "ELECTROWINNING", "FONDERIE"]),
    ("WATER_SERVICES",      "water_services",      []),  # always generated
]


# ============================================================================
# Stream calculation
# ============================================================================

def calc_stream(
    name: str,
    solids_tph: float,
    pct_solids_ww: float,
    solids_sg: float = DEFAULT_ORE_SG,
    au_gt: float | None = None,
    s_pct: float | None = None,
    h_per_d: float = 22.1,
    is_balance_check: bool = False,
    is_recirculation: bool = False,
    sort_order: int = 0,
    extras: dict | None = None,
) -> dict:
    """Calculate a complete stream from solids throughput and percent solids.

    ``h_per_d`` définit les tonnes/jour (t/h × h/j). Défaut 22,1 h/j aligné sur
    bilans type étude (arrêt maintenance) ; les sections complètes utilisent
    ``plant_h_per_d`` issu des critères de conception quand présent.

    Key equations:
        solids_m3h = solids_tph / solids_sg
        water_tph  = solids_tph * (100 - pct_solids) / pct_solids
        water_m3h  = water_tph / 1.0
        slurry_tph = solids_tph + water_tph
        slurry_m3h = solids_m3h + water_m3h
        slurry_sg  = slurry_tph / slurry_m3h
        All _tpd   = _tph * h_per_d
    """
    try:
        pct_s = max(0.01, min(99.99, pct_solids_ww)) / 100.0

        water_tph = solids_tph * (1 - pct_s) / pct_s if pct_s > 0 else 0.0
        solids_m3h = solids_tph / solids_sg if solids_sg > 0 else 0.0
        water_m3h = water_tph / WATER_SG
        slurry_tph = solids_tph + water_tph
        slurry_m3h = solids_m3h + water_m3h
        slurry_sg = slurry_tph / slurry_m3h if slurry_m3h > 0 else 1.0

        return {
            "stream_name": name,
            "hours_per_day": round(h_per_d, 2),
            "solids_tpd": round(solids_tph * h_per_d, 1),
            "solids_tph": round(solids_tph, 1),
            "solids_m3h": round(solids_m3h, 1),
            "solids_sg": round(solids_sg, 2),
            "water_tpd": round(water_tph * h_per_d, 1),
            "water_tph": round(water_tph, 1),
            "water_m3h": round(water_m3h, 1),
            "water_sg": WATER_SG,
            "slurry_tpd": round(slurry_tph * h_per_d, 1),
            "slurry_tph": round(slurry_tph, 1),
            "slurry_m3h": round(slurry_m3h, 1),
            "slurry_pct_w": round(pct_solids_ww, 1),
            "slurry_sg": round(slurry_sg, 2),
            "au_gt": round(au_gt, 3) if au_gt is not None else None,
            "s_pct": round(s_pct, 2) if s_pct is not None else None,
            "is_balance_check": is_balance_check,
            "is_recirculation": is_recirculation,
            "sort_order": sort_order,
            "extras": extras or {},
        }
    except Exception as e:
        logger.error("calc_stream failed for name='%s', solids_tph=%.1f, pct_solids=%.1f: %s",
                     name, solids_tph, pct_solids_ww, e)
        raise RuntimeError(f"calc_stream failed for stream '{name}': {e}") from e


def calc_water_only_stream(
    name: str,
    water_tph: float,
    h_per_d: float = 22.1,
    sort_order: int = 0,
    extras: dict | None = None,
) -> dict:
    """Create a water-only stream (e.g., gland seal, dilution water)."""
    return {
        "stream_name": name,
        "hours_per_day": round(h_per_d, 2),
        "solids_tpd": 0.0,
        "solids_tph": 0.0,
        "solids_m3h": 0.0,
        "solids_sg": 0.0,
        "water_tpd": round(water_tph * h_per_d, 1),
        "water_tph": round(water_tph, 1),
        "water_m3h": round(water_tph / WATER_SG, 1),
        "water_sg": WATER_SG,
        "slurry_tpd": round(water_tph * h_per_d, 1),
        "slurry_tph": round(water_tph, 1),
        "slurry_m3h": round(water_tph / WATER_SG, 1),
        "slurry_pct_w": 0.0,
        "slurry_sg": WATER_SG,
        "au_gt": None,
        "s_pct": None,
        "is_balance_check": False,
        "is_recirculation": False,
        "sort_order": sort_order,
        "extras": extras or {},
    }


def balance_check(streams_in: list[dict], streams_out: list[dict]) -> dict:
    """Generate a balance check row.

    Sums solids/water/slurry for inputs and outputs.  Difference should be ~0.
    """
    def _sum(streams, key):
        return sum(float(s.get(key, 0) or 0) for s in streams)

    solids_diff = _sum(streams_in, "solids_tph") - _sum(streams_out, "solids_tph")
    water_diff = _sum(streams_in, "water_tph") - _sum(streams_out, "water_tph")
    slurry_diff = solids_diff + water_diff

    return {
        "stream_name": "Balance check",
        "hours_per_day": 0,
        "solids_tpd": 0.0,
        "solids_tph": round(solids_diff, 4),
        "solids_m3h": 0.0,
        "solids_sg": 0.0,
        "water_tpd": 0.0,
        "water_tph": round(water_diff, 4),
        "water_m3h": 0.0,
        "water_sg": 0.0,
        "slurry_tpd": 0.0,
        "slurry_tph": round(slurry_diff, 4),
        "slurry_m3h": 0.0,
        "slurry_pct_w": 0.0,
        "slurry_sg": 0.0,
        "au_gt": None,
        "s_pct": None,
        "is_balance_check": True,
        "is_recirculation": False,
        "sort_order": 9999,
        "extras": {
            "source_basis": "Calculated mass/water closure from displayed input and output streams",
            "source_refs": ["mass_balance_engine.balance_check"],
        },
    }


def _pct_solids_from_water(solids_tph: float, water_tph: float) -> float:
    total = max(solids_tph + water_tph, 1e-9)
    return max(0.01, min(99.99, solids_tph / total * 100.0))


def _carry_add(carry: dict, key: str, value: float) -> None:
    carry[key] = float(carry.get(key, 0.0) or 0.0) + float(value or 0.0)


def _source_extras(*refs: str, basis: str = "Calculated from project, design criteria and upstream process streams") -> dict:
    return {"source_basis": basis, "source_refs": [r for r in refs if r]}


# ============================================================================
# Design criteria helpers
# ============================================================================

def _get_dc(
    template_id: str,
    op_code: str,
    item_pattern: str,
    cursor,
    default: float | None = None,
) -> float | None:
    """Get a design criterion value.  Returns design_value or nominal_value."""
    cursor.execute(
        "SELECT design_value, nominal_value "
        "FROM design_criteria_v2 "
        "WHERE template_id = %s AND op_code = %s "
        "  AND item ILIKE %s AND enabled = true "
        "ORDER BY sort_order LIMIT 1",
        (template_id, op_code, f"%{item_pattern}%"),
    )
    row = cursor.fetchone()
    if row is None:
        return default
    val = row.get("design_value") if isinstance(row, dict) else row[0]
    if val is None:
        val = row.get("nominal_value") if isinstance(row, dict) else row[1]
    return float(val) if val is not None else default


def _get_dc_nominal(
    template_id: str,
    op_code: str,
    item_pattern: str,
    cursor,
    default: float | None = None,
) -> float | None:
    """Throughput / rate from DC: nominal_value first (same basis as DC UI nominal column)."""
    cursor.execute(
        "SELECT design_value, nominal_value "
        "FROM design_criteria_v2 "
        "WHERE template_id = %s AND op_code = %s "
        "  AND item ILIKE %s AND enabled = true "
        "ORDER BY sort_order LIMIT 1",
        (template_id, op_code, f"%{item_pattern}%"),
    )
    row = cursor.fetchone()
    if row is None:
        return default
    val = row.get("nominal_value") if isinstance(row, dict) else row[1]
    if val is None:
        val = row.get("design_value") if isinstance(row, dict) else row[0]
    return float(val) if val is not None else default


def _get_enabled_ops(template_id: str, cursor) -> set[str]:
    """Return set of enabled op_codes for the template."""
    cursor.execute(
        "SELECT op_code FROM circuit_operations "
        "WHERE template_id = %s AND enabled = true",
        (template_id,),
    )
    rows = cursor.fetchall()
    return {r["op_code"] if isinstance(r, dict) else r[0] for r in rows}


def _get_project_params(project_id: str, cursor) -> dict:
    """Read key parameters from the projects table."""
    cursor.execute(
        "SELECT target_tph, gold_grade_g_t, availability_pct, ore_sg, "
        "       plant_throughput_tpd "
        "FROM projects WHERE id = %s",
        (project_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError(f"Project {project_id} not found")
    if isinstance(row, dict):
        return {
            "target_tph": float(row.get("target_tph") or 1517),
            "gold_grade": float(row.get("gold_grade_g_t") or 1.5),
            "availability": float(row.get("availability_pct") or 92),
            "ore_sg": float(row.get("ore_sg") or DEFAULT_ORE_SG),
        }
    return {
        "target_tph": float(row[0] or 1517),
        "gold_grade": float(row[1] or 1.5),
        "availability": float(row[2] or 92),
        "ore_sg": float(row[3] or DEFAULT_ORE_SG),
    }


def _get_lims_avg(project_id: str, table: str, column: str, cursor) -> float | None:
    """Get a single LIMS average.

    Uses a SAVEPOINT so that a query failure (e.g. missing column) does not
    abort the enclosing transaction.
    """
    try:
        cursor.execute("SAVEPOINT _lims_avg")
        # Safety: `table` and `column` are only ever called with hardcoded
        # string literals (e.g. "lims_a1", "au_g_t") — never user input.
        cursor.execute(
            f"SELECT AVG({column}) FROM {table} WHERE project_id = %s",  # noqa: S608
            (project_id,),
        )
        row = cursor.fetchone()
        cursor.execute("RELEASE SAVEPOINT _lims_avg")
        if row is None:
            return None
        val = list(row.values())[0] if isinstance(row, dict) else row[0]
        return float(val) if val is not None else None
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT _lims_avg")
        logger.debug("LIMS query failed: %s.%s", table, column, exc_info=True)
        return None


# ============================================================================
# Section generators
# ============================================================================
# Each generator returns (streams_in, streams_out, all_streams) where
# all_streams is the ordered list for display (including balance check).
# The carry-forward dict accumulates state between sections.

def _gen_crushing(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Crushing section: gyratory, screen, cone recirculation."""
    h = dc.get("crush_hours_per_day", 18.0)  # crushing hours per day
    tph = dc.get("crush_nominal_tph") or pp["target_tph"]
    sg = pp["ore_sg"]
    au = pp["gold_grade"]
    pct_sol = dc.get("crush_pct_solids", 97.0)  # ROM humidity ~3%
    recirc_pct = dc.get("cone_recirc_pct", 120.0) / 100.0
    enabled_ops = carry.get("enabled_ops", set())
    has_gyratory = "GIRATOIRE" in enabled_ops or not enabled_ops
    has_screen = "CRIBLE" in enabled_ops or "CONE" in enabled_ops or not enabled_ops
    has_cone = "CONE" in enabled_ops
    has_stockpile = "STOCKPILE" in enabled_ops or not enabled_ops

    streams = []
    s = 0

    # ROM feed
    rom_label = "ROM Feed to Gyratory" if has_gyratory else "ROM Feed to Crushing"
    rom_feed = calc_stream(rom_label, tph, pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(rom_feed)

    # Gyratory product (same solids, slight moisture pickup ignored at this stage)
    if has_gyratory:
        gyr_product = calc_stream("Gyratory Crusher Product", tph, pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
        streams.append(gyr_product)

    # Screen feed = fresh + recirc
    if has_screen:
        screen_feed_solids = tph * (1.0 + recirc_pct if has_cone else 1.0)
        screen_feed = calc_stream("Screen Feed (Fresh + Recirc)" if has_cone else "Scalping Screen Feed", screen_feed_solids, pct_sol, sg, au, h_per_d=h,
                                  sort_order=(s := s + 1))
        streams.append(screen_feed)

    # Screen oversize to cone crusher (recirc fraction)
    screen_os = None
    cone_product = None
    if has_cone:
        screen_os_solids = tph * recirc_pct
        screen_os = calc_stream("Screen O/S to Cone Crusher", screen_os_solids, pct_sol, sg, au, h_per_d=h,
                                is_recirculation=True, sort_order=(s := s + 1))
        streams.append(screen_os)

    # Screen undersize = product
    if has_screen:
        screen_us = calc_stream("Screen U/S to Stockpile" if has_stockpile else "Screen U/S to Grinding", tph, pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
        streams.append(screen_us)

    # Cone crusher product (= screen O/S, returns to screen)
    if has_cone:
        cone_product = calc_stream("Cone Crusher Product", screen_os_solids, pct_sol, sg, au, h_per_d=h,
                                   is_recirculation=True, sort_order=(s := s + 1))
        streams.append(cone_product)

    # Crushed product to stockpile
    product_name = "Crushed Product to Stockpile" if has_stockpile else "Crushed Product to Grinding"
    product = calc_stream(product_name, tph, pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(product)

    # Balance check (in: ROM feed + cone recycle; out: product + cone recycle)
    ins = [rom_feed] + ([cone_product] if cone_product else [])
    outs = [product] + ([screen_os] if screen_os else [])
    streams.append(balance_check(ins, outs))

    # Carry forward
    carry["crush_product_tph"] = tph
    carry["crush_product_pct_sol"] = pct_sol
    carry["crush_h_per_d"] = h
    carry["au_gt"] = au

    return streams


def _gen_comminution_hpgr(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """HPGR comminution circuit."""
    h = dc.get("hpgr_h_per_d", 19.0)
    tph = dc.get("hpgr_nominal_tph") or carry.get("crush_product_tph", pp["target_tph"])
    sg = pp["ore_sg"]
    au = carry.get("au_gt", pp["gold_grade"])
    pct_sol_feed = dc.get("hpgr_feed_pct_solids", 97.0)
    recirc_pct = dc.get("hpgr_recirc_pct", 100.0) / 100.0

    streams = []
    s = 0

    fresh_feed = calc_stream("HPGR Fresh Feed", tph, pct_sol_feed, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(fresh_feed)

    total_feed_solids = tph * (1.0 + recirc_pct)
    total_feed = calc_stream("HPGR Total Feed (Fresh + Recirc)", total_feed_solids, pct_sol_feed, sg, au,
                             h_per_d=h, sort_order=(s := s + 1))
    streams.append(total_feed)

    discharge = calc_stream("HPGR Discharge", total_feed_solids, pct_sol_feed, sg, au, h_per_d=h,
                            sort_order=(s := s + 1))
    streams.append(discharge)

    fines_us = calc_stream("Fines Screen U/S to Ball Mill", tph, pct_sol_feed, sg, au, h_per_d=h,
                           sort_order=(s := s + 1))
    streams.append(fines_us)

    fines_os_solids = tph * recirc_pct
    fines_os = calc_stream("Fines Screen O/S to HPGR (Recirc)", fines_os_solids, pct_sol_feed, sg, au,
                           h_per_d=h, is_recirculation=True, sort_order=(s := s + 1))
    streams.append(fines_os)

    ins = [fresh_feed, fines_os]
    outs = [fines_us, fines_os]
    streams.append(balance_check(ins, outs))

    carry["hpgr_product_tph"] = tph
    carry["hpgr_pct_sol"] = pct_sol_feed

    return streams


def _gen_comminution_sag(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """SAG mill section (alternative to HPGR)."""
    h = dc.get("plant_h_per_d", 22.1)
    tph = dc.get("sag_nominal_tph") or dc.get("mill_nominal_tph") or carry.get("crush_product_tph", pp["target_tph"])
    sg = pp["ore_sg"]
    au = carry.get("au_gt", pp["gold_grade"])
    pct_sol = dc.get("sag_feed_pct_solids", 75.0)

    streams = []
    s = 0

    sag_feed = calc_stream("SAG Mill Feed", tph, pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(sag_feed)

    sag_water = sag_feed["water_tph"] + dc.get("sag_gland_m3h", 1.2)
    sag_discharge = calc_stream("SAG Mill Discharge", tph, _pct_solids_from_water(tph, sag_water), sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(sag_discharge)

    gland = calc_water_only_stream("SAG Mill Gland Seal Water", dc.get("sag_gland_m3h", 1.2), h_per_d=h,
                                   sort_order=(s := s + 1))
    streams.append(gland)

    ins = [sag_feed, gland]
    outs = [sag_discharge]
    streams.append(balance_check(ins, outs))

    carry["sag_product_tph"] = tph
    carry["sag_pct_sol"] = 72.0

    return streams


def _gen_comminution_ball(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Ball mill grinding circuit with cyclone classification.

    This solver uses an iterative successive substitution approach to accurately
    balance the circulating load loop (Ball Mill -> Cyclone -> Underflow -> Ball Mill).
    It guarantees mass and water conservation across the entire node.
    """
    h = dc.get("plant_h_per_d", 22.1)
    tph = dc.get("mill_nominal_tph") or carry.get("hpgr_product_tph", carry.get("sag_product_tph", pp["target_tph"]))
    sg = pp["ore_sg"]
    au = carry.get("au_gt", pp["gold_grade"])

    # Target setpoints
    feed_pct_sol = dc.get("bm_feed_pct_solids", 72.0)
    recirc_cl = dc.get("bm_recirc_cl_pct", 350.0) / 100.0  # e.g. 3.5 -> 350% circulating load
    cyc_of_pct_sol = dc.get("cyc_of_pct_solids", 35.0)
    cyc_uf_pct_sol = dc.get("cyc_uf_pct_solids", 70.0)

    # Water additions (fixed)
    gland_water = dc.get("bm_gland_m3h", 1.2)
    dilution_water_tph = dc.get("bm_dilution_water_tph", 0.0)

    # 1. Fresh Feed definition
    pct_s_feed = max(0.01, min(99.99, feed_pct_sol)) / 100.0
    fresh_water_tph = tph * (1 - pct_s_feed) / pct_s_feed

    # 2. Iterative Solver for the Grinding Loop
    # Loop variables
    uf_solids_tph = tph * recirc_cl
    uf_water_tph = 0.0

    max_iter = 50
    tolerance = 0.001
    converged = False

    # We want to find the exact amount of water needed at the cyclone feed pump box
    # to achieve the target cyclone overflow density (cyc_of_pct_sol).
    target_of_pct_s = max(0.01, min(99.99, cyc_of_pct_sol)) / 100.0
    target_uf_pct_s = max(0.01, min(99.99, cyc_uf_pct_sol)) / 100.0

    for i in range(max_iter):
        # A. Ball Mill Processing
        bm_feed_solids = tph + uf_solids_tph
        bm_feed_water = fresh_water_tph + uf_water_tph + gland_water + dilution_water_tph

        # B. Cyclone Feed (Assuming all BM discharge goes to cyclone)
        # Cyclone Feed = BM Discharge
        # Solids must equal fresh + circulating
        cf_solids_tph = bm_feed_solids
        _cf_water_tph = bm_feed_water

        # C. Cyclone Separation
        # The overflow solids MUST equal fresh feed solids (steady state)
        of_solids_tph = tph

        # The water in overflow is determined by the target overflow density
        of_water_tph = of_solids_tph * (1 - target_of_pct_s) / target_of_pct_s

        # The underflow solids is the remainder
        new_uf_solids_tph = cf_solids_tph - of_solids_tph

        # The underflow water is determined by the target underflow density
        new_uf_water_tph = new_uf_solids_tph * (1 - target_uf_pct_s) / target_uf_pct_s

        # How much water do we need to add to the pump box to make the water balance work?
        # Pump Box Water Addition = (Total Water out of Cyclone) - (Water in BM Discharge)
        # Note: In a real plant, water is added to the mill and the pump box.
        required_pumpbox_water = (of_water_tph + new_uf_water_tph) - bm_feed_water

        # Check convergence on circulating load water
        if abs(new_uf_water_tph - uf_water_tph) < tolerance and abs(new_uf_solids_tph - uf_solids_tph) < tolerance:
            uf_solids_tph = new_uf_solids_tph
            uf_water_tph = new_uf_water_tph
            converged = True
            break

        uf_solids_tph = new_uf_solids_tph
        uf_water_tph = new_uf_water_tph

    if not converged:
        logger.warning("Ball Mill mass balance solver did not converge after %d iterations", max_iter)

    # 3. Create the streams using the solved values
    streams = []
    s = 0

    # Fresh Feed
    bm_fresh = calc_stream("Ball Mill Fresh Feed", tph, feed_pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(bm_fresh)

    # Cyclone Underflow (Circulating Load)
    bm_uf = calc_stream("Cyclone U/F (Circulating Load)", uf_solids_tph, cyc_uf_pct_sol, sg, au, h_per_d=h,
                        is_recirculation=True, sort_order=(s := s + 1))
    streams.append(bm_uf)

    # Ball Mill Total Feed
    bm_total_feed_solids = tph + uf_solids_tph
    bm_total_feed_water = fresh_water_tph + uf_water_tph
    bm_total_pct_solids = (bm_total_feed_solids / (bm_total_feed_solids + bm_total_feed_water)) * 100
    bm_total = calc_stream("Ball Mill Total Feed", bm_total_feed_solids, bm_total_pct_solids, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(bm_total)

    # Water Additions
    gland = calc_water_only_stream("Ball Mill Gland Water", gland_water, h_per_d=h, sort_order=(s := s + 1))
    streams.append(gland)

    if dilution_water_tph > 0:
        dil = calc_water_only_stream("Ball Mill Dilution Water", dilution_water_tph, h_per_d=h, sort_order=(s := s + 1))
        streams.append(dil)

    pumpbox_addition = max(0.0, required_pumpbox_water)
    pumpbox_bleed = max(0.0, -required_pumpbox_water)
    pb_water = calc_water_only_stream(
        "Cyclone Feed Pump Box Water",
        pumpbox_addition,
        h_per_d=h,
        sort_order=(s := s + 1),
        extras=_source_extras("design_criteria_v2.HYDROCYCLONE", "upstream:Ball Mill Fresh Feed"),
    )
    streams.append(pb_water)
    _carry_add(carry, "process_water_demand_m3h", pumpbox_addition)

    # Ball Mill Discharge
    bm_disch_solids = bm_total_feed_solids
    bm_disch_water = bm_total_feed_water + gland_water + dilution_water_tph
    bm_disch_pct = (bm_disch_solids / (bm_disch_solids + bm_disch_water)) * 100
    bm_discharge = calc_stream("Ball Mill Discharge", bm_disch_solids, bm_disch_pct, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(bm_discharge)

    # Cyclone Feed
    cf_solids = bm_disch_solids
    cf_water = bm_disch_water + pumpbox_addition - pumpbox_bleed
    cf_pct = (cf_solids / (cf_solids + cf_water)) * 100
    cyc_feed = calc_stream("Cyclone Feed", cf_solids, cf_pct, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(cyc_feed)

    # Cyclone Overflow (Product)
    cyc_overflow = calc_stream("Cyclone O/F (Grinding Product)", tph, cyc_of_pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(cyc_overflow)

    bleed = None
    if pumpbox_bleed > 0:
        bleed = calc_water_only_stream("Cyclone Feed Water Bleed", pumpbox_bleed, h_per_d=h, sort_order=(s := s + 1))
        streams.append(bleed)

    # Balance Check (Node: Entire Grinding Circuit)
    # IN: Fresh Feed + Gland Water + Dilution Water + Pump Box Water
    # OUT: Cyclone O/F
    ins = [bm_fresh, gland]
    if dilution_water_tph > 0: ins.append(dil)
    ins.append(pb_water)
    outs = [cyc_overflow] + ([bleed] if bleed else [])

    streams.append(balance_check(ins, outs))

    # Carry forward product to next section
    carry["bm_product_tph"] = tph
    carry["bm_product_pct_sol"] = cyc_of_pct_sol
    carry["bm_product_au"] = au
    carry["grind_product_tph"] = tph
    carry["grind_pct_sol"] = cyc_of_pct_sol
    carry["grind_product_au"] = au
    carry["cyc_of_tph"] = tph
    carry["cyc_of_pct_sol"] = cyc_of_pct_sol

    return streams


# ---------------------------------------------------------------------------
# Concentrate chain → leach / CIL feed resolution
# ---------------------------------------------------------------------------

def _resolve_concentrate_feed(carry: dict, pp: dict) -> dict[str, Any]:
    """Resolve solids feed for leaching/CIL from upstream concentrate chain.

    Priority (latest step in the chain wins):
      1. Concentrate thickener underflow
      2. Regrind cyclone overflow (flotation conc or WOL secondary grind)
      3. Combined flotation concentrate (rougher + scavenger)
      4. Primary grinding product (cyclone O/F)
      5. Ball mill product / plant nominal feed
    """
    if "thick_conc_uf_tph" in carry:
        return {
            "source": "concentrate_thickener",
            "tph": float(carry["thick_conc_uf_tph"]),
            "au": float(carry.get("thick_conc_au", pp["gold_grade"])),
            "pct_sol": float(carry.get("thick_conc_uf_pct_sol", 55.0)),
        }
    if "regrind_product_tph" in carry:
        return {
            "source": "concentrate_regrind",
            "tph": float(carry["regrind_product_tph"]),
            "au": float(carry.get("regrind_product_au", pp["gold_grade"])),
            "pct_sol": float(carry.get("regrind_product_pct_sol", 45.0)),
        }
    if "flot_conc_tph" in carry:
        return {
            "source": "flotation",
            "tph": float(carry["flot_conc_tph"]),
            "au": float(carry.get("flot_conc_au", pp["gold_grade"])),
            "pct_sol": float(carry.get("flot_conc_pct_sol", 25.0)),
        }
    if "grind_product_tph" in carry:
        return {
            "source": "grinding",
            "tph": float(carry["grind_product_tph"]),
            "au": float(carry.get("grind_product_au", carry.get("au_gt", pp["gold_grade"]))),
            "pct_sol": float(carry.get("grind_pct_sol", 35.0)),
        }
    if "bm_product_tph" in carry:
        return {
            "source": "ball_mill",
            "tph": float(carry["bm_product_tph"]),
            "au": float(carry.get("bm_product_au", carry.get("au_gt", pp["gold_grade"]))),
            "pct_sol": float(carry.get("bm_product_pct_sol", 35.0)),
        }
    return {
        "source": "plant_feed",
        "tph": float(pp["target_tph"]),
        "au": float(carry.get("au_gt", pp["gold_grade"])),
        "pct_sol": 50.0,
    }


def _publish_leach_feed_carry(carry: dict, pp: dict) -> dict[str, Any]:
    """Sync canonical leach-feed keys for downstream sections and DC cascade."""
    feed = _resolve_concentrate_feed(carry, pp)
    carry["leach_feed_tph"] = feed["tph"]
    carry["leach_feed_au"] = feed["au"]
    carry["leach_feed_pct_sol"] = feed["pct_sol"]
    carry["leach_feed_source"] = feed["source"]
    carry["has_flotation"] = "flot_conc_tph" in carry
    return feed


def _feed_chain_extras(source: str) -> dict:
    return {
        "leach_feed_source": source,
        "source_refs": [f"mass_balance_engine:{source}"],
    }


def _gen_flotation(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Flotation circuit: conditioning, rougher, scavenger."""
    try:
        from .flotation import concentrate_grade
    except ImportError:
        from engines.flotation import concentrate_grade

    h = dc.get("plant_h_per_d", 22.1)
    tph = carry.get("grind_product_tph", carry.get("bm_product_tph", pp["target_tph"]))
    sg = pp["ore_sg"]
    au = carry.get("grind_product_au", carry.get("bm_product_au", carry.get("au_gt", pp["gold_grade"])))
    feed_pct_sol = carry.get("grind_pct_sol", carry.get("bm_product_pct_sol", 35.0))
    mass_pull_pct = dc.get("flot_mass_pull_pct", 8.0) / 100.0
    au_recovery = dc.get("flot_au_recovery_pct", 92.0) / 100.0
    scav_mass_pull_pct = dc.get("scav_mass_pull_pct", 3.0) / 100.0
    scav_au_recovery_remaining = dc.get("scav_au_recovery_pct", 30.0) / 100.0
    gland_water = dc.get("flot_gland_m3h", 0.66)
    trash_screen_water = dc.get("trash_screen_water_tph", 5.0)
    pax_tph = dc.get("pax_tph", 0.05)
    mibc_tph = dc.get("mibc_tph", 0.01)

    streams = []
    s = 0

    # Conditioning
    trash_water = calc_water_only_stream("Trash Screen Water", trash_screen_water, h_per_d=h,
                                         sort_order=(s := s + 1))
    streams.append(trash_water)

    pax = calc_water_only_stream("PAX Addition", pax_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(pax)

    mibc = calc_water_only_stream("MIBC Addition", mibc_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(mibc)

    # Rougher feed
    total_extra_water = trash_screen_water + pax_tph + mibc_tph
    feed_water = tph * (100 - feed_pct_sol) / feed_pct_sol if feed_pct_sol > 0 else 0
    total_water = feed_water + total_extra_water
    rougher_feed_pct_sol = (tph / max(tph + total_water, 1e-9)) * 100.0

    rougher_feed = calc_stream("Rougher Feed", tph, rougher_feed_pct_sol, sg, au, h_per_d=h,
                               sort_order=(s := s + 1))
    streams.append(rougher_feed)

    # Rougher concentrate (grade from recovery × mass pull — aligned with flotation engine)
    conc_solids = tph * mass_pull_pct
    mass_pull_pct_display = mass_pull_pct * 100.0
    au_in_conc = concentrate_grade(au, au_recovery, mass_pull_pct_display) if mass_pull_pct > 0 else au
    rougher_conc = calc_stream("Rougher Concentrate", conc_solids, 25.0, sg, au_in_conc, h_per_d=h,
                               sort_order=(s := s + 1))
    streams.append(rougher_conc)

    # Rougher tails = feed to scavenger
    rougher_tails_solids = tph - conc_solids
    au_in_tails = (au * tph - au_in_conc * conc_solids) / rougher_tails_solids if rougher_tails_solids > 0 else 0
    rougher_tails = calc_stream("Rougher Tails / Scavenger Feed", rougher_tails_solids, rougher_feed_pct_sol, sg,
                                max(0, au_in_tails), h_per_d=h, sort_order=(s := s + 1))
    streams.append(rougher_tails)

    # Scavenger concentrate
    scav_conc_solids = tph * scav_mass_pull_pct
    au_remaining = max(0, au_in_tails) * rougher_tails_solids
    au_scav_recovery_mass = au_remaining * scav_au_recovery_remaining
    au_scav_conc = au_scav_recovery_mass / scav_conc_solids if scav_conc_solids > 0 else 0
    scav_conc = calc_stream("Scavenger Concentrate", scav_conc_solids, 20.0, sg, au_scav_conc, h_per_d=h,
                            sort_order=(s := s + 1))
    streams.append(scav_conc)

    # Scavenger tails (to tailings)
    scav_tails_solids = rougher_tails_solids - scav_conc_solids
    au_scav_tails_mass = au_remaining - au_scav_recovery_mass
    au_scav_tails = au_scav_tails_mass / scav_tails_solids if scav_tails_solids > 0 else 0
    concentrate_water = rougher_conc["water_tph"] + scav_conc["water_tph"]
    scav_tails_water = max(0.0, rougher_feed["water_tph"] + gland_water - concentrate_water)
    scav_tails_pct_sol = _pct_solids_from_water(scav_tails_solids, scav_tails_water)
    scav_tails = calc_stream("Scavenger Tails (to Tailings)", scav_tails_solids, scav_tails_pct_sol, sg,
                             max(0, au_scav_tails), h_per_d=h, sort_order=(s := s + 1))
    streams.append(scav_tails)

    # Gland seal water per pump
    gland = calc_water_only_stream("Flotation Gland Seal Water", gland_water, h_per_d=h,
                                   sort_order=(s := s + 1))
    streams.append(gland)

    _carry_add(carry, "process_water_demand_m3h", trash_screen_water + pax_tph + mibc_tph + gland_water)

    # Balance: rougher_feed already includes conditioning water.
    ins = [rougher_feed, gland]
    outs = [rougher_conc, scav_conc, scav_tails]
    streams.append(balance_check(ins, outs))

    # Total concentrate = rougher + scavenger
    total_conc_solids = conc_solids + scav_conc_solids
    carry["flot_feed_tph"] = tph
    carry["flot_feed_pct_sol"] = rougher_feed_pct_sol
    carry["flot_conc_tph"] = total_conc_solids
    carry["flot_conc_pct_sol"] = 25.0  # approximate
    carry["flot_conc_au"] = ((au_in_conc * conc_solids) + (au_scav_conc * scav_conc_solids)) / total_conc_solids if total_conc_solids > 0 else 0
    carry["flot_tails_tph"] = scav_tails_solids
    carry["flot_tails_au"] = max(0, au_scav_tails)
    carry["flot_tails_pct_sol"] = scav_tails_pct_sol
    carry["flot_au_to_leach_g_h"] = (
        (au_in_conc * conc_solids) + (au_scav_conc * scav_conc_solids)
    )
    carry["flot_feed_au_g_h"] = au * tph
    _publish_leach_feed_carry(carry, pp)

    return streams


def _gen_concentrate_regrind(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Regrind circuit (IsaMill / Vertimill).
    If flotation is present: regrind the flotation concentrate.
    If no flotation (WOL circuit): regrind the full ball mill product (secondary grind).
    """
    h = dc.get("plant_h_per_d", 22.1)
    has_flotation = carry.get("flot_conc_tph") is not None
    if has_flotation:
        tph = carry["flot_conc_tph"]
        au = carry.get("flot_conc_au", pp["gold_grade"] * 10)
        feed_pct_sol = carry.get("flot_conc_pct_sol", 25.0)
    else:
        # WOL circuit: Vertimill grinds full plant feed (secondary grind after ball mill)
        tph = dc.get("mill_nominal_tph") or carry.get("bm_product_tph", pp["target_tph"])
        au = carry.get("bm_product_au", pp["gold_grade"])
        feed_pct_sol = carry.get("bm_product_pct_sol", carry.get("bm_cyc_of_pct_sol", 50.0))
    sg = pp["ore_sg"]
    recirc_cl = dc.get("regrind_recirc_pct", 200.0) / 100.0
    cyc_of_pct_sol = dc.get("regrind_cyc_of_pct_sol", 45.0)
    cyc_uf_pct_sol = dc.get("regrind_cyc_uf_pct_sol", 55.0)
    water_addition_tph = dc.get("regrind_water_tph", 10.0)
    gland_water = dc.get("regrind_gland_m3h", 0.66)

    streams = []
    s = 0

    pumpbox_feed = calc_stream("Regrind Pumpbox Feed", tph, feed_pct_sol, sg, au, h_per_d=h,
                               sort_order=(s := s + 1))
    streams.append(pumpbox_feed)

    water_add = calc_water_only_stream("Regrind Water Addition", water_addition_tph, h_per_d=h,
                                       sort_order=(s := s + 1))
    streams.append(water_add)
    _carry_add(carry, "process_water_demand_m3h", water_addition_tph)

    # Cyclone feed (includes recirc)
    cyc_total_solids = tph * (1.0 + recirc_cl)
    cyc_feed = calc_stream("Regrind Cyclone Feed", cyc_total_solids, 40.0, sg, au, h_per_d=h,
                           sort_order=(s := s + 1))
    streams.append(cyc_feed)

    target_of_water = tph * (100.0 - cyc_of_pct_sol) / cyc_of_pct_sol if cyc_of_pct_sol > 0 else 0.0
    net_water_in = pumpbox_feed["water_tph"] + water_addition_tph + gland_water
    water_bleed_tph = max(0.0, net_water_in - target_of_water)
    of_water = net_water_in - water_bleed_tph
    cyc_of = calc_stream("Regrind Cyclone O/F", tph, _pct_solids_from_water(tph, of_water), sg, au, h_per_d=h,
                         sort_order=(s := s + 1))
    streams.append(cyc_of)

    cyc_uf_solids = tph * recirc_cl
    cyc_uf = calc_stream("Regrind Cyclone U/F", cyc_uf_solids, cyc_uf_pct_sol, sg, au, h_per_d=h,
                         is_recirculation=True, sort_order=(s := s + 1))
    streams.append(cyc_uf)

    # Detect regrind equipment type from carry (set by main generator)
    regrind_type = carry.get("regrind_equipment", "IsaMill")
    mill_feed = calc_stream(f"{regrind_type} Feed", cyc_uf_solids, cyc_uf_pct_sol, sg, au, h_per_d=h,
                            sort_order=(s := s + 1))
    streams.append(mill_feed)

    mill_discharge = calc_stream(f"{regrind_type} Discharge", cyc_uf_solids, cyc_uf_pct_sol, sg, au, h_per_d=h,
                                 sort_order=(s := s + 1))
    streams.append(mill_discharge)

    gland = calc_water_only_stream("Regrind Gland Seal Water", gland_water, h_per_d=h, sort_order=(s := s + 1))
    streams.append(gland)
    _carry_add(carry, "process_water_demand_m3h", gland_water)

    bleed = None
    if water_bleed_tph > 0:
        bleed = calc_water_only_stream("Regrind Cyclone Water Bleed", water_bleed_tph, h_per_d=h, sort_order=(s := s + 1))
        streams.append(bleed)

    ins = [pumpbox_feed, water_add, gland]
    outs = [cyc_of] + ([bleed] if bleed else [])
    streams.append(balance_check(ins, outs))

    carry["regrind_product_tph"] = tph
    carry["regrind_product_pct_sol"] = cyc_of["slurry_pct_w"]
    carry["regrind_product_au"] = au
    _publish_leach_feed_carry(carry, pp)

    return streams


def _gen_concentrate_thickener(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Concentrate thickener section."""
    h = dc.get("plant_h_per_d", 22.1)
    tph = carry.get("regrind_product_tph", carry.get("flot_conc_tph", pp["target_tph"] * 0.08))
    sg = pp["ore_sg"]
    au = carry.get("regrind_product_au", carry.get("flot_conc_au", pp["gold_grade"] * 10))
    feed_pct_sol = carry.get("regrind_product_pct_sol", carry.get("flot_conc_pct_sol", 25.0))
    uf_pct_sol = dc.get("conc_thick_uf_pct_sol", 55.0)
    floc_tph = dc.get("floc_dosage_tph", 0.01)
    gland_water = dc.get("conc_thick_gland_m3h", 0.66)

    streams = []
    s = 0

    thick_feed = calc_stream("Conc. Thickener Feed", tph, feed_pct_sol, sg, au, h_per_d=h,
                             sort_order=(s := s + 1))
    streams.append(thick_feed)

    floc = calc_water_only_stream("Flocculant Addition", floc_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(floc)

    # Overflow — all water minus underflow water
    uf = calc_stream("Conc. Thickener U/F to Pre-aeration", tph, uf_pct_sol, sg, au, h_per_d=h,
                     sort_order=(s := s + 1))

    of_water = thick_feed["water_tph"] + floc_tph + gland_water - uf["water_tph"]
    overflow = calc_water_only_stream("Conc. Thickener O/F to Process Water", max(0, of_water), h_per_d=h,
                                      sort_order=(s := s + 1))
    # Insert overflow before underflow for display order
    streams.append(overflow)
    streams.append(uf)

    gland = calc_water_only_stream("Conc. Thickener Gland Seal Water", gland_water, h_per_d=h,
                                   sort_order=(s := s + 1))
    streams.append(gland)
    _carry_add(carry, "thickener_reclaim_m3h", max(0.0, of_water))

    ins = [thick_feed, floc, gland]
    outs = [overflow, uf]
    streams.append(balance_check(ins, outs))

    carry["thick_conc_uf_tph"] = tph
    carry["thick_conc_uf_pct_sol"] = uf_pct_sol
    carry["thick_conc_au"] = au
    _publish_leach_feed_carry(carry, pp)

    return streams


def _gen_leaching(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Pre-aeration + leach tanks (feed from concentrate chain via _resolve_concentrate_feed)."""
    h = dc.get("plant_h_per_d", 22.1)
    leach_feed = _publish_leach_feed_carry(carry, pp)
    tph = leach_feed["tph"]
    sg = pp["ore_sg"]
    au = leach_feed["au"]
    feed_pct_sol = leach_feed["pct_sol"]
    feed_extras = _feed_chain_extras(leach_feed["source"])
    nacn_kg_t = dc.get("nacn_consumption_kg_t", 2.5)
    cao_kg_t = dc.get("cao_consumption_kg_t", 1.5)
    leach_recovery = dc.get("leach_recovery_pct", 95.0) / 100.0

    streams = []
    s = 0

    # Pre-aeration feed
    preox_feed = calc_stream(
        "Pre-aeration Feed", tph, feed_pct_sol, sg, au, h_per_d=h,
        sort_order=(s := s + 1), extras=feed_extras,
    )
    streams.append(preox_feed)

    # NaCN addition (as slurry approximation)
    nacn_tph = (nacn_kg_t * tph) / 1000.0
    nacn = calc_water_only_stream("NaCN Addition", nacn_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(nacn)

    # CaO addition
    cao_tph = (cao_kg_t * tph) / 1000.0
    cao = calc_water_only_stream("CaO / Lime Addition", cao_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(cao)
    _carry_add(carry, "process_water_demand_m3h", nacn_tph + cao_tph)

    # Leach discharge
    leach_discharge_solids = tph
    total_reagent_water = nacn_tph + cao_tph
    leach_total_water = preox_feed["water_tph"] + total_reagent_water
    leach_pct_sol = (leach_discharge_solids / max(leach_discharge_solids + leach_total_water, 1e-9)) * 100.0
    au_leach_discharge = au * (1.0 - leach_recovery)

    leach_discharge = calc_stream("Leach Tank Discharge", leach_discharge_solids, leach_pct_sol, sg,
                                  au_leach_discharge, h_per_d=h, sort_order=(s := s + 1))
    streams.append(leach_discharge)

    ins = [preox_feed, nacn, cao]
    outs = [leach_discharge]
    streams.append(balance_check(ins, outs))

    carry["leach_discharge_tph"] = leach_discharge_solids
    carry["leach_discharge_pct_sol"] = leach_pct_sol
    carry["leach_discharge_au"] = au_leach_discharge
    # Au dissolved: au (g/t) × recovery (frac) × tph (t/h) = g/h of Au
    # Convert g/h → t/h: divide by 1e6
    carry["au_dissolved_tph"] = (au * leach_recovery * tph) / 1_000_000.0  # t/h of Au
    carry["leach_au_recovered_g_h"] = au * tph * leach_recovery
    carry["nacn_tph"] = nacn_tph
    carry["cao_tph"] = cao_tph

    return streams


def _gen_cip(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Carbon in Pulp section."""
    h = dc.get("plant_h_per_d", 22.1)
    tph = carry.get("leach_discharge_tph", pp["target_tph"] * 0.08)
    sg = pp["ore_sg"]
    au = carry.get("leach_discharge_au", 0.1)
    feed_pct_sol = carry.get("leach_discharge_pct_sol", 50.0)
    gland_water = dc.get("cip_gland_m3h", 1.2)
    cip_stages = int(dc.get("cip_stages", 6))

    streams = []
    s = 0

    cip_feed = calc_stream("CIP Feed", tph, feed_pct_sol, sg, au, h_per_d=h, sort_order=(s := s + 1))
    streams.append(cip_feed)

    # CIP discharge (same solids, slightly lower Au in solids)
    au_cip_discharge = au * 0.1  # ~90% Au extracted by carbon
    cip_water = cip_feed["water_tph"] + gland_water * cip_stages
    cip_discharge = calc_stream("CIP Discharge", tph, _pct_solids_from_water(tph, cip_water), sg, au_cip_discharge, h_per_d=h,
                                sort_order=(s := s + 1))
    streams.append(cip_discharge)

    gland = calc_water_only_stream(f"CIP Gland Seal Water ({cip_stages} stages)", gland_water * cip_stages,
                                   h_per_d=h, sort_order=(s := s + 1))
    streams.append(gland)
    _carry_add(carry, "process_water_demand_m3h", gland_water * cip_stages)

    ins = [cip_feed, gland]
    outs = [cip_discharge]
    streams.append(balance_check(ins, outs))

    carry["cip_discharge_tph"] = tph
    carry["cip_discharge_pct_sol"] = cip_discharge["slurry_pct_w"]
    carry["cip_discharge_au"] = au_cip_discharge

    return streams


def _gen_cil(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Carbon in Leach (alternative to separate leach + CIP)."""
    h = dc.get("plant_h_per_d", 22.1)
    leach_feed = _publish_leach_feed_carry(carry, pp)
    tph = leach_feed["tph"]
    sg = pp["ore_sg"]
    au = leach_feed["au"]
    feed_pct_sol = leach_feed["pct_sol"]
    feed_extras = _feed_chain_extras(leach_feed["source"])
    nacn_kg_t = dc.get("nacn_consumption_kg_t", 2.5)
    cao_kg_t = dc.get("cao_consumption_kg_t", 1.5)
    leach_recovery = dc.get("cil_recovery_pct", 93.0) / 100.0
    gland_water = dc.get("cil_gland_m3h", 1.2)

    streams = []
    s = 0

    cil_feed = calc_stream(
        "CIL Feed", tph, feed_pct_sol, sg, au, h_per_d=h,
        sort_order=(s := s + 1), extras=feed_extras,
    )
    streams.append(cil_feed)

    nacn_tph = (nacn_kg_t * tph) / 1000.0
    nacn = calc_water_only_stream("NaCN Addition", nacn_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(nacn)

    cao_tph = (cao_kg_t * tph) / 1000.0
    cao = calc_water_only_stream("CaO / Lime Addition", cao_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(cao)

    au_discharge = au * (1.0 - leach_recovery)
    cil_water = cil_feed["water_tph"] + nacn_tph + cao_tph + gland_water
    cil_discharge = calc_stream("CIL Discharge", tph, _pct_solids_from_water(tph, cil_water), sg, au_discharge, h_per_d=h,
                                sort_order=(s := s + 1))
    streams.append(cil_discharge)

    gland = calc_water_only_stream("CIL Gland Seal Water", gland_water, h_per_d=h, sort_order=(s := s + 1))
    streams.append(gland)

    ins = [cil_feed, nacn, cao, gland]
    outs = [cil_discharge]
    streams.append(balance_check(ins, outs))

    carry["cip_discharge_tph"] = tph
    _carry_add(carry, "process_water_demand_m3h", nacn_tph + cao_tph + gland_water)
    carry["cip_discharge_pct_sol"] = cil_discharge["slurry_pct_w"]
    carry["cip_discharge_au"] = au_discharge
    carry["leach_au_recovered_g_h"] = au * tph * leach_recovery
    carry["nacn_tph"] = nacn_tph
    carry["cao_tph"] = cao_tph

    return streams


def _gen_detox(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Cyanide destruction (INCO / Caro's acid) + final tailings."""
    h = dc.get("plant_h_per_d", 22.1)
    tph = carry.get("cip_discharge_tph", carry.get("flot_tails_tph", pp["target_tph"] * 0.9))
    sg = pp["ore_sg"]
    au = carry.get("cip_discharge_au", 0.05)
    feed_pct_sol = carry.get("cip_discharge_pct_sol", 50.0)
    cuso4_kg_t = dc.get("cuso4_dosage_kg_t", 0.5)
    cao_kg_t = dc.get("detox_cao_kg_t", 1.0)
    gland_water = dc.get("detox_gland_m3h", 0.66)

    streams = []
    s = 0

    detox_feed = calc_stream("CN Destruction Feed", tph, feed_pct_sol, sg, au, h_per_d=h,
                             sort_order=(s := s + 1))
    streams.append(detox_feed)

    cuso4_tph = (cuso4_kg_t * tph) / 1000.0
    cuso4 = calc_water_only_stream("CuSO4 Addition", cuso4_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(cuso4)

    cao_tph = (cao_kg_t * tph) / 1000.0
    cao = calc_water_only_stream("Lime Addition (Detox)", cao_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(cao)

    # Discharge
    detox_total_water = detox_feed["water_tph"] + cuso4_tph + cao_tph + gland_water
    detox_pct_sol = (tph / max(tph + detox_total_water, 1e-9)) * 100.0
    detox_discharge = calc_stream("CN Destruction Discharge", tph, detox_pct_sol, sg, au, h_per_d=h,
                                  sort_order=(s := s + 1))
    streams.append(detox_discharge)

    gland = calc_water_only_stream("Detox Gland Seal Water", gland_water, h_per_d=h, sort_order=(s := s + 1))
    streams.append(gland)
    _carry_add(carry, "process_water_demand_m3h", cuso4_tph + cao_tph + gland_water)

    ins = [detox_feed, cuso4, cao, gland]
    outs = [detox_discharge]
    streams.append(balance_check(ins, outs))

    carry["detox_discharge_tph"] = tph
    carry["detox_discharge_pct_sol"] = detox_pct_sol
    carry["detox_discharge_au"] = au
    carry["cuso4_tph"] = cuso4_tph

    return streams


def _gen_tailings_thickener(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Final tailings thickener."""
    h = dc.get("plant_h_per_d", 22.1)
    # Combine CIP/CIL tails + scav tails if both present
    tph_detox = carry.get("detox_discharge_tph", 0)
    pct_detox = carry.get("detox_discharge_pct_sol", 50.0)
    tph_scav = carry.get("flot_tails_tph", 0)
    pct_scav = carry.get("flot_tails_pct_sol", 35.0)

    if tph_detox > 0 and tph_scav > 0:
        tph = tph_detox + tph_scav
        # Weighted average % solids from combined sources
        feed_pct_sol = (tph_detox * pct_detox + tph_scav * pct_scav) / max(tph, 1e-9)
    elif tph_scav > 0:
        tph = tph_scav
        feed_pct_sol = pct_scav
    elif tph_detox > 0:
        tph = tph_detox
        feed_pct_sol = pct_detox
    else:
        tph = pp["target_tph"] * 0.9
        feed_pct_sol = dc.get("tails_thick_feed_pct_sol", 35.0)

    sg = pp["ore_sg"]
    # Weighted average gold grade from combined tails
    au_detox = carry.get("detox_discharge_au", 0.05)
    au_scav = carry.get("flot_tails_au", 0.05)
    if tph_detox > 0 and tph_scav > 0:
        au = (tph_detox * au_detox + tph_scav * au_scav) / max(tph, 1e-9)
    else:
        au = au_detox if tph_detox > 0 else au_scav
    uf_pct_sol = dc.get("tails_thick_uf_pct_sol", 60.0)
    floc_tph = dc.get("tails_floc_tph", 0.02)
    gland_water = dc.get("tails_thick_gland_m3h", 0.66)

    streams = []
    s = 0

    thick_feed = calc_stream("Tailings Thickener Feed", tph, feed_pct_sol, sg, au, h_per_d=h,
                             sort_order=(s := s + 1))
    streams.append(thick_feed)

    floc = calc_water_only_stream("Flocculant Addition (Tailings)", floc_tph, h_per_d=h, sort_order=(s := s + 1))
    streams.append(floc)

    uf = calc_stream("Tailings Thickener U/F to TSF", tph, uf_pct_sol, sg, au, h_per_d=h,
                     sort_order=(s := s + 1))

    of_water = thick_feed["water_tph"] + floc_tph + gland_water - uf["water_tph"]
    overflow = calc_water_only_stream("Tailings Thickener O/F (Reclaim)", max(0, of_water), h_per_d=h,
                                      sort_order=(s := s + 1))
    streams.append(overflow)
    streams.append(uf)

    gland = calc_water_only_stream("Tailings Thickener Gland Seal Water", gland_water, h_per_d=h,
                                   sort_order=(s := s + 1))
    streams.append(gland)
    _carry_add(carry, "thickener_reclaim_m3h", max(0.0, of_water))
    _carry_add(carry, "tailings_entrained_water_m3h", uf["water_tph"])

    ins = [thick_feed, floc, gland]
    outs = [overflow, uf]
    streams.append(balance_check(ins, outs))

    carry["final_tails_tph"] = tph
    carry["final_tails_uf_pct_sol"] = uf_pct_sol

    return streams


def _gen_adr(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """ADR: Elution, Electrowinning, Smelting (variable h/d)."""
    streams = []
    s = 0

    # Elution — typically 0.5 to 8 h/d batch
    h_elution = dc.get("elution_h_per_d", 8.0)
    elution_flow_m3h = dc.get("elution_flow_m3h", 5.0)
    elution_water = calc_water_only_stream("Elution Strip Solution", elution_flow_m3h, h_per_d=h_elution,
                                           sort_order=(s := s + 1))
    streams.append(elution_water)

    eluate = calc_water_only_stream("Pregnant Eluate to EW", elution_flow_m3h, h_per_d=h_elution,
                                    sort_order=(s := s + 1))
    streams.append(eluate)

    # Electrowinning — 24 h/d
    h_ew = 24.0
    ew_flow = dc.get("ew_flow_m3h", 3.0)
    ew_feed = calc_water_only_stream("EW Cell Feed", ew_flow, h_per_d=h_ew, sort_order=(s := s + 1))
    streams.append(ew_feed)

    ew_barren = calc_water_only_stream("EW Barren Return", ew_flow, h_per_d=h_ew, sort_order=(s := s + 1))
    streams.append(ew_barren)

    # Smelting — batch, ~0.5 h/d
    h_smelt = dc.get("smelt_h_per_d", 0.5)
    au_dissolved_tph = carry.get("au_dissolved_tph", 0.005)
    # Gold dore production (approximate)
    dore_stream = {
        "stream_name": "Gold Dore Production",
        "hours_per_day": h_smelt,
        "solids_tpd": round(au_dissolved_tph * 22.1 * 1000, 3),  # grams/day
        "solids_tph": round(au_dissolved_tph * 1000, 4),
        "solids_m3h": 0.0,
        "solids_sg": 19.3,  # gold SG
        "water_tpd": 0.0,
        "water_tph": 0.0,
        "water_m3h": 0.0,
        "water_sg": 0.0,
        "slurry_tpd": round(au_dissolved_tph * 22.1 * 1000, 3),
        "slurry_tph": round(au_dissolved_tph * 1000, 4),
        "slurry_m3h": 0.0,
        "slurry_pct_w": 100.0,
        "slurry_sg": 19.3,
        "au_gt": None,
        "s_pct": None,
        "is_balance_check": False,
        "is_recirculation": False,
        "sort_order": (s := s + 1),
    }
    streams.append(dore_stream)

    # ADR balance check is simplified (liquid circuits)
    ins = [elution_water]
    outs = [eluate]
    streams.append(balance_check(ins, outs))

    return streams


def _gen_water_services(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Water services summary — fresh water and process water balance.

    Matches Excel sheet 14 (WATER_BALANCE) structure:
    - Inputs: ROM moisture, make-up water (grinding, leach, reagents), reclaim, TSF decant
    - Outputs: tailings entrained water, evaporation, process losses
    - Fresh water make-up = total demand - reclaim - TSF decant
    """
    h = dc.get("plant_h_per_d", 22.1)
    streams = []
    s = 0

    # Actual process water demand accumulated from all section generators
    process_water_demand = float(carry.get("process_water_demand_m3h", 0.0) or 0.0)
    # Use DC value as minimum floor (in case carry is low due to missing sections)
    process_water_tph = max(process_water_demand, dc.get("process_water_tph", 0.0))

    # Reclaim from thickeners (concentrate + tailings O/F)
    reclaim_tph = float(carry.get("thickener_reclaim_m3h", 0.0) or 0.0)
    if reclaim_tph <= 0:
        reclaim_tph = dc.get("thickener_reclaim_tph", 0.0)

    # TSF decant return water (from DC or estimate as 15% of tailings water)
    tailings_entrained = float(carry.get("tailings_entrained_water_m3h", 0.0) or 0.0)
    tsf_decant_tph = dc.get("tsf_decant_tph", 0.0)
    if tsf_decant_tph <= 0 and tailings_entrained > 0:
        tsf_decant_tph = tailings_entrained * 0.15  # ~15% of tailings water returns as decant

    # Evaporation: from DC or estimate (1.5% of process water + 0.5% of tailings)
    evap_loss_tph = dc.get("evap_loss_tph", 0.0)
    if evap_loss_tph <= 0:
        evap_loss_tph = process_water_tph * 0.015 + tailings_entrained * 0.05

    # ROM moisture contribution (4% of feed solids)
    rom_moisture_tph = pp["target_tph"] * 0.04

    # Total losses = evaporation + tailings entrained water
    losses_tph = evap_loss_tph + tailings_entrained

    # Fresh water make-up = demand + losses - reclaim - decant - ROM moisture
    fresh_water_tph = max(0.0, process_water_tph + losses_tph - reclaim_tph - tsf_decant_tph - rom_moisture_tph)

    streams.append(calc_water_only_stream("Fresh Water Make-up", fresh_water_tph, h_per_d=h,
                                          sort_order=(s := s + 1),
                                          extras=_source_extras("calculated:process_water_demand", "calculated:reclaim", "design_criteria_v2.BASSIN_EAU")))
    streams.append(calc_water_only_stream("Process Water Supply", process_water_tph, h_per_d=h,
                                          sort_order=(s := s + 1)))
    streams.append(calc_water_only_stream("Thickener O/F Reclaim", reclaim_tph, h_per_d=h,
                                          sort_order=(s := s + 1)))
    streams.append(calc_water_only_stream("TSF Decant Return", tsf_decant_tph, h_per_d=h,
                                          sort_order=(s := s + 1)))
    streams.append(calc_water_only_stream("Evaporation / Losses + Tailings Entrained Water",
                                          losses_tph, h_per_d=h,
                                          sort_order=(s := s + 1)))

    ins = [streams[0], streams[2], streams[3]]  # fresh + reclaim + decant
    outs = [streams[1], streams[4]]              # supply + losses
    streams.append(balance_check(ins, outs))

    return streams


def _gen_gravity_recovery(pp: dict, dc: dict, carry: dict) -> list[dict]:
    """Gravity recovery circuit: Knelson / Falcon concentrator.

    Treats a slip-stream of the cyclone overflow (typically 20-40% of flow).
    Produces a gravity concentrate (GRG) and a tails stream that rejoins
    the main circuit ahead of leaching.

    Uses the shared gravity_model (GRG × Knelson × slip × ILR) — aligned with
    ore_to_bullion and design criteria GRAVITE_KNELSON items 02/04/06/17.
    """
    h = dc.get("plant_h_per_d", 22.1)
    sg = pp["ore_sg"]
    au = carry.get("bm_product_au", carry.get("au_gt", pp["gold_grade"]))

    gp = resolve_gravity_params(dc)
    slip_pct = gp.slip_frac
    total_tph = carry.get("bm_product_tph", pp["target_tph"])
    feed_pct_sol = carry.get("bm_product_pct_sol", 35.0)

    gravity_feed_tph = total_tph * slip_pct
    plant_recovery_pct = plant_gravity_recovery_pct(gp)
    mass_pull = gp.mass_pull_frac

    conc_tph = gravity_feed_tph * mass_pull
    au_conc = gravity_concentrate_grade_g_t(au, gp)
    au_tails = blended_head_grade_g_t(au, plant_recovery_pct)

    streams = []
    s = 0

    gravity_feed = calc_stream(
        "Gravity Feed (Cyclone O/F Slip-stream)", gravity_feed_tph, feed_pct_sol, sg, au,
        h_per_d=h, sort_order=(s := s + 1)
    )
    streams.append(gravity_feed)

    # Gland / flush water for concentrator
    flush_water = dc.get("gravity_flush_m3h", 2.0)
    flush = calc_water_only_stream("Knelson Flush Water", flush_water, h_per_d=h, sort_order=(s := s + 1))
    streams.append(flush)
    _carry_add(carry, "process_water_demand_m3h", flush_water)

    # Gravity concentrate (goes to intensive leach or smelt)
    gravity_conc = calc_stream(
        "Gravity Concentrate (GRG)", conc_tph, 70.0, sg, au_conc,
        h_per_d=h, sort_order=(s := s + 1)
    )
    streams.append(gravity_conc)

    # Gravity tails (rejoins main circuit)
    tails_tph = gravity_feed_tph - conc_tph
    tails_water = gravity_feed["water_tph"] + flush_water - gravity_conc["water_tph"]
    tails_pct_sol = _pct_solids_from_water(tails_tph, max(0.0, tails_water))
    gravity_tails = calc_stream(
        "Gravity Tails (to Leach Circuit)", tails_tph, tails_pct_sol, sg, au_tails,
        h_per_d=h, sort_order=(s := s + 1)
    )
    streams.append(gravity_tails)

    ins = [gravity_feed, flush]
    outs = [gravity_conc, gravity_tails]
    streams.append(balance_check(ins, outs))

    # Update carry: gravity tails rejoin the leach feed
    # The main bypass (1 - slip_pct) is already in bm_product; we blend here
    bypass_tph = total_tph * (1 - slip_pct)
    blended_tph = bypass_tph + tails_tph
    blended_au = (
        (bypass_tph * au + tails_tph * au_tails) / blended_tph
        if blended_tph > 0 else au_tails
    )
    carry["grind_product_tph"] = blended_tph
    carry["grind_product_au"] = blended_au
    carry["bm_product_tph"] = blended_tph
    carry["bm_product_au"] = blended_au
    carry["gravity_conc_tph"] = conc_tph
    carry["gravity_conc_au"] = au_conc
    plant_head_au = float(pp["gold_grade"])
    carry["gravity_au_recovered_g_h"] = (
        total_tph * plant_head_au * (plant_recovery_pct / 100.0)
    )

    return streams


# Dispatch table
_GENERATORS = {
    "crushing": _gen_crushing,
    "comminution_hpgr": _gen_comminution_hpgr,
    "comminution_sag": _gen_comminution_sag,
    "comminution_ball": _gen_comminution_ball,
    "gravity_recovery": _gen_gravity_recovery,
    "flotation": _gen_flotation,
    "concentrate_regrind": _gen_concentrate_regrind,
    "concentrate_thickener": _gen_concentrate_thickener,
    "leaching": _gen_leaching,
    "cip": _gen_cip,
    "cil": _gen_cil,
    "detox": _gen_detox,
    "tailings_thickener": _gen_tailings_thickener,
    "adr": _gen_adr,
    "water_services": _gen_water_services,
}


# ============================================================================
# Design criteria → parameter dict builder
# ============================================================================

def _build_dc_params(template_id: str, enabled_ops: set[str], cursor) -> dict:
    """Read design criteria and build a flat dict of parameters for generators.

    Uses multiple ILIKE patterns per parameter to match the actual DC item names,
    which may differ from the canonical names (e.g. "Circulating load" vs
    "recirculating load", "Feed pulp density" vs "feed % solids").
    """
    try:
        return _build_dc_params_impl(template_id, enabled_ops, cursor)
    except Exception as e:
        logger.error("_build_dc_params failed for template_id=%s: %s", template_id, e)
        return {}


def _build_dc_params_impl(template_id: str, enabled_ops: set[str], cursor) -> dict:
    """Internal implementation of _build_dc_params."""
    dc: dict[str, float] = {}

    def _dc(op_code: str, *patterns: str, default: float = 0.0) -> float:
        """Try multiple item patterns until one matches."""
        for pat in patterns:
            val = _get_dc(template_id, op_code, pat, cursor, None)
            if val is not None:
                return val
        return default

    def _dc_nom(op_code: str, *patterns: str, default: float = 0.0) -> float:
        """Throughput from DC nominal column (falls back to design if nominal unset)."""
        for pat in patterns:
            val = _get_dc_nominal(template_id, op_code, pat, cursor, None)
            if val is not None:
                return val
        return default

    # ── Section-specific hours ──────────────────────────────────────────────
    dc["plant_h_per_d"] = _dc("BALL_MILL", "hours per day", "operating hours", default=22.1)
    dc["crush_hours_per_day"] = _dc("GIRATOIRE", "hours per day", "operating hours", default=18.0)
    dc["hpgr_h_per_d"] = _dc("HPGR", "hours per day", default=19.0)

    # ── Nominal solids throughput (t/h) — aligned with Critères de conception ─
    dc["crush_nominal_tph"] = _dc_nom(
        "GIRATOIRE",
        "débit design alimentation",
        "debit design alimentation",
        "processing rate",
        "débit alimentation",
        "debit alimentation",
        default=0.0,
    )
    dc["hpgr_nominal_tph"] = _dc_nom(
        "HPGR",
        "débit fresh feed",
        "debit fresh feed",
        "fresh feed",
        "circuit processing rate",
        default=0.0,
    )
    dc["mill_nominal_tph"] = _dc_nom(
        "BALL_MILL",
        "débit alimentation",
        "debit alimentation",
        "fresh feed",
        "circuit processing rate",
        "Design throughput",
        "processing rate",
        default=0.0,
    )
    if dc["mill_nominal_tph"] <= 0:
        dc["mill_nominal_tph"] = _dc_nom(
            "HYDROCYCLONE",
            "débit fresh feed broyage",
            "debit fresh feed broyage",
            "fresh feed",
            "debit fresh feed",
            default=0.0,
        )
    dc["sag_nominal_tph"] = _dc_nom(
        "SAG_MILL",
        "processing rate",
        "débit alimentation",
        "debit alimentation",
        "fresh feed",
        default=0.0,
    )

    # Design throughput (equipment sizing) — kept for reference / legacy callers
    dc["mill_design_tph"] = _dc("BALL_MILL", "Débit alimentation", "Design throughput", "processing rate", default=0.0)
    if dc["mill_design_tph"] <= 0:
        dc["mill_design_tph"] = _dc("HYDROCYCLONE", "Débit fresh feed broyage", "fresh feed", default=0.0)
    dc["crush_design_tph"] = _dc("GIRATOIRE", "Débit design alimentation", "Design throughput", "processing rate", default=0.0)
    dc["plant_design_tph"] = dc["mill_design_tph"] or _dc("HPGR", "Débit fresh feed", "fresh feed", default=0.0)

    # ── Crushing ────────────────────────────────────────────────────────────
    dc["crush_pct_solids"] = _dc("GIRATOIRE", "humidité", "% solides", "moisture", default=96.0)
    dc["cone_recirc_pct"] = _dc("CONE", "recirculating", "circulating load", default=120.0)

    # ── HPGR ────────────────────────────────────────────────────────────────
    dc["hpgr_feed_pct_solids"] = _dc("HPGR", "feed % solid", "feed density", "feed pulp", default=92.0)
    dc["hpgr_recirc_pct"] = _dc("HPGR", "recirculating", "circulating load", "recycle ratio", default=25.0)

    # ── SAG mill ────────────────────────────────────────────────────────────
    dc["sag_feed_pct_solids"] = _dc("SAG_MILL", "feed % solid", "feed density", "feed pulp", default=75.0)
    dc["sag_gland_m3h"] = _dc("SAG_MILL", "gland seal", "gland water", default=1.2)

    # ── Ball mill ───────────────────────────────────────────────────────────
    dc["bm_feed_pct_solids"] = _dc("BALL_MILL", "feed % solid", "feed density", "feed pulp", default=70.0)
    dc["bm_recirc_cl_pct"] = _dc("HYDROCYCLONE", "Charge circulante", "recirculating", "circulating load", default=300.0)
    dc["cyc_of_pct_solids"] = _dc("HYDROCYCLONE", "overflow % solid", "o/f density", "O/F", default=50.0)
    dc["cyc_uf_pct_solids"] = _dc("HYDROCYCLONE", "underflow % solid", "u/f density", "U/F", default=75.0)
    dc["bm_gland_m3h"] = _dc("BALL_MILL", "gland seal", "gland water", default=1.2)
    dc["bm_dilution_water_tph"] = _dc("BALL_MILL", "dilution water", default=0.0)

    # ── Gravity ─────────────────────────────────────────────────────────────
    gravity_op = next(
        (op for op in ("GRAVITE_KNELSON", "GRAVITE_FALCON", "GRAVITE_GEMENI") if op in enabled_ops),
        "GRAVITE_KNELSON",
    )
    dc["gravity_slip_pct"] = _dc(
        gravity_op,
        "cyclone détourné",
        "détourné gravimétrie",
        "UF cyclone",
        "% UF cyclone",
        default=30.0,
    )
    dc["grg_pct"] = _dc(
        gravity_op,
        "GRG dans minerai",
        "GRG",
        "gravity recoverable",
        default=35.0,
    )
    dc["gravity_knelson_recovery_pct"] = _dc(
        gravity_op,
        "Récupération unitaire Knelson",
        "unitaire Knelson",
        default=50.0,
    )
    dc["gravity_ilr_recovery_pct"] = _dc(
        gravity_op,
        "Récupération Au sur conc",
        "ILR",
        "lixiv. forte",
        default=95.0,
    )
    dc["gravity_mass_pull_pct"] = _dc(gravity_op, "mass pull", default=0.2)
    dc["gravity_flush_m3h"] = _dc(gravity_op, "flush", "gland", default=2.0)

    # ── Flotation ───────────────────────────────────────────────────────────
    dc["flot_mass_pull_pct"] = _dc("FLOTATION_ROUGHER", "mass pull", default=8.0)
    dc["flot_au_recovery_pct"] = _dc("FLOTATION_ROUGHER", "recovery Au", "Au recovery", default=92.0)
    dc["scav_mass_pull_pct"] = _dc("FLOTATION_SCAVENGER", "mass pull", default=3.0)
    dc["scav_au_recovery_pct"] = _dc("FLOTATION_SCAVENGER", "recovery Au", "Au recovery", default=30.0)
    dc["flot_gland_m3h"] = _dc("FLOTATION_ROUGHER", "gland", default=0.66)
    dc["trash_screen_water_tph"] = _dc("FLOTATION_ROUGHER", "trash screen", default=5.0)
    dc["pax_tph"] = _dc("REACTIF_PAX", "consumption", "dosage", default=0.05)
    dc["mibc_tph"] = _dc("REACTIF_MIBC", "consumption", "dosage", default=0.01)

    # ── Regrind ─────────────────────────────────────────────────────────────
    regrind_op = (
        "VERTIMILL_REGRIND" if "VERTIMILL_REGRIND" in enabled_ops
        else "SMD" if "SMD" in enabled_ops
        else "ISAMILL"
    )
    dc["regrind_recirc_pct"] = _dc(regrind_op, "recirculating", "circulating load", default=200.0)
    dc["regrind_cyc_of_pct_sol"] = _dc(regrind_op, "overflow", "O/F", default=45.0)
    dc["regrind_cyc_uf_pct_sol"] = _dc(regrind_op, "underflow", "U/F", default=60.0)
    dc["regrind_water_tph"] = _dc(regrind_op, "water addition", "dilution", default=0.0)
    dc["regrind_gland_m3h"] = _dc(regrind_op, "gland", default=0.66)

    # ── Concentrate thickener ───────────────────────────────────────────────
    dc["conc_thick_uf_pct_sol"] = _dc("EPAISSISSEUR_CONC", "u/f density", "underflow", default=55.0)
    dc["floc_dosage_tph"] = _dc("EPAISSISSEUR_CONC", "flocculant", default=0.01)
    dc["conc_thick_gland_m3h"] = _dc("EPAISSISSEUR_CONC", "gland", default=0.66)

    # ── Leaching ────────────────────────────────────────────────────────────
    dc["nacn_consumption_kg_t"] = _dc("LEACH_CUVES", "NaCN", "cyanide", "nacn dosage", default=0.5)  # industry default 0.5 kg/t (WGC 2013)
    dc["cao_consumption_kg_t"] = _dc("LEACH_CUVES", "CaO", "lime dosage", "lime consumption", default=1.5)
    dc["leach_recovery_pct"] = _dc("LEACH_CUVES", "recovery Au", "Au recovery", default=95.0)

    # ── CIP ─────────────────────────────────────────────────────────────────
    dc["cip_gland_m3h"] = _dc("CIP", "gland", default=1.2)
    dc["cip_stages"] = _dc("CIP", "number of tank", "stages", "tanks", default=6.0)
    dc["cip_recovery_pct"] = _dc("CIP", "recovery Au", "Au recovery", default=97.0)
    dc["cip_pct_solids"] = _dc("CIP", "% solid", "feed % solid", default=50.0)
    dc["cip_carbon_conc_gl"] = _dc("CIP", "carbon concentration", default=20.0)
    dc["cip_residence_h"] = _dc("CIP", "residence time", default=8.0)

    # ── CIL ─────────────────────────────────────────────────────────────────
    dc["cil_recovery_pct"] = _dc("CIL", "recovery Au", "Au recovery", default=93.0)
    dc["cil_gland_m3h"] = _dc("CIL", "gland", default=1.2)

    # ── Detox ───────────────────────────────────────────────────────────────
    dc["cuso4_dosage_kg_t"] = _dc("DETOX_INCO", "CuSO4", "copper sulphate", default=0.5)
    dc["detox_cao_kg_t"] = _dc("DETOX_INCO", "lime dosage", "CaO", default=1.0)
    dc["detox_gland_m3h"] = _dc("DETOX_INCO", "gland", default=0.66)
    dc["detox_so2_ratio"] = _dc("DETOX_INCO", "SO2 dosage", "SO2", default=4.5)
    dc["detox_residence_h"] = _dc("DETOX_INCO", "residence time", default=1.5)

    # ── Tailings thickener ──────────────────────────────────────────────────
    dc["tails_thick_feed_pct_sol"] = _dc("EPAISSISSEUR", "feed density", "feed % solid", default=35.0)
    dc["tails_thick_uf_pct_sol"] = _dc("EPAISSISSEUR", "u/f density", "underflow", default=60.0)
    dc["tails_floc_tph"] = _dc("EPAISSISSEUR", "flocculant", default=0.02)
    dc["tails_thick_gland_m3h"] = _dc("EPAISSISSEUR", "gland", default=0.66)

    # ── ADR ──────────────────────────────────────────────────────────────────
    dc["elution_h_per_d"] = _dc("ELUTION_AARL", "hours per day", "operating hours", default=8.0)
    if dc["elution_h_per_d"] == 8.0:
        dc["elution_h_per_d"] = _dc("ELUTION_ZADRA", "hours per day", "operating hours", default=8.0)
    dc["elution_flow_m3h"] = _dc("ELUTION_AARL", "flow rate", default=5.0)
    dc["ew_flow_m3h"] = _dc("ELECTROWINNING", "flow rate", default=3.0)
    dc["smelt_h_per_d"] = _dc("FONDERIE", "hours per day", default=0.5)

    # ── Water services ──────────────────────────────────────────────────────
    dc["fresh_water_tph"] = _dc("BASSIN_EAU", "fresh water", default=150.0)
    dc["process_water_tph"] = _dc("BASSIN_EAU", "process water", default=500.0)
    dc["thickener_reclaim_tph"] = 300.0
    dc["tsf_decant_tph"] = 100.0
    dc["evap_loss_tph"] = 50.0

    return dc


# ============================================================================
# DB insertion
# ============================================================================

def _delete_existing(project_id: str, cursor):
    """Delete existing mass balance data for regeneration."""
    cursor.execute(
        "DELETE FROM mass_balance_streams_v2 WHERE project_id = %s",
        (project_id,),
    )
    cursor.execute(
        "DELETE FROM mass_balance_sections_v2 WHERE project_id = %s",
        (project_id,),
    )


def _insert_section(project_id: str, template_id: str, section_name: str,
                     op_code: str | None, sort_order: int, cursor) -> str:
    """Insert a section row and return its UUID."""
    section_id = str(uuid.uuid4())
    cursor.execute(
        "INSERT INTO mass_balance_sections_v2 "
        "(id, project_id, template_id, section_name, op_code, sort_order) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (section_id, project_id, template_id, section_name, op_code, sort_order),
    )
    return section_id


def _insert_stream(section_id: str, project_id: str, stream: dict, cursor):
    """Insert a single stream row."""
    cursor.execute(
        "INSERT INTO mass_balance_streams_v2 "
        "(section_id, project_id, stream_name, hours_per_day, "
        " solids_tpd, solids_tph, solids_m3h, solids_sg, "
        " water_tpd, water_tph, water_m3h, water_sg, "
        " slurry_tpd, slurry_tph, slurry_m3h, slurry_pct_w, slurry_sg, "
        " au_gt, s_pct, is_balance_check, is_recirculation, "
        " extras, source, sort_order) "
        "VALUES (%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s, "
        "        %s,%s,%s,%s, %s,%s,%s)",
        (
            section_id, project_id, stream["stream_name"], stream["hours_per_day"],
            stream["solids_tpd"], stream["solids_tph"], stream["solids_m3h"], stream["solids_sg"],
            stream["water_tpd"], stream["water_tph"], stream["water_m3h"], stream["water_sg"],
            stream["slurry_tpd"], stream["slurry_tph"], stream["slurry_m3h"],
            stream["slurry_pct_w"], stream["slurry_sg"],
            stream.get("au_gt"), stream.get("s_pct"),
            stream.get("is_balance_check", False), stream.get("is_recirculation", False),
            json.dumps(stream.get("extras") or {}),
            "calculated", stream.get("sort_order", 0),
        ),
    )


# ============================================================================
# Main entry point
# ============================================================================

def generate_mass_balance(project_id: str, template_id: str, cursor) -> dict:
    """Generate a complete circuit-by-circuit mass balance.

    Steps:
        1. Read project parameters (target_tph, gold_grade_g_t, availability_pct)
        2. Read design criteria v2 for the template
        3. Read LIMS averages (BWi, recovery, grade, density)
        4. For each circuit section, generate streams with full mass/water/slurry balance
        5. Insert sections and streams into mass_balance_sections_v2 / mass_balance_streams_v2
        6. Return summary

    Args:
        project_id:  UUID of the project.
        template_id: UUID of the circuit template.
        cursor:      A psycopg2 RealDictCursor inside an open transaction.

    Returns:
        {sections_created, streams_created, total_feed_tph,
         overall_recovery_pct, annual_gold_oz}
    """
    try:
        return _generate_mass_balance_impl(project_id, template_id, cursor)
    except Exception as e:
        logger.error("generate_mass_balance failed for project_id=%s, template_id=%s: %s",
                     project_id, template_id, e)
        raise RuntimeError(f"generate_mass_balance failed for project {project_id}: {e}") from e


def _generate_mass_balance_impl(project_id: str, template_id: str, cursor) -> dict:
    """Internal implementation of generate_mass_balance."""
    # 1. Project parameters
    pp = _get_project_params(project_id, cursor)

    # Check simulation_params for ore_sg override
    cursor.execute(
        "SELECT param_value FROM simulation_params "
        "WHERE project_id = %s AND param_key = 'ore_sg' LIMIT 1",
        (project_id,),
    )
    _sg_row = cursor.fetchone()
    if _sg_row:
        _sg_val = _sg_row["param_value"] if isinstance(_sg_row, dict) else _sg_row[0]
        if _sg_val:
            pp["ore_sg"] = float(_sg_val)

    logger.info("Mass balance for project %s: %.0f t/h, %.2f g/t Au",
                project_id, pp["target_tph"], pp["gold_grade"])

    # 2. Enabled operations
    enabled_ops = _get_enabled_ops(template_id, cursor)
    logger.info("Enabled operations: %s", sorted(enabled_ops))

    # 3. Build DC parameter dict
    dc = _build_dc_params(template_id, enabled_ops, cursor)

    # 4. Enrich with LIMS where DC value is missing
    # DC / project values take priority over LIMS averages for design parameters.
    # LIMS is used only as fallback when DC has no value set.
    ore_sg_lims = _get_lims_avg(project_id, "lims_a1", "sg", cursor)
    if ore_sg_lims is not None and pp.get("ore_sg", 0) <= 0:
        pp["ore_sg"] = ore_sg_lims

    # Gold grade: DC (project.gold_grade_g_t) is authoritative for design.
    # LIMS A1 average is informational only — do NOT override DC grade.
    if pp.get("gold_grade", 0) <= 0:
        au_lims = _get_lims_avg(project_id, "lims_a1", "au_g_t", cursor)
        if au_lims is not None:
            pp["gold_grade"] = au_lims

    # 5. Delete existing balance for this project
    _delete_existing(project_id, cursor)

    # 6. Generate sections
    carry: dict[str, Any] = {"au_gt": pp["gold_grade"], "enabled_ops": enabled_ops}
    # Detect regrind equipment for proper stream naming
    if "VERTIMILL_REGRIND" in enabled_ops or "VERTIMILL" in enabled_ops:
        carry["regrind_equipment"] = "Vertimill"
    elif "SMD" in enabled_ops:
        carry["regrind_equipment"] = "SMD"
    else:
        carry["regrind_equipment"] = "IsaMill"
    sections_created = 0
    streams_created = 0
    sort_idx = 0

    for section_name, gen_key, required_ops in SECTION_REGISTRY:
        # Check if section should be generated
        if required_ops and not any(op in enabled_ops for op in required_ops):
            continue

        gen_fn = _GENERATORS.get(gen_key)
        if gen_fn is None:
            logger.warning("No generator for section %s", section_name)
            continue

        try:
            section_streams = gen_fn(pp, dc, carry)
        except Exception:
            logger.error("Failed to generate section %s", section_name, exc_info=True)
            continue

        if not section_streams:
            continue

        sort_idx += 1
        op_code = required_ops[0] if required_ops else None
        # Use descriptive section name based on actual equipment
        display_name = section_name
        if section_name == "CONCENTRATE_REGRIND":
            equip = carry.get("regrind_equipment", "IsaMill")
            has_flot = carry.get("flot_conc_tph") is not None
            display_name = f"SECONDARY_GRIND_{equip.upper()}" if not has_flot else f"CONC_REGRIND_{equip.upper()}"
        section_id = _insert_section(project_id, template_id, display_name, op_code, sort_idx, cursor)

        for stream in section_streams:
            _insert_stream(section_id, project_id, stream, cursor)
            streams_created += 1

        sections_created += 1

    # 7. Compute summary (plant feed basis — aligned with economics module)
    total_feed_tph = float(pp["target_tph"])
    plant_head_au = float(pp["gold_grade"])
    plant_feed_au_g_h = total_feed_tph * plant_head_au
    plant_h_per_d = dc.get("plant_h_per_d", 22.1)
    avail_pct = float(pp["availability"])

    leach_au_g_h = float(carry.get("leach_au_recovered_g_h") or 0)
    gravity_au_g_h = float(carry.get("gravity_au_recovered_g_h") or 0)
    recovered_au_g_h = leach_au_g_h + gravity_au_g_h

    overall_recovery = (
        (recovered_au_g_h / plant_feed_au_g_h) * 100.0
        if plant_feed_au_g_h > 0 else 0.0
    )
    gravity_recovery_pct = (
        (gravity_au_g_h / plant_feed_au_g_h) * 100.0 if plant_feed_au_g_h > 0 else None
    )
    leach_feed_au_g_h = float(carry.get("leach_feed_tph") or 0) * float(
        carry.get("leach_feed_au") or 0
    )
    leach_recovery_pct = (
        (leach_au_g_h / leach_feed_au_g_h) * 100.0 if leach_feed_au_g_h > 0 else None
    )

    try:
        from ..helpers import compute_annual_gold_oz
    except ImportError:
        from helpers import compute_annual_gold_oz

    annual_gold_oz = compute_annual_gold_oz(
        total_feed_tph, plant_h_per_d, avail_pct, plant_head_au, overall_recovery,
    )
    au_dissolved_tph = recovered_au_g_h / 1_000_000.0
    availability_frac = avail_pct / 100.0

    summary = {
        "sections_created": sections_created,
        "streams_created": streams_created,
        "total_feed_tph": round(total_feed_tph, 1),
        "nominal_tph": round(pp["target_tph"], 1),
        "overall_recovery_pct": round(overall_recovery, 2),
        "gravity_recovery_pct": round(gravity_recovery_pct, 2)
        if gravity_recovery_pct is not None
        else None,
        "leach_recovery_pct": round(leach_recovery_pct, 2)
        if leach_recovery_pct is not None
        else None,
        "plant_formula_recovery_pct": (
            round(
                gravity_recovery_pct
                + (1.0 - gravity_recovery_pct / 100.0) * leach_recovery_pct,
                2,
            )
            if gravity_recovery_pct is not None and leach_recovery_pct is not None
            else None
        ),
        "annual_gold_oz": round(annual_gold_oz, 0),
        "gold_grade_g_t": round(pp["gold_grade"], 3),
        "ore_sg": round(pp["ore_sg"], 2),
        "availability_pct": round(pp["availability"], 1),
        "h_per_day": round(plant_h_per_d, 2),
        "annual_hours": round(plant_h_per_d * 365.25 * availability_frac, 0),
        # Reagent consumptions (from carry or DC defaults)
        "nacn_kg_t": round(dc.get("nacn_consumption_kg_t", 0.5), 3),
        "cao_kg_t": round(dc.get("cao_consumption_kg_t", 1.5), 3),
        "nacn_kg_h": round(dc.get("nacn_consumption_kg_t", 0.5) * total_feed_tph, 1),
        "cao_kg_h": round(dc.get("cao_consumption_kg_t", 1.5) * total_feed_tph, 1),
        # Au production metrics
        "au_dissolved_g_h": round(au_dissolved_tph * 1_000_000, 1),
        "au_production_kg_d": round(au_dissolved_tph * plant_h_per_d * 1_000_000 / 1000, 2),
        "au_production_oz_d": round(au_dissolved_tph * plant_h_per_d * 1_000_000 * TROY_OZ_PER_GRAM, 1),
        # Water balance summary
        "process_water_demand_m3h": round(carry.get("process_water_demand_m3h", 0), 1),
        "thickener_reclaim_m3h": round(carry.get("thickener_reclaim_m3h", 0), 1),
        "leach_feed_tph": round(carry.get("leach_feed_tph", total_feed_tph), 1),
        "leach_feed_au_g_t": round(carry.get("leach_feed_au", pp["gold_grade"]), 3),
        "leach_feed_source": carry.get("leach_feed_source", "plant_feed"),
        "has_flotation": bool(carry.get("has_flotation")),
        "flot_conc_tph": round(carry["flot_conc_tph"], 1) if "flot_conc_tph" in carry else None,
        "production_basis": "plant_feed",
        "production_formula": "target_tph × gold_grade × overall_recovery × h/d × 365 × availability",
    }

    logger.info("Mass balance complete: %s", summary)
    return summary


# ============================================================================
# Carbon footprint
# ============================================================================

def _get_emission_factor(project_id: str | None, factor_key: str, cursor) -> float:
    """Get emission factor: project-specific first, then global default."""
    if project_id:
        cursor.execute(
            "SELECT factor_value FROM carbon_emission_factors "
            "WHERE project_id = %s AND factor_key = %s",
            (project_id, factor_key),
        )
        row = cursor.fetchone()
        if row:
            val = row["factor_value"] if isinstance(row, dict) else row[0]
            return float(val)

    # Fall back to global default (project_id IS NULL)
    cursor.execute(
        "SELECT factor_value FROM carbon_emission_factors "
        "WHERE project_id IS NULL AND factor_key = %s",
        (factor_key,),
    )
    row = cursor.fetchone()
    if row:
        val = row["factor_value"] if isinstance(row, dict) else row[0]
        return float(val)

    return 0.0


def compute_carbon_footprint(project_id: str, streams: list[dict], cursor) -> dict:
    """Calculate CO2 emissions per operation from streams and emission factors.

    Reads ``carbon_emission_factors`` (project-specific or global defaults) and
    calculates emissions from:
        - Grid electricity consumption
        - NaCN production and transport
        - CaO (lime) calcination
        - H2O2 / CuSO4 / SO2 reagents
        - PAX / MIBC / Flocculant production
        - Smelting

    Args:
        project_id: UUID of the project.
        streams:    List of stream dicts (output of generate_mass_balance carry dict
                    or direct query of mass_balance_streams_v2).
        cursor:     A psycopg2 RealDictCursor.

    Returns:
        {per_operation: [{op, co2_kgh, source}], total_kgh,
         co2_per_oz, wgc_comparison}
    """
    try:
        return _compute_carbon_footprint_impl(project_id, streams, cursor)
    except Exception as e:
        logger.error("compute_carbon_footprint failed for project_id=%s: %s", project_id, e)
        return {"per_operation": [], "total_kgh": 0.0, "co2_per_oz": 0.0, "wgc_comparison": {}}


def _compute_carbon_footprint_impl(project_id: str, streams: list[dict], cursor) -> dict:
    """Internal implementation of compute_carbon_footprint."""
    # Load all emission factors
    grid_factor = _get_emission_factor(project_id, "grid_kgco2_kwh", cursor)
    nacn_factor = _get_emission_factor(project_id, "nacn_kgco2_kg", cursor)
    cao_factor = _get_emission_factor(project_id, "cao_kgco2_kg", cursor)
    _h2o2_factor = _get_emission_factor(project_id, "h2o2_kgco2_kg", cursor)
    cuso4_factor = _get_emission_factor(project_id, "cuso4_kgco2_kg", cursor)
    pax_factor = _get_emission_factor(project_id, "pax_kgco2_kg", cursor)
    mibc_factor = _get_emission_factor(project_id, "mibc_kgco2_kg", cursor)
    floc_factor = _get_emission_factor(project_id, "flocculant_kgco2_kg", cursor)
    smelt_factor = _get_emission_factor(project_id, "smelt_kgco2_oz", cursor)
    _transport_factor = _get_emission_factor(project_id, "transport_kgco2_tkm", cursor)

    # Try to get installed power from project or design criteria
    cursor.execute(
        "SELECT target_tph, gold_grade_g_t FROM projects WHERE id = %s",
        (project_id,),
    )
    proj = cursor.fetchone()
    target_tph = float((proj["target_tph"] if isinstance(proj, dict) else proj[0]) or 1517) if proj else 1517
    gold_grade = float((proj["gold_grade_g_t"] if isinstance(proj, dict) else proj[1]) or 1.5) if proj else 1.5

    # Estimate total power from throughput (rough: ~25 kWh/t for gold plant)
    kwh_per_t = 25.0
    total_power_kw = target_tph * kwh_per_t

    # Estimate reagent consumptions from streams or defaults (kg/h)
    nacn_kgh = 0.0
    cao_kgh = 0.0
    cuso4_kgh = 0.0
    pax_kgh = 0.0
    mibc_kgh = 0.0
    floc_kgh = 0.0

    for s in streams:
        name = (s.get("stream_name") or "").lower()
        tph = float(s.get("water_tph") or s.get("slurry_tph") or 0)
        if "nacn" in name or "cyanide" in name:
            nacn_kgh += tph * 1000  # t/h → kg/h
        elif "cao" in name or "lime" in name:
            cao_kgh += tph * 1000
        elif "cuso4" in name:
            cuso4_kgh += tph * 1000
        elif "pax" in name:
            pax_kgh += tph * 1000
        elif "mibc" in name:
            mibc_kgh += tph * 1000
        elif "flocculant" in name:
            floc_kgh += tph * 1000

    per_operation = []

    # Energy
    co2_energy = total_power_kw * grid_factor
    per_operation.append({"operation": "Grid electricity", "co2_kgh": round(co2_energy, 2),
                          "source": f"{total_power_kw:.0f} kW x {grid_factor} kgCO2/kWh"})

    # NaCN
    co2_nacn = nacn_kgh * nacn_factor
    per_operation.append({"operation": "NaCN production", "co2_kgh": round(co2_nacn, 2),
                          "source": f"{nacn_kgh:.1f} kg/h x {nacn_factor} kgCO2/kg"})

    # CaO
    co2_cao = cao_kgh * cao_factor
    per_operation.append({"operation": "CaO calcination", "co2_kgh": round(co2_cao, 2),
                          "source": f"{cao_kgh:.1f} kg/h x {cao_factor} kgCO2/kg"})

    # CuSO4
    co2_cuso4 = cuso4_kgh * cuso4_factor
    per_operation.append({"operation": "CuSO4 production", "co2_kgh": round(co2_cuso4, 2),
                          "source": f"{cuso4_kgh:.1f} kg/h x {cuso4_factor} kgCO2/kg"})

    # PAX
    co2_pax = pax_kgh * pax_factor
    per_operation.append({"operation": "PAX production", "co2_kgh": round(co2_pax, 2),
                          "source": f"{pax_kgh:.1f} kg/h x {pax_factor} kgCO2/kg"})

    # MIBC
    co2_mibc = mibc_kgh * mibc_factor
    per_operation.append({"operation": "MIBC production", "co2_kgh": round(co2_mibc, 2),
                          "source": f"{mibc_kgh:.1f} kg/h x {mibc_factor} kgCO2/kg"})

    # Flocculant
    co2_floc = floc_kgh * floc_factor
    per_operation.append({"operation": "Flocculant production", "co2_kgh": round(co2_floc, 2),
                          "source": f"{floc_kgh:.1f} kg/h x {floc_factor} kgCO2/kg"})

    total_kgh = sum(item["co2_kgh"] for item in per_operation)

    # CO2 per ounce of gold
    # Gold production: grade (g/t) * throughput (t/h) * recovery → oz/h
    estimated_recovery = 0.90  # default assumption for footprint
    au_oz_per_h = (gold_grade * target_tph * estimated_recovery) * TROY_OZ_PER_GRAM
    co2_per_oz = (total_kgh / au_oz_per_h) if au_oz_per_h > 0 else 0.0

    # Add smelting
    co2_smelt = smelt_factor  # per oz
    per_operation.append({"operation": "Smelting (Scope 1)", "co2_kgh": round(co2_smelt * au_oz_per_h, 2),
                          "source": f"{smelt_factor} kgCO2/oz x {au_oz_per_h:.2f} oz/h"})
    total_kgh += co2_smelt * au_oz_per_h
    co2_per_oz += co2_smelt

    # WGC comparison (World Gold Council average ~0.6-0.8 tCO2/oz)
    wgc_avg_kg_per_oz = 700  # kg CO2/oz — industry average
    wgc_comparison = {
        "project_kgco2_per_oz": round(co2_per_oz, 1),
        "wgc_average_kgco2_per_oz": wgc_avg_kg_per_oz,
        "vs_wgc_pct": round((co2_per_oz / wgc_avg_kg_per_oz - 1) * 100, 1) if wgc_avg_kg_per_oz > 0 else 0,
    }

    return {
        "per_operation": per_operation,
        "total_kgh": round(total_kgh, 2),
        "co2_per_oz": round(co2_per_oz, 1),
        "wgc_comparison": wgc_comparison,
    }
