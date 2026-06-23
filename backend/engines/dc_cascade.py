"""
DC Cascade Recalculation Engine.

Loads a DAG of dependencies between DC parameters from dc_dag_registry.yaml.
When any parameter changes, propagates recalculations through the graph
using topological sort, skipping Manual overrides.
"""
from __future__ import annotations

import logging
import math
import pathlib
from collections import deque
from typing import Any

import yaml

try:
    from .dc_formulas import (
        as_fraction,
        bond_energy_kwh_t,
        circular_diameter_m,
        cylindrical_volume_diameter_m,
        installed_power_kw,
        residence_volume_m3,
        roundup_units,
        shaft_power_kw,
        slurry_density_t_m3,
        slurry_volume_m3h,
    )
except ImportError:  # pragma: no cover - supports direct script imports
    from dc_formulas import (  # type: ignore[no-redef]
        as_fraction,
        bond_energy_kwh_t,
        circular_diameter_m,
        cylindrical_volume_diameter_m,
        installed_power_kw,
        residence_volume_m3,
        roundup_units,
        shaft_power_kw,
        slurry_density_t_m3,
        slurry_volume_m3h,
    )

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM

logger = logging.getLogger("mpdpms.dc_cascade")

_DAG_PATH = pathlib.Path(__file__).parent / "dc_dag_registry.yaml"
_DAG_CACHE: dict | None = None


def load_dag(path: pathlib.Path | None = None) -> dict:
    try:
        global _DAG_CACHE
        if _DAG_CACHE is not None and path is None:
            return _DAG_CACHE
        p = path or _DAG_PATH
        data = yaml.safe_load(p.read_text())
        if path is None:
            _DAG_CACHE = data
        return data
    except Exception as e:
        logger.error("load_dag failed for path=%s: %s", path or _DAG_PATH, e)
        raise RuntimeError(f"load_dag failed for {path or _DAG_PATH}: {e}") from e


def topological_sort(dag: dict) -> list[str]:
    """Kahn's algorithm for topological ordering of the DAG nodes."""
    try:
        nodes = dag["nodes"]
        _inputs = set(dag.get("inputs", []))
        in_degree = {k: 0 for k in nodes}
        adj: dict[str, list[str]] = {k: [] for k in nodes}

        for key, node in nodes.items():
            for dep in node["depends_on"]:
                if dep in nodes:
                    adj[dep].append(key)
                    in_degree[key] += 1

        queue = deque(k for k, d in in_degree.items() if d == 0)
        order = []
        while queue:
            n = queue.popleft()
            order.append(n)
            for child in adj[n]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(order) != len(nodes):
            missing = set(nodes) - set(order)
            raise ValueError(f"Cycle detected in DAG involving: {missing}")
        return order
    except ValueError:
        raise
    except Exception as e:
        logger.error("topological_sort failed: %s", e)
        raise RuntimeError(f"topological_sort failed: {e}") from e


def get_downstream_nodes(dag: dict, changed_keys: list[str]) -> set[str]:
    """BFS to find all nodes transitively dependent on the changed keys."""
    try:
        nodes = dag["nodes"]
        # Build reverse adjacency: key -> list of nodes that depend on key
        reverse_adj: dict[str, list[str]] = {}
        for key, node in nodes.items():
            for dep in node["depends_on"]:
                reverse_adj.setdefault(dep, []).append(key)

        visited = set()
        queue = deque(changed_keys)
        while queue:
            k = queue.popleft()
            for child in reverse_adj.get(k, []):
                if child not in visited:
                    visited.add(child)
                    queue.append(child)
        return visited
    except Exception as e:
        logger.error("get_downstream_nodes failed for changed_keys=%s: %s", changed_keys, e)
        return set()


def compute_formula(formula_ref: str, params: dict[str, float]) -> float:
    """Compute a single formula given its reference and input parameters."""
    f = _FORMULAS.get(formula_ref)
    if not f:
        raise ValueError(f"Unknown formula_ref: {formula_ref}")
    return f(params)


