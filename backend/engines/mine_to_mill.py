# backend/engines/mine_to_mill.py
"""
Mine-to-Mill Geometallurgical Engine for MetalFlow Pro.

The first integrated tool connecting:
  Block Model -> Mine Schedule -> Plant Performance -> LOM Production -> NPV

This engine bridges geology and process engineering by:
  1. Reading the block model and grouping by geometallurgical domain (rock_type)
  2. Generating a simplified mine schedule over the life of mine
  3. Simulating plant performance for each period using the process simulator
  4. Calculating LOM production profiles, NPV, and critical periods
  5. Running Monte Carlo simulations across the full LOM
  6. Generating ESG timeline data per period

Key innovation: ore variability from the block model drives throughput, recovery,
and economic performance period-by-period -- not just a single steady-state average.

BWi estimation from rock_type (LIMS-derived relationship):
  - "Sulphide - Zone Principale"   -> BWi ~18.4 kWh/t
  - "Sulphide - Extension Nord"    -> BWi ~19.3 kWh/t (+5%)
  - "Transition - Zone Profonde"   -> BWi ~20.2 kWh/t (+10%)
  - "Sterile / Hors ressource"     -> BWi ~14.7 kWh/t (-20%)
"""
from __future__ import annotations

import json
import logging
import time
import uuid

import numpy as np

logger = logging.getLogger("mpdpms.mine_to_mill")

# ============================================================================
# Constants
# ============================================================================

try:
    from ..constants import TROY_OZ_PER_GRAM
    from ..settings import get_settings
except ImportError:
    from constants import TROY_OZ_PER_GRAM
    from settings import get_settings

_SETTINGS = get_settings()
GRID_CO2_KG_KWH = _SETTINGS.grid_co2_kg_kwh
WGC_CO2_BENCHMARK = _SETTINGS.wgc_co2_benchmark
HOURS_PER_YEAR = 8760.0
DEFAULT_BWI = 18.0          # kWh/t generic fallback BWi

# Confidence-based grade uncertainty (1-sigma as fraction of grade)
# These are industry-standard NI 43-101 confidence factors, not project-specific.
CONFIDENCE_GRADE_SIGMA: dict[str, float] = {
    "Measured":  0.05,   # +/- 5%
    "Indicated": 0.15,   # +/- 15%
    "Inferred":  0.30,   # +/- 30%
    "Waste":     0.50,   # +/- 50%
}


# ============================================================================
# BWi lookup — dynamic from project LIMS data
# ============================================================================

def _build_bwi_map(project_id: str, cursor) -> dict[str, float]:
    """Build a rock_type → BWi mapping from LIMS data for this project.

    Strategy:
    1. Get the average BWi from LIMS b1 for the project (the design BWi)
    2. Get distinct rock_types from the block model
    3. Assign BWi based on classification confidence:
       - Measured domains: base BWi (well-characterized)
       - Indicated domains: BWi + 5% (conservative)
       - Inferred domains: BWi + 10% (more conservative)
       - Waste: BWi - 20% (softer, less mineralized)
    """
    # Get project average BWi from LIMS
    try:
        cursor.execute("SAVEPOINT bwi_read")
        cursor.execute(
            "SELECT AVG(COALESCE(mb_kwh_t, 0)) FROM lims_b1 WHERE project_id = %s AND mb_kwh_t IS NOT NULL",
            (project_id,)
        )
        row = cursor.fetchone()
        base_bwi = float(row[0]) if row and row[0] else DEFAULT_BWI
        cursor.execute("RELEASE SAVEPOINT bwi_read")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT bwi_read")
        base_bwi = DEFAULT_BWI

    # Get distinct rock types from block model
    bwi_map: dict[str, float] = {}
    try:
        cursor.execute("SAVEPOINT rt_read")
        cursor.execute(
            "SELECT DISTINCT b.rock_type FROM blocks b "
            "JOIN block_model_configs bmc ON bmc.id = b.config_id "
            "WHERE bmc.project_id = %s AND b.rock_type IS NOT NULL",
            (project_id,)
        )
        rock_types = [r[0] for r in cursor.fetchall()]
        cursor.execute("RELEASE SAVEPOINT rt_read")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT rt_read")
        rock_types = []

    # Assign BWi based on rock type keywords (confidence-based scaling)
    for rt in rock_types:
        rt_lower = rt.lower()
        if any(k in rt_lower for k in ["mesuré", "measured", "principale", "primary", "zone a"]):
            bwi_map[rt] = base_bwi
        elif any(k in rt_lower for k in ["indiqué", "indicated", "nord", "extension", "zone b"]):
            bwi_map[rt] = base_bwi * 1.05
        elif any(k in rt_lower for k in ["inféré", "inferred", "profond", "deep", "zone c"]):
            bwi_map[rt] = base_bwi * 1.10
        elif any(k in rt_lower for k in ["stérile", "sterile", "waste", "hors"]):
            bwi_map[rt] = base_bwi * 0.80
        else:
            bwi_map[rt] = base_bwi  # Unknown type gets base BWi

    return bwi_map


def _get_bwi_for_rock_type(rock_type: str, bwi_map: dict[str, float] = None) -> float:
    """Return estimated BWi for a rock type using dynamic mapping."""
    if bwi_map and rock_type in bwi_map:
        return bwi_map[rock_type]
    return DEFAULT_BWI


