"""Circuit Flottation — Flotation simulation (optional)."""
from __future__ import annotations
import math
from ..stream import Stream
from ..constants import flotation_recovery, FLOTATION_SPECIFIC_POWER_KW_M3, FLOTATION_RECOVERY_WARNING_PCT


def simulate_flotation(stream: Stream, params: dict) -> dict:
    """Simulate flotation circuit. Pure function."""
    k_rate = params.get("flotation_k_rate", 1.5)
    residence_min = params.get("flotation_residence_min", 20.0)
    rmax_pct = params.get("flotation_rmax_pct", 90.0)
    mass_pull_pct = params.get("flotation_mass_pull_pct", 8.0) / 100.0
    cell_volume_m3 = params.get("cell_volume_m3", 200.0)

    tph = stream.solids_tph
    au = stream.au_g_t
    _pct_sol = stream.pct_solids

    # Recovery
    recovery_pct = flotation_recovery(rmax_pct, k_rate, residence_min)
    au_recovery_frac = recovery_pct / 100.0

    # Cell sizing
    vol_flow_m3h = stream.volumetric_flow_m3h
    total_volume_m3 = vol_flow_m3h * residence_min / 60.0
    n_cells = max(5, math.ceil(total_volume_m3 / cell_volume_m3))
    total_power = total_volume_m3 * FLOTATION_SPECIFIC_POWER_KW_M3

    # Mass balance
    conc_tph = tph * mass_pull_pct
    tails_tph = tph - conc_tph
    conc_au = au * au_recovery_frac / mass_pull_pct if mass_pull_pct > 0 else au
    tails_au = (au * tph - conc_au * conc_tph) / max(tails_tph, 0.01)
    tails_au = max(0, tails_au)

    # Reagents
    collector_g_t = params.get("collector_g_t", 50.0)
    frother_g_t = params.get("frother_g_t", 15.0)

    equipment = [{
        "type": "Flotation Cell (Mechanical)",
        "quantity": n_cells,
        "volume_m3": cell_volume_m3,
        "total_volume_m3": round(total_volume_m3, 0),
        "power_kw": round(total_power, 0),
        "residence_time_min": residence_min,
    }]

    # Output = tails (concentrate goes to regrind/leach separately)
    output = stream.with_updates(solids_tph=tails_tph, au_g_t=tails_au)

    alerts = []
    if recovery_pct < FLOTATION_RECOVERY_WARNING_PCT:
        alerts.append({"severity": "WARNING", "circuit": "Flotation",
                       "parameter": "recovery", "value": round(recovery_pct, 1),
                       "threshold": FLOTATION_RECOVERY_WARNING_PCT,
                       "action": "Review kinetic parameters, consider regrind before flotation"})

    return {
        "circuit_name": "Flotation",
        "input_stream": stream.to_dict(),
        "output_stream": output.to_dict(),
        "output_stream_obj": output,
        "mass_balance": {
            "feed_tph": round(tph, 1), "feed_au_g_t": round(au, 3),
            "concentrate_tph": round(conc_tph, 1), "concentrate_au_g_t": round(conc_au, 1),
            "tails_tph": round(tails_tph, 1), "tails_au_g_t": round(tails_au, 3),
            "recovery_pct": round(recovery_pct, 1), "mass_pull_pct": round(mass_pull_pct * 100, 1),
        },
        "equipment": equipment,
        "energy_kwh_t": round(total_power / max(tph, 1), 2),
        "power_kw": round(total_power, 0),
        "reagents": {"collector_g_t": collector_g_t, "frother_g_t": frother_g_t},
        "alerts": alerts,
    }
