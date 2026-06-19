"""Circuit Gravité — Gravity recovery simulation (Knelson/Falcon)."""
from __future__ import annotations
import math
from ..stream import Stream
from ..constants import TROY_OZ_PER_GRAM, GRG_RECOMMEND_THRESHOLD_PCT, GRG_NOT_JUSTIFIED_PCT

try:
    from ...gravity_model import (
        plant_gravity_recovery_pct,
        resolve_gravity_params,
        blended_head_grade_g_t,
        gravity_concentrate_grade_g_t,
    )
except ImportError:
    from engines.gravity_model import (
        plant_gravity_recovery_pct,
        resolve_gravity_params,
        blended_head_grade_g_t,
        gravity_concentrate_grade_g_t,
    )


def simulate_gravity(stream: Stream, params: dict) -> dict:
    """Simulate gravity recovery circuit. Pure function."""
    gp = resolve_gravity_params(params)
    grg_pct = gp.grg_pct
    slip_pct = gp.slip_frac
    concentrator_type = params.get("concentrator_type", "Knelson")
    unit_capacity_tph = params.get("unit_capacity_tph", 150.0)
    operating_hours_day = params.get("operating_hours_day", 22.1)
    availability_pct = params.get("availability_pct", 92.0)

    tph = stream.solids_tph
    au = stream.au_g_t

    # Gravity feed = slip-stream of cyclone underflow
    gravity_feed_tph = tph * slip_pct
    _gravity_feed_m3h = gravity_feed_tph / 2.75 / 0.35  # Approximate at 35% solids

    # Number of concentrators
    n_units = max(1, math.ceil(gravity_feed_tph / unit_capacity_tph))
    n_installed = n_units + 1  # +1 standby

    overall_gravity_recovery_pct = plant_gravity_recovery_pct(gp)

    # Mass balance
    au_recovered_g_h = au * tph * (overall_gravity_recovery_pct / 100.0)
    mass_pull_frac = gp.mass_pull_frac
    concentrate_tpd = gravity_feed_tph * mass_pull_frac * operating_hours_day
    au_tails = blended_head_grade_g_t(au, overall_gravity_recovery_pct)

    # Annual production from gravity
    annual_hours = operating_hours_day * 365 * availability_pct / 100
    annual_au_g = au_recovered_g_h * annual_hours
    annual_au_oz = annual_au_g * TROY_OZ_PER_GRAM

    # ILR sizing
    ilr_volume_m3 = max(0.5, concentrate_tpd / 5.0)  # ~5 t/m³/day capacity
    ilr_retention_h = 24.0

    equipment = [
        {"type": f"{concentrator_type} Concentrator", "quantity": n_installed,
         "operating": n_units, "capacity_tph": unit_capacity_tph,
         "power_kw": round(n_installed * 75, 0), "model": f"{concentrator_type} XD-48"},
        {"type": "Intensive Leach Reactor (ILR)", "volume_m3": round(ilr_volume_m3, 1),
         "retention_time_h": ilr_retention_h, "power_kw": 30},
    ]
    total_power = n_installed * 75 + 30

    # Output stream (tails from gravity → next circuit)
    output = stream.with_updates(au_g_t=au_tails)

    # Alerts
    alerts = []
    if grg_pct >= GRG_RECOMMEND_THRESHOLD_PCT and not params.get("_gravity_already_active"):
        alerts.append({"severity": "INFO", "circuit": "Gravity", "parameter": "GRG",
                       "value": grg_pct, "threshold": GRG_RECOMMEND_THRESHOLD_PCT,
                       "action": f"GRG={grg_pct}% — gravity circuit recommended. Incremental recovery: {round(annual_au_oz, 0)} oz/year"})
    if grg_pct < GRG_NOT_JUSTIFIED_PCT:
        alerts.append({"severity": "INFO", "circuit": "Gravity", "parameter": "GRG",
                       "value": grg_pct, "threshold": GRG_NOT_JUSTIFIED_PCT,
                       "action": "GRG < 5% — gravity recovery may not be economically justified"})

    return {
        "circuit_name": "Gravity",
        "input_stream": stream.to_dict(),
        "output_stream": output.to_dict(),
        "output_stream_obj": output,
        "mass_balance": {
            "feed_solids_tph": round(gravity_feed_tph, 1),
            "feed_au_g_t": round(au, 3),
            "concentrate_tpd": round(concentrate_tpd, 2),
            "concentrate_grade_g_t": round(gravity_concentrate_grade_g_t(au, gp), 0),
            "tails_au_g_t": round(au_tails, 3),
            "gravity_recovery_pct": round(overall_gravity_recovery_pct, 2),
            "annual_gold_oz": round(annual_au_oz, 0),
        },
        "equipment": equipment,
        "energy_kwh_t": round(total_power / max(tph, 1), 3),
        "power_kw": total_power,
        "reagents": {"nacn_ilr_kg_batch": 5.0},
        "alerts": alerts,
    }