def _build_confidence_map(project_id: str, cursor) -> dict[str, str]:
    """Build rock_type → confidence mapping from block model attributes or naming."""
    conf_map: dict[str, str] = {}
    try:
        cursor.execute("SAVEPOINT conf_read")
        cursor.execute(
            "SELECT DISTINCT b.rock_type FROM blocks b "
            "JOIN block_model_configs bmc ON bmc.id = b.config_id "
            "WHERE bmc.project_id = %s AND b.rock_type IS NOT NULL",
            (project_id,)
        )
        for (rt,) in cursor.fetchall():
            rt_lower = rt.lower()
            if any(k in rt_lower for k in ["mesuré", "measured", "principale", "primary"]):
                conf_map[rt] = "Measured"
            elif any(k in rt_lower for k in ["indiqué", "indicated", "nord", "extension"]):
                conf_map[rt] = "Indicated"
            elif any(k in rt_lower for k in ["inféré", "inferred", "profond", "deep"]):
                conf_map[rt] = "Inferred"
            elif any(k in rt_lower for k in ["stérile", "sterile", "waste", "hors"]):
                conf_map[rt] = "Waste"
            else:
                conf_map[rt] = "Indicated"  # default to indicated confidence
        cursor.execute("RELEASE SAVEPOINT conf_read")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT conf_read")
    return conf_map


def _get_confidence(rock_type: str, conf_map: dict[str, str] = None) -> str:
    """Return resource classification for a rock type."""
    if conf_map and rock_type in conf_map:
        return conf_map[rock_type]
    rt_lower = rock_type.lower()
    if "principale" in rt_lower:
        return "Measured"
    elif "nord" in rt_lower or "extension" in rt_lower:
        return "Indicated"
    elif "profonde" in rt_lower or "transition" in rt_lower:
        return "Inferred"
    else:
        return "Waste"


# ============================================================================
# Mine Schedule Generation
# ============================================================================

def generate_mine_schedule(project_id: str, cursor,
                           n_years: int = 15,
                           period_type: str = "year") -> list[dict]:
    """
    Generate a simplified mine schedule from the block model.

    Steps:
      1. Read block model (blocks table) for the project
      2. Group blocks by rock_type (geometallurgical domain)
      3. Determine target annual tonnage from project settings
      4. Distribute blocks across periods by confidence tier:
         - Year 1-3: primarily Measured blocks (high confidence)
         - Year 4-8: mix of Measured + Indicated
         - Year 9-12: primarily Indicated + some Inferred
         - Year 13-15: Inferred + remaining
      5. Calculate weighted averages per period
      6. Insert into mine_schedule table

    Args:
        project_id: UUID of the project
        cursor: database cursor (RealDictCursor)
        n_years: Number of mine-life years (default 15)
        period_type: 'year' or 'quarter'

    Returns:
        List of period dicts with tonnage, grade, BWi, rock_type_mix, etc.
    """
    try:
        return _generate_mine_schedule_impl(project_id, cursor, n_years, period_type)
    except ValueError:
        raise
    except Exception as e:
        logger.error("generate_mine_schedule failed for project_id=%s, n_years=%d: %s",
                     project_id, n_years, e)
        raise RuntimeError(f"generate_mine_schedule failed for project {project_id}: {e}") from e


