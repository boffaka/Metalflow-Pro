"""
MetalFlow Pro — DC Generator Engine.

Enriches design criteria (design_criteria_v2) with real LIMS project data.
After default criteria are generated from the catalog (source_code='I'),
this engine looks up LIMS averages and fills lims_value / design_value.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("mpdpms.dc_generator")

# =============================================================================
# LIMS FIELD MAPPINGS
# =============================================================================
# Key:   logical reference used in the enrichment engine
# Value: (table, column) — we query  SELECT AVG(column) FROM table WHERE project_id=%s
#
# The mapping keys follow the convention "test_code.column_name" which mirrors
# the LIMS table structure in the database.
# =============================================================================

LIMS_FIELD_MAP: dict[str, tuple[str, str]] = {
    # Comminution — lims_b1
    "b1.bwi_kwh_t":          ("lims_b1", "bwi_kwh_t"),
    "b1.cwi_kwh_t":          ("lims_b1", "crushing_wi_kwh_t"),
    "b1.mb_kwh_t":           ("lims_b1", "mib_kwh_t"),
    "b1.f80_um":             ("lims_b1", "f80_um"),
    "b1.abrasion_index_ai":  ("lims_b1", "abrasion_index_ai"),
    "b1.p80_target_um":      ("lims_b1", "p80_target_um"),
    "b1.ucs_mpa":            ("lims_b1", "ucs_mpa"),
    "b1.mia_kwh_t":          ("lims_b1", "mia_kwh_t"),
    "b1.mic_kwh_t":          ("lims_b1", "mic_kwh_t"),
    "b1.mih_kwh_t":          ("lims_b1", "mih_kwh_t"),

    # Head assays — lims_a1
    "a1.au_g_t":             ("lims_a1", "au_g_t"),
    "a1.s_sulfide_pct":      ("lims_a1", "s_sulfide_pct"),
    "a1.c_organic_pct":      ("lims_a1", "c_organic_pct"),
    "a1.fe_pct":             ("lims_a1", "fe_pct"),
    "a1.as_ppm":             ("lims_a1", "as_ppm"),
    "a1.s_total_pct":        ("lims_a1", "s_total_pct"),
    "a1.cu_pct":             ("lims_a1", "cu_pct"),

    # Granulometry / PSD — lims_a2, liberation A3, mineralogy M1
    "a2.p80_um":             ("lims_a2", "p80_um"),
    "a3.p80_broyage_um":     ("lims_a3", "p80_broyage_um"),
    "m1.k80_um":             ("lims_m1", "k80_um"),

    # Gravity recovery — lims_c2
    "c2.grg_rec_pct":        ("lims_c2", "grg_rec_pct"),
    "c2.au_recovery_pct":    ("lims_c2", "au_recovery_pct"),
    "c2.mass_pull_pct":      ("lims_c2", "mass_pull_pct"),

    # Flotation — lims_flotation
    "g1.au_recovery_pct":          ("lims_flotation", "au_recovery_pct"),
    "g1.concentrate_wt_pct":       ("lims_flotation", "concentrate_wt_pct"),
    "g1.au_concentrate_g_t":       ("lims_flotation", "au_concentrate_g_t"),
    "g1.concentrate_grade_g_t":    ("lims_flotation", "concentrate_grade_g_t"),
    "g1.mass_pull_pct":            ("lims_flotation", "mass_pull_pct"),
    "g1.sulphide_recovery_pct":    ("lims_flotation", "sulphide_recovery_pct"),

    # Leaching — lims_d1
    "d1.leach_rec_48h_pct":        ("lims_d1", "leach_rec_48h_pct"),
    "d1.au_recovery_pct":          ("lims_d1", "au_recovery_pct"),
    "d1.nacn_consumption_kg_t":    ("lims_d1", "nacn_consumption_kg_t"),
    "d1.cao_consumption_kg_t":     ("lims_d1", "cao_consumption_kg_t"),

    # Thickening — lims_e1
    "e1.unit_area_m2_t_d":         ("lims_e1", "unit_area_m2_t_d"),
    "e1.flocculant_dosage_g_t":    ("lims_e1", "flocculant_dosage_g_t"),
    "e1.underflow_density_pct_solids": ("lims_e1", "underflow_density_pct_solids"),

    # Elution — lims_elution
    "h1.recup_au_elution_pct":     ("lims_elution", "recup_au_elution_pct"),
    "h1.elution_t_c":              ("lims_elution", "elution_t_c"),
}

# =============================================================================
# ITEM-TO-LIMS MATCHING
# =============================================================================
# Maps (op_code or pattern, item substring) → lims_field key.
# This allows the enrichment engine to match catalog criteria items
# to the correct LIMS query.  The match is case-insensitive on item.
# =============================================================================

# Unit conversion factors: LIMS value → DC value.
# Applied when LIMS stores µm but DC expects mm (or similar mismatches).
_LIMS_UNIT_CONVERSION: dict[str, float] = {
    "b1.f80_um": 0.001,       # µm → mm when the criterion unit is mm
    "b1.p80_target_um": 0.001,
    "a2.p80_um": 0.001,
    "a3.p80_broyage_um": 0.001,
    "m1.k80_um": 0.001,
    "psd.grind_p80_um": 0.001,
    "psd.regrind_p80_um": 0.001,
}


# LIMS-key → DAG-key mapping (Chunk 1.5.B — Option A).
# When LIMS enrichment writes a value into a row that has no `dag_key` yet
# (typically a legacy row from before the catalog learned its mapping), the
# writer back-fills the DAG key opportunistically. The set is intentionally
# minimal — only LIMS-derived inputs that are themselves DAG inputs.
_LIMS_TO_DAG_KEY: dict[str, str] = {
    "b1.bwi_kwh_t":          "avg_bwi",
    "b1.f80_um":             "avg_f80_um",
    "b1.p80_target_um":      "avg_p80_um",
    "a2.p80_um":             "avg_p80_um",
    "a3.p80_broyage_um":     "avg_p80_um",
    "m1.k80_um":             "regrind_p80_um",
    "psd.grind_p80_um":      "avg_p80_um",
    "psd.regrind_p80_um":    "regrind_p80_um",
    "c2.grg_rec_pct":        "avg_grg_pct",
    "c2.au_recovery_pct":    "avg_au_recovery_pct",
    "g1.au_recovery_pct":    "avg_au_recovery_pct",
    "g1.mass_pull_pct":      "flot_mass_pull_pct",
    "d1.au_recovery_pct":    "avg_au_recovery_pct",
    "d1.nacn_consumption_kg_t": "avg_nacn_kg_t",
    "d1.cao_consumption_kg_t":  "avg_cao_kg_t",
    "e1.unit_area_m2_t_d":   "avg_unit_area",
    "a1.au_g_t":             "gold_grade_g_t",
}

_ITEM_LIMS_RULES: list[tuple[str | None, str, str]] = [
    # (op_code filter or None for any, item substring, lims_field key)
    #
    # Items are matched as case-insensitive SUBSTRING. French aliases are
    # listed first so they get priority when the new (2026-05) catalog is
    # in use; English aliases follow for legacy item text compatibility.

    # ── Comminution Bond / abrasion / UCS ────────────────────────────────
    (None, "Bond BWi",               "b1.bwi_kwh_t"),
    (None, "BWi (",                  "b1.bwi_kwh_t"),  # "BWi (Ball mill...)"
    (None, "Bond work index",        "b1.bwi_kwh_t"),
    (None, "BWi",                    "b1.bwi_kwh_t"),
    (None, "Bond CWi",               "b1.cwi_kwh_t"),
    (None, "CWi (",                  "b1.cwi_kwh_t"),
    (None, "Crushing work index",    "b1.cwi_kwh_t"),
    (None, "Indice d'abrasion",      "b1.abrasion_index_ai"),
    (None, "Abrasion index",         "b1.abrasion_index_ai"),
    (None, "UCS",                    "b1.ucs_mpa"),
    # F80 lab — restrict to grinding circuits only (not crushers)
    ("SAG_MILL", "F80 alimentation", "b1.f80_um"),
    ("BALL_MILL", "F80 alimentation","b1.f80_um"),
    ("SAG_MILL", "Feed F80",         "b1.f80_um"),
    ("BALL_MILL", "Feed F80",        "b1.f80_um"),
    ("BALL_MILL", "P80 cible", "psd.grind_p80_um"),
    ("BALL_MILL", "P80 produit", "psd.grind_p80_um"),
    ("HYDROCYCLONE", "P80",           "psd.grind_p80_um"),
    ("ISAMILL", "P80 produit",        "psd.regrind_p80_um"),
    ("VERTIMILL_REGRIND", "P80 produit", "psd.regrind_p80_um"),
    ("VERTIMILL_REGRIND", "Product P80", "psd.regrind_p80_um"),
    ("SMD", "P80 produit",            "psd.regrind_p80_um"),
    ("SMD", "Product P80",            "psd.regrind_p80_um"),

    # ── Head assays (LIMS A1) ────────────────────────────────────────────
    (None, "Teneur Au alim",         "a1.au_g_t"),
    (None, "Teneur tête Au",         "a1.au_g_t"),
    (None, "head grade Au",          "a1.au_g_t"),
    (None, "Au head grade",          "a1.au_g_t"),
    (None, "Au grade",               "a1.au_g_t"),
    (None, "Gold grade",             "a1.au_g_t"),
    (None, "Teneur tête S",          "a1.s_sulfide_pct"),
    (None, "Sulphide S",             "a1.s_sulfide_pct"),
    (None, "S sulphide",             "a1.s_sulfide_pct"),
    (None, "Carbone organique",      "a1.c_organic_pct"),
    (None, "Organic carbon",         "a1.c_organic_pct"),
    (None, "C organic",              "a1.c_organic_pct"),

    # ── Gravity (LIMS C2) ────────────────────────────────────────────────
    (None, "GRG dans minerai",       "c2.grg_rec_pct"),
    (None, "GRG (Knelson",           "c2.grg_rec_pct"),
    (None, "GRG recovery",           "c2.grg_rec_pct"),
    (None, "Gravity recovery",       "c2.grg_rec_pct"),
    (None, "Récupération unitaire Knelson", "c2.au_recovery_pct"),
    (None, "Gravity Au recovery",    "c2.au_recovery_pct"),

    # ── Flotation (LIMS G1) ──────────────────────────────────────────────
    (None, "Récupération Au rougher","g1.au_recovery_pct"),
    (None, "Récup Au rougher",       "g1.au_recovery_pct"),
    (None, "Flotation Au recovery",  "g1.au_recovery_pct"),
    (None, "Teneur concentré rougher","g1.concentrate_grade_g_t"),
    (None, "Concentrate grade",      "g1.concentrate_grade_g_t"),
    (None, "Concentrate weight",     "g1.concentrate_wt_pct"),
    (None, "Mass pull rougher",      "g1.mass_pull_pct"),
    (None, "Mass pull",              "g1.mass_pull_pct"),
    (None, "Récupération sulfure",   "g1.sulphide_recovery_pct"),
    (None, "Sulphide recovery",      "g1.sulphide_recovery_pct"),

    # ── Leaching (LIMS D1) ───────────────────────────────────────────────
    ("CIL", "Récupération attendue", "d1.au_recovery_pct"),
    ("CIP", "Récupération attendue", "d1.au_recovery_pct"),
    ("LEACH_CUVES", "Récupération attendue", "d1.au_recovery_pct"),
    (None, "Récupération Au attendue","d1.au_recovery_pct"),
    (None, "Leach recovery 48h",     "d1.leach_rec_48h_pct"),
    (None, "Au recovery",            "d1.au_recovery_pct"),
    (None, "Leach recovery",         "d1.au_recovery_pct"),
    # NaCN — note CIL/CIP catalog shows "NaCN dosage" which is project design,
    # but if we want to enrich with LIMS-measured consumption use "consumption"
    (None, "NaCN consumption",       "d1.nacn_consumption_kg_t"),
    (None, "Cyanide consumption",    "d1.nacn_consumption_kg_t"),
    (None, "CaO consumption",        "d1.cao_consumption_kg_t"),
    (None, "Lime consumption",       "d1.cao_consumption_kg_t"),

    # ── Thickening (LIMS E1) ─────────────────────────────────────────────
    (None, "Unit area",              "e1.unit_area_m2_t_d"),
    (None, "SLR design",             "e1.unit_area_m2_t_d"),
    (None, "Dosage floculant",       "e1.flocculant_dosage_g_t"),
    (None, "Flocculant dosage",      "e1.flocculant_dosage_g_t"),
    (None, "% solides UF cible",     "e1.underflow_density_pct_solids"),
    (None, "Underflow density",      "e1.underflow_density_pct_solids"),

    # ── Elution / ADR (LIMS H1) ──────────────────────────────────────────
    (None, "Stripping efficiency",   "h1.recup_au_elution_pct"),
    (None, "Elution efficiency",     "h1.recup_au_elution_pct"),
    (None, "Elution recovery",       "h1.recup_au_elution_pct"),
    (None, "Température élution",    "h1.elution_t_c"),
    (None, "Elution temperature",    "h1.elution_t_c"),
]


def _match_lims_field(op_code: str, item: str) -> str | None:
    """Return the lims_field key for a given criterion, or None."""
    item_lower = (item or "").lower()
    for rule_op, rule_substr, lims_key in _ITEM_LIMS_RULES:
        if rule_op is not None and rule_op != op_code:
            continue
        if rule_substr.lower() in item_lower:
            return lims_key
    return None


# =============================================================================
# PUBLIC API
# =============================================================================

def get_lims_summary(project_id: str, cursor) -> dict:
    """Return all available LIMS averages for a project.

    Returns: {"b1.bwi_kwh_t": 18.4, "a1.au_g_t": 1.52, ...}
    Only keys with non-null averages are included.
    """
    try:
        result: dict[str, float] = {}

        for lims_key, (table, column) in LIMS_FIELD_MAP.items():
            # Safety: `table` and `column` are from LIMS_FIELD_MAP, never user input.
            sql = f"SELECT AVG({column}) AS avg_value FROM {table} WHERE project_id = %s"  # noqa: S608
            try:
                cursor.execute("SAVEPOINT _lims_dc_avg")
                cursor.execute(sql, (project_id,))
                row = cursor.fetchone()
                cursor.execute("RELEASE SAVEPOINT _lims_dc_avg")
            except Exception:
                try:
                    cursor.execute("ROLLBACK TO SAVEPOINT _lims_dc_avg")
                except Exception:
                    pass
                logger.warning("LIMS query failed for %s.%s", table, column, exc_info=True)
                continue

            if row is None:
                continue

            val = row.get("avg_value") if isinstance(row, dict) else row[0]
            if val is not None:
                result[lims_key] = float(val)

        # Canonical PSD design signals used by DC criteria. The priority mirrors
        # the Granulometry/PSD module: liberation target first, Bond target next,
        # measured A2 PSD last. Regrind target comes from mineralogy K80 when
        # available, otherwise a conservative half of primary grind P80.
        grind_p80 = (
            result.get("a3.p80_broyage_um")
            or result.get("b1.p80_target_um")
            or result.get("a2.p80_um")
        )
        if grind_p80 is not None:
            result["psd.grind_p80_um"] = grind_p80

        regrind_p80 = result.get("m1.k80_um")
        if regrind_p80 is None and grind_p80 is not None:
            regrind_p80 = grind_p80 * 0.5
        if regrind_p80 is not None:
            result["psd.regrind_p80_um"] = regrind_p80

        return result
    except Exception as e:
        logger.error("get_lims_summary failed for project_id=%s: %s", project_id, e)
        return {}


def enrich_criteria_with_lims(
    project_id: str,
    template_id: str,
    cursor,
) -> dict:
    """Enrich design_criteria_v2 rows with LIMS project averages.

    For each criterion in the template:
      1. Determine if the criterion item matches a LIMS field.
      2. Query the LIMS table for the project average.
      3. Set lims_value.  If a value is found, also set design_value
         and source_code='A' (actual data) — unless the user has already
         overridden it (source_code not in ('I', 'X')).

    Args:
        project_id:  UUID of the project.
        template_id: UUID of the circuit template.
        cursor:      A psycopg2 RealDictCursor (inside an open transaction).

    Returns:
        {"updated": int, "lims_found": int, "total": int}
    """
    try:
        return _enrich_criteria_with_lims_impl(project_id, template_id, cursor)
    except Exception as e:
        logger.error("enrich_criteria_with_lims failed for project_id=%s, template_id=%s: %s",
                     project_id, template_id, e)
        raise RuntimeError(f"enrich_criteria_with_lims failed for project {project_id}: {e}") from e


def _enrich_criteria_with_lims_impl(project_id: str, template_id: str, cursor) -> dict:
    """Internal implementation of enrich_criteria_with_lims."""
    # 1. Fetch all enabled criteria for this template
    cursor.execute(
        "SELECT id, op_code, item, unit, source_code, dag_key "
        "FROM design_criteria_v2 "
        "WHERE template_id = %s AND enabled = true "
        "ORDER BY sort_order, ref_number",
        (template_id,),
    )
    criteria = cursor.fetchall()
    total = len(criteria)

    # 2. Pre-fetch all LIMS averages for efficiency
    lims_summary = get_lims_summary(project_id, cursor)

    updated = 0
    lims_found = 0

    for crit in criteria:
        lims_key = _match_lims_field(crit["op_code"], crit["item"])
        if lims_key is None:
            continue

        lims_val = lims_summary.get(lims_key)
        if lims_val is None:
            continue

        lims_found += 1

        # Apply unit conversion only when the DC row is expressed in mm. Most
        # PSD/LIMS fields are stored in µm and most DC criteria also use µm.
        unit = (crit.get("unit") or "").lower()
        factor = _LIMS_UNIT_CONVERSION.get(lims_key, 1.0) if "mm" in unit else 1.0
        design_val = lims_val * factor

        # Always set lims_value (raw LIMS).
        # Overwrite design_value if the criterion is unset or its source
        # explicitly says it should come from LIMS (L) or is just a default.
        # User-edited values (source="O" Owner / "M" Manual) are preserved.
        source = crit["source_code"] or "X"

        # Opportunistic dag_key back-fill: if the row was seeded before the
        # catalog learned its dag_key (legacy projects), use the LIMS→DAG map
        # to set it now. Never overwrite an existing dag_key — the catalog
        # mapping is canonical when present.
        new_dag_key = None
        if not crit.get("dag_key"):
            new_dag_key = _LIMS_TO_DAG_KEY.get(lims_key)

        if source in ("I", "X", "L", "D"):
            if new_dag_key:
                cursor.execute(
                    "UPDATE design_criteria_v2 "
                    "SET lims_value = %s, design_value = %s, source_code = 'L', "
                    "    dag_key = COALESCE(dag_key, %s), updated_at = NOW() "
                    "WHERE id = %s",
                    (lims_val, design_val, new_dag_key, crit["id"]),
                )
            else:
                cursor.execute(
                    "UPDATE design_criteria_v2 "
                    "SET lims_value = %s, design_value = %s, source_code = 'L', "
                    "    updated_at = NOW() "
                    "WHERE id = %s",
                    (lims_val, design_val, crit["id"]),
                )
        else:
            # User has overridden (source="O" or "M") — only update lims_value
            # for reference; their custom design_value is preserved.
            if new_dag_key:
                cursor.execute(
                    "UPDATE design_criteria_v2 "
                    "SET lims_value = %s, dag_key = COALESCE(dag_key, %s), "
                    "    updated_at = NOW() "
                    "WHERE id = %s",
                    (lims_val, new_dag_key, crit["id"]),
                )
            else:
                cursor.execute(
                    "UPDATE design_criteria_v2 "
                    "SET lims_value = %s, updated_at = NOW() "
                    "WHERE id = %s",
                    (lims_val, crit["id"]),
                )
        updated += 1

    return {"updated": updated, "lims_found": lims_found, "total": total}
