"""Circuit Lixiviation — Leaching (CIL/CIP) simulation."""
from __future__ import annotations
import math
from ..stream import Stream
from ..constants import leach_recovery, AGITATOR_SPECIFIC_POWER_KW_M3, LEACH_RECOVERY_WARNING_PCT


def simulate_leaching(stream: Stream, params: dict) -> dict:
    """Simulate CIL/CIP leaching circuit. Pure function."""
    leach_type = params.get("leaching_type", "cil")
    srt_h = params.get("leaching_srt_h", 24.0)
    n_tanks = params.get("leaching_n_tanks", 8)
    nacn_kg_t = params.get("leaching_nacn_kg_t", 0.5)
    cao_kg_t = params.get("leaching_cao_kg_t", 1.5)
    target_recovery = params.get("leaching_recovery_pct", 92.0)
    pct_solids_leach = params.get("leaching_pct_solids", 45.0)
    carbon_conc_g_l = params.get("carbon_conc_g_l", 20.0)

    tph = stream.solids_tph
    au = stream.au_g_t
    ore_sg = params.get("ore_sg", 2.75)

    # Clamp tanks to [4, 12]
    n_tanks = max(4, min(12, n_tanks))

    # ── Kinetics ──
    # Base rate constant from target recovery and SRT
    # R = 1 - exp(-k × t) → k = -ln(1 - R/100) / t
    k_base = -math.log(1 - min(target_recovery, 99.5) / 100.0) / max(srt_h, 1)

    # Modifiers (CN, DO, pH, Temperature) — simplified
    k_cn = 1.0  # Assume adequate CN
    k_do = 1.0  # Assume adequate DO
    k_ph = 1.0  # Assume pH 10.5
    k_temp = 1.0  # Assume 25°C
    k_eff = k_base * k_cn * k_do * k_ph * k_temp

    recovery_pct = leach_recovery(k_eff, srt_h)

    # ── Tank sizing ──
    # Slurry SG
    w = pct_solids_leach / 100.0
    sg_slurry = 1.0 / (w / ore_sg + (1 - w) / 1.0)
    # Volumetric flow
    vol_flow_m3h = tph / (sg_slurry * w)
    vol_total_m3 = vol_flow_m3h * srt_h
    vol_per_tank = vol_total_m3 / n_tanks
    # Tank dimensions (H/D = 1.0)
    tank_diam = (4 * vol_per_tank / math.pi) ** (1 / 3)
    tank_height = tank_diam  # H/D = 1.0

    # ── Agitation power ──
    agitator_power_per_tank = AGITATOR_SPECIFIC_POWER_KW_M3 * vol_per_tank
    total_agitator_power = agitator_power_per_tank * n_tanks

    # ── Reagent consumption ──
    nacn_kg_h = nacn_kg_t * tph
    cao_kg_h = cao_kg_t * tph

    # ── Carbon inventory (CIL/CIP) ──
    carbon_per_tank_t = carbon_conc_g_l * vol_per_tank / 1000.0
    total_carbon_t = carbon_per_tank_t * (n_tanks - 1)  # Tank 1 = pre-leach (no carbon)
    operating_hours_day = params.get("operating_hours_day", 22.1)

    # ── Mass balance ──
    au_tails = au * (1 - recovery_pct / 100.0)
    au_recovered_g_h = au * tph * recovery_pct / 100.0
    # g Au per tonne of carbon in circuit (daily load onto carbon inventory)
    au_loaded_g_day = au_recovered_g_h * operating_hours_day
    carbon_loading_g_t = au_loaded_g_day / max(total_carbon_t, 1e-6)
    # Pregnant liquor concentration (g/m³ ≡ mg/L)
    solids_vol_m3h = tph / ore_sg
    liquor_m3h = max(vol_flow_m3h - solids_vol_m3h, 0.01)
    pregnant_solution_mg_l = au_recovered_g_h / liquor_m3h

    # ── Output stream ──
    output = stream.with_updates(au_g_t=au_tails, pct_solids=pct_solids_leach)

    equipment = [{
        "type": f"{'CIL' if leach_type == 'cil' else 'CIP'} Tank",
        "quantity": n_tanks,
        "volume_m3": round(vol_per_tank, 0),
        "diameter_m": round(tank_diam, 1),
        "height_m": round(tank_height, 1),
        "agitator_kw": round(agitator_power_per_tank, 0),
        "total_volume_m3": round(vol_total_m3, 0),
        "residence_time_h": srt_h,
    }]

    total_power = total_agitator_power + 500  # +500 kW for compressors, pumps
    alerts = []
    if recovery_pct < LEACH_RECOVERY_WARNING_PCT:
        alerts.append({"severity": "WARNING", "circuit": "Leaching",
                       "parameter": "recovery", "value": round(recovery_pct, 1),
                       "threshold": LEACH_RECOVERY_WARNING_PCT,
                       "action": "Review CN concentration, DO level, pH, or increase residence time"})

    return {
        "circuit_name": "Leaching",
        "input_stream": stream.to_dict(),
        "output_stream": output.to_dict(),
        "output_stream_obj": output,
        "mass_balance": {
            "feed_solids_tph": round(tph, 1),
            "feed_au_g_t": round(au, 3),
            "tails_au_g_t": round(au_tails, 3),
            "recovery_pct": round(recovery_pct, 1),
            "au_recovered_g_h": round(au_recovered_g_h, 1),
            "pregnant_solution_mg_l": round(pregnant_solution_mg_l, 2),
            "carbon_loading_g_t": round(carbon_loading_g_t, 0),
            "total_carbon_inventory_t": round(total_carbon_t, 1),
        },
        "equipment": equipment,
        "energy_kwh_t": round(total_power / max(tph, 1), 2),
        "power_kw": round(total_power, 0),
        "reagents": {"nacn_kg_t": nacn_kg_t, "nacn_kg_h": round(nacn_kg_h, 1),
                     "cao_kg_t": cao_kg_t, "cao_kg_h": round(cao_kg_h, 1),
                     "carbon_makeup_kg_t": 0.04},
        "alerts": alerts,
        "metadata": {"leach_type": leach_type, "k_eff": round(k_eff, 4), "n_tanks": n_tanks},
    }