def _generate_mine_schedule_impl(project_id: str, cursor,
                                  n_years: int = 15,
                                  period_type: str = "year") -> list[dict]:
    """Internal implementation of generate_mine_schedule."""
    # 1. Get block model config for the project
    cursor.execute(
        "SELECT id FROM block_model_configs WHERE project_id = %s LIMIT 1",
        (project_id,),
    )
    config_row = cursor.fetchone()
    if config_row is None:
        raise ValueError(f"No block model configuration found for project {project_id}")
    config_id = str(config_row["id"])

    # 2. Read all blocks
    cursor.execute(
        "SELECT id, grade_au, density, volume, rock_type "
        "FROM blocks WHERE config_id = %s ORDER BY z_center DESC, y_center, x_center",
        (config_id,),
    )
    all_blocks = cursor.fetchall()
    if not all_blocks:
        raise ValueError("No blocks found in block model")

    logger.info("Mine schedule: loaded %d blocks for project %s", len(all_blocks), project_id)

    # 2b. Build dynamic BWi and confidence maps from project LIMS data
    bwi_map = _build_bwi_map(project_id, cursor)
    conf_map = _build_confidence_map(project_id, cursor)

    # 3. Group blocks by rock_type (confidence tier)
    blocks_by_confidence: dict[str, list[dict]] = {
        "Measured": [],
        "Indicated": [],
        "Inferred": [],
        "Waste": [],
    }
    for b in all_blocks:
        conf = _get_confidence(b["rock_type"], conf_map)
        blocks_by_confidence[conf].append(b)

    # 4. Load project economics for target tonnage
    cursor.execute(
        "SELECT target_tph, mine_life_years, operating_hours_day, availability_pct "
        "FROM projects WHERE id = %s",
        (project_id,),
    )
    proj = cursor.fetchone()
    if proj is None:
        raise ValueError(f"Project {project_id} not found")

    target_tph = float(proj.get("target_tph") or 1517)
    hours_day = float(proj.get("operating_hours_day") or 24.0)
    avail = float(proj.get("availability_pct") or 92.0)
    mine_life = int(proj.get("mine_life_years") or n_years)
    n_years = min(n_years, mine_life)

    annual_target_t = target_tph * hours_day * 365.0 * (avail / 100.0)

    # 5. Define mining sequence by period (which confidence tiers to draw from)
    # Proportions: [Measured, Indicated, Inferred, Waste]
    period_mix_rules: list[list[float]] = []
    for yr in range(1, n_years + 1):
        if yr <= 3:
            # Years 1-3: primarily Measured
            mix = [0.75, 0.20, 0.03, 0.02]
        elif yr <= 8:
            # Years 4-8: mix of Measured + Indicated
            m_frac = max(0.10, 0.75 - 0.13 * (yr - 3))
            i_frac = min(0.60, 0.20 + 0.08 * (yr - 3))
            inf_frac = min(0.20, 0.03 + 0.03 * (yr - 3))
            w_frac = max(0.02, 1.0 - m_frac - i_frac - inf_frac)
            mix = [m_frac, i_frac, inf_frac, w_frac]
        elif yr <= 12:
            # Years 9-12: Indicated + Inferred
            mix = [0.05, 0.40, 0.45, 0.10]
        else:
            # Years 13-15: Inferred + remaining
            mix = [0.02, 0.15, 0.65, 0.18]
        period_mix_rules.append(mix)

    # 6. Build schedule: allocate blocks to periods
    # Track remaining blocks per confidence
    remaining: dict[str, list[dict]] = {
        k: list(v) for k, v in blocks_by_confidence.items()
    }
    confidence_keys = ["Measured", "Indicated", "Inferred", "Waste"]

    schedule: list[dict] = []

    for yr_idx, mix in enumerate(period_mix_rules):
        period_order = yr_idx + 1
        period_label = f"Year {period_order}"
        period_blocks: list[dict] = []
        period_tonnage = 0.0

        for ci, conf_key in enumerate(confidence_keys):
            target_from_conf = annual_target_t * mix[ci]
            drawn_tonnage = 0.0

            while drawn_tonnage < target_from_conf and remaining[conf_key]:
                block = remaining[conf_key].pop(0)
                tonnage = float(block["density"]) * float(block["volume"])
                period_blocks.append(block)
                drawn_tonnage += tonnage
                period_tonnage += tonnage

        if not period_blocks:
            # No more blocks available
            break

        # Calculate weighted averages
        total_t = 0.0
        sum_grade_t = 0.0
        sum_density_t = 0.0
        rock_type_tonnes: dict[str, float] = {}

        for b in period_blocks:
            t = float(b["density"]) * float(b["volume"])
            g = float(b["grade_au"])
            d = float(b["density"])
            total_t += t
            sum_grade_t += g * t
            sum_density_t += d * t

            rt = b["rock_type"]
            rock_type_tonnes[rt] = rock_type_tonnes.get(rt, 0) + t

        grade_avg = sum_grade_t / total_t if total_t > 0 else 0
        density_avg = sum_density_t / total_t if total_t > 0 else 2.74

        # BWi weighted average from rock_type mix
        bwi_sum = 0.0
        for rt, rt_t in rock_type_tonnes.items():
            bwi_sum += _get_bwi_for_rock_type(rt, bwi_map) * rt_t
        bwi_avg = bwi_sum / total_t if total_t > 0 else DEFAULT_BWI

        # S% estimate: sulphide zones have higher S
        s_pct_sum = 0.0
        for rt, rt_t in rock_type_tonnes.items():
            rt_lower = rt.lower()
            if "sulphide" in rt_lower:
                s_pct_sum += 2.5 * rt_t
            elif "transition" in rt_lower:
                s_pct_sum += 1.5 * rt_t
            else:
                s_pct_sum += 0.3 * rt_t
        s_pct_avg = s_pct_sum / total_t if total_t > 0 else 1.5

        # Rock type mix as proportions
        rock_type_mix = {
            rt: round(rt_t / total_t * 100, 1)
            for rt, rt_t in rock_type_tonnes.items()
        }

        block_ids = [str(b["id"]) for b in period_blocks[:100]]  # cap to avoid huge arrays

        period_dict = {
            "period_type": period_type,
            "period_label": period_label,
            "period_order": period_order,
            "tonnage_planned": round(total_t, 0),
            "grade_au_avg": round(grade_avg, 4),
            "bwi_avg": round(bwi_avg, 2),
            "s_pct_avg": round(s_pct_avg, 2),
            "density_avg": round(density_avg, 3),
            "rock_type_mix": rock_type_mix,
            "block_ids": block_ids,
        }
        schedule.append(period_dict)

    # 7. Insert into mine_schedule table
    # Clear existing schedule for this project first
    cursor.execute(
        "DELETE FROM mine_schedule WHERE project_id = %s", (project_id,)
    )

    for period in schedule:
        cursor.execute(
            "INSERT INTO mine_schedule "
            "(project_id, period_type, period_label, period_order, "
            " tonnage_planned, grade_au_avg, bwi_avg, s_pct_avg, density_avg, "
            " rock_type_mix, block_ids) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)",
            (
                project_id,
                period["period_type"],
                period["period_label"],
                period["period_order"],
                period["tonnage_planned"],
                period["grade_au_avg"],
                period["bwi_avg"],
                period["s_pct_avg"],
                period["density_avg"],
                json.dumps(period["rock_type_mix"]),
                json.dumps(period["block_ids"]),
            ),
        )

    logger.info("Mine schedule: generated %d periods for project %s",
                len(schedule), project_id)

    return schedule


# ============================================================================
# LOM Simulation
# ============================================================================

def simulate_lom(project_id: str, template_id: str,
                 schedule: list[dict], cursor) -> dict:
    """
    Simulate plant performance for each period of the mine schedule.

    For each period:
      1. Use the period's feed characteristics (grade, BWi, S%, density)
      2. Run simulate_circuit() with those parameters
      3. Calculate throughput limitation from BWi
      4. Calculate production, revenue, cashflow
      5. Identify critical periods

    Args:
        project_id: UUID of the project
        template_id: UUID of the circuit template
        schedule: List of period dicts from generate_mine_schedule
        cursor: database cursor

    Returns:
        {
            run_id,
            periods: [{period_label, tonnage, grade, throughput, recovery,
                       production_oz, revenue, opex, cashflow, cumulative_cf,
                       co2_per_oz, is_critical, critical_reason}],
            summary: {total_production_oz, total_revenue, total_opex,
                      npv_5pct, npv_8pct, irr, payback_year, ...}
        }
    """
    try:
        return _simulate_lom_impl(project_id, template_id, schedule, cursor)
    except Exception as e:
        logger.error("simulate_lom failed for project_id=%s, template_id=%s, n_periods=%d: %s",
                     project_id, template_id, len(schedule), e)
        raise RuntimeError(f"simulate_lom failed for project {project_id}: {e}") from e


