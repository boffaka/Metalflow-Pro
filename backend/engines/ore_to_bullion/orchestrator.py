"""Orchestrator — runs all circuits sequentially and assembles results."""
from __future__ import annotations
import time
from .models import FeedParameters, CircuitConfig, CircuitResult, SimulationResult
from .stream import Stream
from .constants import TROY_OZ_PER_GRAM


def run_simulation(feed_params: FeedParameters, config: CircuitConfig, overrides: dict | None = None) -> SimulationResult:
    """Execute full ore-to-bullion simulation. Pure function."""
    t0 = time.perf_counter()
    ov = overrides or {}

    # Build initial ROM stream
    stream = Stream.from_feed(
        tph=feed_params.feed_rate_tph,
        au=feed_params.gold_grade_g_t,
        sg=feed_params.ore_sg,
        pct_sol=96.0,  # ROM ~4% moisture
        p80=600_000.0,  # ROM F80 = 600mm
    )

    circuit_results: list[CircuitResult] = []
    all_alerts: list[dict] = []
    initial_au = stream.au_mass_g_h

    # ── 1. Crushing ──
    if config.crushing_enabled:
        from .circuits.crushing import simulate_crushing
        params = {"cwi_kwh_t": feed_params.cwi_kwh_t, "target_p80_mm": config.crushing_target_p80_mm,
                  "grinding_target_f80_um": config.grinding_target_p80_um * 50, **ov}
        try:
            r = simulate_crushing(stream, params)
            stream = r.pop("output_stream_obj")
            circuit_results.append(CircuitResult(**{k: v for k, v in r.items() if k != "output_stream_obj"}))
            all_alerts.extend(r.get("alerts", []))
        except Exception as e:
            all_alerts.append({"severity": "CRITICAL", "circuit": "Crushing", "parameter": "engine_error", "value": str(e), "threshold": None, "action": "Check input parameters"})

    # ── 2. Grinding ──
    from .circuits.grinding import simulate_grinding
    params = {"grinding_type": config.grinding_type, "bwi_kwh_t": feed_params.bwi_kwh_t,
              "axb": feed_params.axb, "target_p80_um": config.grinding_target_p80_um,
              "circulating_load_pct": 300.0, "cyc_of_pct_solids": 35.0, **ov}
    try:
        r = simulate_grinding(stream, params)
        stream = r.pop("output_stream_obj")
        circuit_results.append(CircuitResult(**{k: v for k, v in r.items() if k != "output_stream_obj"}))
        all_alerts.extend(r.get("alerts", []))
    except Exception as e:
        all_alerts.append({"severity": "CRITICAL", "circuit": "Grinding", "parameter": "engine_error", "value": str(e), "threshold": None, "action": "Check BWi and grinding parameters"})

    # ── 3. Gravity ──
    if config.gravity_enabled:
        from .circuits.gravity import simulate_gravity
        params = {
            "grg_pct": config.grg_pct,
            "gravity_slip_pct": config.gravity_slip_pct,
            "knelson_unit_recovery_pct": config.knelson_unit_recovery_pct,
            "ilr_recovery_pct": config.ilr_recovery_pct,
            "gravity_mass_pull_pct": config.gravity_mass_pull_pct,
            "operating_hours_day": feed_params.operating_hours_day,
            "availability_pct": feed_params.availability_pct,
            **ov,
        }
        try:
            r = simulate_gravity(stream, params)
            stream = r.pop("output_stream_obj")
            circuit_results.append(CircuitResult(**{k: v for k, v in r.items() if k != "output_stream_obj"}))
            all_alerts.extend(r.get("alerts", []))
        except Exception as e:
            all_alerts.append({"severity": "CRITICAL", "circuit": "Gravity", "parameter": "engine_error", "value": str(e), "threshold": None, "action": "Check GRG parameters"})

    # ── 4. Flotation (optional) ──
    if config.flotation_enabled:
        from .circuits.flotation import simulate_flotation
        params = {"flotation_k_rate": config.flotation_k_rate, "flotation_residence_min": config.flotation_residence_min,
                  "flotation_rmax_pct": config.flotation_rmax_pct, "flotation_mass_pull_pct": config.flotation_mass_pull_pct, **ov}
        try:
            r = simulate_flotation(stream, params)
            stream = r.pop("output_stream_obj")
            circuit_results.append(CircuitResult(**{k: v for k, v in r.items() if k != "output_stream_obj"}))
            all_alerts.extend(r.get("alerts", []))
        except Exception as e:
            all_alerts.append({"severity": "CRITICAL", "circuit": "Flotation", "parameter": "engine_error", "value": str(e), "threshold": None, "action": "Check flotation parameters"})

    # ── 5. Leaching (CIL/CIP) ──
    from .circuits.leaching import simulate_leaching
    params = {"leaching_type": config.leaching_type, "leaching_srt_h": config.leaching_srt_h,
              "leaching_n_tanks": config.leaching_n_tanks, "leaching_nacn_kg_t": config.leaching_nacn_kg_t,
              "leaching_cao_kg_t": config.leaching_cao_kg_t, "leaching_recovery_pct": config.leaching_recovery_pct,
              "ore_sg": feed_params.ore_sg,
              "operating_hours_day": feed_params.operating_hours_day, **ov}
    leach_mb: dict = {}
    try:
        r = simulate_leaching(stream, params)
        leach_mb = r.get("mass_balance", {})
        stream = r.pop("output_stream_obj")
        circuit_results.append(CircuitResult(**{k: v for k, v in r.items() if k != "output_stream_obj"}))
        all_alerts.extend(r.get("alerts", []))
    except Exception as e:
        leach_mb = {}
        all_alerts.append({"severity": "CRITICAL", "circuit": "Leaching", "parameter": "engine_error", "value": str(e), "threshold": None, "action": "Check leaching parameters"})

    # ── 6. ADR ──
    from .circuits.adr import simulate_adr
    params = {
        "elution_type": config.elution_type,
        "au_recovered_g_h": leach_mb.get("au_recovered_g_h", 0),
        "carbon_loading_g_t": leach_mb.get("carbon_loading_g_t", 3000.0),
        "operating_hours_day": feed_params.operating_hours_day,
        "availability_pct": feed_params.availability_pct,
        **ov,
    }
    try:
        r = simulate_adr(stream, params)
        stream = r.pop("output_stream_obj")
        circuit_results.append(CircuitResult(**{k: v for k, v in r.items() if k != "output_stream_obj"}))
        all_alerts.extend(r.get("alerts", []))
    except Exception as e:
        all_alerts.append({"severity": "CRITICAL", "circuit": "ADR", "parameter": "engine_error", "value": str(e), "threshold": None, "action": "Check ADR parameters"})

    # ── 7. Detox ──
    from .circuits.detox import simulate_detox
    params = {"detox_process": config.detox_process, "detox_wad_cn_inlet_mg_l": config.detox_wad_cn_inlet_mg_l,
              "ore_sg": feed_params.ore_sg, "operating_hours_day": feed_params.operating_hours_day,
              "availability_pct": feed_params.availability_pct, **ov}
    try:
        r = simulate_detox(stream, params)
        stream = r.pop("output_stream_obj")
        circuit_results.append(CircuitResult(**{k: v for k, v in r.items() if k != "output_stream_obj"}))
        all_alerts.extend(r.get("alerts", []))
    except Exception as e:
        all_alerts.append({"severity": "CRITICAL", "circuit": "Detox", "parameter": "engine_error", "value": str(e), "threshold": None, "action": "Check detox parameters"})

    # ── Summaries ──
    total_power = sum(cr.power_kw for cr in circuit_results)
    total_energy = sum(cr.energy_kwh_t for cr in circuit_results)
    annual_hours = feed_params.operating_hours_day * 365 * feed_params.availability_pct / 100
    annual_energy_mwh = total_power * annual_hours / 1000
    annual_energy_cost = annual_energy_mwh * 1000 * config.energy_rate_usd_kwh
    co2_per_t = total_energy * config.grid_co2_kg_kwh

    # Overall recovery
    final_au = stream.au_mass_g_h
    overall_recovery = (1 - final_au / initial_au) * 100 if initial_au > 0 else 0
    annual_gold_g = (initial_au - final_au) * annual_hours
    annual_gold_oz = annual_gold_g * TROY_OZ_PER_GRAM
    co2_per_oz = (co2_per_t * feed_params.feed_rate_tph * annual_hours) / max(annual_gold_oz, 1)

    # Reagent summary
    reagent_totals: dict[str, float] = {}
    for cr in circuit_results:
        for k, v in cr.reagents.items():
            if isinstance(v, (int, float)):
                reagent_totals[k] = reagent_totals.get(k, 0) + v
    nacn_total = reagent_totals.get("nacn_kg_h", 0)
    cao_total = reagent_totals.get("cao_kg_h", 0)
    reagent_opex_usd_h = nacn_total * config.nacn_price_usd_kg + cao_total * config.cao_price_usd_kg
    reagent_opex_usd_t = reagent_opex_usd_h / max(feed_params.feed_rate_tph, 1)
    reagent_opex_usd_oz = (reagent_opex_usd_h * annual_hours) / max(annual_gold_oz, 1)

    # Energy breakdown
    energy_breakdown = []
    for cr in circuit_results:
        pct = (cr.power_kw / total_power * 100) if total_power > 0 else 0
        energy_breakdown.append({"circuit": cr.circuit_name, "power_kw": cr.power_kw,
                                 "energy_kwh_t": cr.energy_kwh_t, "pct_of_total": round(pct, 1)})

    # Sort alerts by severity
    severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    all_alerts.sort(key=lambda a: severity_order.get(a.get("severity", "INFO"), 3))
    n_crit = sum(1 for a in all_alerts if a.get("severity") == "CRITICAL")
    n_warn = sum(1 for a in all_alerts if a.get("severity") == "WARNING")
    n_info = sum(1 for a in all_alerts if a.get("severity") == "INFO")

    computation_time = time.perf_counter() - t0

    return SimulationResult(
        feed_params=feed_params.model_dump(),
        circuit_results=circuit_results,
        overall_recovery_pct=round(overall_recovery, 2),
        annual_gold_oz=round(annual_gold_oz, 0),
        total_energy_kwh_t=round(total_energy, 2),
        total_power_kw=round(total_power, 0),
        total_reagent_opex_usd_t=round(reagent_opex_usd_t, 2),
        total_reagent_opex_usd_oz=round(reagent_opex_usd_oz, 2),
        annual_energy_mwh=round(annual_energy_mwh, 0),
        annual_energy_cost_usd=round(annual_energy_cost, 0),
        co2_kg_per_t=round(co2_per_t, 2),
        co2_kg_per_oz=round(co2_per_oz, 1),
        reagent_summary=[{"reagent": k, "value": round(v, 2)} for k, v in reagent_totals.items()],
        energy_breakdown=energy_breakdown,
        alerts=all_alerts,
        alerts_summary={"critical": n_crit, "warning": n_warn, "info": n_info},
        computation_time_s=round(computation_time, 3),
    )
