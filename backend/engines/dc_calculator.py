"""
Design Criteria Calculator — Automatic propagation of calculated values.

When a user changes a primary input (throughput, grade, recovery, BWi, etc.),
this engine recalculates ALL dependent criteria across the entire document.

Calculation chain:
  Primary Inputs → Crushing rates → Comminution rates → Flotation rates →
  Concentrate rates → Regrind rates → Thickener sizing → Leach/CIP sizing →
  Reagent consumption → Detox sizing → Tailings → Water balance → Gold production
"""
from __future__ import annotations
import math
import logging

try:
    from .dc_formulas import (
        bond_energy_kwh_t,
        circular_diameter_m,
        cylindrical_volume_diameter_m,
        corrected_bond_energy_kwh_t,
        hpgr_total_roll_tph,
        installed_power_kw,
        mill_design_tph as formula_mill_design_tph,
        residence_volume_m3,
        rowland_ef4,
        rowland_ef5,
        shaft_power_kw,
        slurry_density_t_m3,
        slurry_volume_m3h,
    )
except ImportError:  # pragma: no cover - supports direct script imports
    from dc_formulas import (  # type: ignore[no-redef]
        bond_energy_kwh_t,
        circular_diameter_m,
        cylindrical_volume_diameter_m,
        corrected_bond_energy_kwh_t,
        hpgr_total_roll_tph,
        installed_power_kw,
        mill_design_tph as formula_mill_design_tph,
        residence_volume_m3,
        rowland_ef4,
        rowland_ef5,
        shaft_power_kw,
        slurry_density_t_m3,
        slurry_volume_m3h,
    )

try:
    from ..constants import TROY_OZ_PER_GRAM
    from .. import config as cfg
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM
    import config as cfg

logger = logging.getLogger("mpdpms.dc_calculator")


# Map calc_key (OP_CODE:item_pattern) → canonical DAG key (Chunk 1.5.B).
# When the calculator overwrites a design_value on a row that has no
# `dag_key` yet, opportunistically back-fill it. The mapping is intentionally
# narrow — only entries that correspond to an actual node or input in
# `dc_dag_registry.yaml`. Non-matching calcs stay unmapped (they're just
# legacy display values, not used by the cascade).
_CALC_TO_DAG_KEY: dict[str, str] = {
    # Crusher — design rate is NOT target_tph; it includes availability correction + design margin.
    # dag_key="crusher_design_tph" prevents the DAG cascade from overwriting with raw target_tph.
    "GIRATOIRE:débit design alimentation":  "crusher_design_tph",
    "GIRATOIRE:debit design alimentation":  "crusher_design_tph",
    "GIRATOIRE:processing rate":            "crusher_design_tph",
    "CONE:processing rate":                 "cone_design_tph",
    "CONE:débit alim":                      "cone_design_tph",
    "CONE:debit alim":                      "cone_design_tph",
    "CRIBLE:processing rate":               "screen_design_tph",
    "CRIBLE:débit alimentation":            "screen_design_tph",
    "CRIBLE:debit alimentation":            "screen_design_tph",
    # Ball mill
    "BALL_MILL:installed power": "bm_power_kw",
    "BALL_MILL:motor power":     "bm_power_kw",
    "BALL_MILL:power":           "bm_power_kw",
    "HYDROCYCLONE:feed":         "cyc_feed_tph",
    "FLOTATION_ROUGHER:concentrate production": "flot_conc_tph",
    "LEACH_CUVES:solid - feed":  "leach_feed_tph",
    "LEACH_CUVES:feed leach":    "leach_feed_tph",
    "LEACH_CUVES:processing circuit rate": "leach_feed_tph",
    "LEACH_CUVES:pulp - feed volumetric":  "vol_flow_m3h",
    "LEACH_CUVES:pulp specific gravity":   "slurry_sg",
    "LEACH_CUVES:total live volume":       "cil_volume_m3",
    "LEACH_CUVES:nacn consumption": "nacn_kg_h",
    "LEACH_CUVES:lime consumption": "cao_kg_h",
    "EPAISSISSEUR:thickening area":     "thickener_area_m2",
    "EPAISSISSEUR:thickener diameter":  "thickener_diameter_m",
    "VERTIMILL_REGRIND:regrind circuit feed": "regrind_feed_tph",
    "VERTIMILL_REGRIND:feed p80": "regrind_feed_p80_um",
    "VERTIMILL_REGRIND:product p80": "regrind_product_p80_um",
    "VERTIMILL_REGRIND:specific energy": "regrind_specific_energy_kwh_t",
    "VERTIMILL_REGRIND:shaft power": "regrind_shaft_power_kw",
    "VERTIMILL_REGRIND:installed power": "regrind_installed_power_kw",
}


def recalculate_all(project_id: str, template_id: str, cursor) -> dict:
    """
    Recalculate all derived design criteria values from primary inputs.

    Reads all enabled criteria, identifies inputs vs calculated values,
    then propagates calculations through the entire process chain.

    Returns: {updated: int, total: int, errors: [str]}
    """
    try:
        return _recalculate_all_impl(project_id, template_id, cursor)
    except Exception as e:
        logger.error("recalculate_all failed for project_id=%s, template_id=%s: %s", project_id, template_id, e)
        raise RuntimeError(f"recalculate_all failed for project {project_id}: {e}") from e