def _simulate_lom_impl(project_id: str, template_id: str,
                        schedule: list[dict], cursor) -> dict:
    """Internal implementation of simulate_lom."""
    try:
        from engines.process_simulator import simulate_circuit, _load_project_economics
    except ImportError:
        from .process_simulator import simulate_circuit, _load_project_economics
    try:
        from engines.dcf import compute_npv, compute_irr
    except ImportError:
        from .dcf import compute_npv, compute_irr

    t0 = time.time()
    run_id = str(uuid.uuid4())

    econ = _load_project_economics(project_id, cursor)
    gold_price = econ.get("gold_price_usd_oz") or 2340.0
    discount_5 = 0.05
    discount_8 = 0.08
    capex_musd = econ.get("capex_musd") or 150.0
    capex_usd = capex_musd * 1e6
    design_tph = econ.get("target_tph") or 1517.0
    hours_day = econ.get("operating_hours_day") or 24.0
    avail = econ.get("availability_pct") or 92.0
    elec_rate = econ.get("electricity_rate") or 0.075

    # Design BWi (from DC or default)
    design_bwi = 18.0

    periods_out: list[dict] = []
    cashflows_list: list[float] = []
    cumulative_cf = 0.0
    total_production = 0.0
    total_revenue = 0.0
    total_opex = 0.0
    peak_production = 0.0
    peak_year = ""
    lowest_production = float("inf")
    lowest_year = ""
    critical_count = 0

    for period in schedule:
        period_label = period["period_label"]
        tonnage = float(period["tonnage_planned"])
        grade = float(period["grade_au_avg"])
        bwi = float(period["bwi_avg"])
        _s_pct = float(period.get("s_pct_avg", 1.5))
        _density = float(period.get("density_avg", 2.74))

        # Throughput limitation by BWi
        # actual_tph = design_tph * (design_bwi / actual_bwi)^0.8
        actual_tph = design_tph * (design_bwi / bwi) ** 0.8
        actual_tph = min(actual_tph, design_tph * 1.15)  # cap at 115%

        # Annual operating hours
        annual_hours = hours_day * 365.0 * (avail / 100.0)
        annual_tonnes = actual_tph * annual_hours

        # Run process simulation for this period's feed characteristics
        params_override = {
            "feed_tph": actual_tph,
            "feed_grade_au": grade,
        }

        try:
            sim_result = simulate_circuit(
                project_id, template_id,
                params_override=params_override,
                cursor=cursor,
            )
            ov = sim_result.get("overall", {})
            recovery = float(ov.get("total_recovery_pct", 90.0))
            energy_kwh_t = float(ov.get("total_energy_kwh_t", 15.0))
            nacn_kg_t = float(ov.get("total_nacn_kg_t", 0.8))
        except Exception as exc:
            logger.warning("LOM sim failed for %s: %s — using defaults", period_label, exc)
            recovery = 90.0
            energy_kwh_t = 15.0
            nacn_kg_t = 0.8

        # Production
        production_g = annual_tonnes * grade * (recovery / 100.0)
        production_oz = production_g * TROY_OZ_PER_GRAM

        # Revenue
        revenue = production_oz * gold_price

        # OPEX
        opex_per_t = energy_kwh_t * elec_rate + nacn_kg_t * 2.5 + 2.5 * 0.15 + 5.0  # base G&A
        opex = opex_per_t * annual_tonnes

        # Cashflow
        sustaining = capex_usd * 0.03
        royalty = revenue * 0.05
        cashflow = revenue - opex - sustaining - royalty

        cumulative_cf += cashflow
        cashflows_list.append(cashflow)

        # CO2
        co2_total_kg = energy_kwh_t * annual_tonnes * GRID_CO2_KG_KWH
        co2_per_oz = co2_total_kg / production_oz if production_oz > 0 else 0

        # Water estimate (m3/h) — ~1.5 m3/t for gold processing
        water_m3h = actual_tph * 1.5

        # Critical period detection
        is_critical = False
        critical_reason = None

        if bwi > design_bwi * 1.10:
            is_critical = True
            critical_reason = f"Hard ore: BWi {bwi:.1f} > design {design_bwi:.1f} kWh/t — throughput limited"
        elif grade < 0.5:
            is_critical = True
            critical_reason = f"Low grade: {grade:.2f} g/t — below economic cut-off"
        elif recovery < 85.0:
            is_critical = True
            critical_reason = f"Low recovery: {recovery:.1f}% — below target 85%"

        if is_critical:
            critical_count += 1

        # Track peaks
        total_production += production_oz
        total_revenue += revenue
        total_opex += opex

        if production_oz > peak_production:
            peak_production = production_oz
            peak_year = period_label
        if production_oz < lowest_production:
            lowest_production = production_oz
            lowest_year = period_label

        periods_out.append({
            "period_label": period_label,
            "period_order": period["period_order"],
            "tonnage": round(annual_tonnes, 0),
            "tonnage_planned": round(tonnage, 0),
            "grade": round(grade, 4),
            "bwi": round(bwi, 2),
            "throughput_tph": round(actual_tph, 1),
            "recovery": round(recovery, 2),
            "production_oz": round(production_oz, 0),
            "revenue": round(revenue, 2),
            "opex": round(opex, 2),
            "cashflow": round(cashflow, 2),
            "cumulative_cf": round(cumulative_cf, 2),
            "co2_per_oz": round(co2_per_oz, 1),
            "water_m3h": round(water_m3h, 1),
            "is_critical": is_critical,
            "critical_reason": critical_reason,
        })

    # NPV calculations
    npv_5 = compute_npv(cashflows_list, discount_5, capex_usd)
    npv_8 = compute_npv(cashflows_list, discount_8, capex_usd)
    irr = compute_irr(cashflows_list, capex_usd)

    # Payback year
    payback_year = None
    cum = -capex_usd
    for yi, cf in enumerate(cashflows_list, 1):
        cum += cf
        if cum >= 0:
            payback_year = yi
            break

    duration = time.time() - t0

    # Save to database
    cursor.execute(
        "INSERT INTO simulation_runs_v2 "
        "(id, project_id, template_id, run_type, status, results, duration_s) "
        "VALUES (%s, %s, %s, 'lom_simulation', 'completed', %s::jsonb, %s)",
        (
            run_id, project_id, template_id,
            json.dumps({"n_periods": len(periods_out)}, default=str),
            round(duration, 2),
        ),
    )

    # Save period profiles to lom_profiles
    for p in periods_out:
        cursor.execute(
            "INSERT INTO lom_profiles "
            "(run_id, period_order, period_label, tonnage, feed_grade_au, bwi_avg, "
            " throughput_tph, recovery_pct, production_oz, revenue_usd, opex_usd, "
            " cashflow_usd, cumulative_cf, co2_kg_per_oz, water_m3h, "
            " is_critical, critical_reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                p["period_order"],
                p["period_label"],
                p["tonnage"],
                p["grade"],
                p["bwi"],
                p["throughput_tph"],
                p["recovery"],
                p["production_oz"],
                p["revenue"],
                p["opex"],
                p["cashflow"],
                p["cumulative_cf"],
                p["co2_per_oz"],
                p["water_m3h"],
                p["is_critical"],
                p["critical_reason"],
            ),
        )

    summary = {
        "total_production_oz": round(total_production, 0),
        "total_revenue": round(total_revenue, 2),
        "total_opex": round(total_opex, 2),
        "npv_5pct": round(npv_5 / 1e6, 2),
        "npv_8pct": round(npv_8 / 1e6, 2),
        "irr": round(irr * 100, 2) if irr is not None else None,
        "payback_year": payback_year,
        "peak_production_year": peak_year,
        "lowest_production_year": lowest_year,
        "critical_periods_count": critical_count,
    }

    return {
        "run_id": run_id,
        "periods": periods_out,
        "summary": summary,
        "duration_s": round(duration, 2),
    }


