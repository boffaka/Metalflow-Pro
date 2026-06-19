"""Circuit Concassage — Crushing circuit simulation."""
from __future__ import annotations
import math
from ..stream import Stream
from ..constants import bond_energy, CRUSHER_MECHANICAL_EFFICIENCY


def simulate_crushing(stream: Stream, params: dict) -> dict:
    """Simulate crushing circuit. Pure function.

    Params:
        cwi_kwh_t: Crushing Work Index
        target_p80_mm: Target product P80 (mm)
        rom_f80_mm: ROM feed F80 (mm) — derived from stream.p80_um
        availability_pct: Crushing circuit availability (default 75%)
        design_factor_pct: Design safety factor (default 25%)
    """
    cwi = params.get("cwi_kwh_t", 12.0)
    target_p80_mm = params.get("target_p80_mm", 35.0)
    rom_f80_mm = stream.p80_um / 1000.0  # µm → mm
    avail_pct = params.get("crushing_availability_pct", 75.0)
    design_factor = params.get("design_factor_pct", 25.0) / 100.0
    tph = stream.solids_tph

    # Operating hours for crushing (typically 18h/day)
    crush_h_per_d = 24.0 * avail_pct / 100.0
    # Design throughput (with safety factor)
    design_tph = tph * (1 + design_factor) * (24.0 / crush_h_per_d)

    # ── Primary crusher (Gyratory) ──
    primary_f80_um = stream.p80_um
    primary_p80_mm = min(rom_f80_mm * 0.25, 150.0)  # Typical reduction ratio 4:1
    primary_p80_um = primary_p80_mm * 1000.0
    primary_energy = bond_energy(cwi, primary_p80_um, primary_f80_um)
    primary_power_shaft = primary_energy * design_tph
    primary_power_installed = primary_power_shaft / CRUSHER_MECHANICAL_EFFICIENCY * (1 + 0.30)
    primary_css_mm = primary_p80_mm / 0.96  # P80 ≈ 0.96 × CSS
    primary_feed_opening_mm = rom_f80_mm * 1.5 * 1.2  # 1.2 × top size

    equipment = [{
        "type": "Gyratory Crusher",
        "power_kw": round(primary_power_installed, 0),
        "feed_opening_mm": round(primary_feed_opening_mm, 0),
        "css_mm": round(primary_css_mm, 0),
        "capacity_tph": round(design_tph, 0),
        "f80_mm": round(rom_f80_mm, 0),
        "p80_mm": round(primary_p80_mm, 0),
        "reduction_ratio": round(rom_f80_mm / primary_p80_mm, 1),
    }]

    # ── Secondary crusher (Cone) if needed ──
    secondary_needed = primary_p80_mm > target_p80_mm
    secondary_power_installed = 0.0
    if secondary_needed:
        sec_f80_um = primary_p80_um
        sec_p80_um = target_p80_mm * 1000.0
        sec_energy = bond_energy(cwi, sec_p80_um, sec_f80_um)
        # Screen recirculation: ~120% oversize returns
        recirc_ratio = 1.2
        sec_feed_tph = design_tph * recirc_ratio
        sec_power_shaft = sec_energy * sec_feed_tph
        secondary_power_installed = sec_power_shaft / CRUSHER_MECHANICAL_EFFICIENCY * (1 + 0.25)
        sec_css_mm = target_p80_mm / 1.0  # P80 ≈ CSS for cone
        equipment.append({
            "type": "Cone Crusher (Secondary)",
            "power_kw": round(secondary_power_installed, 0),
            "feed_opening_mm": round(primary_p80_mm * 2, 0),
            "css_mm": round(sec_css_mm, 0),
            "capacity_tph": round(sec_feed_tph, 0),
            "f80_mm": round(primary_p80_mm, 0),
            "p80_mm": round(target_p80_mm, 0),
            "reduction_ratio": round(primary_p80_mm / target_p80_mm, 1),
        })

    # ── Vibrating screen ──
    screen_area_m2 = design_tph / 40.0  # VSMA: ~40 t/h/m² at 50mm cut
    n_screens = max(1, math.ceil(screen_area_m2 / 24.0))  # 3×8m = 24m² per screen
    equipment.append({
        "type": "Vibrating Screen",
        "quantity": n_screens,
        "area_m2": round(screen_area_m2, 1),
        "cut_size_mm": 50,
        "power_kw": round(n_screens * 30, 0),
    })

    # ── Totals ──
    total_power = primary_power_installed + secondary_power_installed + n_screens * 30
    total_energy = total_power / max(tph, 1)
    product_p80_um = target_p80_mm * 1000.0

    # ── Output stream ──
    output = stream.with_updates(p80_um=product_p80_um)

    # ── Alerts ──
    alerts = []
    grinding_target_p80_um = params.get("grinding_target_f80_um", target_p80_mm * 1000)
    if product_p80_um > grinding_target_p80_um * 1.2:
        alerts.append({
            "severity": "WARNING",
            "circuit": "Crushing",
            "parameter": "product_p80",
            "value": round(product_p80_um, 0),
            "threshold": round(grinding_target_p80_um * 1.2, 0),
            "action": "Consider additional crushing stage or HPGR to reduce feed size to grinding",
        })

    return {
        "circuit_name": "Crushing",
        "input_stream": stream.to_dict(),
        "output_stream": output.to_dict(),
        "output_stream_obj": output,
        "mass_balance": {
            "feed_solids_tph": round(tph, 1),
            "product_solids_tph": round(tph, 1),
            "feed_p80_mm": round(rom_f80_mm, 0),
            "product_p80_mm": round(target_p80_mm, 1),
            "design_tph": round(design_tph, 0),
            "screen_recirc_tph": round(design_tph * 1.2, 0) if secondary_needed else 0,
            "operating_hours_day": round(crush_h_per_d, 1),
        },
        "equipment": equipment,
        "energy_kwh_t": round(total_energy, 2),
        "power_kw": round(total_power, 0),
        "reagents": {},
        "alerts": alerts,
    }
