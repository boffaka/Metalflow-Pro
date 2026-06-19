"""Circuit ADR — Elution + Electrowinning + Smelting."""
from __future__ import annotations
import math
from ..stream import Stream
from ..constants import (ew_current, EW_CELL_VOLTAGE, CARBON_BULK_DENSITY_KG_M3,
                         TROY_OZ_PER_GRAM, ELUTION_EFFICIENCY_WARNING_PCT, FARADAY_EFFICIENCY)


def simulate_adr(stream: Stream, params: dict) -> dict:
    """Simulate ADR circuit (Elution + EW + Smelting). Pure function."""
    elution_type = params.get("elution_type", "aarl")
    carbon_loading_g_t = params.get("carbon_loading_g_t", 3000.0)
    carbon_transfer_tpd = params.get("carbon_transfer_tpd", 5.0)
    operating_hours_day = params.get("operating_hours_day", 22.1)
    availability_pct = params.get("availability_pct", 92.0)

    tph = stream.solids_tph
    _au = stream.au_g_t
    # Gold recovered from leaching (passed via params or estimated)
    _au_recovered_g_h = params.get("au_recovered_g_h", stream.au_mass_g_h * 0.92)

    # ── Elution ──
    elution_temp_c = 110.0 if elution_type == "aarl" else 90.0
    elution_time_h = 14.0 if elution_type == "aarl" else 36.0
    batch_size_t = carbon_transfer_tpd
    # Elution efficiency model (temperature-dependent)
    base_eff = 0.95 if elution_type == "aarl" else 0.90
    temp_factor = min(1.0, elution_temp_c / 120.0)
    elution_efficiency = base_eff * temp_factor

    # Column sizing
    column_volume_m3 = batch_size_t * 1000 / CARBON_BULK_DENSITY_KG_M3
    column_height_m = 5.0
    column_diam_m = math.sqrt(4 * column_volume_m3 / (math.pi * column_height_m))

    # Gold in eluate (g Au per batch = loading g/t × carbon t/batch × elution efficiency)
    gold_eluted_g_batch = carbon_loading_g_t * batch_size_t * elution_efficiency
    # Prefer leach mass-balance when carbon loading is not supplied explicitly
    au_recovered_g_h = params.get("au_recovered_g_h")
    if au_recovered_g_h and au_recovered_g_h > 0:
        gold_eluted_g_day = au_recovered_g_h * operating_hours_day
    else:
        gold_eluted_g_day = gold_eluted_g_batch  # 1 batch/day typical
    eluate_volume_m3 = column_volume_m3 * 8  # ~8 BV
    eluate_au_mg_l = gold_eluted_g_batch * 1000 / max(eluate_volume_m3 * 1000, 1)

    # ── Electrowinning ──
    i_total = ew_current(gold_eluted_g_day)
    ew_power_kw = i_total * EW_CELL_VOLTAGE / 1000.0
    n_cells = max(1, math.ceil(i_total / 5000))  # Max 5000A per cell
    cathode_sludge_g_day = gold_eluted_g_day * 1.1  # ~10% impurities

    # ── Smelting ──
    bullion_kg_batch = gold_eluted_g_day / 1000.0 * 1.15  # Au + Ag + impurities
    bullion_purity_pct = 85.0  # Doré bar
    furnace_power_kw = 150.0  # Induction furnace
    _annual_hours = operating_hours_day * 365 * availability_pct / 100
    annual_bullion_kg = gold_eluted_g_day / 1000.0 * 365 * availability_pct / 100
    annual_bullion_oz = annual_bullion_kg * 1000 * TROY_OZ_PER_GRAM

    equipment = [
        {"type": f"Elution Column ({elution_type.upper()})", "volume_m3": round(column_volume_m3, 1),
         "height_m": column_height_m, "diameter_m": round(column_diam_m, 2),
         "temperature_c": elution_temp_c, "cycle_time_h": elution_time_h,
         "batch_size_t": batch_size_t, "efficiency_pct": round(elution_efficiency * 100, 1)},
        {"type": "Electrowinning Cell", "quantity": n_cells, "current_a": round(i_total, 0),
         "voltage_v": EW_CELL_VOLTAGE, "power_kw": round(ew_power_kw, 1),
         "faraday_efficiency_pct": round(FARADAY_EFFICIENCY * 100, 0)},
        {"type": "Induction Furnace (Smelting)", "capacity_kg_batch": round(bullion_kg_batch * 2, 0),
         "power_kw": furnace_power_kw, "purity_pct": bullion_purity_pct},
        {"type": "Carbon Regeneration Kiln", "capacity_kg_h": round(batch_size_t * 1000 / 10, 0),
         "temperature_c": 750, "power_kw": 150},
    ]

    total_power = ew_power_kw + furnace_power_kw + 150 + 50  # +50 pumps

    # Alerts
    alerts = []
    if elution_efficiency * 100 < ELUTION_EFFICIENCY_WARNING_PCT:
        alerts.append({"severity": "WARNING", "circuit": "ADR",
                       "parameter": "elution_efficiency",
                       "value": round(elution_efficiency * 100, 1),
                       "threshold": ELUTION_EFFICIENCY_WARNING_PCT,
                       "action": "Review elution temperature, reagent concentrations, or column sizing"})

    # Output stream (ADR doesn't modify the main pulp stream — it processes the carbon)
    output = stream.passthrough()

    return {
        "circuit_name": "ADR",
        "input_stream": stream.to_dict(),
        "output_stream": output.to_dict(),
        "output_stream_obj": output,
        "mass_balance": {
            "loaded_carbon_tpd": round(carbon_transfer_tpd, 1),
            "carbon_loading_g_t": carbon_loading_g_t,
            "gold_eluted_g_day": round(gold_eluted_g_day, 1),
            "eluate_au_mg_l": round(eluate_au_mg_l, 1),
            "cathode_sludge_g_day": round(cathode_sludge_g_day, 1),
            "bullion_kg_day": round(bullion_kg_batch, 2),
            "bullion_purity_pct": bullion_purity_pct,
            "annual_bullion_oz": round(annual_bullion_oz, 0),
            "annual_bullion_kg": round(annual_bullion_kg, 1),
        },
        "equipment": equipment,
        "energy_kwh_t": round(total_power / max(tph, 1), 3),
        "power_kw": round(total_power, 0),
        "reagents": {"naoh_kg_batch": round(batch_size_t * 10, 1),
                     "nacn_elution_kg_batch": round(batch_size_t * 2, 1),
                     "hcl_acid_wash_l": round(column_volume_m3 * 2, 0)},
        "alerts": alerts,
        "metadata": {"elution_type": elution_type, "elution_efficiency": round(elution_efficiency, 3)},
    }