# ============================================================================
# Blend Optimization
# ============================================================================

def optimize_blend(project_id: str, schedule: list[dict],
                   cursor) -> dict:
    """
    Suggest blend strategies to smooth variability across mine periods.

    Strategy:
      1. Calculate period-to-period variability (BWi, grade)
      2. Identify high-variability periods
      3. Suggest blending adjacent zones
      4. Score each strategy by reduced variability and maintained production

    Args:
        project_id: UUID of the project
        schedule: List of period dicts from generate_mine_schedule
        cursor: database cursor

    Returns:
        {
            original_variability: {bwi_cv, grade_cv},
            optimized_variability: {bwi_cv, grade_cv},
            recommendations: [{period, action, expected_improvement}]
        }
    """
    try:
        return _optimize_blend_impl(project_id, schedule, cursor)
    except Exception as e:
        logger.error("optimize_blend failed for project_id=%s, n_periods=%d: %s",
                     project_id, len(schedule), e)
        return {
            "original_variability": {"bwi_cv": 0.0, "grade_cv": 0.0},
            "optimized_variability": {"bwi_cv": 0.0, "grade_cv": 0.0},
            "recommendations": [],
        }


def _optimize_blend_impl(project_id: str, schedule: list[dict],
                          cursor) -> dict:
    """Internal implementation of optimize_blend."""
    if len(schedule) < 2:
        return {
            "original_variability": {"bwi_cv": 0.0, "grade_cv": 0.0},
            "optimized_variability": {"bwi_cv": 0.0, "grade_cv": 0.0},
            "recommendations": [],
        }

    bwi_values = np.array([float(p["bwi_avg"]) for p in schedule])
    grade_values = np.array([float(p["grade_au_avg"]) for p in schedule])
    _tonnage_values = np.array([float(p["tonnage_planned"]) for p in schedule])

    # Coefficient of variation
    bwi_cv = float(np.std(bwi_values) / np.mean(bwi_values)) if np.mean(bwi_values) > 0 else 0
    grade_cv = float(np.std(grade_values) / np.mean(grade_values)) if np.mean(grade_values) > 0 else 0

    recommendations: list[dict] = []
    optimized_bwi = bwi_values.copy()
    optimized_grade = grade_values.copy()

    for i in range(len(schedule)):
        period = schedule[i]
        period_label = period["period_label"]
        bwi = bwi_values[i]
        grade = grade_values[i]

        # High BWi period: suggest blending with softer ore
        if bwi > np.mean(bwi_values) + 0.5 * np.std(bwi_values):
            # Find a softer-ore period to blend from
            softer_periods = [j for j in range(len(schedule))
                              if bwi_values[j] < np.mean(bwi_values) - 0.3 * np.std(bwi_values)
                              and j != i]
            if softer_periods:
                blend_from = softer_periods[0]
                blend_pct = 20.0  # blend 20% from softer zone
                new_bwi = bwi * (1 - blend_pct / 100) + bwi_values[blend_from] * (blend_pct / 100)
                improvement = (bwi - new_bwi) / bwi * 100
                optimized_bwi[i] = new_bwi

                recommendations.append({
                    "period": period_label,
                    "action": (
                        f"Blend {blend_pct:.0f}% of feed from {schedule[blend_from]['period_label']} "
                        f"stockpile (BWi {bwi_values[blend_from]:.1f}) to reduce "
                        f"BWi from {bwi:.1f} to {new_bwi:.1f} kWh/t"
                    ),
                    "expected_improvement": f"BWi reduction: {improvement:.1f}%, "
                                            f"throughput increase: ~{improvement * 0.8:.1f}%",
                    "type": "bwi_smoothing",
                })

        # Low grade period: suggest high-grade stockpile blending
        if grade < np.mean(grade_values) - 0.5 * np.std(grade_values) and grade < 1.0:
            higher_periods = [j for j in range(len(schedule))
                              if grade_values[j] > np.mean(grade_values) + 0.3 * np.std(grade_values)
                              and j != i]
            if higher_periods:
                blend_from = higher_periods[0]
                blend_pct = 15.0
                new_grade = grade * (1 - blend_pct / 100) + grade_values[blend_from] * (blend_pct / 100)
                improvement = (new_grade - grade) / grade * 100 if grade > 0 else 0
                optimized_grade[i] = new_grade

                recommendations.append({
                    "period": period_label,
                    "action": (
                        f"Blend {blend_pct:.0f}% high-grade stockpile from "
                        f"{schedule[blend_from]['period_label']} "
                        f"(grade {grade_values[blend_from]:.2f} g/t) to improve "
                        f"feed from {grade:.2f} to {new_grade:.2f} g/t"
                    ),
                    "expected_improvement": f"Grade increase: {improvement:.1f}%, "
                                            f"production increase: ~{improvement:.1f}%",
                    "type": "grade_smoothing",
                })

        # High period-to-period jump
        if i > 0:
            bwi_jump = abs(bwi - bwi_values[i - 1]) / np.mean(bwi_values) * 100
            if bwi_jump > 10:
                recommendations.append({
                    "period": period_label,
                    "action": (
                        f"Transition zone: BWi changes {bwi_jump:.1f}% from "
                        f"{schedule[i-1]['period_label']}. Consider gradual transition "
                        f"with stockpile blending over 2-3 months."
                    ),
                    "expected_improvement": "Reduces mill operating variability, "
                                            "reduces liner/media consumption spikes",
                    "type": "transition_smoothing",
                })

    # Calculate optimized variability
    opt_bwi_cv = float(np.std(optimized_bwi) / np.mean(optimized_bwi)) if np.mean(optimized_bwi) > 0 else 0
    opt_grade_cv = float(np.std(optimized_grade) / np.mean(optimized_grade)) if np.mean(optimized_grade) > 0 else 0

    return {
        "original_variability": {
            "bwi_cv": round(bwi_cv * 100, 1),
            "grade_cv": round(grade_cv * 100, 1),
        },
        "optimized_variability": {
            "bwi_cv": round(opt_bwi_cv * 100, 1),
            "grade_cv": round(opt_grade_cv * 100, 1),
        },
        "recommendations": recommendations,
        "n_recommendations": len(recommendations),
    }


