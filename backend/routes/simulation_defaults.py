"""
MPDPMS — Simulation defaults aggregator.

Single source of truth for every numeric default the Simulation & Optimisation
module needs. The frontend has NO hardcoded numbers anymore — it always reads
from this endpoint.

For each parameter, we resolve in priority order:
  1. project record  (target_tph, gold_grade_g_t, ore_sg, gold_price_usd_oz, …)
  2. LIMS aggregates (flotation, kinetics, comminution …)
  3. costs module    (OPEX per tonne)
  4. industry-default fallback (single Python dict — no values scattered in JS)

Every key returns a non-null `value` so the frontend always has something to
draw, plus a `source` for the provenance badge.

Sources: 'project' | 'lims' | 'costs' | 'default' (industry constant).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends

try:
    from ..auth import project_user
    from ..db import qone
    from .. import config as cfg
except ImportError:  # pragma: no cover
    from auth import project_user
    from db import qone
    import config as cfg


router = APIRouter(prefix="/api/v1/projects", tags=["simulation"])
logger = logging.getLogger("mpdpms.simulation_defaults")


# ─── Industry defaults — the single source for "starting values" ──────────────
# These are the ONLY hardcoded numbers in the simulation module. They are PFS
# typical values for a CIL/Flotation gold plant. The frontend reads these via
# the API; nothing is hardcoded in JS anymore.
_INDUSTRY_DEFAULTS: dict[str, float] = {
    # Feed
    "feed_tph":         913.0,
    "head_grade_au":    1.0,
    "head_grade_ag":    0.71,
    "feed_density":     2.68,
    "feed_bwi":         13.8,
    # Crushing
    "crusher_css":      150.0,
    "crusher_f80":      118.0,
    "crusher_power":    500.0,
    "crush_avail":      70.0,
    # Grinding (SABC)
    "sag_power":        10.5,
    "ball_power":       5.25,
    "circ_load":        350.0,
    "cycl_of_density":  35.0,
    "grind_p80":        180.0,
    "sag_ball_charge":  12.0,
    "ball_ball_charge": 29.0,
    # Gravity
    "grav_units":       4.0,
    "grav_mass_pull":   1.2,
    "grav_rec_au":      18.0,
    "grav_rec_ag":      6.0,
    # Flotation
    "flot_rec_au":      88.0,
    "flot_mass_pull":   3.0,
    "flot_grade_au":    10.0,
    "flot_rough_time":  18.0,
    "flot_clean_time":  13.0,
    "pax_dosage":       135.0,
    "mibc_dosage":      71.0,
    "cmc_dosage":       65.0,
    # Regrind
    "regrind_p80":      22.0,
    "regrind_power":    500.0,
    # CIL / Leach
    "cil_tanks":        8.0,
    "cil_time":         48.0,
    "cil_rec_au":       97.3,
    "cil_solids":       50.0,
    "cil_o2":           20.0,
    "nacn_consumption": 117.0,
    "lime_consumption": 112.0,
    # Elution / Doré
    "elut_temp":        130.0,
    "elut_strips":      7.0,
    "elut_batch":       5.0,
    "elut_duration":    16.0,
    "kiln_temp":        750.0,
    # Detox
    "detox_time":       120.0,
    "detox_wad_target": 2.0,
    "detox_so2_rate":   6.0,
    "smbs_dosage":      162.0,
    # Financiers
    "gold_price":       2400.0,
    "silver_price":     24.0,
    "capex_initial":    607.2,
    "opex_per_tonne":   19.38,
    "discount_rate":    8.0,
    "mine_life":        12.0,
    "fx_rate":          0.76,
    "nsr_royalty":      1.5,
    # Project operating defaults (read by _simV3GlobalKpis / NPV calc)
    "mill_avail":           91.3,   # %
    "operating_hours_day":  24.0,
    "availability_pct":     92.0,
    "electricity_rate":     0.075,  # $/kWh
    "sustaining_capex_pct": 6.0,    # % of initial CAPEX over LOM (replaces 290.5e6 magic)
    # KPI thresholds (used by _kpi() and the recovery progress bar colors)
    "kpi_recovery_good":    88.0,
    "kpi_recovery_ok":      80.0,
    "kpi_energy_max":       20.0,
    "kpi_aisc_max":         1200.0,
    "kpi_irr_min":          15.0,
}

_LABELS: dict[str, str] = {
    "feed_tph":         "Débit alimentation",
    "head_grade_au":    "Grade Au tête",
    "head_grade_ag":    "Grade Ag tête",
    "feed_density":     "Densité minerai",
    "feed_bwi":         "Bond BWi",
    "crusher_css":      "CSS concasseur",
    "crusher_f80":      "F80 concasseur",
    "crusher_power":    "Puissance concasseur",
    "crush_avail":      "Disponibilité concassage",
    "sag_power":        "Puissance SAG",
    "ball_power":       "Puissance Ball Mill",
    "circ_load":        "Charge circulante",
    "cycl_of_density":  "Densité cyclone overflow (% solids)",
    "grind_p80":        "P80 broyage (µm)",
    "sag_ball_charge":  "Charge boulets SAG",
    "ball_ball_charge": "Charge boulets Ball",
    "grav_units":       "Nb concentrateurs gravimétriques",
    "grav_mass_pull":   "Mass pull gravimétrie",
    "grav_rec_au":      "Récupération Au gravimétrie",
    "grav_rec_ag":      "Récupération Ag gravimétrie",
    "flot_rec_au":      "Récupération Au flottation",
    "flot_mass_pull":   "Mass pull flottation",
    "flot_grade_au":    "Grade concentré Au",
    "flot_rough_time":  "Temps rougher",
    "flot_clean_time":  "Temps cleaner",
    "pax_dosage":       "Dosage PAX",
    "mibc_dosage":      "Dosage MIBC",
    "cmc_dosage":       "Dosage CMC",
    "regrind_p80":      "P80 rebroyage (µm)",
    "regrind_power":    "Puissance HIGmill",
    "cil_tanks":        "Nombre de tanks CIL",
    "cil_time":         "Temps de résidence CIL",
    "cil_rec_au":       "Récupération Au lixiviation",
    "cil_solids":       "Densité pulpe CIL",
    "cil_o2":           "O2 dissous cible",
    "nacn_consumption": "Consommation NaCN",
    "lime_consumption": "Consommation chaux",
    "elut_temp":        "Température Zadra",
    "elut_strips":      "Strips par semaine",
    "elut_batch":       "Batch carbone",
    "elut_duration":    "Durée élution",
    "kiln_temp":        "Température four réactivation",
    "detox_time":       "Temps résidence détox",
    "detox_wad_target": "WAD CN cible",
    "detox_so2_rate":   "Taux SO2",
    "smbs_dosage":      "Dosage SMBS",
    "gold_price":       "Prix Au",
    "silver_price":     "Prix Ag",
    "capex_initial":    "CAPEX initial",
    "opex_per_tonne":   "OPEX total",
    "discount_rate":    "Taux actualisation",
    "mine_life":        "Durée de vie mine",
    "fx_rate":          "Taux change US$/C$",
    "nsr_royalty":      "Redevance NSR",
    "mill_avail":           "Disponibilité usine",
    "operating_hours_day":  "Heures d'opération / jour",
    "availability_pct":     "Disponibilité projet",
    "electricity_rate":     "Coût de l'électricité",
    "sustaining_capex_pct": "Sustaining CAPEX (% du CAPEX initial)",
    "kpi_recovery_good":    "Seuil Recovery « bon »",
    "kpi_recovery_ok":      "Seuil Recovery acceptable",
    "kpi_energy_max":       "Énergie max (kWh/t)",
    "kpi_aisc_max":         "AISC max ($/oz)",
    "kpi_irr_min":          "TRI min",
}


def _f(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entry(key: str, value: Optional[float], source: str) -> dict:
    """Build a response entry. Falls back to industry default if value is None."""
    if value is None or source == "default":
        return {
            "value": _INDUSTRY_DEFAULTS.get(key),
            "source": "default",
            "label": _LABELS.get(key, key),
        }
    return {
        "value": value,
        "source": source,
        "label": _LABELS.get(key, key),
    }


# ORM/API factory values — treated as "unset" so LIMS / industry defaults apply.
_FACTORY_PROJECT_SENTINELS: dict[str, float] = {
    "gold_price_usd_oz": cfg.DEFAULT_GOLD_PRICE_USD_OZ,
    "mine_life_years": 10.0,
    "discount_rate_pct": 5.0,
    "availability_pct": 92.0,
    "operating_hours_day": 24.0,
}


def _project_scalar(proj: dict, field: str) -> Optional[float]:
    """Project column value, or None if missing / factory sentinel."""
    raw = _f(proj.get(field))
    if raw is None:
        return None
    sentinel = _FACTORY_PROJECT_SENTINELS.get(field)
    if sentinel is not None and abs(raw - sentinel) < 1e-6:
        return None
    return raw


def build_project_simulation_defaults(pid: str) -> dict[str, dict]:
    """Resolved defaults for a project (shared by API route, dashboard, helpers)."""
    out: dict[str, dict] = {}
    proj = qone("SELECT * FROM projects WHERE id=%s", (pid,)) or {}

    def _put_project(key: str, field: str) -> None:
        val = _project_scalar(proj, field)
        out[key] = _entry(key, val, "project" if val is not None else "default")

    _put_project("feed_tph", "target_tph")
    _put_project("head_grade_au", "gold_grade_g_t")
    _put_project("feed_density", "ore_sg")
    _put_project("gold_price", "gold_price_usd_oz")
    _put_project("capex_initial", "capex_musd")
    _put_project("discount_rate", "discount_rate_pct")
    _put_project("mine_life", "mine_life_years")
    _put_project("operating_hours_day", "operating_hours_day")
    _put_project("availability_pct", "availability_pct")
    avail = _project_scalar(proj, "availability_pct")
    out["mill_avail"] = _entry("mill_avail", avail, "project" if avail is not None else "default")
    _put_project("electricity_rate", "electricity_rate")

    flot = qone(
        "SELECT AVG(au_recovery_pct) AS rec, AVG(mass_pull_pct) AS mp, "
        "       AVG(concentrate_grade_g_t) AS gr "
        "FROM lims_flotation WHERE project_id=%s",
        (pid,),
    ) or {}
    out["flot_rec_au"] = _entry("flot_rec_au", _f(flot.get("rec")), "lims")
    out["flot_mass_pull"] = _entry("flot_mass_pull", _f(flot.get("mp")), "lims")
    out["flot_grade_au"] = _entry("flot_grade_au", _f(flot.get("gr")), "lims")

    kin = qone(
        "SELECT AVG(rec_24h) AS rec FROM lims_kinetics WHERE project_id=%s",
        (pid,),
    ) or {}
    out["cil_rec_au"] = _entry("cil_rec_au", _f(kin.get("rec")), "lims")

    grav = qone(
        "SELECT AVG(au_recovery_pct) AS rec, AVG(grg_rec_pct) AS grg "
        "FROM lims_c2 WHERE project_id=%s",
        (pid,),
    ) or {}
    grav_rec = _f(grav.get("grg")) or _f(grav.get("rec"))
    out["grav_rec_au"] = _entry("grav_rec_au", grav_rec, "lims")

    opex_total = qone(
        "SELECT SUM(total_cost_usd) AS total "
        "FROM cost_line_items cli "
        "JOIN cost_models cm ON cm.id = cli.model_id "
        "WHERE cm.project_id=%s AND cm.model_type='OPEX'",
        (pid,),
    ) or {}
    feed_tph = _f(proj.get("target_tph")) or out["feed_tph"]["value"]
    avail_v = _f(proj.get("availability_pct")) or out["availability_pct"]["value"] or 92.0
    hours = _f(proj.get("operating_hours_day")) or out["operating_hours_day"]["value"] or 24.0
    opex_pt: Optional[float] = None
    if opex_total.get("total") and feed_tph:
        annual_tonnes = feed_tph * hours * 365 * (avail_v / 100.0)
        if annual_tonnes > 0:
            opex_pt = round(_f(opex_total["total"]) / annual_tonnes, 2)
    out["opex_per_tonne"] = _entry("opex_per_tonne", opex_pt, "costs")

    for key in _INDUSTRY_DEFAULTS:
        if key not in out:
            out[key] = _entry(key, None, "default")

    return out


def flat_simulation_defaults(pid: str) -> dict[str, float]:
    """Flat key → numeric value for helpers / dashboard."""
    return {k: float(v["value"]) for k, v in build_project_simulation_defaults(pid).items() if v.get("value") is not None}


@router.get("/{pid}/simulation/defaults")
def get_simulation_defaults(pid: str, user=Depends(project_user)) -> dict:
    """Return all ~50 simulation defaults, resolved against project/LIMS/costs."""
    out = build_project_simulation_defaults(pid)
    stats: dict[str, int] = {}
    for v in out.values():
        stats[v["source"]] = stats.get(v["source"], 0) + 1
    return {"values": out, "stats": {"by_source": stats, "total": len(out)}}
