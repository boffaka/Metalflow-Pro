"""Circuit Broyage — Grinding circuit simulation (SAG/Ball/HPGR/Vertimill)."""
from __future__ import annotations
import math
from ..stream import Stream
from ..constants import bond_energy, starkey_sag_power, hpgr_energy, MILL_MECHANICAL_EFFICIENCY, GRINDING_ENERGY_WARNING_KWH_T


def simulate_grinding(stream: Stream, params: dict) -> dict:
    """Simulate grinding circuit. Pure function.

    Params:
        grinding_type: sag_ball | hpgr_ball | ball_only | vertimill
        bwi_kwh_t: Bond Ball Mill Work Index
        axb: JK Drop Weight parameter
        target_p80_um: Target grind P80 (µm)
        circulating_load_pct: Ball mill circulating load (default 300%)
        cyc_of_pct_solids: Cyclone overflow % solids (default 35%)
    """
    grinding_type = params.get("grinding_type", "hpgr_ball")
    bwi = params.get("bwi_kwh_t", 14.0)
    axb = params.get("axb", 45.0)
    target_p80 = params.get("target_p80_um", 75.0)
    cl_pct = params.get("circulating_load_pct", 300.0)
    cyc_of_pct_sol = params.get("cyc_of_pct_solids", 35.0)
    tph = stream.solids_tph
    f80_um = stream.p80_um

    equipment = []
    total_power = 0.0
    sag_power = 0.0
    hpgr_power = 0.0
    bm_power = 0.0
    regrind_power = 0.0

    # Determine which sub-circuits are active based on grinding_type
    has_sag = grinding_type in ("sag_ball", "sag_ball_pebble", "sag_ball_verti")
    has_hpgr = grinding_type in ("hpgr_ball", "hpgr_ball_verti")
    has_ball = grinding_type not in ("vertimill",)
    has_verti = grinding_type in ("hpgr_ball_verti", "sag_ball_verti", "ball_verti", "vertimill")
    has_pebble = grinding_type == "sag_ball_pebble"

    # ── SAG Mill ──
    sag_p80_um = 2000.0  # SAG product ~2mm
    if has_sag:
        spi_min = 120.0 / max(axb, 1)  # Approximate SPI from Axb
        sag_power = starkey_sag_power(tph, spi_min)
        sag_diam = (sag_power / 1000) ** 0.4 * 3.5  # Empirical sizing
        sag_length = sag_diam * 0.5
        equipment.append({
            "type": "SAG Mill",
            "power_kw": round(sag_power, 0),
            "diameter_m": round(sag_diam, 1),
            "length_m": round(sag_length, 1),
            "spi_min": round(spi_min, 1),
            "f80_um": round(f80_um, 0),
            "p80_um": round(sag_p80_um, 0),
        })
        total_power += sag_power
        f80_um = sag_p80_um  # Ball mill feed = SAG product

    # ── Pebble Crusher (SAG recycle) ──
    if has_pebble:
        pebble_tph = tph * 0.15  # ~15% of feed recycles as pebbles
        pebble_power = pebble_tph * 0.5  # ~0.5 kWh/t for pebble crushing
        equipment.append({
            "type": "Pebble Crusher",
            "power_kw": round(pebble_power, 0),
            "capacity_tph": round(pebble_tph, 0),
            "f80_mm": 50,
            "p80_mm": 12,
            "note": "SAG discharge screen oversize recycle",
        })
        total_power += pebble_power

    # ── HPGR ──
    hpgr_p80_um = 4000.0  # HPGR product ~4mm
    if has_hpgr:
        spf = 4.5  # N/mm² typical
        hpgr_e = hpgr_energy(spf, f80_um / 1000.0, hpgr_p80_um / 1000.0)
        hpgr_power = hpgr_e * tph / 0.95  # 95% transmission efficiency
        roll_diam = 2.4  # m typical
        roll_length = 1.7  # m
        equipment.append({
            "type": "HPGR",
            "power_kw": round(hpgr_power, 0),
            "specific_energy_kwh_t": round(hpgr_e, 2),
            "roll_diameter_m": roll_diam,
            "roll_length_m": roll_length,
            "specific_force_n_mm2": spf,
            "f80_mm": round(f80_um / 1000, 1),
            "p80_mm": round(hpgr_p80_um / 1000, 1),
        })
        total_power += hpgr_power
        f80_um = hpgr_p80_um

    # ── Ball Mill ──
    if has_ball:
        bm_f80 = f80_um
        # For verti circuits, ball mill grinds to intermediate P80
        bm_target_p80 = target_p80 if not has_verti else max(target_p80 * 2, 150.0)
        bm_energy = bond_energy(bwi, bm_target_p80, bm_f80)
        bm_power_shaft = bm_energy * tph
        bm_power = bm_power_shaft / MILL_MECHANICAL_EFFICIENCY
        # Dimensions from power (empirical: P ∝ D^2.5 × L)
        bm_diam = (bm_power / 1000) ** 0.35 * 2.2
        bm_length = bm_diam * 1.5
        equipment.append({
            "type": "Ball Mill",
            "power_kw": round(bm_power, 0),
            "specific_energy_kwh_t": round(bm_energy, 2),
            "diameter_m": round(bm_diam, 1),
            "length_m": round(bm_length, 1),
            "f80_um": round(bm_f80, 0),
            "p80_um": round(bm_target_p80, 0),
            "circulating_load_pct": cl_pct,
        })
        total_power += bm_power
        f80_um = bm_target_p80  # Update for downstream

    # ── Vertimill (secondary/regrind) ──
    if has_verti:
        verti_f80 = f80_um if has_ball else f80_um
        verti_p80 = target_p80
        # Morrell Sig model: Vertimill is 30% more efficient than ball mill for fine grinding
        sig_factor = 0.7
        verti_e = bond_energy(bwi, verti_p80, verti_f80) * sig_factor
        regrind_power = verti_e * tph / 0.95
        _verti_diam = 3.0  # VTM-4500 typical
        n_vertimills = max(1, math.ceil(regrind_power / 4500))  # ~4500 kW per VTM-4500
        equipment.append({
            "type": "Vertimill (VTM-4500)",
            "quantity": n_vertimills,
            "power_kw": round(regrind_power, 0),
            "power_per_unit_kw": round(regrind_power / n_vertimills, 0),
            "specific_energy_kwh_t": round(verti_e, 2),
            "f80_um": round(verti_f80, 0),
            "p80_um": round(verti_p80, 0),
            "efficiency_factor": sig_factor,
        })
        total_power += regrind_power

    # ── Hydrocyclones ──
    cyc_feed_tph = tph * (1 + cl_pct / 100)
    cyc_of_tph = tph  # Steady state: overflow = fresh feed
    cyc_uf_tph = cyc_feed_tph - cyc_of_tph
    # Cyclone sizing
    cyc_feed_m3h = cyc_feed_tph / (2.75 * (65 / 100))  # Approximate
    cyc_unit_cap = 600  # m³/h per 660mm cyclone
    n_cyclones = max(4, math.ceil(cyc_feed_m3h / cyc_unit_cap))
    pump_power = cyc_feed_m3h * 2.14 * 9.81 * 25 / (3600 * 0.65)  # TDH=25m, η=65%
    equipment.append({
        "type": "Hydrocyclone Cluster",
        "quantity": n_cyclones + 1,  # +1 standby
        "operating": n_cyclones,
        "diameter_mm": 660,
        "feed_m3h": round(cyc_feed_m3h, 0),
        "overflow_tph": round(cyc_of_tph, 0),
        "underflow_tph": round(cyc_uf_tph, 0),
        "circulating_load_pct": cl_pct,
        "pump_power_kw": round(pump_power, 0),
    })
    total_power += pump_power

    total_energy = total_power / max(tph, 1)

    # ── Ore hardness classification (SME) ──
    if bwi < 7:
        hardness = "Very Soft"
    elif bwi < 10:
        hardness = "Soft"
    elif bwi < 14:
        hardness = "Medium"
    elif bwi < 20:
        hardness = "Hard"
    else:
        hardness = "Very Hard"

    # ── Output stream ──
    output = stream.with_updates(p80_um=target_p80, pct_solids=cyc_of_pct_sol)

    # ── Alerts ──
    alerts = []
    if total_energy > GRINDING_ENERGY_WARNING_KWH_T:
        alerts.append({
            "severity": "WARNING",
            "circuit": "Grinding",
            "parameter": "specific_energy",
            "value": round(total_energy, 1),
            "threshold": GRINDING_ENERGY_WARNING_KWH_T,
            "action": "Evaluate HPGR pre-grinding or ore sorting to reduce energy consumption",
        })

    return {
        "circuit_name": "Grinding",
        "input_stream": stream.to_dict(),
        "output_stream": output.to_dict(),
        "output_stream_obj": output,
        "mass_balance": {
            "fresh_feed_tph": round(tph, 1),
            "cyclone_feed_tph": round(cyc_feed_tph, 1),
            "cyclone_overflow_tph": round(cyc_of_tph, 1),
            "cyclone_underflow_tph": round(cyc_uf_tph, 1),
            "product_p80_um": round(target_p80, 0),
            "circulating_load_pct": cl_pct,
            "ore_hardness": hardness,
        },
        "equipment": equipment,
        "energy_kwh_t": round(total_energy, 2),
        "power_kw": round(total_power, 0),
        "reagents": {"steel_balls_kg_t": round(bwi * 0.04, 2)},
        "alerts": alerts,
        "metadata": {"grinding_type": grinding_type, "bwi": bwi, "hardness": hardness},
    }
