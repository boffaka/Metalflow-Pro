"""Circuit Détox — Cyanide destruction (INCO/Caro/H2O2)."""
from __future__ import annotations
import math
from ..stream import Stream
from ..constants import inco_so2_consumption, WAD_CN_IFC_LIMIT_MG_L


def simulate_detox(stream: Stream, params: dict) -> dict:
    """Simulate cyanide destruction circuit. Pure function."""
    process = params.get("detox_process", "inco")
    wad_cn_inlet = params.get("detox_wad_cn_inlet_mg_l", 50.0)
    retention_time_h = params.get("detox_retention_h", 1.5)
    cuso4_mg_l = params.get("cuso4_catalyst_mg_l", 50.0)
    so2_price_usd_kg = params.get("so2_price_usd_kg", 0.30)
    cuso4_price_usd_kg = params.get("cuso4_price_usd_kg", 2.50)

    tph = stream.solids_tph
    pct_sol = stream.pct_solids
    ore_sg = params.get("ore_sg", 2.75)

    # Volumetric flow
    w = max(pct_sol, 1) / 100.0
    sg_sl = 1.0 / (w / ore_sg + (1 - w) / 1.0)
    pulp_m3h = (tph / (sg_sl * w)) if w > 0 else tph
    solution_m3h = pulp_m3h * (1 - w)

    # ── INCO SO2/Air process ──
    if process == "inco":
        so2_kg_h = inco_so2_consumption(wad_cn_inlet, solution_m3h)
        cuso4_kg_h = cuso4_mg_l * solution_m3h / 1000.0
        # Destruction efficiency: ~99% with adequate retention
        destruction_eff = min(0.99, 1 - math.exp(-3.0 * retention_time_h))
        wad_cn_outlet = wad_cn_inlet * (1 - destruction_eff)
    elif process == "caro":
        # Caro's acid (H2SO5): 3.5 g H2O2 per g WAD CN
        _h2o2_kg_h = wad_cn_inlet * solution_m3h * 3.5 / 1000.0
        so2_kg_h = 0.0
        cuso4_kg_h = 0.0
        destruction_eff = min(0.995, 1 - math.exp(-4.0 * retention_time_h))
        wad_cn_outlet = wad_cn_inlet * (1 - destruction_eff)
    else:  # peroxide
        _h2o2_kg_h = wad_cn_inlet * solution_m3h * 2.5 / 1000.0
        so2_kg_h = 0.0
        cuso4_kg_h = 0.0
        destruction_eff = min(0.98, 1 - math.exp(-2.5 * retention_time_h))
        wad_cn_outlet = wad_cn_inlet * (1 - destruction_eff)

    # Reactor sizing
    reactor_volume_m3 = pulp_m3h * retention_time_h
    n_reactors = max(2, math.ceil(reactor_volume_m3 / 3000))
    vol_per_reactor = reactor_volume_m3 / n_reactors
    reactor_diam = (4 * vol_per_reactor / math.pi) ** (1/3)

    # IFC compliance check
    ifc_compliant = wad_cn_outlet <= WAD_CN_IFC_LIMIT_MG_L

    # Annual reagent cost
    annual_hours = params.get("operating_hours_day", 22.1) * 365 * params.get("availability_pct", 92) / 100
    annual_so2_cost = so2_kg_h * annual_hours * so2_price_usd_kg
    annual_cuso4_cost = cuso4_kg_h * annual_hours * cuso4_price_usd_kg
    annual_total_cost = annual_so2_cost + annual_cuso4_cost

    equipment = [{
        "type": f"CN Destruction Reactor ({process.upper()})",
        "quantity": n_reactors,
        "volume_m3": round(vol_per_reactor, 0),
        "diameter_m": round(reactor_diam, 1),
        "retention_time_h": retention_time_h,
        "agitator_kw": round(vol_per_reactor * 0.08, 0),
    }]
    total_power = sum(e.get("agitator_kw", 0) * e.get("quantity", 1) for e in equipment)

    # Alerts
    alerts = []
    if not ifc_compliant:
        alerts.append({"severity": "CRITICAL", "circuit": "Detox",
                       "parameter": "WAD_CN_outlet",
                       "value": round(wad_cn_outlet, 2),
                       "threshold": WAD_CN_IFC_LIMIT_MG_L,
                       "action": "Increase reactor retention time or SO2 dosage to achieve IFC compliance (WAD CN < 0.5 mg/L)"})

    output = stream.passthrough()

    return {
        "circuit_name": "Detox",
        "input_stream": stream.to_dict(),
        "output_stream": output.to_dict(),
        "output_stream_obj": output,
        "mass_balance": {
            "tailings_flow_m3h": round(pulp_m3h, 1),
            "wad_cn_inlet_mg_l": round(wad_cn_inlet, 1),
            "wad_cn_outlet_mg_l": round(wad_cn_outlet, 2),
            "destruction_efficiency_pct": round(destruction_eff * 100, 1),
            "ifc_compliant": ifc_compliant,
            "so2_kg_h": round(so2_kg_h, 1),
            "cuso4_kg_h": round(cuso4_kg_h, 1),
        },
        "equipment": equipment,
        "energy_kwh_t": round(total_power / max(tph, 1), 3),
        "power_kw": round(total_power, 0),
        "reagents": {"so2_kg_h": round(so2_kg_h, 1), "cuso4_kg_h": round(cuso4_kg_h, 1),
                     "annual_reagent_cost_usd": round(annual_total_cost, 0)},
        "alerts": alerts,
        "metadata": {"process": process, "ifc_compliant": ifc_compliant},
    }
