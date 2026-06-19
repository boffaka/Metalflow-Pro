"""
Shared gravity-recovery metallurgical model.

Used by ore_to_bullion and mass_balance_engine so GRG, slip-stream, Knelson,
and ILR parameters share one definition.

Parameter semantics
-------------------
grg_pct
    % of feed gold that is gravity-recoverable (lab GRG / Knelson characterization).
gravity_slip_pct
    % of grinding product diverted to the gravity circuit (cyclone U/F slip).
knelson_unit_recovery_pct  (legacy alias: gravity_grg_recovery_pct)
    % of GRG gold recovered in the concentrator on the slip stream.
ilr_recovery_pct
    % of gold in the gravity concentrate recovered in the ILR / Acacia.
gravity_mass_pull_pct
    Mass pull to concentrate, % of gravity feed (stream sizing only).

Plant recovery (% of total feed Au):
    R = (grg/100) × (knelson/100) × (slip/100) × (ilr/100) × 100
"""
from __future__ import annotations

from dataclasses import dataclass


def _pct_to_fraction(value: float) -> float:
    """Accept 0–100 (percent) or 0–1 (fraction)."""
    if value <= 0:
        return 0.0
    return value / 100.0 if value > 1.0 else value


@dataclass(frozen=True)
class GravityParams:
    grg_pct: float
    gravity_slip_pct: float
    knelson_unit_recovery_pct: float
    ilr_recovery_pct: float
    gravity_mass_pull_pct: float

    @property
    def slip_frac(self) -> float:
        return _pct_to_fraction(self.gravity_slip_pct)

    @property
    def grg_frac(self) -> float:
        return _pct_to_fraction(self.grg_pct)

    @property
    def knelson_frac(self) -> float:
        return _pct_to_fraction(self.knelson_unit_recovery_pct)

    @property
    def ilr_frac(self) -> float:
        return _pct_to_fraction(self.ilr_recovery_pct)

    @property
    def mass_pull_frac(self) -> float:
        return _pct_to_fraction(self.gravity_mass_pull_pct)


def _coalesce(params: dict, *keys: str, default: float | None = None):
    for key in keys:
        if key in params and params[key] is not None:
            return params[key]
    return default


def resolve_gravity_params(params: dict) -> GravityParams:
    """Normalize gravity inputs from DC, simulation, or ore-to-bullion configs.

    Simulation param keys (category ``concentration``):
      - ``gravity_grg`` → ore GRG %
      - ``gravity_slip`` → cyclone slip-stream %
      - ``gravity_rec`` → Knelson unit recovery on GRG %
      - ``gravity_ilr`` → ILR recovery on concentrate %
      - ``gravity_mass_pull`` → mass pull on gravity feed %
    """
    grg_pct = _coalesce(
        params, "grg_pct", "gravity_grg", "avg_grg_pct", default=35.0
    )

    knelson = _coalesce(
        params,
        "knelson_unit_recovery_pct",
        "gravity_knelson_recovery_pct",
        "gravity_rec",
        "gravity_grg_recovery_pct",
        default=50.0,
    )

    slip = _coalesce(params, "gravity_slip_pct", "gravity_slip", default=30.0)
    ilr = _coalesce(
        params, "ilr_recovery_pct", "gravity_ilr_recovery_pct", "gravity_ilr", default=95.0
    )
    mass_pull = _coalesce(
        params, "gravity_mass_pull_pct", "gravity_mass_pull", default=0.2
    )

    return GravityParams(
        grg_pct=float(grg_pct),
        gravity_slip_pct=float(slip),
        knelson_unit_recovery_pct=float(knelson),
        ilr_recovery_pct=float(ilr),
        gravity_mass_pull_pct=float(mass_pull),
    )


def simulation_params_index(rows: list[dict]) -> dict[str, float]:
    """Build ``param_key → value`` from ``simulation_params`` rows."""
    idx: dict[str, float] = {}
    for row in rows:
        key = row.get("param_key")
        val = row.get("param_value")
        if not key or val is None:
            continue
        try:
            idx[str(key)] = float(val)
        except (TypeError, ValueError):
            continue
    return idx


def gravity_dc_from_simulation(sim: dict[str, float]) -> dict[str, float]:
    """Flat DC-style dict for mass_balance_engine / process_simulator."""
    gp = resolve_gravity_params(sim)
    return {
        "grg_pct": gp.grg_pct,
        "gravity_slip_pct": gp.gravity_slip_pct,
        "gravity_knelson_recovery_pct": gp.knelson_unit_recovery_pct,
        "gravity_ilr_recovery_pct": gp.ilr_recovery_pct,
        "gravity_mass_pull_pct": gp.gravity_mass_pull_pct,
    }


def plant_gravity_recovery_pct(gp: GravityParams) -> float:
    """Overall plant gold recovery to gravity concentrate (% of feed Au)."""
    return (
        100.0
        * gp.grg_frac
        * gp.knelson_frac
        * gp.slip_frac
        * gp.ilr_frac
    )


def blended_head_grade_g_t(feed_au_g_t: float, recovery_pct: float) -> float:
    """Head grade after gravity (bypass + slip tails blended)."""
    return feed_au_g_t * (1.0 - recovery_pct / 100.0)


def gravity_concentrate_grade_g_t(feed_au_g_t: float, gp: GravityParams) -> float:
    """Concentrate Au grade (g/t) from mass pull on the gravity slip stream."""
    # gravity_mass_pull_pct is always stored as a true percentage (e.g., 0.2 means 0.2%).
    # _pct_to_fraction fails for values < 1.0 (0.2 → 0.2 instead of 0.002),
    # so we divide explicitly by 100 here.
    mp = gp.gravity_mass_pull_pct / 100.0
    if mp <= 0:
        return feed_au_g_t * 10.0
    rec = plant_gravity_recovery_pct(gp) / 100.0
    # Gold to conc = feed_au × tph × rec; conc mass = tph × slip × mp → grade = au×rec/(slip×mp)
    return feed_au_g_t * rec / (gp.slip_frac * mp)
