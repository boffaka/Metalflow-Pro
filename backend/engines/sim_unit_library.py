"""Sim Module v2 — Unit Operation Library.
All 40+ unit types with mathematical models for the flowsheet simulator.
Each calculator receives inlet streams, parameters, and optional feed_input and returns
a UnitOutput with output streams, recovery, energy, and KPIs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("mpdpms.sim_unit_library")

# ── Physical constants ────────────────────────────────────────────────────────
FARADAY = 96485  # C/mol
MOLAR_MASS_AU = 196.97  # g/mol
AU_ELECTRONS = 3  # Au³⁺


# ── Core stream dataclass ─────────────────────────────────────────────────────


@dataclass
class SimStream:
    """Rich stream state for the Sim Module v2 simulator."""

    mass_flow: float = 0.0  # t/h solids
    volume_flow: float = 0.0  # m³/h
    solids_pct: float = 0.0  # % solids by mass
    gold_grade: float = 0.0  # g/t Au (solids basis)
    gold_flow: float = 0.0  # kg/h Au total (dissolved + solid)
    dissolved_gold: float = 0.0  # mg/L (solution phase)
    cyanide_ppm: float = 0.0  # ppm free CN⁻
    pH: float = 10.5
    temperature: float = 25.0  # °C
    p80_um: float = 75.0  # µm P80
    energy_kwh_t: float = 0.0  # kWh/t (cumulative)
    silver_grade: float = 0.0  # g/t Ag
    sulphide_pct: float = 0.0  # % sulphide minerals

    @classmethod
    def from_feed(
        cls,
        feed_rate: float,
        gold_grade: float,
        p80: float,
        silver_grade: float = 0.0,
        sulphide_pct: float = 0.0,
    ) -> "SimStream":
        water = feed_rate * 2.0  # default ~33 % solids
        volume = feed_rate / 2.7 + water  # SG solids = 2.7
        solids_pct = feed_rate / (feed_rate + water) * 100
        return cls(
            mass_flow=feed_rate,
            volume_flow=volume,
            solids_pct=solids_pct,
            gold_grade=gold_grade,
            gold_flow=feed_rate * gold_grade / 1000,
            p80_um=p80,
            silver_grade=silver_grade,
            sulphide_pct=sulphide_pct,
        )

    def mix(self, other: "SimStream") -> "SimStream":
        total_mass = self.mass_flow + other.mass_flow
        total_volume = self.volume_flow + other.volume_flow
        if total_mass == 0:
            return SimStream()
        total_water = (self.volume_flow - self.mass_flow / 2.7) + (other.volume_flow - other.mass_flow / 2.7)
        total_gold = self.gold_flow + other.gold_flow
        return SimStream(
            mass_flow=total_mass,
            volume_flow=total_volume,
            solids_pct=(total_mass / (total_mass + total_water) * 100 if total_water > 0 else 100.0),
            gold_grade=total_gold / total_mass * 1000 if total_mass > 0 else 0.0,
            gold_flow=total_gold,
            dissolved_gold=(
                (self.dissolved_gold * self.volume_flow + other.dissolved_gold * other.volume_flow) / total_volume
                if total_volume > 0
                else 0.0
            ),
            cyanide_ppm=(
                (self.cyanide_ppm * self.volume_flow + other.cyanide_ppm * other.volume_flow) / total_volume
                if total_volume > 0
                else 0.0
            ),
            pH=min(self.pH, other.pH),
            temperature=(
                (self.temperature * self.volume_flow + other.temperature * other.volume_flow) / total_volume
                if total_volume > 0
                else 25.0
            ),
            p80_um=max(self.p80_um, other.p80_um),
            energy_kwh_t=(self.energy_kwh_t + other.energy_kwh_t) / 2,
            silver_grade=(
                (self.silver_grade * self.mass_flow + other.silver_grade * other.mass_flow) / total_mass
                if total_mass > 0
                else 0.0
            ),
            sulphide_pct=(
                (self.sulphide_pct * self.mass_flow + other.sulphide_pct * other.mass_flow) / total_mass
                if total_mass > 0
                else 0.0
            ),
        )

    def copy(self, **overrides: Any) -> "SimStream":
        from dataclasses import replace

        return replace(self, **overrides)


@dataclass
class UnitOutput:
    streams: dict[str, SimStream] = field(default_factory=dict)
    recovery_pct: float = 0.0
    energy_kwh_t: float = 0.0
    reagent_consumptions: dict[str, float] = field(default_factory=dict)  # kg/t
    utilization_rate: float = 0.0
    kpis: dict[str, Any] = field(default_factory=dict)


# ── Math helpers ──────────────────────────────────────────────────────────────


def _bond_energy(wi: float, p80: float, f80: float) -> float:
    """Bond Work Index equation: W = Wi * (10/√P80 - 10/√F80) kWh/t."""
    if p80 <= 0 or f80 <= 0 or p80 >= f80:
        return 0.0
    return max(0.0, wi * (10.0 / math.sqrt(p80) - 10.0 / math.sqrt(f80)))


def _safe_exp(x: float) -> float:
    """Clamp exponent to avoid overflow."""
    return math.exp(max(-700.0, min(700.0, x)))


def _primary_inlet(inlet_streams: dict[str, SimStream]) -> SimStream:
    """Return the main inlet stream, trying common port names first."""
    for port in ("feed", "in", "inlet", "pulp", "slurry"):
        if port in inlet_streams:
            return inlet_streams[port]
    return next(iter(inlet_streams.values()), SimStream())


def _utilization(feed_rate: float, design_capacity: float, availability: float) -> float:
    """Effective utilisation fraction (0–1)."""
    if design_capacity <= 0:
        return 0.0
    effective_cap = design_capacity * (availability / 100.0)
    return min(feed_rate / effective_cap, 2.0) if effective_cap > 0 else 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  COMMINUTION CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _calc_jaw_crusher(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    wi = float(params.get("wi", 14))
    red_ratio = float(params.get("reduction_ratio", 4))
    p80_out = s.p80_um / red_ratio
    p80_out = max(p80_out, 5000.0)  # jaw crusher minimum ~5 mm
    energy = _bond_energy(wi, p80_out, s.p80_um) * 0.3  # crushing correction factor
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"p80_in_um": s.p80_um, "p80_out_um": p80_out, "reduction_ratio": red_ratio},
    )


def _calc_gyratory_crusher(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    wi = float(params.get("wi", 14))
    red_ratio = float(params.get("reduction_ratio", 5))
    p80_out = s.p80_um / red_ratio
    p80_out = max(p80_out, 3000.0)
    # Whiten model simplified
    energy = 0.4 * wi * (10.0 / math.sqrt(max(p80_out, 1)) - 10.0 / math.sqrt(max(s.p80_um, 1)))
    energy = max(0.0, energy)
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"p80_out_um": p80_out, "reduction_ratio": red_ratio},
    )


def _calc_cone_crusher(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    wi = float(params.get("wi", 14))
    css_mm = float(params.get("css_mm", 25))
    p80_out = css_mm * 1500.0  # CSS × 1.5 mm → µm
    p80_out = max(p80_out, 500.0)
    energy = _bond_energy(wi, p80_out, s.p80_um) * 0.5
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"css_mm": css_mm, "p80_out_um": p80_out},
    )


def _calc_sag_mill(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    wi = float(params.get("wi", 14))
    p80_out = float(params.get("p80_out_um", 2000))
    spi = float(params.get("spi", 50))  # Ore hardness index 0–100
    load_pct = float(params.get("load_pct", 30))
    cs_pct = float(params.get("critical_speed_pct", 75))
    # SPI-based energy (Starkey & Dobby simplified)
    energy = 0.2 * (spi / 100.0) * wi * (10.0 / math.sqrt(max(p80_out, 1)) - 10.0 / math.sqrt(max(s.p80_um, 1)))
    energy = max(0.0, energy)
    # Adjust for mill loading and speed
    load_factor = 1.0 + (load_pct - 30.0) * 0.005
    speed_factor = 1.0 + (cs_pct - 75.0) * 0.003
    energy *= load_factor * speed_factor
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"p80_out_um": p80_out, "spi": spi, "energy_kwh_t": energy},
    )


def _calc_ball_mill(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    wi = float(params.get("wi", 14))
    p80_out = float(params.get("p80_out_um", 75))
    energy = _bond_energy(wi, p80_out, s.p80_um)
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"p80_out_um": p80_out, "bond_wi": wi},
    )


def _calc_rod_mill(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    wi = float(params.get("wi", 14))
    p80_out = float(params.get("p80_out_um", 1000))
    # Rod mill: Bond +10% energy vs ball mill
    energy = _bond_energy(wi, p80_out, s.p80_um) * 1.10
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"p80_out_um": p80_out},
    )


def _calc_hpgr(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    spf = float(params.get("specific_pressing_force_kn_m2", 3.5))
    p80_out = s.p80_um * 0.4  # HPGR characteristically reduces P80 by ~60 %
    p80_out = max(p80_out, 50.0)
    energy = spf * 0.3  # simplified HPGR specific energy
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"specific_pressing_force": spf, "p80_out_um": p80_out},
    )


def _calc_isamill(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    c1 = float(params.get("ore_constant", 30))
    p80_out = float(params.get("p80_out_um", 20))
    p80_in = max(s.p80_um, 1.0)
    # Ishikawa simplified: W = c1 * (1/P80_out - 1/P80_in)
    energy = max(0.0, c1 * (1.0 / max(p80_out, 1.0) - 1.0 / p80_in))
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    product = s.copy(p80_um=p80_out, energy_kwh_t=s.energy_kwh_t + energy)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"ore_constant": c1, "p80_out_um": p80_out},
    )


def _calc_hydrocyclone(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    d50c = float(params.get("d50c_um", 50))
    # Plitt model: efficiency = 1 - exp(-0.693*(p80/d50c)^2)
    # Overflow (underflow in cyclone = dense) fraction = coarse split
    ratio = s.p80_um / max(d50c, 1.0)
    # fraction of solids reporting to underflow (coarse)
    frac_underflow = 1.0 - _safe_exp(-0.693 * ratio**2)
    frac_underflow = max(0.05, min(0.95, frac_underflow))
    frac_overflow = 1.0 - frac_underflow

    uf_mass = s.mass_flow * frac_underflow
    of_mass = s.mass_flow * frac_overflow
    uf_vol = s.volume_flow * 0.3  # underflow is thicker ~30 % of volume
    of_vol = s.volume_flow * 0.7
    p80_of = max(d50c * 0.5, 5.0)
    p80_uf = min(d50c * 2.0, s.p80_um)

    overflow = s.copy(
        mass_flow=of_mass,
        volume_flow=of_vol,
        p80_um=p80_of,
        gold_flow=s.gold_flow * frac_overflow,
        gold_grade=s.gold_grade,
    )
    underflow = s.copy(
        mass_flow=uf_mass,
        volume_flow=uf_vol,
        p80_um=p80_uf,
        gold_flow=s.gold_flow * frac_underflow,
        gold_grade=s.gold_grade,
    )
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"overflow": overflow, "underflow": underflow},
        utilization_rate=util,
        kpis={"d50c_um": d50c, "overflow_frac": round(frac_overflow, 3)},
    )


def _calc_vibrating_screen(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    aperture_mm = float(params.get("aperture_mm", 10))
    efficiency = float(params.get("efficiency", 0.90))
    cut_size_um = aperture_mm * 1000.0

    # Fraction passing the screen
    if s.p80_um <= 0:
        frac_undersize = efficiency
    else:
        frac_undersize = min(efficiency, efficiency * cut_size_um / max(s.p80_um, 1.0))
    frac_undersize = max(0.0, min(1.0, frac_undersize))
    frac_oversize = 1.0 - frac_undersize

    undersize = s.copy(
        mass_flow=s.mass_flow * frac_undersize,
        volume_flow=s.volume_flow * frac_undersize,
        gold_flow=s.gold_flow * frac_undersize,
        p80_um=min(s.p80_um, cut_size_um),
    )
    oversize = s.copy(
        mass_flow=s.mass_flow * frac_oversize,
        volume_flow=s.volume_flow * frac_oversize,
        gold_flow=s.gold_flow * frac_oversize,
        p80_um=s.p80_um,
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"undersize": undersize, "oversize": oversize},
        utilization_rate=util,
        kpis={"aperture_mm": aperture_mm, "efficiency": efficiency, "frac_undersize": round(frac_undersize, 3)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  LEACHING CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _calc_agitated_tank_leach(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    k_leach = float(params.get("k_leach", 0.08))  # h⁻¹ Elsner rate constant
    tau = float(params.get("residence_time_h", 4))
    cn_dose = float(params.get("cn_dose_kg_t", 1.5))  # kg NaCN / t ore
    o2_mg_l = float(params.get("o2_mg_l", 8))

    # Elsner steady-state: recovery = 1 - exp(-k_eff * tau)
    recovery = 1.0 - _safe_exp(-k_leach * tau)
    recovery = max(0.0, min(0.99, recovery))

    au_leached_kg_h = s.gold_flow * recovery
    au_solid_kg_h = s.gold_flow * (1.0 - recovery)

    # Dissolved gold in solution (mg/L)
    dissolved_au = au_leached_kg_h * 1e6 / max(s.volume_flow, 0.001)  # mg/L

    # Cyanide consumption: ~2.6 kg NaCN per kg Au (Elsner ratio)
    cn_consumed_kg_t = min(cn_dose, 2.6 * au_leached_kg_h / max(s.mass_flow, 0.001))
    reagents = {"NaCN_kg_t": round(cn_dose, 3), "O2_mg_L": o2_mg_l}

    product = s.copy(
        gold_grade=au_solid_kg_h / max(s.mass_flow, 0.001) * 1000,
        gold_flow=au_solid_kg_h,
        dissolved_gold=dissolved_au,
        cyanide_ppm=max(0, s.cyanide_ppm - cn_consumed_kg_t * 1000),
        pH=max(s.pH, 10.5),
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": product},
        recovery_pct=recovery * 100,
        reagent_consumptions=reagents,
        utilization_rate=util,
        kpis={
            "leach_recovery_pct": round(recovery * 100, 2),
            "dissolved_au_mg_l": round(dissolved_au, 3),
            "residence_time_h": tau,
        },
    )


def _calc_cil_reactor(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    k_leach = float(params.get("k_leach", 0.10))
    tau = float(params.get("residence_time_h", 8))
    carbon_g_l = float(params.get("carbon_g_l", 15))
    K_F = 50.0  # Freundlich constant
    n_exp = 0.5  # Freundlich exponent

    recovery_leach = 1.0 - _safe_exp(-k_leach * tau)
    au_dissolved_kg_h = s.gold_flow * recovery_leach
    dissolved_conc_g_l = au_dissolved_kg_h * 1000.0 / max(s.volume_flow, 0.001)

    # Freundlich adsorption: q (g Au/kg C) = K_F * C^(1/n)
    q = K_F * (max(dissolved_conc_g_l * 1000, 0.001) ** (1.0 / n_exp))  # C in mg/L
    carbon_kg_per_m3 = carbon_g_l / 1000.0
    au_adsorbed_kg_h = q / 1e6 * carbon_kg_per_m3 * s.volume_flow * 1000  # g/kg * kg/L * L/h → kg/h

    # Total recovery limited to leach recovery
    au_adsorbed_kg_h = min(au_adsorbed_kg_h, au_dissolved_kg_h)
    total_au_recovered_kg_h = au_adsorbed_kg_h
    total_recovery = total_au_recovered_kg_h / max(s.gold_flow, 0.001)
    total_recovery = min(total_recovery, 0.99)

    au_solid_remaining = s.gold_flow * (1.0 - total_recovery)
    cn_dose = float(params.get("cn_dose_kg_t", 2.0))
    reagents = {"NaCN_kg_t": cn_dose, "carbon_g_l": carbon_g_l}

    product = s.copy(
        gold_grade=au_solid_remaining / max(s.mass_flow, 0.001) * 1000,
        gold_flow=au_solid_remaining,
        dissolved_gold=max(0.0, dissolved_conc_g_l * 1000 - q * carbon_kg_per_m3 * 1000),
        pH=max(s.pH, 10.5),
    )
    # Loaded carbon output (represents Au in carbon)
    loaded_carbon = SimStream(
        mass_flow=carbon_g_l * s.volume_flow / 1e6,  # very small carbon mass
        gold_flow=au_adsorbed_kg_h,
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": product, "loaded_carbon": loaded_carbon},
        recovery_pct=total_recovery * 100,
        reagent_consumptions=reagents,
        utilization_rate=util,
        kpis={
            "leach_recovery_pct": round(recovery_leach * 100, 2),
            "adsorption_recovery_pct": round(total_recovery * 100, 2),
            "carbon_loading_g_t": round(q, 1),
        },
    )


def _calc_cip_reactor(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    """CIP: leach only (carbon adsorption in a separate CIP vessel downstream)."""
    s = _primary_inlet(inlet_streams)
    k_leach = float(params.get("k_leach", 0.08))
    tau = float(params.get("residence_time_h", 6))
    cn_dose = float(params.get("cn_dose_kg_t", 1.5))

    recovery = 1.0 - _safe_exp(-k_leach * tau)
    recovery = max(0.0, min(0.99, recovery))
    au_leached = s.gold_flow * recovery
    dissolved_au = au_leached * 1e6 / max(s.volume_flow, 0.001)

    product = s.copy(
        gold_grade=(s.gold_flow - au_leached) / max(s.mass_flow, 0.001) * 1000,
        gold_flow=s.gold_flow - au_leached,
        dissolved_gold=dissolved_au,
        pH=max(s.pH, 10.5),
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": product},
        recovery_pct=recovery * 100,
        reagent_consumptions={"NaCN_kg_t": cn_dose},
        utilization_rate=util,
        kpis={"leach_recovery_pct": round(recovery * 100, 2), "dissolved_au_mg_l": round(dissolved_au, 3)},
    )


def _calc_vat_leach(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    k_leach = float(params.get("k_leach", 0.02))
    tau = float(params.get("residence_time_h", 48))

    recovery = 1.0 - _safe_exp(-k_leach * tau)
    recovery = max(0.0, min(0.95, recovery))
    au_leached = s.gold_flow * recovery
    dissolved_au = au_leached * 1e6 / max(s.volume_flow, 0.001)

    product = s.copy(
        gold_flow=s.gold_flow - au_leached,
        gold_grade=(s.gold_flow - au_leached) / max(s.mass_flow, 0.001) * 1000,
        dissolved_gold=dissolved_au,
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": product},
        recovery_pct=recovery * 100,
        reagent_consumptions={"NaCN_kg_t": float(params.get("cn_dose_kg_t", 1.0))},
        utilization_rate=util,
        kpis={"recovery_pct": round(recovery * 100, 2), "residence_time_h": tau},
    )


def _calc_heap_leach_pad(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    ultimate_recovery = float(params.get("ultimate_recovery", 0.70))
    k_h = float(params.get("leach_rate_h", 0.001))
    time_h = float(params.get("time_h", 2160))  # 90 days default
    # Kinetic model
    recovery = ultimate_recovery * (1.0 - _safe_exp(-k_h * time_h))
    # Override with direct recovery_pct param if provided
    if "recovery_pct" in params:
        recovery = float(params["recovery_pct"]) / 100.0
    recovery = max(0.0, min(0.95, recovery))

    au_recovered = s.gold_flow * recovery
    pls_flow = s.volume_flow * 0.8  # pregnant leach solution ~80 % of applied solution
    dissolved_au = au_recovered * 1e6 / max(pls_flow, 0.001)

    pls = SimStream(
        mass_flow=0.0,
        volume_flow=pls_flow,
        dissolved_gold=dissolved_au,
        gold_flow=au_recovered,
        cyanide_ppm=float(params.get("cn_ppm_applied", 250)),
        pH=float(params.get("pH_applied", 10.5)),
    )
    tailings = s.copy(
        gold_flow=s.gold_flow - au_recovered,
        gold_grade=(s.gold_flow - au_recovered) / max(s.mass_flow, 0.001) * 1000,
        dissolved_gold=0.0,
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"pls": pls, "tailings": tailings},
        recovery_pct=recovery * 100,
        reagent_consumptions={"NaCN_kg_t": float(params.get("cn_dose_kg_t", 0.5))},
        utilization_rate=util,
        kpis={"recovery_pct": round(recovery * 100, 2), "ultimate_recovery": ultimate_recovery},
    )


def _calc_pressure_oxidation(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    sulfide_conv = float(params.get("sulfide_conversion", 0.95))
    temp_c = float(params.get("temperature_c", 225))

    # Gold recovery potential improves after POX (refractory → free-milling)
    # Sulfide oxidation: FeS₂ + O₂ → Fe²⁺ + 2S + H₂SO₄ (simplified)
    sulphide_remaining = s.sulphide_pct * (1.0 - sulfide_conv)
    acid_generated_kg_t = s.sulphide_pct / 100.0 * 2.0 * sulfide_conv * 98.0  # kg H₂SO₄/t
    o2_consumption_kg_t = s.sulphide_pct / 100.0 * sulfide_conv * 64.0  # kg O₂/t

    # Gold is now accessible — downstream leach recovery improves significantly
    oxidized_feed = s.copy(
        sulphide_pct=sulphide_remaining,
        temperature=min(temp_c, 25.0),  # cooled before leach
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"oxidized_feed": oxidized_feed},
        energy_kwh_t=float(params.get("energy_kwh_t", 80)),
        reagent_consumptions={"O2_kg_t": round(o2_consumption_kg_t, 2)},
        utilization_rate=util,
        kpis={
            "sulfide_conversion_pct": sulfide_conv * 100,
            "acid_generated_kg_t": round(acid_generated_kg_t, 2),
            "temperature_c": temp_c,
        },
    )


def _calc_roaster(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    temp_c = float(params.get("temperature_c", 650))
    oxidation_eff = float(params.get("oxidation_efficiency", 0.95))

    # SO₂ emission: S + O₂ → SO₂; molar ratio 1:1; S MW=32, SO₂ MW=64
    sulphide_oxidised = s.sulphide_pct / 100.0 * oxidation_eff
    so2_kg_t = sulphide_oxidised * 2.0 * (64.0 / 32.0)  # kg SO₂/t ore
    sulphide_remaining = s.sulphide_pct * (1.0 - oxidation_eff)
    energy = float(params.get("energy_kwh_t", 120))

    calcine = s.copy(
        sulphide_pct=sulphide_remaining,
        temperature=25.0,  # quenched
        energy_kwh_t=s.energy_kwh_t + energy,
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"calcine": calcine},
        energy_kwh_t=energy,
        reagent_consumptions={"SO2_emitted_kg_t": round(so2_kg_t, 2)},
        utilization_rate=util,
        kpis={"temperature_c": temp_c, "so2_kg_t": round(so2_kg_t, 2), "oxidation_eff": oxidation_eff},
    )


def _calc_bioleach_tank(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    oxidation_pct = float(params.get("oxidation_pct", 90))
    tau = float(params.get("residence_time_h", 96))
    mu_max = float(params.get("mu_max", 0.05))  # h⁻¹ bacterial growth rate

    # Simplified bacterial kinetics
    oxidation_conv = 1.0 - _safe_exp(-mu_max * tau)
    oxidation_conv = min(oxidation_conv, oxidation_pct / 100.0)

    sulphide_remaining = s.sulphide_pct * (1.0 - oxidation_conv)
    oxidized_feed = s.copy(
        sulphide_pct=sulphide_remaining,
        pH=1.8,  # acidic bioleach environment
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"oxidized_feed": oxidized_feed},
        energy_kwh_t=float(params.get("energy_kwh_t", 15)),
        utilization_rate=util,
        kpis={"oxidation_pct": round(oxidation_conv * 100, 1), "tau_h": tau, "pH_out": 1.8},
    )


def _calc_oxygen_plant(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    o2_mg_l = float(params.get("o2_mg_l", 8))
    float(params.get("o2_purity", 0.93))
    # O₂ addition to pulp — pass-through with O₂ enrichment marker
    enriched = s.copy()  # stream properties unchanged; O₂ tracked externally
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    o2_kg_h = o2_mg_l * s.volume_flow / 1000.0  # kg/h
    return UnitOutput(
        streams={"enriched": enriched},
        energy_kwh_t=float(params.get("energy_kwh_t", 0.5)),
        reagent_consumptions={"O2_kg_h": round(o2_kg_h, 2)},
        utilization_rate=util,
        kpis={"o2_dissolved_mg_l": o2_mg_l, "o2_kg_h": round(o2_kg_h, 2)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  CARBON / ADR CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _calc_carbon_adsorption(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    K_F = float(params.get("K_F", 50.0))
    n_exp = float(params.get("n_freundlich", 0.5))
    n_stages = int(params.get("n_stages", 5))
    carbon_g_l = float(params.get("carbon_g_l", 15))

    dissolved_conc = s.dissolved_gold  # mg/L
    total_adsorbed_kg_h = 0.0

    for _ in range(n_stages):
        if dissolved_conc <= 0:
            break
        q = K_F * (max(dissolved_conc, 0.001) ** (1.0 / n_exp))  # g Au / kg C
        carbon_kg_m3 = carbon_g_l / 1000.0
        au_per_stage = q / 1e6 * carbon_kg_m3 * s.volume_flow * 1000  # kg/h
        au_per_stage = min(au_per_stage, dissolved_conc * s.volume_flow / 1e6)
        total_adsorbed_kg_h += au_per_stage
        dissolved_conc = max(0, dissolved_conc - au_per_stage * 1e6 / max(s.volume_flow, 0.001))

    recovery = total_adsorbed_kg_h / max(s.gold_flow, 0.001)
    recovery = min(recovery, 0.999)

    raffinate = s.copy(dissolved_gold=dissolved_conc, gold_flow=s.gold_flow - total_adsorbed_kg_h)
    loaded_carbon = SimStream(gold_flow=total_adsorbed_kg_h)
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"raffinate": raffinate, "loaded_carbon": loaded_carbon},
        recovery_pct=recovery * 100,
        utilization_rate=util,
        kpis={
            "adsorption_recovery_pct": round(recovery * 100, 2),
            "raffinate_au_mg_l": round(dissolved_conc, 4),
            "n_stages": n_stages,
        },
    )


def _calc_elution_column(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    elution_eff = float(params.get("elution_efficiency", 0.98))
    strip_vol_factor = float(params.get("strip_volume_factor", 5))  # BV of eluent

    au_eluted_kg_h = s.gold_flow * elution_eff
    strip_volume_m3_h = s.mass_flow * strip_vol_factor * 0.001  # rough estimate
    au_conc_strip = au_eluted_kg_h * 1e6 / max(strip_volume_m3_h, 0.001)  # mg/L (very high)

    strip_solution = SimStream(
        volume_flow=strip_volume_m3_h,
        gold_flow=au_eluted_kg_h,
        dissolved_gold=au_conc_strip,
        temperature=80.0,  # hot strip solution
        cyanide_ppm=float(params.get("cn_eluent_ppm", 10000)),
    )
    barren_carbon = s.copy(gold_flow=s.gold_flow - au_eluted_kg_h, dissolved_gold=0.0)
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"strip_solution": strip_solution, "barren_carbon": barren_carbon},
        recovery_pct=elution_eff * 100,
        energy_kwh_t=float(params.get("energy_kwh_t", 25)),
        reagent_consumptions={"NaOH_kg_t": 1.5, "NaCN_kg_t": 0.5},
        utilization_rate=util,
        kpis={"elution_efficiency": elution_eff, "strip_au_mg_l": round(au_conc_strip, 1)},
    )


def _calc_carbon_kiln(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    carbon_loss_pct = float(params.get("carbon_loss_pct", 0.5))
    reactivation_temp = float(params.get("temperature_c", 700))

    carbon_remaining = s.mass_flow * (1.0 - carbon_loss_pct / 100.0)
    reactivated = s.copy(mass_flow=carbon_remaining)
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"reactivated_carbon": reactivated},
        energy_kwh_t=float(params.get("energy_kwh_t", 30)),
        utilization_rate=util,
        kpis={"carbon_loss_pct": carbon_loss_pct, "temperature_c": reactivation_temp},
    )


def _calc_carbon_screen(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    screen_eff = float(params.get("screen_efficiency", 0.998))

    # Carbon is coarse (>1 mm) and is screened from pulp (fine)
    carbon_frac = float(params.get("carbon_fraction", 0.01))  # ~1 % of stream is carbon
    carbon_recovered = s.mass_flow * carbon_frac * screen_eff
    pulp_through = s.copy(
        mass_flow=s.mass_flow * (1.0 - carbon_frac * screen_eff),
        volume_flow=s.volume_flow,
    )
    separated_carbon = SimStream(mass_flow=carbon_recovered, gold_flow=s.gold_flow * carbon_frac)
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"pulp": pulp_through, "separated_carbon": separated_carbon},
        utilization_rate=util,
        kpis={"screen_efficiency": screen_eff},
    )


def _calc_resin_adsorption(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    K_R = float(params.get("K_R", 80.0))
    n_exp = float(params.get("n_freundlich", 0.45))
    resin_vol_l = float(params.get("resin_volume_l", 1000))
    n_stages = int(params.get("n_stages", 4))

    dissolved_conc = s.dissolved_gold
    total_adsorbed = 0.0
    for _ in range(n_stages):
        if dissolved_conc <= 0:
            break
        q = K_R * (max(dissolved_conc, 0.001) ** (1.0 / n_exp))
        au_per_stage = q / 1e6 * resin_vol_l / 1000 * 1000  # kg/h
        au_per_stage = min(au_per_stage, dissolved_conc * s.volume_flow / 1e6)
        total_adsorbed += au_per_stage
        dissolved_conc = max(0, dissolved_conc - au_per_stage * 1e6 / max(s.volume_flow, 0.001))

    recovery = total_adsorbed / max(s.gold_flow, 0.001)
    recovery = min(recovery, 0.999)
    raffinate = s.copy(dissolved_gold=dissolved_conc, gold_flow=s.gold_flow - total_adsorbed)
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"raffinate": raffinate, "loaded_resin": SimStream(gold_flow=total_adsorbed)},
        recovery_pct=recovery * 100,
        utilization_rate=util,
        kpis={"adsorption_recovery_pct": round(recovery * 100, 2), "raffinate_au_mg_l": round(dissolved_conc, 4)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ELECTROMETALLURGY CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _calc_electrowinning(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    current_eff = min(1.0, float(params.get("current_efficiency_pct", 90)) / 100.0)
    current_a = float(params.get("current_A", 1000))
    n_cells = int(params.get("n_cells", 4))
    total_current = current_a * n_cells

    # Faraday: m = (M * I * t * CE) / (n * F)  [g/h]
    # t = 3600 s/h (for 1 hour production rate)
    m_au_g_h = (MOLAR_MASS_AU * total_current * 3600.0 * current_eff) / (AU_ELECTRONS * FARADAY)
    m_au_kg_h = m_au_g_h / 1000.0

    # Limit to available dissolved gold
    au_available_kg_h = s.dissolved_gold * s.volume_flow / 1e6
    m_au_kg_h = min(m_au_kg_h, au_available_kg_h)

    recovery = m_au_kg_h / max(s.gold_flow, 0.001)
    recovery = min(recovery, 0.999)
    spent_solution = s.copy(
        dissolved_gold=max(0, s.dissolved_gold - m_au_kg_h * 1e6 / max(s.volume_flow, 0.001)),
        gold_flow=s.gold_flow - m_au_kg_h,
    )
    sludge = SimStream(gold_flow=m_au_kg_h, mass_flow=m_au_kg_h, gold_grade=800000.0)  # ~80 % purity sludge

    cell_voltage = float(params.get("cell_voltage_V", 3.5))
    power_kw = total_current * cell_voltage / 1000.0
    energy_kwh_t = power_kw / max(m_au_kg_h, 0.001)

    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"sludge": sludge, "spent_solution": spent_solution},
        recovery_pct=recovery * 100,
        energy_kwh_t=energy_kwh_t,
        utilization_rate=util,
        kpis={
            "au_deposited_kg_h": round(m_au_kg_h, 4),
            "current_efficiency": current_eff,
            "power_kw": round(power_kw, 2),
        },
    )


def _calc_induction_furnace(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    dore_purity = float(params.get("dore_purity", 0.92))  # mass fraction Au+Ag in doré

    total_precious_kg_h = s.gold_flow + s.silver_grade * s.mass_flow / 1e6
    dore_mass_kg_h = total_precious_kg_h / dore_purity
    slag_mass_kg_h = dore_mass_kg_h - total_precious_kg_h
    energy = float(params.get("energy_kwh_t", 50))

    dore_bar = SimStream(
        mass_flow=dore_mass_kg_h / 1000.0,
        gold_flow=s.gold_flow,
        gold_grade=s.gold_flow / max(dore_mass_kg_h / 1000.0, 0.001) * 1000,
    )
    slag = SimStream(mass_flow=slag_mass_kg_h / 1000.0)
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"dore_bar": dore_bar, "slag": slag},
        recovery_pct=dore_purity * 100,
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"dore_purity_pct": dore_purity * 100, "dore_kg_h": round(dore_mass_kg_h, 4)},
    )


def _calc_refinery(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    au_purity = 0.9999
    ag_byproduct_ratio = float(params.get("ag_au_ratio", 0.08))  # Ag per kg Au

    refined_au_kg_h = s.gold_flow * au_purity
    ag_kg_h = refined_au_kg_h * ag_byproduct_ratio

    refined_au = SimStream(
        gold_flow=refined_au_kg_h,
        mass_flow=refined_au_kg_h,
        gold_grade=999900.0,  # g/t = 999.9 g/kg
    )
    ag_byproduct = SimStream(mass_flow=ag_kg_h / 1000.0)
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"refined_au": refined_au, "ag_byproduct": ag_byproduct},
        recovery_pct=au_purity * 100,
        energy_kwh_t=float(params.get("energy_kwh_t", 80)),
        utilization_rate=util,
        kpis={"au_purity": au_purity, "ag_byproduct_kg_h": round(ag_kg_h, 4)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  SOLID-LIQUID SEPARATION CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _calc_thickener(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    uf_solids_pct = float(params.get("underflow_solids_pct", 55))
    of_water_pct = float(params.get("overflow_water_pct", 85))

    # Mass balance
    # Underflow: solids_pct determined by thickening
    water_in = s.volume_flow - s.mass_flow / 2.7
    water_to_overflow = water_in * (of_water_pct / 100.0)
    water_to_underflow = water_in - water_to_overflow

    uf_volume = s.mass_flow / 2.7 + water_to_underflow
    of_volume = water_to_overflow

    # Suspended solids in overflow (assume 99 % clarification)
    solids_in_overflow = s.mass_flow * 0.01
    solids_in_underflow = s.mass_flow - solids_in_overflow
    au_with_water = s.gold_flow * (water_to_overflow / max(water_in, 0.001)) * 0.05  # 5 % loss to OF

    overflow = SimStream(
        mass_flow=solids_in_overflow,
        volume_flow=of_volume,
        gold_flow=au_with_water,
        dissolved_gold=s.dissolved_gold,
        pH=s.pH,
    )
    underflow = s.copy(
        mass_flow=solids_in_underflow,
        volume_flow=uf_volume,
        solids_pct=uf_solids_pct,
        gold_flow=s.gold_flow - au_with_water,
        gold_grade=(s.gold_flow - au_with_water) / max(solids_in_underflow, 0.001) * 1000,
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"underflow": underflow, "overflow": overflow},
        utilization_rate=util,
        energy_kwh_t=float(params.get("energy_kwh_t", 0.5)),
        kpis={"underflow_solids_pct": uf_solids_pct, "overflow_water_recovery": of_water_pct},
    )


def _calc_filter(
    inlet_streams, params, feed_input, design_capacity_tph, availability_pct, default_moisture_pct: float = 12.0
):
    s = _primary_inlet(inlet_streams)
    cake_moisture = float(params.get("cake_moisture_pct", default_moisture_pct))

    # Cake mass: moisture is % of wet cake mass
    wet_cake_mass = s.mass_flow / (1.0 - cake_moisture / 100.0)
    water_in_cake = wet_cake_mass - s.mass_flow
    total_water = s.volume_flow - s.mass_flow / 2.7
    filtrate_water = max(0.0, total_water - water_in_cake)

    filter_cake = s.copy(
        volume_flow=water_in_cake / 1.0,  # density water = 1 t/m³
        solids_pct=100.0 - cake_moisture,
    )
    filtrate = SimStream(
        volume_flow=filtrate_water,
        dissolved_gold=s.dissolved_gold,
        gold_flow=s.dissolved_gold * filtrate_water / 1e6,
        cyanide_ppm=s.cyanide_ppm,
        pH=s.pH,
    )
    energy = float(params.get("energy_kwh_t", 8))
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"filter_cake": filter_cake, "filtrate": filtrate},
        energy_kwh_t=energy,
        utilization_rate=util,
        kpis={"cake_moisture_pct": cake_moisture, "filtrate_m3_h": round(filtrate_water, 2)},
    )


def _calc_pressure_filter(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    return _calc_filter(inlet_streams, params, feed_input, design_capacity_tph, availability_pct, 12.0)


def _calc_disc_filter(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    return _calc_filter(inlet_streams, params, feed_input, design_capacity_tph, availability_pct, 18.0)


def _calc_belt_filter(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    return _calc_filter(inlet_streams, params, feed_input, design_capacity_tph, availability_pct, 20.0)


def _calc_ccd_circuit(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    n_stages = int(params.get("n_stages", 5))
    wash_ratio = float(params.get("wash_ratio", 1.5))

    # Washing efficiency: E = R^n / (R^n - 1) for R = wash_ratio > 1
    if wash_ratio > 1.0:
        r_n = wash_ratio**n_stages
        wash_efficiency = r_n / (r_n - 1.0)
        wash_efficiency = min(wash_efficiency, 0.999)
    else:
        wash_efficiency = 0.5

    # PLS (overflow) carries dissolved gold
    pls_au = s.dissolved_gold * s.volume_flow / 1e6 * wash_efficiency
    pls_volume = s.volume_flow * float(params.get("dilution_factor", 0.7))

    pls = SimStream(
        volume_flow=pls_volume,
        dissolved_gold=pls_au / max(pls_volume, 0.001) * 1e6,
        gold_flow=pls_au,
        cyanide_ppm=s.cyanide_ppm,
        pH=s.pH,
    )
    # Washed cake underflow
    cake_au_residual = s.gold_flow - pls_au
    washed_cake = s.copy(
        dissolved_gold=s.dissolved_gold * (1.0 - wash_efficiency),
        gold_flow=cake_au_residual,
        gold_grade=cake_au_residual / max(s.mass_flow, 0.001) * 1000,
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"pls": pls, "washed_cake": washed_cake},
        recovery_pct=wash_efficiency * 100,
        energy_kwh_t=float(params.get("energy_kwh_t", 2.0)),
        utilization_rate=util,
        kpis={"wash_efficiency_pct": round(wash_efficiency * 100, 2), "n_stages": n_stages},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  EFFLUENT TREATMENT CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _calc_cn_destruction_so2(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    efficiency = float(params.get("efficiency", 0.98))
    so2_ratio = 2.5  # kg SO₂ per kg CN (INCO process)

    cn_kg_h = s.cyanide_ppm * s.volume_flow / 1e6 * 1000  # t/h * 1000 = kg/h
    cn_destroyed = cn_kg_h * efficiency
    so2_consumed = cn_destroyed * so2_ratio
    cu_consumed = cn_destroyed * 0.05  # catalyst

    treated = s.copy(
        cyanide_ppm=s.cyanide_ppm * (1.0 - efficiency),
        pH=max(8.5, s.pH),
    )
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"treated_effluent": treated},
        reagent_consumptions={"SO2_kg_h": round(so2_consumed, 2), "CuSO4_kg_h": round(cu_consumed, 3)},
        energy_kwh_t=float(params.get("energy_kwh_t", 1.5)),
        utilization_rate=util,
        kpis={"cn_reduction_pct": efficiency * 100, "so2_consumed_kg_h": round(so2_consumed, 2)},
    )


def _calc_cn_destruction_h2o2(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    efficiency = float(params.get("efficiency", 0.95))
    h2o2_ratio = 3.5  # kg H₂O₂ per kg CN

    cn_kg_h = s.cyanide_ppm * s.volume_flow / 1e6 * 1000
    cn_destroyed = cn_kg_h * efficiency
    h2o2_consumed = cn_destroyed * h2o2_ratio

    treated = s.copy(
        cyanide_ppm=s.cyanide_ppm * (1.0 - efficiency),
        pH=max(7.0, s.pH - 1.0),
    )
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"treated_effluent": treated},
        reagent_consumptions={"H2O2_kg_h": round(h2o2_consumed, 2)},
        energy_kwh_t=float(params.get("energy_kwh_t", 0.5)),
        utilization_rate=util,
        kpis={"cn_reduction_pct": efficiency * 100, "h2o2_consumed_kg_h": round(h2o2_consumed, 2)},
    )


def _calc_sart_process(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    cu_recovery = float(params.get("cu_recovery", 0.90))
    cn_recovery = float(params.get("cn_recovery", 0.80))
    # Cu precipitation as CuS; CN regenerated as HCN then re-alkalised
    cu_ppm = float(params.get("cu_ppm_inlet", 200))
    cu_kg_h = cu_ppm * s.volume_flow / 1e6 * 1000
    cus_kg_h = cu_kg_h * cu_recovery * (95.6 / 63.5)  # CuS molar mass

    cn_kg_h = s.cyanide_ppm * s.volume_flow / 1e6 * 1000
    cn_kg_h * cn_recovery

    cus_precipitate = SimStream(mass_flow=cus_kg_h / 1000.0)
    cn_solution = s.copy(
        cyanide_ppm=s.cyanide_ppm * cn_recovery,  # regenerated CN
        pH=max(s.pH, 10.5),
    )
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"cus_precipitate": cus_precipitate, "cn_solution": cn_solution},
        reagent_consumptions={"H2SO4_kg_h": round(cn_kg_h * 0.5, 2), "NaOH_kg_h": round(cn_kg_h * 0.3, 2)},
        energy_kwh_t=float(params.get("energy_kwh_t", 2.0)),
        utilization_rate=util,
        kpis={
            "cu_recovery_pct": cu_recovery * 100,
            "cn_recovery_pct": cn_recovery * 100,
            "cus_kg_h": round(cus_kg_h, 2),
        },
    )


def _calc_tailings_storage(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    density_t_m3 = float(params.get("density_t_m3", 1.4))
    tsf_volume_m3_h = s.mass_flow / density_t_m3
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={},  # TSF is a sink
        utilization_rate=util,
        kpis={
            "tailings_rate_tph": round(s.mass_flow, 2),
            "tsf_volume_m3_h": round(tsf_volume_m3_h, 2),
            "gold_loss_kg_h": round(s.gold_flow, 4),
        },
    )


def _calc_water_recovery(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    recovery_frac = float(params.get("recovery_pct", 75)) / 100.0

    recovered_volume = s.volume_flow * recovery_frac
    effluent_volume = s.volume_flow * (1.0 - recovery_frac)

    recovered_water = SimStream(
        volume_flow=recovered_volume,
        pH=7.5,
        dissolved_gold=s.dissolved_gold * 0.01,  # 99 % gold removed
    )
    effluent = s.copy(volume_flow=effluent_volume)
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"recovered_water": recovered_water, "effluent": effluent},
        energy_kwh_t=float(params.get("energy_kwh_t", 3.0)),
        utilization_rate=util,
        kpis={"water_recovery_pct": recovery_frac * 100, "recovered_m3_h": round(recovered_volume, 2)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITY CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════


def _calc_feed_source(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    feed_rate = float(params.get("feed_tph") or feed_input.get("feed_rate_tph") or params.get("feed_rate_tph", 1000))
    gold_grade = float(params.get("gold_grade_g_t") or feed_input.get("gold_grade_g_t", 1.5))
    p80 = float(params.get("p80_um") or feed_input.get("p80_um", 100000))
    silver_grade = float(params.get("silver_grade_g_t") or feed_input.get("silver_grade_g_t", 0.0))
    sulphide_pct = float(params.get("sulphide_pct") or feed_input.get("sulphide_pct", 0.0))

    stream = SimStream.from_feed(feed_rate, gold_grade, p80, silver_grade, sulphide_pct)
    util = _utilization(feed_rate, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": stream},
        utilization_rate=util,
        kpis={"feed_rate_tph": feed_rate, "gold_grade_g_t": gold_grade, "p80_um": p80},
    )


def _calc_product_sink(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    total_mass = sum(s.mass_flow for s in inlet_streams.values())
    total_gold = sum(s.gold_flow for s in inlet_streams.values())
    sum(s.volume_flow for s in inlet_streams.values())

    avg_grade = total_gold / max(total_mass, 0.001) * 1000
    gold_price = float(params.get("gold_price_usd", 2000))
    oz_per_hour = total_gold / 0.0311035
    revenue_usd_h = oz_per_hour * gold_price

    util = _utilization(total_mass, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={},  # sink
        utilization_rate=util,
        kpis={
            "total_mass_tph": round(total_mass, 2),
            "total_gold_kg_h": round(total_gold, 4),
            "product_grade_g_t": round(avg_grade, 2),
            "oz_per_hour": round(oz_per_hour, 3),
            "revenue_usd_h": round(revenue_usd_h, 2),
        },
    )


def _calc_stream_splitter(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    split_ratio = float(params.get("split_ratio", 0.5))
    split_ratio = max(0.0, min(1.0, split_ratio))

    stream_a = s.copy(
        mass_flow=s.mass_flow * split_ratio,
        volume_flow=s.volume_flow * split_ratio,
        gold_flow=s.gold_flow * split_ratio,
    )
    stream_b = s.copy(
        mass_flow=s.mass_flow * (1.0 - split_ratio),
        volume_flow=s.volume_flow * (1.0 - split_ratio),
        gold_flow=s.gold_flow * (1.0 - split_ratio),
    )
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"stream_a": stream_a, "stream_b": stream_b},
        utilization_rate=util,
        kpis={"split_ratio": split_ratio},
    )


def _calc_stream_mixer(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    streams = list(inlet_streams.values())
    if not streams:
        return UnitOutput(streams={"product": SimStream()})
    mixed = streams[0]
    for other in streams[1:]:
        mixed = mixed.mix(other)
    util = _utilization(mixed.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": mixed},
        utilization_rate=util,
        kpis={"n_inlets": len(streams), "mixed_mass_tph": round(mixed.mass_flow, 2)},
    )


def _calc_pump(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    head_m = float(params.get("head_m", 30))
    pump_eff = float(params.get("efficiency", 0.75))
    sg = float(params.get("slurry_sg", 1.4))

    # Hydraulic power: P = ρ·g·Q·H / η  kW; Q in m³/s
    flow_m3_s = s.volume_flow / 3600.0
    power_kw = (sg * 1000 * 9.81 * flow_m3_s * head_m) / (pump_eff * 1000)
    energy_kwh_t = power_kw / max(s.mass_flow, 0.001)

    product = s.copy(energy_kwh_t=s.energy_kwh_t + energy_kwh_t)
    util = _utilization(s.volume_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": product},
        energy_kwh_t=energy_kwh_t,
        utilization_rate=util,
        kpis={"head_m": head_m, "power_kw": round(power_kw, 2)},
    )


def _calc_reagent_addition(inlet_streams, params, feed_input, design_capacity_tph, availability_pct):
    s = _primary_inlet(inlet_streams)
    reagent_type = params.get("reagent_type", "NaCN")
    dose_kg_t = float(params.get("dose_kg_t", 1.0))
    additions: dict[str, Any] = {}

    if reagent_type == "NaCN":
        # NaCN dose in kg/t → CN⁻ ppm in solution
        cn_added_kg_h = dose_kg_t * s.mass_flow
        cn_added_ppm = cn_added_kg_h * 1e6 / max(s.volume_flow * 1000, 0.001)
        additions["cyanide_ppm"] = s.cyanide_ppm + cn_added_ppm
    elif reagent_type in ("CaO", "Ca(OH)2", "lime"):
        additions["pH"] = min(12.5, s.pH + float(params.get("ph_adjustment", 0.5)))
    elif reagent_type == "H2SO4":
        additions["pH"] = max(1.0, s.pH - float(params.get("ph_adjustment", 1.0)))
    elif reagent_type == "NaOH":
        additions["pH"] = min(14.0, s.pH + float(params.get("ph_adjustment", 0.3)))

    product = s.copy(**additions)
    total_reagent_kg_h = dose_kg_t * s.mass_flow
    util = _utilization(s.mass_flow, design_capacity_tph, availability_pct)
    return UnitOutput(
        streams={"product": product},
        reagent_consumptions={f"{reagent_type}_kg_t": dose_kg_t},
        utilization_rate=util,
        kpis={"reagent_type": reagent_type, "dose_kg_t": dose_kg_t, "total_reagent_kg_h": round(total_reagent_kg_h, 2)},
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  DISPATCHER
# ═══════════════════════════════════════════════════════════════════════════════

_DISPATCH: dict[str, Any] = {
    # Comminution
    "jaw_crusher": _calc_jaw_crusher,
    "gyratory_crusher": _calc_gyratory_crusher,
    "cone_crusher": _calc_cone_crusher,
    "sag_mill": _calc_sag_mill,
    "ball_mill": _calc_ball_mill,
    "rod_mill": _calc_rod_mill,
    "hpgr": _calc_hpgr,
    "isamill": _calc_isamill,
    "hydrocyclone": _calc_hydrocyclone,
    "vibrating_screen": _calc_vibrating_screen,
    # Leaching
    "agitated_tank_leach": _calc_agitated_tank_leach,
    "cil_reactor": _calc_cil_reactor,
    "cip_reactor": _calc_cip_reactor,
    "vat_leach": _calc_vat_leach,
    "heap_leach_pad": _calc_heap_leach_pad,
    "pressure_oxidation": _calc_pressure_oxidation,
    "roaster": _calc_roaster,
    "bioleach_tank": _calc_bioleach_tank,
    "oxygen_plant": _calc_oxygen_plant,
    # Carbon / ADR
    "carbon_adsorption": _calc_carbon_adsorption,
    "elution_column": _calc_elution_column,
    "carbon_kiln": _calc_carbon_kiln,
    "carbon_screen": _calc_carbon_screen,
    "resin_adsorption": _calc_resin_adsorption,
    # Electrometallurgy
    "electrowinning": _calc_electrowinning,
    "induction_furnace": _calc_induction_furnace,
    "refinery": _calc_refinery,
    # Solid-liquid separation
    "thickener": _calc_thickener,
    "pressure_filter": _calc_pressure_filter,
    "disc_filter": _calc_disc_filter,
    "belt_filter": _calc_belt_filter,
    "ccd_circuit": _calc_ccd_circuit,
    # Effluents
    "cn_destruction_so2": _calc_cn_destruction_so2,
    "cn_destruction_h2o2": _calc_cn_destruction_h2o2,
    "sart_process": _calc_sart_process,
    "tailings_storage": _calc_tailings_storage,
    "water_recovery": _calc_water_recovery,
    # Utilities
    "feed_source": _calc_feed_source,
    "product_sink": _calc_product_sink,
    "stream_splitter": _calc_stream_splitter,
    "stream_mixer": _calc_stream_mixer,
    "pump": _calc_pump,
    "reagent_addition": _calc_reagent_addition,
}


def calculate_unit(
    unit_type: str,
    inlet_streams: dict[str, SimStream],
    params: dict,
    feed_input: dict,
    design_capacity_tph: float = 0,
    availability_pct: float = 95,
) -> UnitOutput:
    """Dispatch to the appropriate unit model and return enriched UnitOutput.

    Falls back to a pass-through model for unknown unit types.
    """
    fn = _DISPATCH.get(unit_type)
    if fn is None:
        logger.warning("Unknown unit type '%s' — applying pass-through model", unit_type)
        primary = _primary_inlet(inlet_streams)
        return UnitOutput(
            streams={"product": primary.copy()},
            utilization_rate=_utilization(primary.mass_flow, design_capacity_tph, availability_pct),
            kpis={"warning": f"Unknown unit type: {unit_type}"},
        )
    try:
        return fn(inlet_streams, params, feed_input, design_capacity_tph, availability_pct)
    except Exception as exc:
        logger.error("Unit calculation failed for '%s': %s", unit_type, exc)
        primary = _primary_inlet(inlet_streams)
        return UnitOutput(
            streams={"product": primary.copy()},
            kpis={"error": str(exc)},
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  UNIT REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════


def _ps(name, label, typ="number", default=None, unit="", min_v=None, max_v=None, options=None):
    """Build a parameter schema entry."""
    entry: dict[str, Any] = {"name": name, "label": label, "type": typ, "default": default, "unit": unit}
    if min_v is not None:
        entry["min"] = min_v
    if max_v is not None:
        entry["max"] = max_v
    if options is not None:
        entry["options"] = options
    return entry


UNIT_REGISTRY: dict[str, dict] = {
    # ── COMMINUTION ────────────────────────────────────────────────────────────
    "jaw_crusher": {
        "unit_type": "jaw_crusher",
        "display_name": "Concasseur à mâchoires",
        "category": "comminution",
        "default_params": {"wi": 14, "reduction_ratio": 4, "p80_out_um": 50000},
        "param_schema": [
            _ps("wi", "Bond Work Index", default=14, unit="kWh/t", min_v=5, max_v=50),
            _ps("reduction_ratio", "Ratio de réduction", default=4, unit="-", min_v=2, max_v=8),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "solid",
        "description": "Concasseur primaire pour minerai ROM. Modèle Bond-crushing (Ci=0.3).",
    },
    "gyratory_crusher": {
        "unit_type": "gyratory_crusher",
        "display_name": "Concasseur giratoire",
        "category": "comminution",
        "default_params": {"wi": 14, "reduction_ratio": 5},
        "param_schema": [
            _ps("wi", "Bond Work Index", default=14, unit="kWh/t", min_v=5, max_v=50),
            _ps("reduction_ratio", "Ratio de réduction", default=5, unit="-", min_v=3, max_v=10),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "solid",
        "description": "Concasseur primaire haute capacité. Modèle Whiten simplifié.",
    },
    "cone_crusher": {
        "unit_type": "cone_crusher",
        "display_name": "Concasseur à cône",
        "category": "comminution",
        "default_params": {"wi": 14, "css_mm": 25},
        "param_schema": [
            _ps("wi", "Bond Work Index", default=14, unit="kWh/t", min_v=5, max_v=50),
            _ps("css_mm", "CSS (ouverture fermée)", default=25, unit="mm", min_v=5, max_v=100),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "solid",
        "description": "Concasseur secondaire/tertiaire. P80 = 1.5 × CSS.",
    },
    "sag_mill": {
        "unit_type": "sag_mill",
        "display_name": "Broyeur SAB (SAG)",
        "category": "comminution",
        "default_params": {"wi": 14, "p80_out_um": 2000, "spi": 50, "load_pct": 30, "critical_speed_pct": 75},
        "param_schema": [
            _ps("wi", "Bond Work Index", default=14, unit="kWh/t", min_v=5, max_v=50),
            _ps("p80_out_um", "P80 produit cible", default=2000, unit="µm", min_v=100, max_v=50000),
            _ps("spi", "SPI (ore hardness index)", default=50, unit="-", min_v=1, max_v=100),
            _ps("load_pct", "Charge broyeur", default=30, unit="%", min_v=15, max_v=45),
            _ps("critical_speed_pct", "Vitesse critique", default=75, unit="%", min_v=60, max_v=85),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Broyeur semi-autogène. Modèle SPI (Starkey & Dobby).",
    },
    "ball_mill": {
        "unit_type": "ball_mill",
        "display_name": "Broyeur à boulets",
        "category": "comminution",
        "default_params": {"wi": 14, "p80_out_um": 75},
        "param_schema": [
            _ps("wi", "Bond Work Index", default=14, unit="kWh/t", min_v=5, max_v=50),
            _ps("p80_out_um", "P80 produit cible", default=75, unit="µm", min_v=10, max_v=5000),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Broyeur secondaire. Équation Bond Ball Mill: W = Wi(10/√P80 - 10/√F80).",
    },
    "rod_mill": {
        "unit_type": "rod_mill",
        "display_name": "Broyeur à barres",
        "category": "comminution",
        "default_params": {"wi": 14, "p80_out_um": 1000},
        "param_schema": [
            _ps("wi", "Bond Work Index", default=14, unit="kWh/t", min_v=5, max_v=50),
            _ps("p80_out_um", "P80 produit cible", default=1000, unit="µm", min_v=100, max_v=10000),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Broyeur à barres. +10 % énergie vs broyeur à boulets (Bond Rod Mill).",
    },
    "hpgr": {
        "unit_type": "hpgr",
        "display_name": "HPGR (broyeur à rouleaux haute pression)",
        "category": "comminution",
        "default_params": {"specific_pressing_force_kn_m2": 3.5},
        "param_schema": [
            _ps(
                "specific_pressing_force_kn_m2",
                "Force de pressage spécifique",
                default=3.5,
                unit="kN/m²",
                min_v=1.0,
                max_v=10.0,
            ),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "solid",
        "description": "HPGR: énergie = force × 0.3 kWh/t. Réduction P80 de 60 %.",
    },
    "isamill": {
        "unit_type": "isamill",
        "display_name": "IsaMill (broyeur stirred)",
        "category": "comminution",
        "default_params": {"ore_constant": 30, "p80_out_um": 20},
        "param_schema": [
            _ps("ore_constant", "Constante de minerai (c1)", default=30, unit="-", min_v=5, max_v=100),
            _ps("p80_out_um", "P80 produit cible", default=20, unit="µm", min_v=1, max_v=200),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Broyeur stirred ultra-fin. Modèle Ishikawa: W = c1 × (1/P80_out - 1/P80_in).",
    },
    "hydrocyclone": {
        "unit_type": "hydrocyclone",
        "display_name": "Hydrocyclone",
        "category": "comminution",
        "default_params": {"d50c_um": 50},
        "param_schema": [
            _ps("d50c_um", "d50c coupure", default=50, unit="µm", min_v=5, max_v=500),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["overflow", "underflow"],
        "stream_type": "pulp",
        "description": "Classification centrifuge. Modèle Plitt simplifié.",
    },
    "vibrating_screen": {
        "unit_type": "vibrating_screen",
        "display_name": "Crible vibrant",
        "category": "comminution",
        "default_params": {"aperture_mm": 10, "efficiency": 0.90},
        "param_schema": [
            _ps("aperture_mm", "Ouverture de maille", default=10, unit="mm", min_v=0.5, max_v=200),
            _ps("efficiency", "Efficacité de criblage", default=0.90, unit="-", min_v=0.5, max_v=1.0),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["undersize", "oversize"],
        "stream_type": "solid",
        "description": "Crible de classification. Séparation fines/grossiers par efficacité.",
    },
    # ── LEACHING ───────────────────────────────────────────────────────────────
    "agitated_tank_leach": {
        "unit_type": "agitated_tank_leach",
        "display_name": "Réacteur de lixiviation agité",
        "category": "leaching",
        "default_params": {"k_leach": 0.08, "residence_time_h": 4, "cn_dose_kg_t": 1.5, "o2_mg_l": 8},
        "param_schema": [
            _ps("k_leach", "Constante de lixiviation k (Elsner)", default=0.08, unit="h⁻¹", min_v=0.01, max_v=1.0),
            _ps("residence_time_h", "Temps de résidence", default=4, unit="h", min_v=0.5, max_v=48),
            _ps("cn_dose_kg_t", "Dose NaCN", default=1.5, unit="kg/t", min_v=0.1, max_v=10),
            _ps("o2_mg_l", "O₂ dissous", default=8, unit="mg/L", min_v=1, max_v=20),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Lixiviation au cyanure en réacteur agité. Cinétique Elsner steady-state.",
    },
    "cil_reactor": {
        "unit_type": "cil_reactor",
        "display_name": "Réacteur CIL",
        "category": "leaching",
        "default_params": {"k_leach": 0.10, "residence_time_h": 8, "carbon_g_l": 15, "cn_dose_kg_t": 2.0},
        "param_schema": [
            _ps("k_leach", "Constante k (Elsner)", default=0.10, unit="h⁻¹", min_v=0.01, max_v=1.0),
            _ps("residence_time_h", "Temps de résidence", default=8, unit="h", min_v=1, max_v=72),
            _ps("carbon_g_l", "Inventaire charbon", default=15, unit="g/L", min_v=1, max_v=30),
            _ps("cn_dose_kg_t", "Dose NaCN", default=2.0, unit="kg/t", min_v=0.1, max_v=10),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product", "loaded_carbon"],
        "stream_type": "pulp",
        "description": "Carbon-in-Leach. Dissolution Elsner + adsorption Freundlich (KF=50, n=0.5).",
    },
    "cip_reactor": {
        "unit_type": "cip_reactor",
        "display_name": "Réacteur CIP (lixiviation seulement)",
        "category": "leaching",
        "default_params": {"k_leach": 0.08, "residence_time_h": 6, "cn_dose_kg_t": 1.5},
        "param_schema": [
            _ps("k_leach", "Constante k (Elsner)", default=0.08, unit="h⁻¹", min_v=0.01, max_v=1.0),
            _ps("residence_time_h", "Temps de résidence", default=6, unit="h", min_v=1, max_v=48),
            _ps("cn_dose_kg_t", "Dose NaCN", default=1.5, unit="kg/t", min_v=0.1, max_v=10),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Carbon-in-Pulp: lixiviation uniquement. Adsorption dans circuit séparé.",
    },
    "vat_leach": {
        "unit_type": "vat_leach",
        "display_name": "Lixiviation en cuve",
        "category": "leaching",
        "default_params": {"k_leach": 0.02, "residence_time_h": 48, "cn_dose_kg_t": 1.0},
        "param_schema": [
            _ps("k_leach", "Constante k (Elsner)", default=0.02, unit="h⁻¹", min_v=0.001, max_v=0.2),
            _ps("residence_time_h", "Temps de résidence", default=48, unit="h", min_v=12, max_v=240),
            _ps("cn_dose_kg_t", "Dose NaCN", default=1.0, unit="kg/t", min_v=0.1, max_v=5),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Lixiviation statique en cuve. Cinétique lente (k = 0.02 h⁻¹).",
    },
    "heap_leach_pad": {
        "unit_type": "heap_leach_pad",
        "display_name": "Tas de lixiviation (Heap Leach)",
        "category": "leaching",
        "default_params": {
            "ultimate_recovery": 0.70,
            "leach_rate_h": 0.001,
            "time_h": 2160,
            "cn_dose_kg_t": 0.5,
            "cn_ppm_applied": 250,
        },
        "param_schema": [
            _ps("ultimate_recovery", "Récupération ultime", default=0.70, unit="-", min_v=0.1, max_v=0.95),
            _ps("leach_rate_h", "Taux de lixiviation", default=0.001, unit="h⁻¹", min_v=0.0001, max_v=0.01),
            _ps("time_h", "Durée de lixiviation", default=2160, unit="h", min_v=120, max_v=8760),
            _ps("cn_dose_kg_t", "Dose NaCN", default=0.5, unit="kg/t", min_v=0.05, max_v=2),
            _ps("cn_ppm_applied", "Concentration CN appliqué", default=250, unit="ppm", min_v=50, max_v=1000),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["pls", "tailings"],
        "stream_type": "solid",
        "description": "Tas de lixiviation. Modèle cinétique Darcy + percolation.",
    },
    "pressure_oxidation": {
        "unit_type": "pressure_oxidation",
        "display_name": "Oxydation sous pression (POX)",
        "category": "leaching",
        "default_params": {"sulfide_conversion": 0.95, "temperature_c": 225, "energy_kwh_t": 80},
        "param_schema": [
            _ps("sulfide_conversion", "Conversion sulfures", default=0.95, unit="-", min_v=0.5, max_v=0.99),
            _ps("temperature_c", "Température autoclave", default=225, unit="°C", min_v=150, max_v=250),
            _ps("energy_kwh_t", "Énergie spécifique", default=80, unit="kWh/t", min_v=20, max_v=200),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["oxidized_feed"],
        "stream_type": "pulp",
        "description": "Prétraitement minéraux réfractaires. Oxydation sulfures à haute T°/P°.",
    },
    "roaster": {
        "unit_type": "roaster",
        "display_name": "Four de grillage",
        "category": "leaching",
        "default_params": {"temperature_c": 650, "oxidation_efficiency": 0.95, "energy_kwh_t": 120},
        "param_schema": [
            _ps("temperature_c", "Température de grillage", default=650, unit="°C", min_v=400, max_v=800),
            _ps("oxidation_efficiency", "Efficacité d'oxydation", default=0.95, unit="-", min_v=0.5, max_v=0.99),
            _ps("energy_kwh_t", "Énergie spécifique", default=120, unit="kWh/t", min_v=50, max_v=300),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["calcine"],
        "stream_type": "solid",
        "description": "Grillage à haute température. Conversion sulfures → SO₂ + calcine.",
    },
    "bioleach_tank": {
        "unit_type": "bioleach_tank",
        "display_name": "Biooxydation en réservoir",
        "category": "leaching",
        "default_params": {"oxidation_pct": 90, "residence_time_h": 96, "mu_max": 0.05, "energy_kwh_t": 15},
        "param_schema": [
            _ps("oxidation_pct", "Oxydation cible", default=90, unit="%", min_v=50, max_v=99),
            _ps("residence_time_h", "Temps de résidence", default=96, unit="h", min_v=24, max_v=336),
            _ps("mu_max", "Taux de croissance bactérien max", default=0.05, unit="h⁻¹", min_v=0.01, max_v=0.2),
            _ps("energy_kwh_t", "Énergie aération+agitation", default=15, unit="kWh/t", min_v=5, max_v=50),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["oxidized_feed"],
        "stream_type": "pulp",
        "description": "Biooxydation bactérienne (Acidithiobacillus). pH 1.5-2, T° 35-45°C.",
    },
    "oxygen_plant": {
        "unit_type": "oxygen_plant",
        "display_name": "Usine d'oxygène (VSA/PSA)",
        "category": "leaching",
        "default_params": {"o2_mg_l": 8, "o2_purity": 0.93, "energy_kwh_t": 0.5},
        "param_schema": [
            _ps("o2_mg_l", "O₂ dissous cible", default=8, unit="mg/L", min_v=1, max_v=40),
            _ps("o2_purity", "Pureté O₂", default=0.93, unit="-", min_v=0.5, max_v=0.99),
            _ps("energy_kwh_t", "Énergie VSA/PSA", default=0.5, unit="kWh/t", min_v=0.1, max_v=5),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["enriched"],
        "stream_type": "pulp",
        "description": "Production et injection d'oxygène pur pour la lixiviation.",
    },
    # ── CARBON / ADR ───────────────────────────────────────────────────────────
    "carbon_adsorption": {
        "unit_type": "carbon_adsorption",
        "display_name": "Adsorption sur charbon activé",
        "category": "adsorption",
        "default_params": {"K_F": 50, "n_freundlich": 0.5, "n_stages": 5, "carbon_g_l": 15},
        "param_schema": [
            _ps("K_F", "Constante Freundlich KF", default=50, unit="-", min_v=10, max_v=200),
            _ps("n_freundlich", "Exposant Freundlich n", default=0.5, unit="-", min_v=0.1, max_v=1.0),
            _ps("n_stages", "Nombre d'étages", typ="integer", default=5, unit="-", min_v=1, max_v=10),
            _ps("carbon_g_l", "Inventaire charbon", default=15, unit="g/L", min_v=1, max_v=30),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["raffinate", "loaded_carbon"],
        "stream_type": "solution",
        "description": "Adsorption en cascade. Isotherme Freundlich: q = KF·C^(1/n).",
    },
    "elution_column": {
        "unit_type": "elution_column",
        "display_name": "Colonne d'élution",
        "category": "adsorption",
        "default_params": {"elution_efficiency": 0.98, "strip_volume_factor": 5, "energy_kwh_t": 25},
        "param_schema": [
            _ps("elution_efficiency", "Efficacité d'élution", default=0.98, unit="-", min_v=0.5, max_v=0.999),
            _ps("strip_volume_factor", "Volumes de lit (BV)", default=5, unit="-", min_v=1, max_v=20),
            _ps("energy_kwh_t", "Énergie (chauffage)", default=25, unit="kWh/t", min_v=5, max_v=100),
            _ps("cn_eluent_ppm", "CN éluant", default=10000, unit="ppm", min_v=1000, max_v=50000),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["strip_solution", "barren_carbon"],
        "stream_type": "solution",
        "description": "Désorption thermique AARL ou Zadra. Efficacité 98 % par défaut.",
    },
    "carbon_kiln": {
        "unit_type": "carbon_kiln",
        "display_name": "Four de régénération du charbon",
        "category": "adsorption",
        "default_params": {"carbon_loss_pct": 0.5, "temperature_c": 700, "energy_kwh_t": 30},
        "param_schema": [
            _ps("carbon_loss_pct", "Perte de charbon", default=0.5, unit="%", min_v=0.1, max_v=5),
            _ps("temperature_c", "Température four", default=700, unit="°C", min_v=600, max_v=800),
            _ps("energy_kwh_t", "Énergie spécifique", default=30, unit="kWh/t", min_v=10, max_v=100),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["reactivated_carbon"],
        "stream_type": "solid",
        "description": "Régénération thermique charbon barren. Perte ~0.5 %/cycle.",
    },
    "carbon_screen": {
        "unit_type": "carbon_screen",
        "display_name": "Crible de séparation charbon",
        "category": "adsorption",
        "default_params": {"screen_efficiency": 0.998, "carbon_fraction": 0.01},
        "param_schema": [
            _ps("screen_efficiency", "Efficacité séparation", default=0.998, unit="-", min_v=0.9, max_v=1.0),
            _ps("carbon_fraction", "Fraction charbon dans pulpe", default=0.01, unit="-", min_v=0.001, max_v=0.1),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["pulp", "separated_carbon"],
        "stream_type": "pulp",
        "description": "Séparation mécanique charbon (>1 mm) de la pulpe fine.",
    },
    "resin_adsorption": {
        "unit_type": "resin_adsorption",
        "display_name": "Adsorption sur résine (RIL/RIP)",
        "category": "adsorption",
        "default_params": {"K_R": 80, "n_freundlich": 0.45, "n_stages": 4, "resin_volume_l": 1000},
        "param_schema": [
            _ps("K_R", "Constante Freundlich résine KR", default=80, unit="-", min_v=10, max_v=300),
            _ps("n_freundlich", "Exposant Freundlich n", default=0.45, unit="-", min_v=0.1, max_v=1.0),
            _ps("n_stages", "Nombre d'étages", typ="integer", default=4, unit="-", min_v=1, max_v=8),
            _ps("resin_volume_l", "Volume résine", default=1000, unit="L", min_v=100, max_v=50000),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["raffinate", "loaded_resin"],
        "stream_type": "solution",
        "description": "Résine échangeuse d'ions RIL/RIP. Meilleure cinétique que charbon.",
    },
    # ── ELECTROMETALLURGY ──────────────────────────────────────────────────────
    "electrowinning": {
        "unit_type": "electrowinning",
        "display_name": "Électrodéposition (EW)",
        "category": "refining",
        "default_params": {"current_efficiency_pct": 90, "current_A": 1000, "n_cells": 4, "cell_voltage_V": 3.5},
        "param_schema": [
            _ps("current_efficiency_pct", "Efficacité courant", default=90, unit="%", min_v=50, max_v=100),
            _ps("current_A", "Intensité par cellule", default=1000, unit="A", min_v=100, max_v=10000),
            _ps("n_cells", "Nombre de cellules", typ="integer", default=4, unit="-", min_v=1, max_v=50),
            _ps("cell_voltage_V", "Tension cellule", default=3.5, unit="V", min_v=2, max_v=6),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["sludge", "spent_solution"],
        "stream_type": "solution",
        "description": "Récupération Au par électrodéposition. Loi de Faraday: m = MIt×CE/(nF).",
    },
    "induction_furnace": {
        "unit_type": "induction_furnace",
        "display_name": "Four à induction (fusion doré)",
        "category": "refining",
        "default_params": {"dore_purity": 0.92, "energy_kwh_t": 50},
        "param_schema": [
            _ps("dore_purity", "Pureté doré (Au+Ag)", default=0.92, unit="-", min_v=0.5, max_v=0.99),
            _ps("energy_kwh_t", "Énergie de fusion", default=50, unit="kWh/t", min_v=10, max_v=200),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["dore_bar", "slag"],
        "stream_type": "solid",
        "description": "Fusion des boues EW. Production lingots dorés (~92 % Au+Ag).",
    },
    "refinery": {
        "unit_type": "refinery",
        "display_name": "Raffinerie (Miller/Wohlwill)",
        "category": "refining",
        "default_params": {"energy_kwh_t": 80, "ag_au_ratio": 0.08},
        "param_schema": [
            _ps("energy_kwh_t", "Énergie spécifique", default=80, unit="kWh/t", min_v=20, max_v=300),
            _ps("ag_au_ratio", "Ratio Ag/Au sous-produit", default=0.08, unit="kg/kg", min_v=0, max_v=1),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["refined_au", "ag_byproduct"],
        "stream_type": "solid",
        "description": "Raffinage final. Procédé Miller (chlore) puis Wohlwill (électrolyse). Pureté 99.99 %.",
    },
    # ── SOLID-LIQUID SEPARATION ────────────────────────────────────────────────
    "thickener": {
        "unit_type": "thickener",
        "display_name": "Épaississeur (thickener)",
        "category": "separation",
        "default_params": {"underflow_solids_pct": 55, "overflow_water_pct": 85, "energy_kwh_t": 0.5},
        "param_schema": [
            _ps("underflow_solids_pct", "Densité boues (%solides)", default=55, unit="%", min_v=20, max_v=75),
            _ps("overflow_water_pct", "Récupération eau overflow", default=85, unit="%", min_v=50, max_v=99),
            _ps("energy_kwh_t", "Énergie (lent agitateur)", default=0.5, unit="kWh/t", min_v=0.1, max_v=3),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["underflow", "overflow"],
        "stream_type": "pulp",
        "description": "Séparation gravimétrique. Modèle Coe & Clevenger pour débit unitaire.",
    },
    "pressure_filter": {
        "unit_type": "pressure_filter",
        "display_name": "Filtre presse",
        "category": "separation",
        "default_params": {"cake_moisture_pct": 12, "energy_kwh_t": 8},
        "param_schema": [
            _ps("cake_moisture_pct", "Humidité gâteau", default=12, unit="%", min_v=5, max_v=30),
            _ps("energy_kwh_t", "Énergie filtration", default=8, unit="kWh/t", min_v=2, max_v=30),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["filter_cake", "filtrate"],
        "stream_type": "pulp",
        "description": "Filtration sous pression. Humidité gâteau ~12 %.",
    },
    "disc_filter": {
        "unit_type": "disc_filter",
        "display_name": "Filtre à disques",
        "category": "separation",
        "default_params": {"cake_moisture_pct": 18, "energy_kwh_t": 6},
        "param_schema": [
            _ps("cake_moisture_pct", "Humidité gâteau", default=18, unit="%", min_v=10, max_v=35),
            _ps("energy_kwh_t", "Énergie filtration", default=6, unit="kWh/t", min_v=1, max_v=20),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["filter_cake", "filtrate"],
        "stream_type": "pulp",
        "description": "Filtre à disques. Humidité gâteau ~18 %.",
    },
    "belt_filter": {
        "unit_type": "belt_filter",
        "display_name": "Filtre à bande",
        "category": "separation",
        "default_params": {"cake_moisture_pct": 20, "energy_kwh_t": 4},
        "param_schema": [
            _ps("cake_moisture_pct", "Humidité gâteau", default=20, unit="%", min_v=12, max_v=40),
            _ps("energy_kwh_t", "Énergie filtration", default=4, unit="kWh/t", min_v=1, max_v=15),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["filter_cake", "filtrate"],
        "stream_type": "pulp",
        "description": "Filtre à bande continue. Humidité gâteau ~20 %.",
    },
    "ccd_circuit": {
        "unit_type": "ccd_circuit",
        "display_name": "Circuit CCD (lavage à contre-courant)",
        "category": "separation",
        "default_params": {"n_stages": 5, "wash_ratio": 1.5, "energy_kwh_t": 2.0},
        "param_schema": [
            _ps("n_stages", "Nombre d'étages", typ="integer", default=5, unit="-", min_v=2, max_v=12),
            _ps("wash_ratio", "Ratio de lavage R", default=1.5, unit="-", min_v=1.0, max_v=5.0),
            _ps("energy_kwh_t", "Énergie (pompes+agitateurs)", default=2.0, unit="kWh/t", min_v=0.5, max_v=10),
            _ps("dilution_factor", "Facteur de dilution PLS", default=0.7, unit="-", min_v=0.1, max_v=1.0),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["pls", "washed_cake"],
        "stream_type": "pulp",
        "description": "Lavage à contre-courant. E = R^n / (R^n - 1).",
    },
    # ── EFFLUENTS ─────────────────────────────────────────────────────────────
    "cn_destruction_so2": {
        "unit_type": "cn_destruction_so2",
        "display_name": "Destruction CN — procédé INCO (SO₂/Air)",
        "category": "effluents",
        "default_params": {"efficiency": 0.98, "energy_kwh_t": 1.5},
        "param_schema": [
            _ps("efficiency", "Efficacité destruction CN", default=0.98, unit="-", min_v=0.5, max_v=0.999),
            _ps("energy_kwh_t", "Énergie aération", default=1.5, unit="kWh/t", min_v=0.5, max_v=10),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["treated_effluent"],
        "stream_type": "solution",
        "description": "Procédé INCO: CN + SO₂ + O₂ + H₂O → SCN + H₂SO₄. Ratio SO₂ = 2.5 kg/kg CN.",
    },
    "cn_destruction_h2o2": {
        "unit_type": "cn_destruction_h2o2",
        "display_name": "Destruction CN — H₂O₂",
        "category": "effluents",
        "default_params": {"efficiency": 0.95, "energy_kwh_t": 0.5},
        "param_schema": [
            _ps("efficiency", "Efficacité destruction CN", default=0.95, unit="-", min_v=0.5, max_v=0.999),
            _ps("energy_kwh_t", "Énergie", default=0.5, unit="kWh/t", min_v=0.1, max_v=5),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["treated_effluent"],
        "stream_type": "solution",
        "description": "Oxydation CN par H₂O₂. Consommation 3.5 kg H₂O₂ / kg CN.",
    },
    "sart_process": {
        "unit_type": "sart_process",
        "display_name": "Procédé SART",
        "category": "effluents",
        "default_params": {"cu_recovery": 0.90, "cn_recovery": 0.80, "energy_kwh_t": 2.0},
        "param_schema": [
            _ps("cu_recovery", "Récupération Cu", default=0.90, unit="-", min_v=0.5, max_v=0.99),
            _ps("cn_recovery", "Récupération CN", default=0.80, unit="-", min_v=0.3, max_v=0.95),
            _ps("cu_ppm_inlet", "Cu entrant", default=200, unit="ppm", min_v=1, max_v=2000),
            _ps("energy_kwh_t", "Énergie SART", default=2.0, unit="kWh/t", min_v=0.5, max_v=10),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["cus_precipitate", "cn_solution"],
        "stream_type": "solution",
        "description": "Sulfidisation-Acidification-Récupération-Épaississement. Précipite CuS + récupère CN.",
    },
    "tailings_storage": {
        "unit_type": "tailings_storage",
        "display_name": "Parc à résidus (TSF)",
        "category": "effluents",
        "default_params": {"density_t_m3": 1.4},
        "param_schema": [
            _ps("density_t_m3", "Densité boues résidus", default=1.4, unit="t/m³", min_v=1.0, max_v=2.0),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": [],  # sink
        "stream_type": "pulp",
        "description": "Parc à résidus (sink). Calcul du volume occupé.",
    },
    "water_recovery": {
        "unit_type": "water_recovery",
        "display_name": "Récupération d'eau (barrage recyclage)",
        "category": "effluents",
        "default_params": {"recovery_pct": 75, "energy_kwh_t": 3.0},
        "param_schema": [
            _ps("recovery_pct", "Taux de récupération eau", default=75, unit="%", min_v=20, max_v=99),
            _ps("energy_kwh_t", "Énergie pompage/traitement", default=3.0, unit="kWh/t", min_v=0.5, max_v=15),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["recovered_water", "effluent"],
        "stream_type": "solution",
        "description": "Récupération et recyclage eau process. Traitement avant rejet.",
    },
    # ── UTILITIES ─────────────────────────────────────────────────────────────
    "feed_source": {
        "unit_type": "feed_source",
        "display_name": "Source d'alimentation",
        "category": "utilities",
        "default_params": {
            "feed_tph": 1000,
            "gold_grade_g_t": 1.5,
            "p80_um": 100000,
            "silver_grade_g_t": 0.0,
            "sulphide_pct": 0.0,
        },
        "param_schema": [
            _ps("feed_tph", "Débit d'alimentation", default=1000, unit="t/h", min_v=1, max_v=50000),
            _ps("gold_grade_g_t", "Teneur en or", default=1.5, unit="g/t", min_v=0.01, max_v=100),
            _ps("p80_um", "P80 alimentation", default=100000, unit="µm", min_v=1000, max_v=500000),
            _ps("silver_grade_g_t", "Teneur en argent", default=0.0, unit="g/t", min_v=0, max_v=500),
            _ps("sulphide_pct", "Teneur en sulfures", default=0.0, unit="%", min_v=0, max_v=30),
        ],
        "inlet_ports": [],
        "outlet_ports": ["product"],
        "stream_type": "solid",
        "description": "Nœud source: génère le flux d'alimentation depuis les paramètres.",
    },
    "product_sink": {
        "unit_type": "product_sink",
        "display_name": "Puits de produit",
        "category": "utilities",
        "default_params": {"gold_price_usd": 2000},
        "param_schema": [
            _ps("gold_price_usd", "Prix de l'or", default=2000, unit="USD/oz", min_v=500, max_v=5000),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": [],  # sink
        "stream_type": "solid",
        "description": "Nœud collecteur: agrège tous les flux entrants et calcule les KPIs produit.",
    },
    "stream_splitter": {
        "unit_type": "stream_splitter",
        "display_name": "Diviseur de flux",
        "category": "utilities",
        "default_params": {"split_ratio": 0.5},
        "param_schema": [
            _ps("split_ratio", "Ratio de division (flux A)", default=0.5, unit="-", min_v=0.01, max_v=0.99),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["stream_a", "stream_b"],
        "stream_type": "pulp",
        "description": "Division proportionnelle d'un flux en deux. Conservation de masse.",
    },
    "stream_mixer": {
        "unit_type": "stream_mixer",
        "display_name": "Mélangeur de flux",
        "category": "utilities",
        "default_params": {},
        "param_schema": [],
        "inlet_ports": ["feed_1", "feed_2", "feed_3"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Bilan de masse de plusieurs flux entrants → un seul flux mixte.",
    },
    "pump": {
        "unit_type": "pump",
        "display_name": "Pompe centrifuge",
        "category": "utilities",
        "default_params": {"head_m": 30, "efficiency": 0.75, "slurry_sg": 1.4},
        "param_schema": [
            _ps("head_m", "Hauteur manométrique totale", default=30, unit="m", min_v=5, max_v=500),
            _ps("efficiency", "Rendement pompe", default=0.75, unit="-", min_v=0.3, max_v=0.95),
            _ps("slurry_sg", "Densité pulpe", default=1.4, unit="t/m³", min_v=1.0, max_v=2.0),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Pompe centrifuge. P = ρgQH/η. Ajoute l'énergie hydraulique au flux.",
    },
    "reagent_addition": {
        "unit_type": "reagent_addition",
        "display_name": "Dosage de réactif",
        "category": "utilities",
        "default_params": {"reagent_type": "NaCN", "dose_kg_t": 1.0},
        "param_schema": [
            _ps(
                "reagent_type",
                "Type de réactif",
                typ="select",
                default="NaCN",
                options=["NaCN", "CaO", "Ca(OH)2", "H2SO4", "NaOH"],
            ),
            _ps("dose_kg_t", "Dose", default=1.0, unit="kg/t", min_v=0.01, max_v=50),
            _ps("ph_adjustment", "Ajustement pH", default=0.5, unit="-", min_v=-5, max_v=5),
        ],
        "inlet_ports": ["feed"],
        "outlet_ports": ["product"],
        "stream_type": "pulp",
        "description": "Addition de réactifs (cyanure, chaux, acide). Met à jour chimie du flux.",
    },
}