# ============================================================================
# Monte Carlo LOM
# ============================================================================

def monte_carlo_lom(project_id: str, template_id: str,
                    schedule: list[dict], cursor,
                    n_sims: int = 5000) -> dict:
    """
    Monte Carlo simulation over the full Life of Mine.

    For each simulation:
      1. Perturb each period's parameters based on resource classification:
         - Grade: +/- sigma based on M/I/I confidence
         - BWi: +/-10% (normal distribution)
         - Recovery: +/-3 percentage points
         - Gold price: +/-20% (log-normal)
      2. Run simplified LOM calculation
      3. Calculate total production, NPV

    Args:
        project_id: UUID of the project
        template_id: UUID of the circuit template
        schedule: List of period dicts from generate_mine_schedule
        cursor: database cursor
        n_sims: Number of Monte Carlo simulations (default 5000)

    Returns:
        {
            run_id,
            production: {p10, p50, p90, mean},
            npv: {p10, p50, p90, mean},
            probability_npv_positive: float,
            per_period: [{period_label, production_p10, production_p50, production_p90}]
        }
    """
    try:
        return _monte_carlo_lom_impl(project_id, template_id, schedule, cursor, n_sims)
    except Exception as e:
        logger.error("monte_carlo_lom failed for project_id=%s, template_id=%s, n_sims=%d: %s",
                     project_id, template_id, n_sims, e)
        raise RuntimeError(f"monte_carlo_lom failed for project {project_id}: {e}") from e