def _recalculate_all_impl(project_id: str, template_id: str, cursor) -> dict:
    """Internal implementation of recalculate_all."""
    def _row_val(row, idx: int, key: str):
        if isinstance(row, dict):
            return row.get(key)
        return row[idx]

    # 1. Read all criteria for this template (include dag_key for robust matching)
    cursor.execute(
        "SELECT id, op_code, ref_number, item, unit, design_value, nominal_value, "
        "min_value, max_value, source_code, dag_key "
        "FROM design_criteria_v2 "
        "WHERE template_id = %s AND enabled = TRUE "
        "ORDER BY sort_order, ref_number",
        (template_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return {"updated": 0, "total": 0, "errors": ["No criteria found"]}

    # Build lookup: item_lower -> row dict
    criteria = {}
    # Secondary lookup by dag_key for robust matching when item text has encoding drift
    by_dag_key: dict[str, dict] = {}
    for r in rows:
        op_code = _row_val(r, 1, "op_code")
        item = _row_val(r, 3, "item")
        dag_key = _row_val(r, 10, "dag_key") if len(r) > 10 else (r.get("dag_key") if isinstance(r, dict) else None)
        key = (op_code or "").upper() + ":" + (item or "").lower()  # OP_CODE:item
        row_dict = {
            "id": str(_row_val(r, 0, "id")),
            "op_code": op_code,
            "ref": _row_val(r, 2, "ref_number"),
            "item": item,
            "unit": _row_val(r, 4, "unit"),
            "design": float(_row_val(r, 5, "design_value")) if _row_val(r, 5, "design_value") is not None else None,
            "nominal": float(_row_val(r, 6, "nominal_value")) if _row_val(r, 6, "nominal_value") is not None else None,
            "min": float(_row_val(r, 7, "min_value")) if _row_val(r, 7, "min_value") is not None else None,
            "max": float(_row_val(r, 8, "max_value")) if _row_val(r, 8, "max_value") is not None else None,
            "source": _row_val(r, 9, "source_code"),
            "dag_key": dag_key,
        }
        criteria[key] = row_dict
        if dag_key:
            by_dag_key[dag_key] = row_dict

    # Also build by item name only (for cross-operation lookups)
    by_item = {}
    for k, v in criteria.items():
        item_lower = (v["item"] or "").lower()
        by_item[item_lower] = v
        # Also store with op_code prefix
        by_item[k] = v

    # 2. Read project parameters
    cursor.execute(
        "SELECT target_tph, gold_grade_g_t, availability_pct, operating_hours_day, "
        "mine_life_years, gold_price_usd_oz "
        "FROM projects WHERE id = %s", (project_id,)
    )
    proj = cursor.fetchone()
    if not proj:
        return {"updated": 0, "total": len(rows), "errors": ["Project not found"]}

    # 3. Extract primary inputs from criteria or project
    # English → French aliases. Each EN pattern returns the list of FR
    # equivalents to also try when fuzzy-matching item text. Order matters:
    # more specific aliases should come first to avoid spurious matches.
    _EN_FR_ALIASES = {
        "processing rate":  ["débit fresh feed", "debit fresh feed",
                             "débit design alimentation", "debit design alimentation",
                             "débit alimentation"],
        "fresh feed":       ["débit fresh feed", "debit fresh feed", "fresh feed"],
        "head grade":       ["teneur au alim", "teneur tête au", "teneur au",
                             "head grade", "grade au"],
        "availability":     ["disponibilité", "disponibilite",
                             "operating percentage", "% disponibilité"],
        "hours per day":    ["heures opération/jour", "heures par jour",
                             "operating hours per day"],
        "circulating load": ["charge circulante", "circ load", "circulating load"],
        "ball charge":      ["remplissage boulets", "charge boulets", "ball charge"],
        "feed density":     ["densité pulpe alimentation", "densité pulpe feed",
                             "% solides feed", "feed density"],
        "feed % solids":    ["% solides feed", "% solides leach", "% solides alimentation"],
        "% solids":         ["% solides", "feed % solids"],
        "residence time":   ["temps résidence", "temps de résidence",
                             "residence time"],
        "number of tanks":  ["nombre réservoirs", "nombre cuves", "n_tanks",
                             "number of tanks"],
        "recovery":         ["récupération attendue", "récupération au",
                             "récup au", "recovery"],
        "extraction":       ["récupération attendue", "extraction"],
        "mass pull":        ["mass pull", "ratio masse concentré"],
        "nacn":             ["nacn dosage", "[nacn]", "nacn"],
        "cyanide":          ["nacn dosage", "[nacn]", "cyanide", "cyanure"],
        "lime":             ["lime dosage", "dosage chaux", "lime", "chaux"],
        "cao":              ["lime dosage", "dosage chaux", "cao"],
        "o2":               ["o₂ dissous", "o2 dissous", "dissolved o2", "o2"],
        "pax":              ["pax addition", "pax", "collecteur pax"],
        "collector":        ["pax addition", "collecteur", "collector"],
        "mibc":             ["mibc addition", "mibc", "moussant mibc"],
        "frother":          ["mibc addition", "moussant", "frother"],
        "flocculant":       ["dosage floculant", "floculant", "flocculant"],
        "specific gravity": ["sg minerai", "specific gravity", "ore sg"],
        "density":          ["densité pulpe", "sg minerai", "density"],
        "average density":  ["densité pulpe", "average density"],
        "feed rate":        ["débit alimentation", "débit feed", "feed rate"],
        "humidity":         ["humidité", "humidity"],
        "product p80":      ["p80 produit", "product p80", "p80 cible"],
        "p80":              ["p80 produit", "p80 cible", "p80"],
        "discharge p80":    ["p80 produit", "discharge p80"],
        "feed p80":         ["f80 alimentation", "feed p80", "f80"],
        "installed power":  ["puissance installée", "installed power"],
        "motor power":      ["puissance moteur", "motor power", "puissance arbre"],
        "motor efficiency": ["rendement moteur", "motor efficiency", "η_motor"],
        "installation margin": ["marge installation", "installation margin"],
        "power":            ["puissance", "power"],
        "bond":             ["bond bwi", "bond cwi", "bond"],
        "bwi":              ["bond bwi", "bwi"],
        "cwi":              ["bond cwi", "cwi"],
        "scale up factor":  ["facteur d'échelle", "facteur échelle", "scale up factor"],
        "residence time, lab": ["temps résidence (labo)", "residence time, lab"],
        "aeration factor":  ["facteur foisonnement", "aeration factor"],
        "cell volume":      ["volume cellule", "cell volume"],
    }

    def _get(item_pattern, op=None, default=None):
        """Find a criterion value by fuzzy item name match.

        Tries the literal pattern first, then any French aliases registered
        in _EN_FR_ALIASES so the legacy English-named patterns continue to
        resolve against the French catalog rolled out in 2026-05.
        """
        patterns = [item_pattern.lower()]
        # Add aliases — first the EN→FR mapping, then any FR pattern that
        # contains the EN word (looser fallback).
        for en, fr_list in _EN_FR_ALIASES.items():
            if en == patterns[0] or en in patterns[0]:
                for alias in fr_list:
                    if alias not in patterns:
                        patterns.append(alias)

        for pat in patterns:
            # Try exact op:item match first
            if op:
                key = op.upper() + ":" + pat
                # exact key
                if key in by_item and by_item[key]["design"] is not None:
                    return by_item[key]["design"]
                # also try fuzzy within the op
                for k, v in by_item.items():
                    if k.startswith(op.upper() + ":") and pat in k.lower() and v["design"] is not None:
                        return v["design"]
            # Item-only fuzzy
        for k, v in by_item.items():
            if pat in k.lower() and v["design"] is not None:
                return v["design"]
        return default

    def _to_um(value, default_um=None, *, convert_small_mm: bool = False):
        """Normalize a P80/F80 value to microns.

        Catalog rows are mixed: some legacy rows are in mm, newer criteria are
        in µm/um. Values <= 50 are treated as mm because process P80 values in
        microns are not realistically that small at these stages.
        """
        if value is None:
            return default_um
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default_um
        if v <= 0:
            return default_um
        return v * 1000 if convert_small_mm and v <= 50 else v

    # Primary inputs
    plant_tph = float(_row_val(proj, 0, "target_tph") or 0) or _get("processing rate", "BALL_MILL", 1517)
    grade_au = float(_row_val(proj, 1, "gold_grade_g_t") or 0) or _get("head grade", default=1.5)
    plant_avail = float(_row_val(proj, 2, "availability_pct") or 92) / 100
    plant_hpd = float(_row_val(proj, 3, "operating_hours_day") or 22.1)
    _mine_life = int(_row_val(proj, 4, "mine_life_years") or 15)
    au_price = float(_row_val(proj, 5, "gold_price_usd_oz") or cfg.DEFAULT_GOLD_PRICE_USD_OZ)
    ore_sg = _get("density", "GIRATOIRE", 2.74) or _get("average density", default=2.74)
    _humidity = _get("humidity", default=3.0)

    crushing_avail = (
        _get("operating percentage - crushing circuit", default=None)
        or _get("availability", "GIRATOIRE", 75)
    ) / 100
    _crushing_hpd = _get("hours per day", "GIRATOIRE", 18)
    _hpgr_avail = _get("availability", "HPGR", 80) / 100
    _hpgr_hpd = _get("hours per day", "HPGR", 19)

    bwi = _get("bond", "BALL_MILL") or _get("bwi", default=18.0)
    _ball_charge = _get("ball charge", "BALL_MILL", 33)
    circ_load_bm = _get("circulating load", "BALL_MILL", 350)
    bm_motor_eff = _get("motor efficiency", "BALL_MILL", 95)
    bm_motor_eff = (bm_motor_eff / 100) if bm_motor_eff and bm_motor_eff > 1.5 else (bm_motor_eff or 0.95)
    bm_install_margin = _get("installation margin", "BALL_MILL", 10)
    bm_install_margin = (bm_install_margin / 100) if bm_install_margin and bm_install_margin > 1.5 else (bm_install_margin or 0.10)

    flot_mass_pull = _get("mass pull", "FLOTATION_ROUGHER", 6) / 100
    flot_recovery = _get("recovery", "FLOTATION_ROUGHER", 96) / 100
    flot_feed_density = _get("feed density", "FLOTATION_ROUGHER", 35) / 100

    leach_recovery = _get("recovery", "CIP") or _get("extraction", "CIP", 92)
    if leach_recovery and leach_recovery > 1: leach_recovery /= 100
    else: leach_recovery = 0.92

    cip_pct_solids = _get("feed % solids", "CIP") or _get("% solids", "CIP", 33)
    cip_pct_solids = cip_pct_solids / 100 if cip_pct_solids > 1 else cip_pct_solids
    cip_srt = _get("residence time", "CIP", 16)
    cip_n_tanks = _get("number of tanks", "CIP", 8)
    leach_srt = _get("residence time", "LEACH_CUVES", 45)
    leach_n_tanks = _get("number of tanks", "LEACH_CUVES", 5)

    nacn_kg_t = _get("nacn", "LEACH_CUVES") or _get("cyanide", "LEACH_CUVES", 1.0)
    cao_kg_t_leach = _get("lime", "LEACH_CUVES") or _get("cao", "LEACH_CUVES", 2.0)
    _o2_mg_l = _get("o2", "LEACH_CUVES", 20)

    pax_gt = _get("pax", "FLOTATION_ROUGHER") or _get("collector", "FLOTATION_ROUGHER", 69)
    mibc_gt = _get("mibc", "FLOTATION_ROUGHER") or _get("frother", "FLOTATION_ROUGHER", 50)
    floc_gt_thk = _get("flocculant", "EPAISSISSEUR", 35)

    # Concentrate SG
    conc_sg = _get("specific gravity", "FLOTATION_ROUGHER", 2.80)

    # 4. Calculate derived values
    calcs = {}  # item_pattern -> design_value
    nominal_calcs = {}  # item_pattern -> nominal_value

    # ── CRUSHING ──
    # Design logic (PDC workbook):
    #   nominal_crusher = plant_tph × (mill_avail / crusher_avail)   [crusher runs fewer hours]
    #   design_crusher  = nominal_crusher × (1 + design_factor)       [15% equipment margin]
    #
    # CRITICAL: crusher_nominal and crusher_design are computed directly from project-level
    # plant_tph and plant_avail (from projects.availability_pct) WITHOUT fuzzy _get() lookups.
    # _get("availability", "GIRATOIRE") and _get("operating percentage", "BALL_MILL") can
    # return unexpected values due to alias matching, making the ratio grinding_avail/
    # crushing_avail collapse to 1.0 → design = nominal (wrong).  Using project-level values
    # guarantees design > nominal at all times.
    concentrator_design_factor = (
        _get("concentrator plant equipment design factor", default=None)
        or _get("grinding plant equipment design factor", default=None)
        or _get("milling plant equipment design factor", default=None)
        or 15.0
    )
    # Ensure design_factor is a percentage (not a fraction like 0.15)
    if concentrator_design_factor and concentrator_design_factor < 1.0:
        concentrator_design_factor *= 100.0

    # Crusher feed rates — computed ONLY from project-level values.
    # Hardcoded crusher availability = 75 % (standard primary gyratory operating factor).
    # This MUST NOT come from _get(): alias matching in _EN_FR_ALIASES can accidentally
    # return mill_avail (92 %) for the crusher, collapsing the ratio to 1.0 so that
    # crushing_tph = mill_design_tph (wrong — design = nominal).
    _CRUSHER_AVAIL_PCT = 75.0   # primary gyratory standard — never read from _get()
    _mill_avail_pct    = max(plant_avail * 100.0, 1.0)  # projects.availability_pct

    mill_design_tph  = formula_mill_design_tph(plant_tph, concentrator_design_factor)
    mill_nominal_tph = plant_tph

    _avail_ratio         = _mill_avail_pct / _CRUSHER_AVAIL_PCT      # e.g. 92/75 = 1.227
    crushing_nominal_tph = round(plant_tph * _avail_ratio, 1)         # nominal when running
    crushing_tph         = round(                                       # design with margin
        crushing_nominal_tph * (1.0 + concentrator_design_factor / 100.0), 1
    )

    # Sanity guard: design MUST strictly exceed nominal
    if crushing_tph <= crushing_nominal_tph:
        crushing_tph = round(crushing_nominal_tph * 1.15, 1)

    # grinding_avail used by downstream calcs (leach, flotation, etc.)
    grinding_avail = plant_avail

    calcs["GIRATOIRE:processing rate"] = crushing_tph
    calcs["GIRATOIRE:débit design alimentation"] = crushing_tph
    calcs["GIRATOIRE:debit design alimentation"] = crushing_tph
    calcs["CRIBLE:processing rate"] = crushing_tph
    calcs["CRIBLE:débit alimentation"] = crushing_tph
    nominal_calcs["GIRATOIRE:processing rate"] = crushing_nominal_tph
    nominal_calcs["GIRATOIRE:débit design alimentation"] = crushing_nominal_tph
    nominal_calcs["GIRATOIRE:debit design alimentation"] = crushing_nominal_tph
    nominal_calcs["CRIBLE:processing rate"] = crushing_nominal_tph
    nominal_calcs["CRIBLE:débit alimentation"] = crushing_nominal_tph

    screen_passing_pct = _get("% passant", "CRIBLE", 65)
    screen_passing = screen_passing_pct / 100 if screen_passing_pct and screen_passing_pct > 1 else (screen_passing_pct or 0.65)
    screen_passing = min(max(screen_passing, 0.0), 1.0)
    screen_oversize_design_tph = crushing_tph * (1 - screen_passing)
    screen_oversize_nominal_tph = crushing_nominal_tph * (1 - screen_passing)
    calcs["CRIBLE:débit undersize"] = crushing_tph * screen_passing
    calcs["CRIBLE:débit oversize"] = screen_oversize_design_tph
    nominal_calcs["CRIBLE:débit undersize"] = crushing_nominal_tph * screen_passing
    nominal_calcs["CRIBLE:débit oversize"] = screen_oversize_nominal_tph
    calcs["CONE:processing rate"] = screen_oversize_design_tph
    calcs["CONE:débit alim"] = screen_oversize_design_tph
    nominal_calcs["CONE:processing rate"] = screen_oversize_nominal_tph
    nominal_calcs["CONE:débit alim"] = screen_oversize_nominal_tph

    pc_f80_um = _get("f80 alimentation", "GIRATOIRE", 528000)
    pc_p80_um = _get("p80 produit", "GIRATOIRE", 135000)
    pc_f100_um = _get("f100 alimentation", "GIRATOIRE", 1_000_000)
    cwi = _get("crushing work index", "GIRATOIRE") or _get("cwi", "GIRATOIRE", 14)
    pc_eff = _get("rendement mécanique", "GIRATOIRE") or _get("mechanical efficiency", "GIRATOIRE", 93)
    pc_eff = pc_eff / 100 if pc_eff and pc_eff > 1.5 else (pc_eff or 0.93)
    pc_margin = _get("marge installation", "GIRATOIRE") or _get("installation margin", "GIRATOIRE", 30)
    pc_margin = pc_margin / 100 if pc_margin and pc_margin > 1.5 else (pc_margin or 0.30)
    pc_p80_um = max(float(pc_p80_um or 1), 1.0)
    pc_f80_um = max(float(pc_f80_um or pc_p80_um + 1), pc_p80_um + 1.0)
    pc_energy = bond_energy_kwh_t(cwi, pc_f80_um, pc_p80_um)
    pc_shaft_power = shaft_power_kw(pc_energy, crushing_tph)
    pc_installed_power = installed_power_kw(pc_shaft_power, pc_eff, pc_margin)
    calcs["GIRATOIRE:ratio de réduction"] = pc_f80_um / pc_p80_um
    calcs["GIRATOIRE:ratio de reduction"] = pc_f80_um / pc_p80_um
    calcs["GIRATOIRE:énergie bond"] = pc_energy
    calcs["GIRATOIRE:energie bond"] = pc_energy
    calcs["GIRATOIRE:puissance arbre"] = pc_shaft_power
    calcs["GIRATOIRE:puissance installée"] = pc_installed_power
    calcs["GIRATOIRE:puissance installee"] = pc_installed_power
    calcs["GIRATOIRE:ouverture alim"] = pc_f100_um / 1000 * 1.2

    sc_f80_um = _get("f80 alimentation", "CONE", pc_p80_um)
    sc_p80_um = _get("p80 produit", "CONE", 35000)
    sc_eff = _get("rendement mécanique", "CONE", pc_eff * 100)
    sc_eff = sc_eff / 100 if sc_eff and sc_eff > 1.5 else (sc_eff or pc_eff)
    sc_margin = _get("marge installation", "CONE", 15)
    sc_margin = sc_margin / 100 if sc_margin and sc_margin > 1.5 else (sc_margin or 0.15)
    sc_f80_um = max(float(sc_f80_um or pc_p80_um), 1.0)
    sc_p80_um = max(float(sc_p80_um or 35000), 1.0)
    sc_f80_um = max(sc_f80_um, sc_p80_um + 1.0)
    sc_energy = bond_energy_kwh_t(cwi, sc_f80_um, sc_p80_um)
    sc_shaft_power = shaft_power_kw(sc_energy, screen_oversize_design_tph)
    sc_installed_power = installed_power_kw(sc_shaft_power, sc_eff, sc_margin)
    calcs["CONE:ratio de réduction"] = sc_f80_um / sc_p80_um
    calcs["CONE:ratio de reduction"] = sc_f80_um / sc_p80_um
    calcs["CONE:énergie bond"] = sc_energy
    calcs["CONE:energie bond"] = sc_energy
    calcs["CONE:puissance arbre"] = sc_shaft_power
    calcs["CONE:puissance installée"] = sc_installed_power
    calcs["CONE:puissance installee"] = sc_installed_power

    # ── HPGR ──
    hpgr_fresh_tph = mill_design_tph
    hpgr_fresh_nominal_tph = mill_nominal_tph
    hpgr_recycle_pct = _get("recycle ratio", "HPGR", 25.0)
    hpgr_recycle = hpgr_recycle_pct / 100 if hpgr_recycle_pct and hpgr_recycle_pct > 1.5 else (hpgr_recycle_pct or 0.25)
    hpgr_total_tph = hpgr_total_roll_tph(hpgr_fresh_tph, hpgr_recycle)
    hpgr_total_nominal_tph = hpgr_total_roll_tph(hpgr_fresh_nominal_tph, hpgr_recycle)
    hpgr_f80_um = _to_um(_get("f80 alimentation", "HPGR", sc_p80_um), sc_p80_um, convert_small_mm=True)
    hpgr_p80_um = _to_um(_get("p80 produit", "HPGR", _get("coupure crible", "HPGR", 6000)), 6000, convert_small_mm=True)
    calcs["HPGR:circuit processing rate"] = hpgr_fresh_tph
    calcs["HPGR:fresh feed"] = hpgr_fresh_tph
    calcs["HPGR:débit fresh feed"] = hpgr_fresh_tph
    calcs["HPGR:debit fresh feed"] = hpgr_fresh_tph
    calcs["HPGR:débit total roll"] = hpgr_total_tph
    calcs["HPGR:debit total roll"] = hpgr_total_tph
    calcs["HPGR:feed crible"] = hpgr_total_tph
    calcs["HPGR:undersize"] = hpgr_fresh_tph
    calcs["HPGR:oversize"] = hpgr_total_tph - hpgr_fresh_tph
    calcs["HPGR:ratio de réduction"] = hpgr_f80_um / hpgr_p80_um if hpgr_p80_um else None
    calcs["HPGR:ratio de reduction"] = hpgr_f80_um / hpgr_p80_um if hpgr_p80_um else None
    nominal_calcs["HPGR:circuit processing rate"] = hpgr_fresh_nominal_tph
    nominal_calcs["HPGR:fresh feed"] = hpgr_fresh_nominal_tph
    nominal_calcs["HPGR:débit fresh feed"] = hpgr_fresh_nominal_tph
    nominal_calcs["HPGR:debit fresh feed"] = hpgr_fresh_nominal_tph
    nominal_calcs["HPGR:débit total roll"] = hpgr_total_nominal_tph
    nominal_calcs["HPGR:debit total roll"] = hpgr_total_nominal_tph
    nominal_calcs["HPGR:feed crible"] = hpgr_total_nominal_tph
    nominal_calcs["HPGR:undersize"] = hpgr_fresh_nominal_tph
    nominal_calcs["HPGR:oversize"] = hpgr_total_nominal_tph - hpgr_fresh_nominal_tph

    # ── BALL MILL ──
    bm_fresh_tph = mill_design_tph
    bm_fresh_nominal_tph = mill_nominal_tph
    bm_total_tph = bm_fresh_tph * (1 + circ_load_bm / 100)
    calcs["BALL_MILL:circuit processing rate"] = bm_fresh_tph
    calcs["BALL_MILL:fresh feed"] = bm_fresh_tph
    calcs["BALL_MILL:débit alimentation"] = bm_fresh_tph
    calcs["BALL_MILL:debit alimentation"] = bm_fresh_tph
    calcs["BALL_MILL:recirculating"] = bm_total_tph - bm_fresh_tph
    nominal_calcs["BALL_MILL:circuit processing rate"] = bm_fresh_nominal_tph
    nominal_calcs["BALL_MILL:fresh feed"] = bm_fresh_nominal_tph
    nominal_calcs["BALL_MILL:débit alimentation"] = bm_fresh_nominal_tph
    nominal_calcs["BALL_MILL:debit alimentation"] = bm_fresh_nominal_tph

    # Ball mill power (Bond 3rd Law: W = 10 × Wi × (1/√P80 - 1/√F80))
    # BM F80 depends on the comminution circuit upstream:
    #   - If HPGR: BM F80 = HPGR P80 (typically 4-8 mm → 4000-8000 µm)
    #   - If SAG:  BM F80 = SAG P80 (typically 1-3 mm → 1000-3000 µm)
    #   - Default: 3000 µm
    p80_um = _to_um(_get("p80 cible", "BALL_MILL", _get("product p80", "BALL_MILL", 75)), 75)
    # Detect upstream: HPGR takes priority over SAG (if both exist, unusual)
    sag_p80_um = _to_um(_get("product p80", "SAG_MILL") or _get("discharge p80", "SAG_MILL"), None, convert_small_mm=True)
    f80_um = (hpgr_p80_um * 0.75) if hpgr_p80_um else (sag_p80_um or sc_p80_um or 3000)
    calcs["BALL_MILL:f80 alimentation"] = f80_um
    calcs["BALL_MILL:p80 cible"] = p80_um
    calcs["BALL_MILL:ratio de réduction"] = f80_um / p80_um if p80_um else None
    calcs["BALL_MILL:ratio de reduction"] = f80_um / p80_um if p80_um else None
    if p80_um and f80_um and bwi:
        p80_um = max(p80_um, 1.0)
        f80_um = max(f80_um, p80_um + 1.0)
        bm_energy = corrected_bond_energy_kwh_t(
            bwi,
            f80_um,
            p80_um,
            1,
            1,
            1,
            rowland_ef4(bwi, f80_um, p80_um),
            rowland_ef5(p80_um),
            1,
            1,
            1,
        )
        calcs["BALL_MILL:énergie bond non corrigée"] = round(max(bm_energy, 0), 4)
        calcs["BALL_MILL:energie bond non corrigee"] = round(max(bm_energy, 0), 4)
        calcs["BALL_MILL:énergie corrigée"] = round(max(bm_energy, 0), 4)
        calcs["BALL_MILL:energie corrigee"] = round(max(bm_energy, 0), 4)
        bm_shaft_power = shaft_power_kw(bm_energy, bm_fresh_tph)
        bm_installed_power = installed_power_kw(bm_shaft_power, bm_motor_eff, bm_install_margin)
        calcs["BALL_MILL:puissance arbre"] = round(bm_shaft_power, 0)
        calcs["BALL_MILL:installed power"] = round(bm_installed_power, 0)
        calcs["BALL_MILL:motor power"] = round(bm_installed_power, 0)
        calcs["BALL_MILL:power"] = round(bm_installed_power, 0)

    # ── SECONDARY VERTIMILL (full-stream secondary grind) ──
    verti_feed_tph = bm_fresh_tph
    verti_feed_nominal_tph = bm_fresh_nominal_tph
    verti_f80_um = p80_um
    verti_p80_um = _to_um(_get("p80 cible", "VERTIMILL", _get("product p80", "VERTIMILL", 38)), 38)
    verti_bwi = _get("bond", "VERTIMILL") or _get("bwi", "VERTIMILL", bwi)
    verti_factor = _get("facteur efficacité", "VERTIMILL", 0.70)
    verti_eff = _get("motor efficiency", "VERTIMILL", 95)
    verti_eff = verti_eff / 100 if verti_eff and verti_eff > 1.5 else (verti_eff or 0.95)
    verti_factor = verti_factor if verti_factor and verti_factor <= 1.5 else (verti_factor or 70) / 100
    if verti_f80_um and verti_p80_um and verti_bwi:
        verti_f80_um = max(verti_f80_um, verti_p80_um + 1.0)
        verti_energy = bond_energy_kwh_t(verti_bwi, verti_f80_um, verti_p80_um) * verti_factor
        verti_shaft_kw = shaft_power_kw(verti_energy, verti_feed_tph)
        verti_installed_kw = installed_power_kw(verti_shaft_kw, verti_eff, 10)
        calcs["VERTIMILL:débit alimentation"] = verti_feed_tph
        calcs["VERTIMILL:debit alimentation"] = verti_feed_tph
        calcs["VERTIMILL:f80 alimentation"] = verti_f80_um
        calcs["VERTIMILL:p80 cible"] = verti_p80_um
        calcs["VERTIMILL:énergie spécifique"] = round(verti_energy, 4)
        calcs["VERTIMILL:energie specifique"] = round(verti_energy, 4)
        calcs["VERTIMILL:puissance arbre"] = round(verti_shaft_kw, 0)
        calcs["VERTIMILL:puissance installée"] = round(verti_installed_kw, 0)
        calcs["VERTIMILL:puissance installee"] = round(verti_installed_kw, 0)
        nominal_calcs["VERTIMILL:débit alimentation"] = verti_feed_nominal_tph
        nominal_calcs["VERTIMILL:debit alimentation"] = verti_feed_nominal_tph

    # ── HYDROCYCLONE ──
    calcs["HYDROCYCLONE:feed"] = bm_total_tph
    calcs["HYDROCYCLONE:overflow"] = bm_fresh_tph
    nominal_calcs["HYDROCYCLONE:feed"] = bm_fresh_nominal_tph * (1 + circ_load_bm / 100)
    nominal_calcs["HYDROCYCLONE:overflow"] = bm_fresh_nominal_tph

    # ── FLOTATION ──
    flot_feed_tph = mill_design_tph
    flot_feed_nominal_tph = mill_nominal_tph
    flot_conc_tph = flot_feed_tph * flot_mass_pull
    flot_conc_nominal_tph = flot_feed_nominal_tph * flot_mass_pull
    flot_tails_tph = flot_feed_tph - flot_conc_tph
    flot_feed_pulp_sg = 1 / (flot_feed_density / ore_sg + (1 - flot_feed_density) / 1.0)
    flot_feed_m3h = flot_feed_tph / flot_feed_pulp_sg if flot_feed_pulp_sg > 0 else flot_feed_tph

    calcs["FLOTATION_ROUGHER:feed rate"] = flot_feed_tph
    nominal_calcs["FLOTATION_ROUGHER:feed rate"] = flot_feed_nominal_tph
    calcs["FLOTATION_ROUGHER:head grade"] = grade_au
    calcs["FLOTATION_ROUGHER:feed density"] = flot_feed_density * 100
    calcs["FLOTATION_ROUGHER:feed rate, pulp"] = round(flot_feed_m3h, 0)
    calcs["FLOTATION_ROUGHER:circuit mass pull"] = flot_mass_pull * 100
    calcs["FLOTATION_ROUGHER:concentrate production"] = round(flot_conc_tph, 1)
    conc_grade_au = grade_au * flot_recovery / flot_mass_pull if flot_mass_pull > 0 else 0
    calcs["FLOTATION_ROUGHER:concentrate grade"] = round(conc_grade_au, 1)
    tails_grade = grade_au * (1 - flot_recovery)
    calcs["FLOTATION_ROUGHER:tailings grade"] = round(tails_grade, 3)

    # Flotation volume
    flot_lab_rt = _get("residence time, lab", "FLOTATION_ROUGHER", 10)
    flot_scaleup = _get("scale up factor", "FLOTATION_ROUGHER", 2.5)
    flot_design_rt = flot_lab_rt * flot_scaleup
    calcs["FLOTATION_ROUGHER:residence time, design"] = flot_design_rt
    flot_vol_live = flot_feed_m3h * (flot_design_rt / 60)
    calcs["FLOTATION_ROUGHER:total flotation volume"] = round(flot_vol_live, 0)
    flot_aeration = _get("aeration factor", "FLOTATION_ROUGHER", 15) / 100
    flot_vol_total = flot_vol_live * (1 + flot_aeration)
    cell_vol = _get("cell volume", "FLOTATION_ROUGHER", 300)
    n_cells = math.ceil(flot_vol_total / cell_vol) if cell_vol > 0 else 6
    calcs["FLOTATION_ROUGHER:cells required"] = n_cells

    # ── REGRIND / ISAMILL ──
    regrind_feed_tph = flot_conc_tph
    regrind_feed_nominal_tph = flot_conc_nominal_tph
    calcs["ISAMILL:feed"] = round(regrind_feed_tph, 1)
    calcs["ISAMILL:regrind circuit feed"] = round(regrind_feed_tph, 1)
    nominal_calcs["ISAMILL:feed"] = round(regrind_feed_nominal_tph, 1)
    nominal_calcs["ISAMILL:regrind circuit feed"] = round(regrind_feed_nominal_tph, 1)

    regrind_f80 = _get("feed p80", "VERTIMILL_REGRIND", _get("feed p80", "ISAMILL", 106))
    regrind_p80 = _get("product p80", "VERTIMILL_REGRIND", _get("product p80", "ISAMILL", 25))
    regrind_sig = _get("signature plot", "VERTIMILL_REGRIND", 7.5)
    regrind_eff = _get("motor efficiency", "VERTIMILL_REGRIND", 94)
    regrind_eff = (regrind_eff / 100) if regrind_eff and regrind_eff > 1.5 else (regrind_eff or 0.94)
    regrind_margin = _get("installation margin", "VERTIMILL_REGRIND", 15)
    regrind_margin = (regrind_margin / 100) if regrind_margin and regrind_margin > 1.5 else (regrind_margin or 0.15)
    regrind_f80 = max(regrind_f80 or 106, 1.0)
    regrind_p80 = max(regrind_p80 or 25, 1.0)
    regrind_f80 = max(regrind_f80, regrind_p80 + 1.0)
    regrind_energy = regrind_sig * math.log(regrind_f80 / regrind_p80)
    regrind_shaft_kw = regrind_feed_tph * regrind_energy
    regrind_installed_kw = regrind_shaft_kw / max(regrind_eff, 0.5) * (1 + regrind_margin)
    calcs["VERTIMILL_REGRIND:regrind circuit feed"] = round(regrind_feed_tph, 1)
    calcs["VERTIMILL_REGRIND:feed"] = round(regrind_feed_tph, 1)
    calcs["VERTIMILL_REGRIND:feed p80"] = regrind_f80
    calcs["VERTIMILL_REGRIND:product p80"] = regrind_p80
    calcs["VERTIMILL_REGRIND:specific energy"] = round(regrind_energy, 2)
    calcs["VERTIMILL_REGRIND:shaft power"] = round(regrind_shaft_kw, 0)
    calcs["VERTIMILL_REGRIND:installed power"] = round(regrind_installed_kw, 0)
    calcs["VERTIMILL_REGRIND:installed power per mill"] = round(regrind_installed_kw, 0)
    nominal_calcs["VERTIMILL_REGRIND:regrind circuit feed"] = round(regrind_feed_nominal_tph, 1)
    nominal_calcs["VERTIMILL_REGRIND:feed"] = round(regrind_feed_nominal_tph, 1)

    # ── CONCENTRATE THICKENER ──
    calcs["EPAISSISSEUR_CONC:feed rate"] = round(regrind_feed_tph, 1)
    thk_conc_ua = _get("settling flux", "EPAISSISSEUR_CONC") or _get("solids settling", "EPAISSISSEUR_CONC", 0.25)
    if thk_conc_ua and thk_conc_ua > 0:
        thk_conc_area = (regrind_feed_tph * plant_hpd) / thk_conc_ua if thk_conc_ua > 0 else 500
        thk_conc_diam = circular_diameter_m(thk_conc_area)
        calcs["EPAISSISSEUR_CONC:thickening area"] = round(thk_conc_area, 0)
        calcs["EPAISSISSEUR_CONC:thickener diameter"] = round(thk_conc_diam, 1)

    # ── LEACHING ──
    leach_feed_tph = regrind_feed_tph  # concentrate after thickening
    leach_feed_nominal_tph = regrind_feed_nominal_tph
    _leach_feed_tpd = leach_feed_tph * plant_hpd
    calcs["LEACH_CUVES:solid - feed"] = round(leach_feed_tph, 1)
    calcs["LEACH_CUVES:feed leach"] = round(leach_feed_tph, 1)
    calcs["LEACH_CUVES:processing circuit rate"] = round(leach_feed_tph, 1)
    nominal_calcs["LEACH_CUVES:solid - feed"] = round(leach_feed_nominal_tph, 1)
    nominal_calcs["LEACH_CUVES:feed leach"] = round(leach_feed_nominal_tph, 1)
    nominal_calcs["LEACH_CUVES:processing circuit rate"] = round(leach_feed_nominal_tph, 1)

    leach_pct_solids = _get("feed % solid", "LEACH_CUVES") or _get("leach feed % solid", "LEACH_CUVES") or _get("% solid", "LEACH_CUVES", 40)
    if leach_pct_solids > 1: leach_pct_solids /= 100
    leach_pulp_sg = slurry_density_t_m3(conc_sg, leach_pct_solids)
    leach_pulp_m3h = slurry_volume_m3h(leach_feed_tph, conc_sg, leach_pct_solids)
    calcs["LEACH_CUVES:pulp - feed volumetric"] = round(leach_pulp_m3h, 0)
    calcs["LEACH_CUVES:pulp specific gravity"] = round(leach_pulp_sg, 2)

    # Leach tank sizing
    if leach_srt and leach_n_tanks:
        _air_holdup = _get("air hold", "LEACH_CUVES", 5) / 100
        leach_vol_total = residence_volume_m3(leach_pulp_m3h, leach_srt)
        leach_vol_per_tank = leach_vol_total / max(leach_n_tanks, 1)
        calcs["LEACH_CUVES:total live volume"] = round(leach_vol_total, 0)
        calcs["LEACH_CUVES:total live volume per tank"] = round(leach_vol_per_tank, 0)
        hd_ratio = _get("ratio h/d", "LEACH_CUVES", 1.5)
        if hd_ratio > 0 and leach_vol_per_tank > 0:
            d_live = cylindrical_volume_diameter_m(leach_vol_per_tank, hd_ratio)
            h_live = d_live * hd_ratio
            calcs["LEACH_CUVES:live diameter"] = round(d_live, 1)
            calcs["LEACH_CUVES:live height"] = round(h_live, 1)
            freeboard = _get("freeboard", "LEACH_CUVES", 15) / 100
            d_design = d_live * (1 + freeboard) ** (1/3)
            h_design = h_live * (1 + freeboard)
            v_design = leach_vol_per_tank * (1 + freeboard)
            calcs["LEACH_CUVES:design volume per tank"] = round(v_design, 0)
            calcs["LEACH_CUVES:design diameter per tank"] = round(d_design, 1)
            calcs["LEACH_CUVES:design height per tank"] = round(h_design, 1)

    # Reagent consumption rates
    calcs["LEACH_CUVES:nacn consumption"] = round(nacn_kg_t * leach_feed_tph, 1) if nacn_kg_t else None
    calcs["LEACH_CUVES:lime consumption"] = round(cao_kg_t_leach * leach_feed_tph, 1) if cao_kg_t_leach else None

    # ── CIP ──
    cip_feed_tph = leach_feed_tph
    cip_feed_nominal_tph = leach_feed_nominal_tph
    calcs["CIP:solid - feed"] = round(cip_feed_tph, 1)
    calcs["CIP:feed cip tanks"] = round(cip_feed_tph, 1)
    nominal_calcs["CIP:solid - feed"] = round(cip_feed_nominal_tph, 1)
    nominal_calcs["CIP:feed cip tanks"] = round(cip_feed_nominal_tph, 1)

    if 0 < cip_pct_solids < 1 and conc_sg > 0:
        cip_pulp_sg = slurry_density_t_m3(conc_sg, cip_pct_solids)
        if cip_pulp_sg > 0:
            cip_pulp_m3h = slurry_volume_m3h(cip_feed_tph, conc_sg, cip_pct_solids)
        else:
            cip_pulp_m3h = cip_feed_tph * 3  # fallback if SG is invalid
        calcs["CIP:pulp - feed volumetric"] = round(cip_pulp_m3h, 0)
    else:
        cip_pulp_sg = 1.4  # default slurry SG
        cip_pulp_m3h = cip_feed_tph * 3

    if cip_srt and cip_n_tanks:
        cip_vol_total = residence_volume_m3(cip_pulp_m3h, cip_srt)
        cip_vol_per_tank = cip_vol_total / max(cip_n_tanks, 1)
        calcs["CIP:total live volume"] = round(cip_vol_total, 0)
        calcs["CIP:total live volume per tank"] = round(cip_vol_per_tank, 0)
        hd = _get("ratio h/d", "CIP", 1.5)
        if hd > 0 and cip_vol_per_tank > 0:
            d = cylindrical_volume_diameter_m(cip_vol_per_tank, hd)
            h = d * hd
            calcs["CIP:live diameter"] = round(d, 1)
            calcs["CIP:live height"] = round(h, 1)
            fb = _get("freeboard", "CIP", 15) / 100
            calcs["CIP:design volume per tank"] = round(cip_vol_per_tank * (1 + fb), 0)
            calcs["CIP:design diameter per tank"] = round(d * (1 + fb) ** (1/3), 1)
            calcs["CIP:design height per tank"] = round(h * (1 + fb), 1)

    # Gold units in solution
    overall_recovery = flot_recovery * leach_recovery
    au_units_gh = cip_feed_tph * conc_grade_au * leach_recovery * 1000  # g/h
    calcs["CIP:units of gold"] = round(au_units_gh / 1000, 2)  # kg/h

    # ── DETOX ──
    detox_feed_tph = cip_feed_tph
    detox_feed_nominal_tph = cip_feed_nominal_tph
    calcs["DETOX_INCO:circuit feed"] = round(detox_feed_tph, 1)
    nominal_calcs["DETOX_INCO:circuit feed"] = round(detox_feed_nominal_tph, 1)
    so2_ratio = _get("so2 dosage", "DETOX_INCO", 6)  # g SO2 / g CN_WAD
    wad_cn = _get("feed wad", "DETOX_INCO", 575)  # mg/L
    if so2_ratio and wad_cn and detox_feed_tph:
        detox_pulp_m3h = detox_feed_tph / (0.33 * 1.27) if conc_sg else detox_feed_tph * 3
        so2_kgh = so2_ratio * wad_cn * detox_pulp_m3h / 1e6 * 1000
        calcs["DETOX_INCO:so2 required"] = round(so2_kgh, 0)
        o2_kgh = so2_kgh * 3  # stoichiometry
        calcs["DETOX_INCO:oxygen consumption"] = round(o2_kgh, 0)

    # ── FINAL TAILINGS THICKENER ──
    _tails_feed_tph = flot_tails_tph + detox_feed_tph  # combined
    calcs["EPAISSISSEUR:feed rate"] = round(plant_tph, 0)  # total plant feed to final tails
    nominal_calcs["EPAISSISSEUR:feed rate"] = round(mill_nominal_tph, 0)
    tails_ua = _get("settling flux", "EPAISSISSEUR") or _get("solids settling", "EPAISSISSEUR", 0.75)
    if tails_ua and tails_ua > 0:
        tails_area = (plant_tph * plant_hpd) / tails_ua
        tails_diam = circular_diameter_m(tails_area)
        calcs["EPAISSISSEUR:thickening area"] = round(tails_area, 0)
        calcs["EPAISSISSEUR:thickener diameter"] = round(tails_diam, 0)

    # ── REAGENT CONSUMPTION RATES ──
    calcs["REACTIF_PAX:total dosage"] = pax_gt
    calcs["REACTIF_PAX:total flow"] = round(pax_gt * plant_tph / 1e6 * 1000, 1) if pax_gt else None

    calcs["REACTIF_MIBC:total dosage"] = mibc_gt
    calcs["REACTIF_MIBC:total flow"] = round(mibc_gt * plant_tph / 1e6 * 1000, 1) if mibc_gt else None

    calcs["REACTIF_NACN:total dosage"] = nacn_kg_t
    calcs["REACTIF_NACN:total flow"] = round(nacn_kg_t * leach_feed_tph / 1000, 1) if nacn_kg_t else None

    calcs["REACTIF_LIME:total dosage"] = round(cao_kg_t_leach + (so2_ratio * wad_cn / 1e6 * 2 if so2_ratio and wad_cn else 0), 3)

    calcs["REACTIF_FLOCCULANT:total dosage"] = floc_gt_thk

    # ── GOLD PRODUCTION ──
    annual_hours = plant_hpd * 365 * plant_avail
    annual_tonnes = plant_tph * annual_hours
    annual_gold_g = annual_tonnes * grade_au * overall_recovery
    annual_gold_oz = annual_gold_g * TROY_OZ_PER_GRAM
    annual_revenue = annual_gold_oz * au_price

    # 5. Apply calculated values to DB
    updated = 0
    errors = []

    for calc_key, calc_value in calcs.items():
        if calc_value is None:
            continue

        # Find matching criterion
        parts = calc_key.split(":", 1)
        op_code = parts[0] if len(parts) > 1 else None
        item_pattern = parts[-1].lower()

        matched_id = None
        for k, v in criteria.items():
            v_item = (v["item"] or "").lower()
            v_op = (v["op_code"] or "").upper()
            if op_code and v_op != op_code.upper():
                continue
            if item_pattern in v_item or v_item in item_pattern:
                matched_id = v["id"]
                break

        # Fallback: match by dag_key when item-text matching fails (Unicode NFC/NFD drift)
        if matched_id is None:
            _dk = _CALC_TO_DAG_KEY.get(calc_key)
            if _dk:
                fallback = by_dag_key.get(_dk)
                if fallback and (not op_code or (fallback.get("op_code") or "").upper() == op_code.upper()):
                    matched_id = fallback["id"]

        if matched_id:
            try:
                # Opportunistic dag_key back-fill: if the catalog mapping was
                # added after this row was seeded, set dag_key now (never
                # overwrite — the catalog is canonical when present).
                dag_key_for_calc = _CALC_TO_DAG_KEY.get(calc_key)
                nominal_value = nominal_calcs.get(calc_key)
                if dag_key_for_calc and nominal_value is not None:
                    cursor.execute(
                        "UPDATE design_criteria_v2 SET design_value = %s, nominal_value = %s, source_code = 'C', "
                        "dag_key = COALESCE(dag_key, %s), "
                        "version = version + 1, updated_at = NOW() "
                        "WHERE id = %s AND template_id = %s "
                        "  AND COALESCE(source_code, 'X') NOT IN ('M', 'O', 'Manual')",
                        (round(calc_value, 4), round(nominal_value, 4), dag_key_for_calc, matched_id, template_id),
                    )
                elif dag_key_for_calc:
                    cursor.execute(
                        "UPDATE design_criteria_v2 SET design_value = %s, source_code = 'C', "
                        "dag_key = COALESCE(dag_key, %s), "
                        "version = version + 1, updated_at = NOW() "
                        "WHERE id = %s AND template_id = %s "
                        "  AND COALESCE(source_code, 'X') NOT IN ('M', 'O', 'Manual')",
                        (round(calc_value, 4), dag_key_for_calc, matched_id, template_id),
                    )
                elif nominal_value is not None:
                    cursor.execute(
                        "UPDATE design_criteria_v2 SET design_value = %s, nominal_value = %s, source_code = 'C', "
                        "version = version + 1, updated_at = NOW() "
                        "WHERE id = %s AND template_id = %s "
                        "  AND COALESCE(source_code, 'X') NOT IN ('M', 'O', 'Manual')",
                        (round(calc_value, 4), round(nominal_value, 4), matched_id, template_id),
                    )
                else:
                    cursor.execute(
                        "UPDATE design_criteria_v2 SET design_value = %s, source_code = 'C', "
                        "version = version + 1, updated_at = NOW() "
                        "WHERE id = %s AND template_id = %s "
                        "  AND COALESCE(source_code, 'X') NOT IN ('M', 'O', 'Manual')",
                        (round(calc_value, 4), matched_id, template_id),
                    )
                if getattr(cursor, "rowcount", 1):
                    updated += 1
            except Exception as e:
                errors.append(f"{calc_key}: {e}")

    return {
        "updated": updated,
        "total": len(rows),
        "errors": errors,
        "production_summary": {
            "annual_tonnes": round(annual_tonnes, 0),
            "annual_gold_oz": round(annual_gold_oz, 0),
            "annual_revenue_musd": round(annual_revenue / 1e6, 1),
            "overall_recovery_pct": round(overall_recovery * 100, 1),
            "plant_tph": plant_tph,
            "concentrate_tph": round(flot_conc_tph, 1),
            "leach_feed_tph": round(leach_feed_tph, 1),
        },
    }