def cascade_recalculate(
    dag: dict,
    current_values: dict[str, float],
    source_map: dict[str, str],
    changes: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Apply changes and propagate cascade through the DAG.

    Args:
        dag: Loaded DAG registry
        current_values: {key: current_value} for all params
        source_map: {key: source} where "Manual" nodes are skipped
        changes: [{"key": str, "value": float}]

    Returns:
        (updates, alerts) where:
        - updates: [{"key", "old", "new", "reason"}]
        - alerts: [{"severity", "message"}]
    """
    try:
        return _cascade_recalculate_impl(dag, current_values, source_map, changes)
    except Exception as e:
        logger.error("cascade_recalculate failed for changes=%s: %s", changes, e)
        return [], [{"severity": "error", "message": f"Cascade recalculation failed: {e}"}]


def _cascade_recalculate_impl(
    dag: dict,
    current_values: dict[str, float],
    source_map: dict[str, str],
    changes: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Internal implementation of cascade_recalculate."""
    values = dict(current_values)
    updates = []
    alerts = []

    # Apply direct changes
    changed_keys = []
    for c in changes:
        _old = values.get(c["key"])
        values[c["key"]] = c["value"]
        changed_keys.append(c["key"])

    # Find all downstream nodes
    downstream = get_downstream_nodes(dag, changed_keys)
    if not downstream:
        return updates, alerts

    # Get topological order, filter to downstream only
    full_order = topological_sort(dag)
    recalc_order = [k for k in full_order if k in downstream]

    # Recalculate in order
    for key in recalc_order:
        if source_map.get(key) in ("Manual", "M", "O"):
            continue  # Never overwrite engineer's manual override

        node = dag["nodes"][key]
        # Gather inputs for this formula
        params = {}
        for dep in node["depends_on"]:
            if dep in values:
                params[dep] = values[dep]

        try:
            new_val = compute_formula(node["formula_ref"], params)
        except Exception as exc:
            alerts.append({
                "severity": "error",
                "message": f"Erreur calcul {key} ({node['formula_ref']}): {exc}",
            })
            logger.error("Error computing %s (%s): %s", key, node["formula_ref"], exc)
            continue

        old_val = values.get(key)
        if old_val is not None and abs(new_val - old_val) < 0.001:
            continue  # No meaningful change

        values[key] = new_val
        reason = ", ".join(f"{c['key']}={c['value']}" for c in changes)
        updates.append({
            "key": key,
            "old": round(old_val, 4) if old_val is not None else None,
            "new": round(new_val, 4),
            "reason": f"Cascade from {reason}",
        })

        # Check bounds (if defined in the node)
        node_min = node.get("min_value")
        node_max = node.get("max_value")
        if node_min is not None and new_val < node_min:
            alerts.append({"severity": "warning", "message": f"{key}: {new_val:.1f} below minimum {node_min}"})
        if node_max is not None and new_val > node_max:
            alerts.append({"severity": "warning", "message": f"{key}: {new_val:.1f} exceeds maximum {node_max}"})

    return updates, alerts


# ── Formula implementations ─────────────────────────────────────────────────
# Each formula takes a params dict and returns a float.
# These wrap the existing calculations from design.py and dc_calculator.py.

def _bond_power_bm(p: dict) -> float:
    """Bond 3rd Law for BALL MILL: W = 10 × Wi × (1/√P80 - 1/√F80) [kWh/t]
    F80 = ball mill feed (from SAG/HPGR product, in µm)
    P80 = ball mill product (target grind, in µm)
    Ref: Bond F.C. (1961), Allis-Chalmers Technical Papers."""
    tph = p.get("target_tph", 0)
    wi = p.get("avg_bwi", 14.0)
    f80 = p.get("bm_f80_um", 3000)  # BM feed = upstream product in µm
    p80 = p.get("avg_p80_um", 75.0)  # BM product target in µm
    w = bond_energy_kwh_t(wi, f80, p80)
    shaft_kw = shaft_power_kw(w, tph)
    installed_kw = installed_power_kw(shaft_kw, p.get("mech_efficiency", 0.95), p.get("bm_install_margin_pct", 10.0))
    return round(installed_kw, 0)


def _bond_power_sag(p: dict) -> float:
    """Bond 3rd Law for SAG MILL: sizes in mm, converted to µm internally.
    F80 = SAG feed (from crusher product, in mm)
    P80 = SAG discharge (in mm)"""
    tph = p.get("target_tph", 0)
    wi = p.get("avg_bwi", 14.0)
    f80_mm = p.get("sag_f80_mm", 100.0)  # SAG feed in mm
    p80_mm = p.get("sag_p80_mm", 2.0)    # SAG discharge in mm
    # Convert mm → µm for Bond equation
    w = bond_energy_kwh_t(wi, f80_mm * 1000, p80_mm * 1000)
    return round(installed_power_kw(shaft_power_kw(w, tph), p.get("mech_efficiency", 0.95), 0), 0)


def _normalize_fraction(value: Any, default: float) -> float:
    """Accept either a fraction (0.95) or a percent (95) from PDC rows."""
    return as_fraction(value, default)


def _crush_product(p: dict) -> float:
    css = p.get("pc_css_mm") or p.get("sc_css_mm", 35.0)
    return round(css * 0.96, 1)


def _cascade_feed(p: dict) -> float:
    return p.get("sc_p80_mm", 35.0)


def _sag_to_bm_feed(p: dict) -> float:
    return p.get("sag_p80_mm", 2.0) * 1000  # mm to um


def _sum_power(p: dict) -> float:
    return (p.get("sag_power_kw", 0) + p.get("bm_power_kw", 0))


def _circ_load_feed(p: dict) -> float:
    tph = p.get("target_tph", 0)
    cl = p.get("bm_circ_load_pct", 250.0)
    return round(tph * (1 + cl / 100), 1)


def _mass_pull_tonnage(p: dict) -> float:
    tph = p.get("target_tph", 0)
    pull = p.get("flot_mass_pull_pct", 17.0)
    return round(tph * pull / 100, 1)


def _regrind_feed(p: dict) -> float:
    tph = p.get("target_tph", 0)
    pull = p.get("flot_mass_pull_pct", 6.0)
    has_flot = p.get("has_flotation", True)
    if has_flot:
        return round(tph * pull / 100, 1)
    return float(tph)


def _regrind_specific_energy(p: dict) -> float:
    sig = p.get("regrind_sig_kwh_t", 7.5)
    f80 = max(p.get("regrind_feed_p80_um", 106.0), 1.0)
    p80 = max(p.get("regrind_product_p80_um", 25.0), 1.0)
    f80 = max(f80, p80 + 1.0)
    return round(sig * math.log(f80 / p80), 2)


def _regrind_shaft_power(p: dict) -> float:
    feed = p.get("regrind_feed_tph", 0)
    energy = p.get("regrind_specific_energy_kwh_t", 0)
    return round(shaft_power_kw(energy, feed), 0)


def _regrind_power(p: dict) -> float:
    shaft = _regrind_shaft_power(p)
    return round(installed_power_kw(shaft, p.get("regrind_mech_efficiency", 0.94), p.get("regrind_install_margin_pct", 0.15)), 0)


def _leach_feed(p: dict) -> float:
    tph = p.get("target_tph", 0)
    pull = p.get("flot_mass_pull_pct", 17.0)
    has_flot = p.get("has_flotation", False)
    if has_flot:
        return round(tph * pull / 100, 1)
    return float(tph)


def _slurry_density(p: dict) -> float:
    sg = p.get("ore_sg", 2.75)
    return round(slurry_density_t_m3(sg, p.get("cil_pct_solids", 45.0)), 4)


def _volumetric_flow(p: dict) -> float:
    feed = p.get("leach_feed_tph", 0)
    ore_sg = p.get("ore_sg") or p.get("solids_sg") or 2.75
    if p.get("ore_sg") or p.get("solids_sg"):
        return round(slurry_volume_m3h(feed, ore_sg, p.get("cil_pct_solids", 45.0)), 1)
    cs = max(as_fraction(p.get("cil_pct_solids", 45.0), 0.45), 0.01)
    pulp_sg = max(float(p.get("slurry_sg", 1.4) or 1.4), 0.01)
    return round(float(feed) / (pulp_sg * cs), 1)


def _residence_volume(p: dict) -> float:
    flow = p.get("vol_flow_m3h", 0)
    srt = p.get("cil_srt_h", 24.0)
    return round(residence_volume_m3(flow, srt), 0)


def _tank_count(p: dict) -> int:
    vol = p.get("cil_volume_m3", 0)
    max_vol = p.get("max_vol_per_tank", 1200)
    return roundup_units(vol, max_vol)


def _tank_geometry(p: dict) -> float:
    vol = p.get("cil_volume_m3", 0)
    n = p.get("cil_n_tanks", 8)
    hd = p.get("cil_hd_ratio", 1.0)
    vol_per = vol / max(n, 1)
    # V = pi/4 * D^2 * H, H = hd*D → V = pi/4 * D^3 * hd
    d = cylindrical_volume_diameter_m(vol_per, hd)
    return round(d, 2)


def _nacn_rate(p: dict) -> float:
    """NaCN consumption (kg/h) = leach feed (t/h) × NaCN dosage (kg/t)."""
    feed = p.get("leach_feed_tph", 0)
    rate = p.get("avg_nacn_kg_t", 0)
    if rate is None:
        rate = 0
    return round(feed * rate, 1)


def _cao_rate(p: dict) -> float:
    """CaO/lime consumption (kg/h) = leach feed (t/h) × CaO dosage (kg/t)."""
    feed = p.get("leach_feed_tph", 0)
    rate = p.get("avg_cao_kg_t", 0)
    if rate is None:
        rate = 0
    return round(feed * rate, 1)


def _thickener_area(p: dict) -> float:
    """Area = tpd × unit_area [m²/(t/d)] × safety_factor → m²
    UA convention: m² per tonne per day (m².d/t or m²/tpd).
    Ref: Côté Gold NI 43-101 Table 17-1: UA = 0.075 m²/tpd.
    Higher UA → larger thickener (more area per tonne)."""
    tph = float(p.get("target_tph") or 0)
    ua  = max(float(p.get("avg_unit_area") or 0.075), 1e-6)  # m²/tpd; clamped > 0
    sf  = float(p.get("thickener_safety_factor") or 1.15)
    tpd = tph * 24
    return round(tpd * ua * sf, 0)


def _circular_diameter(p: dict) -> float:
    area = p.get("thickener_area_m2", 0)
    return round(circular_diameter_m(area), 1)


def _unit_count_by_max(p: dict) -> int:
    d = p.get("thickener_diameter_m", 0)
    d_max = p.get("thickener_max_diameter_m", 45.0)
    if d <= d_max:
        return 1
    return math.ceil(d / d_max)


def _process_water(p: dict) -> float:
    """Process water flow (m³/h). Water tph ≈ m³/h since water density ≈ 1 t/m³."""
    feed_solids_tph = p.get("leach_feed_tph", 0)
    pct = p.get("cil_pct_solids", 45.0) / 100.0  # fraction solids by mass
    # Water_tph = Solids_tph × (1 - pct_solids) / pct_solids
    water_tph = feed_solids_tph * (1.0 - pct) / max(pct, 0.01)
    # Convert t/h to m³/h: water density = 1.0 t/m³
    return round(water_tph, 1)


def _tailings_water(p: dict) -> float:
    """Tailings water loss (m³/h). Water entrained in thickener underflow."""
    tph = p.get("target_tph", 0)  # total solids to tailings
    uf = p.get("underflow_pct_solids", 55.0) / 100.0  # fraction solids in underflow
    # Water in underflow = solids × (1 - pct_solids) / pct_solids
    water_tph = tph * (1.0 - uf) / max(uf, 0.01)
    return round(water_tph, 1)  # t/h ≈ m³/h


def _evaporation(p: dict) -> float:
    pw = p.get("process_water_m3h", 0)
    ef = p.get("evap_factor", 0.015)
    return round(pw * ef, 1)


def _fresh_water(p: dict) -> float:
    tl = p.get("tailings_water_loss_m3h", 0)
    ev = p.get("evaporation_m3h", 0)
    return round(tl + ev, 1)


def _annual_production(p: dict) -> float:
    tph = p.get("target_tph", 0)
    grade = p.get("gold_grade_g_t", 1.0)
    rec = p.get("avg_au_recovery_pct", 89.0) / 100.0
    avail = p.get("availability_pct", 92.0) / 100.0
    hours = p.get("operating_hours_day", 24.0)
    annual_t = tph * hours * 365 * avail
    gold_g = annual_t * grade * rec
    return round(gold_g * TROY_OZ_PER_GRAM, 0)


def _total_power(p: dict) -> float:
    """Total installed power = comminution + auxiliary (pumps, agitators, lighting, etc.).
    Aux power estimated at 5 kWh/t × tph if not explicitly provided."""
    commin = p.get("total_commin_power_kw", 0)
    tph = p.get("target_tph", 0)
    aux_kwh_t = p.get("aux_energy_kwh_t", 5.0)
    aux_power = tph * aux_kwh_t
    return round(commin + aux_power, 0)


def _energy_cost(p: dict) -> float:
    """Specific energy cost ($/t ore processed).
    = (installed_power_kW / throughput_tph) × energy_rate_$/kWh → $/t"""
    power = p.get("total_installed_power_kw", 0)
    tph = p.get("target_tph", 1)
    rate = p.get("energy_rate_usd_kwh", 0.08)
    specific_kwh_t = power / max(tph, 1)
    return round(specific_kwh_t * rate, 2)


_FORMULAS = {
    "bond_power_bm": _bond_power_bm,
    "bond_power_sag": _bond_power_sag,
    "crush_product": _crush_product,
    "cascade_feed": _cascade_feed,
    "sag_to_bm_feed": _sag_to_bm_feed,
    "sum_power": _sum_power,
    "circ_load_feed": _circ_load_feed,
    "mass_pull_tonnage": _mass_pull_tonnage,
    "regrind_feed": _regrind_feed,
    "regrind_specific_energy": _regrind_specific_energy,
    "regrind_shaft_power": _regrind_shaft_power,
    "regrind_power": _regrind_power,
    "leach_feed": _leach_feed,
    "slurry_density": _slurry_density,
    "volumetric_flow": _volumetric_flow,
    "residence_volume": _residence_volume,
    "tank_count": _tank_count,
    "tank_geometry": _tank_geometry,
    "nacn_rate": _nacn_rate,
    "cao_rate": _cao_rate,
    "thickener_area": _thickener_area,
    "circular_diameter": _circular_diameter,
    "unit_count_by_max": _unit_count_by_max,
    "process_water": _process_water,
    "tailings_water": _tailings_water,
    "evaporation": _evaporation,
    "fresh_water": _fresh_water,
    "annual_production": _annual_production,
    "total_power": _total_power,
    "energy_cost": _energy_cost,
}