def _monte_carlo_lom_impl(project_id: str, template_id: str,
                           schedule: list[dict], cursor,
                           n_sims: int = 5000) -> dict:
    """Internal implementation of monte_carlo_lom."""
    try:
        from engines.process_simulator import _load_project_economics
    except ImportError:
        from .process_simulator import _load_project_economics
    try:
        from engines.dcf import compute_npv
    except ImportError:
        from .dcf import compute_npv

    t0 = time.time()
    run_id = str(uuid.uuid4())
    rng = np.random.default_rng(seed=None)  # random seed for true MC

    econ = _load_project_economics(project_id, cursor)
    base_gold_price = econ.get("gold_price_usd_oz") or 2340.0
    capex_musd = econ.get("capex_musd") or 150.0
    capex_usd = capex_musd * 1e6
    design_tph = econ.get("target_tph") or 1517.0
    hours_day = econ.get("operating_hours_day") or 24.0
    avail = econ.get("availability_pct") or 92.0
    elec_rate = econ.get("electricity_rate") or 0.075
    # Get design BWi from LIMS (average from b1 testwork)
    try:
        cursor.execute("SAVEPOINT mc_bwi")
        cursor.execute("SELECT AVG(mb_kwh_t) FROM lims_b1 WHERE project_id=%s AND mb_kwh_t IS NOT NULL", (project_id,))
        r = cursor.fetchone()
        design_bwi = float(r[0]) if r and r[0] else DEFAULT_BWI
        cursor.execute("RELEASE SAVEPOINT mc_bwi")
    except Exception:
        cursor.execute("ROLLBACK TO SAVEPOINT mc_bwi")
        design_bwi = DEFAULT_BWI

    # Build confidence map for grade uncertainty
    conf_map_mc = _build_confidence_map(project_id, cursor)

    annual_hours = hours_day * 365.0 * (avail / 100.0)
    n_periods = len(schedule)

    # Pre-compute base period data
    base_grades = np.array([float(p["grade_au_avg"]) for p in schedule])
    base_bwis = np.array([float(p["bwi_avg"]) for p in schedule])

    # Determine confidence per period for grade sigma
    period_confidence = []
    for p in schedule:
        rtm = p.get("rock_type_mix", {})
        # Dominant rock type determines confidence
        if rtm:
            dominant_rt = max(rtm.items(), key=lambda x: x[1])[0]
        else:
            dominant_rt = "Unknown"
        conf = _get_confidence(dominant_rt, conf_map_mc)
        period_confidence.append(conf)

    grade_sigmas = np.array([
        CONFIDENCE_GRADE_SIGMA.get(c, 0.15) for c in period_confidence
    ])

    # Arrays to collect simulation results
    total_production_arr = np.zeros(n_sims)
    npv_arr = np.zeros(n_sims)
    per_period_production = np.zeros((n_sims, n_periods))

    # Gold price perturbation parameters (log-normal)
    price_sigma = 0.20
    log_mu = np.log(base_gold_price) - 0.5 * price_sigma ** 2

    for sim in range(n_sims):
        # Sample gold price for this simulation (same for all periods)
        gold_price = float(rng.lognormal(log_mu, price_sigma))

        total_prod = 0.0
        cashflows_sim = []

        for pi in range(n_periods):
            # Perturb grade
            grade_sigma_abs = base_grades[pi] * grade_sigmas[pi]
            grade = max(0.01, float(rng.normal(base_grades[pi], grade_sigma_abs)))

            # Perturb BWi (+/-10%)
            bwi = max(10.0, float(rng.normal(base_bwis[pi], base_bwis[pi] * 0.10)))

            # Throughput from BWi
            actual_tph = design_tph * (design_bwi / bwi) ** 0.8
            actual_tph = min(actual_tph, design_tph * 1.15)

            annual_tonnes = actual_tph * annual_hours

            # Perturb recovery (+/-3 pp)
            base_recovery = 90.0  # simplified base
            recovery = np.clip(float(rng.normal(base_recovery, 3.0)), 50.0, 99.5) / 100.0

            # Production
            production_g = annual_tonnes * grade * recovery
            production_oz = production_g * TROY_OZ_PER_GRAM

            # Economics
            revenue = production_oz * gold_price
            opex_per_t = 15.0 * elec_rate + 0.8 * 2.5 + 5.0  # simplified
            opex = opex_per_t * annual_tonnes
            sustaining = capex_usd * 0.03
            royalty = revenue * 0.05
            cashflow = revenue - opex - sustaining - royalty

            total_prod += production_oz
            cashflows_sim.append(cashflow)
            per_period_production[sim, pi] = production_oz

        total_production_arr[sim] = total_prod
        npv_val = compute_npv(cashflows_sim, 0.05, capex_usd)
        npv_arr[sim] = npv_val

    # Aggregate results
    per_period_results = []
    for pi in range(n_periods):
        pp = per_period_production[:, pi]
        per_period_results.append({
            "period_label": schedule[pi]["period_label"],
            "production_p10": round(float(np.percentile(pp, 10)), 0),
            "production_p50": round(float(np.percentile(pp, 50)), 0),
            "production_p90": round(float(np.percentile(pp, 90)), 0),
            "production_mean": round(float(np.mean(pp)), 0),
        })

    duration = time.time() - t0

    # Save run metadata
    cursor.execute(
        "INSERT INTO simulation_runs_v2 "
        "(id, project_id, template_id, run_type, status, params, results, duration_s) "
        "VALUES (%s, %s, %s, 'monte_carlo_lom', 'completed', %s::jsonb, %s::jsonb, %s)",
        (
            run_id, project_id, template_id,
            json.dumps({"n_sims": n_sims, "n_periods": n_periods}),
            json.dumps({
                "npv_p50": round(float(np.percentile(npv_arr, 50)) / 1e6, 2),
                "production_p50": round(float(np.percentile(total_production_arr, 50)), 0),
                "prob_npv_positive": round(float(np.mean(npv_arr > 0)), 4),
            }),
            round(duration, 2),
        ),
    )

    # Update lom_profiles with P10/P50/P90 if a previous LOM run exists
    # Find the latest LOM run for this project
    cursor.execute(
        "SELECT id FROM simulation_runs_v2 "
        "WHERE project_id = %s AND run_type = 'lom_simulation' "
        "ORDER BY created_at DESC LIMIT 1",
        (project_id,),
    )
    lom_run = cursor.fetchone()
    if lom_run:
        lom_run_id = str(lom_run["id"])
        for ppr in per_period_results:
            cursor.execute(
                "UPDATE lom_profiles SET "
                "production_p10 = %s, production_p50 = %s, production_p90 = %s "
                "WHERE run_id = %s AND period_label = %s",
                (
                    ppr["production_p10"],
                    ppr["production_p50"],
                    ppr["production_p90"],
                    lom_run_id,
                    ppr["period_label"],
                ),
            )

    logger.info("Monte Carlo LOM: %d sims in %.1fs, NPV P50=%.1f $M",
                n_sims, duration,
                float(np.percentile(npv_arr, 50)) / 1e6)

    return {
        "run_id": run_id,
        "n_simulations": n_sims,
        "production": {
            "p10": round(float(np.percentile(total_production_arr, 10)), 0),
            "p50": round(float(np.percentile(total_production_arr, 50)), 0),
            "p90": round(float(np.percentile(total_production_arr, 90)), 0),
            "mean": round(float(np.mean(total_production_arr)), 0),
        },
        "npv": {
            "p10": round(float(np.percentile(npv_arr, 10)) / 1e6, 2),
            "p50": round(float(np.percentile(npv_arr, 50)) / 1e6, 2),
            "p90": round(float(np.percentile(npv_arr, 90)) / 1e6, 2),
            "mean": round(float(np.mean(npv_arr)) / 1e6, 2),
        },
        "probability_npv_positive": round(float(np.mean(npv_arr > 0)), 4),
        "per_period": per_period_results,
        "duration_s": round(duration, 2),
    }


# ============================================================================
# ESG Timeline
# ============================================================================

def esg_timeline(project_id: str, lom_profiles: list[dict],
                 cursor) -> dict:
    """
    Generate ESG dashboard data per period from LOM simulation results.

    Provides CO2/oz, water usage, cumulative tailings, and sustainability
    recommendations benchmarked against WGC standards.

    Args:
        project_id: UUID of the project
        lom_profiles: List of period dicts from simulate_lom
        cursor: database cursor

    Returns:
        {
            co2_per_oz_by_period: [{period, value}],
            water_m3h_by_period: [{period, value}],
            cumulative_tailings_mt: [{period, value}],
            peak_co2_year: str,
            average_co2_oz: float,
            wgc_benchmark: 800,
            recommendations: [str]
        }
    """
    try:
        return _esg_timeline_impl(project_id, lom_profiles, cursor)
    except Exception as e:
        logger.error("esg_timeline failed for project_id=%s, n_profiles=%d: %s",
                     project_id, len(lom_profiles), e)
        return {
            "co2_per_oz_by_period": [],
            "water_m3h_by_period": [],
            "cumulative_tailings_mt": [],
            "peak_co2_year": "",
            "peak_co2_oz": 0.0,
            "average_co2_oz": 0.0,
            "wgc_benchmark": WGC_CO2_BENCHMARK,
            "total_tailings_mt": 0.0,
            "recommendations": [],
        }


def _esg_timeline_impl(project_id: str, lom_profiles: list[dict],
                        cursor) -> dict:
    """Internal implementation of esg_timeline."""
    co2_by_period = []
    water_by_period = []
    tailings_by_period = []
    cumulative_tailings = 0.0

    peak_co2 = 0.0
    peak_co2_year = ""
    total_co2_weighted = 0.0
    total_oz = 0.0

    for p in lom_profiles:
        period_label = p.get("period_label", "")
        co2 = float(p.get("co2_per_oz", 0))
        water = float(p.get("water_m3h", 0))
        tonnage = float(p.get("tonnage", 0))
        production_oz = float(p.get("production_oz", 0))

        co2_by_period.append({"period": period_label, "value": round(co2, 1)})
        water_by_period.append({"period": period_label, "value": round(water, 1)})

        # Cumulative tailings (assuming ~99% of feed reports to tailings)
        cumulative_tailings += tonnage * 0.99 / 1e6  # Mt
        tailings_by_period.append({
            "period": period_label,
            "value": round(cumulative_tailings, 2),
        })

        if co2 > peak_co2:
            peak_co2 = co2
            peak_co2_year = period_label

        total_co2_weighted += co2 * production_oz
        total_oz += production_oz

    average_co2 = total_co2_weighted / total_oz if total_oz > 0 else 0

    # Generate recommendations
    recommendations = []

    if average_co2 > WGC_CO2_BENCHMARK:
        recommendations.append(
            f"Average CO2 intensity ({average_co2:.0f} kg/oz) exceeds WGC benchmark "
            f"({WGC_CO2_BENCHMARK} kg/oz). Consider renewable energy integration."
        )
    else:
        recommendations.append(
            f"CO2 intensity ({average_co2:.0f} kg/oz) is below WGC benchmark "
            f"({WGC_CO2_BENCHMARK} kg/oz) — good environmental performance."
        )

    if peak_co2 > WGC_CO2_BENCHMARK * 1.5:
        recommendations.append(
            f"Peak CO2 in {peak_co2_year} ({peak_co2:.0f} kg/oz) is significantly high. "
            f"This correlates with hard ore (high BWi) — consider HPGR pre-treatment."
        )

    if cumulative_tailings > 100:
        recommendations.append(
            f"Cumulative tailings of {cumulative_tailings:.0f} Mt require significant "
            f"TSF capacity. Consider dry-stack tailings for later years."
        )

    # Water-related
    avg_water = np.mean([float(w["value"]) for w in water_by_period]) if water_by_period else 0
    if avg_water > 2000:
        recommendations.append(
            f"Average water consumption ({avg_water:.0f} m3/h) is high. "
            f"Consider water recycling from tailings thickener overflow."
        )

    recommendations.append(
        "Implement real-time emissions monitoring to track Scope 1 & 2 "
        "emissions against Paris Agreement targets."
    )

    return {
        "co2_per_oz_by_period": co2_by_period,
        "water_m3h_by_period": water_by_period,
        "cumulative_tailings_mt": tailings_by_period,
        "peak_co2_year": peak_co2_year,
        "peak_co2_oz": round(peak_co2, 1),
        "average_co2_oz": round(average_co2, 1),
        "wgc_benchmark": WGC_CO2_BENCHMARK,
        "total_tailings_mt": round(cumulative_tailings, 2),
        "recommendations": recommendations,
    }
