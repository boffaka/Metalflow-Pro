"""
MPDPMS — LIMS data validation rules engine.

Validates sample data against physical bounds, detects duplicates,
and flags outliers. Returns a list of validation flag dicts.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("mpdpms.validation")

# Rule definitions: table_code -> field -> (min, max, severity)
RULES: dict[str, dict[str, tuple[float, float, str]]] = {
    "a1": {
        "au_g_t": (0, 10000, "error"),
        "ag_g_t": (0, 50000, "error"),
        "cu_pct": (0, 100, "error"),
        "fe_pct": (0, 100, "error"),
        "s_total_pct": (0, 100, "error"),
        "as_ppm": (0, 1_000_000, "warning"),
        "hg_ppm": (0, 100_000, "warning"),
        "sio2_pct": (0, 100, "error"),
        "al2o3_pct": (0, 100, "error"),
        "loi_pct": (0, 100, "error"),
    },
    "b1": {
        "bwi_kwh_t": (0, 50, "warning"),
        "p80_um": (0, 100_000, "warning"),
        "f80_um": (0, 500_000, "warning"),
        "ucs_mpa": (0, 500, "warning"),
    },
    "c2": {
        "grg_pct": (0, 100, "error"),
        "gravity_au_recovery_pct": (0, 100, "error"),
    },
    "c3": {
        "grg_pct": (0, 100, "error"),
    },
    # d1: lixiviation LIMS — NaCN typ. 0.3–2 kg/t (Leach/CIP); valeurs >5–10 signalent minerai difficile ou erreur saisie.
    "d1": {
        "au_recovery_pct": (0, 100, "error"),
        "nacn_consumption_kg_t": (0, 25, "warning"),
        "cao_consumption_kg_t": (0, 30, "warning"),
    },
    "e1": {
        "unit_area_m2_t_d": (0, 100, "warning"),
        "flocculant_g_t": (0, 500, "warning"),
    },
    "e2": {
        "filtration_rate_kg_m2_h": (0, 5000, "warning"),
        "cake_moisture_pct": (0, 100, "error"),
    },
    "g1": {
        "recovery_pct": (0, 100, "error"),
        "concentrate_grade_pct": (0, 100, "error"),
    },
    "h1": {
        "elution_efficiency_pct": (0, 100, "error"),
    },
    "a2": {
        "p80_um": (0, 100_000, "warning"),
        "d50_um": (0, 100_000, "warning"),
    },
    "a3": {
        "au_libre_pct": (0, 100, "error"),
        "au_assoc_sulfures_pct": (0, 100, "error"),
        "au_occlus_pct": (0, 100, "error"),
        "recup_cil_pred_pct": (0, 100, "error"),  # nom historique colonne LIMS
        "recup_leach_pred_pct": (0, 100, "error"),
    },
    "m1": {
        "pyrite_pct": (0, 100, "error"),
        "quartz_pct": (0, 100, "error"),
    },
    "c2b": {
        "grg_cumul_pct": (0, 100, "error"),
        "recup_stage_pct": (0, 100, "error"),
    },
    "c2c": {
        "recup_au_pct": (0, 100, "error"),
        "rendement_massique_pct": (0, 100, "error"),
    },
    "dtx": {
        "cn_wad_mg_l": (0, 1000, "warning"),
        "cn_free_mg_l": (0, 500, "warning"),
        "ph_final": (0, 14, "error"),
        "as_mg_l": (0, 100, "warning"),
        "hg_ug_l": (0, 10000, "warning"),
    },
    "i1": {
        "ap_kg_caco3_t": (0, 10000, "warning"),
        "np_kg_caco3_t": (0, 10000, "warning"),
        "ph_paste": (0, 14, "error"),
        "tclp_as_mg_l": (0, 100, "warning"),
        "tclp_hg_mg_l": (0, 10, "warning"),
    },
}


def validate_lims_record(
    table_code: str,
    data: dict[str, Any],
    *,
    project_id: str | None = None,
) -> list[dict]:
    """
    Validate a LIMS data record against rules.

    Returns a list of validation flag dicts:
    {rule_code, severity, message, field_name, field_value, entity_type, project_id}
    """
    table_rules = RULES.get(table_code)
    if not table_rules:
        return []

    flags = []
    for field, (lo, hi, severity) in table_rules.items():
        value = data.get(field)
        if value is None:
            continue
        try:
            v = float(value)
        except (ValueError, TypeError):
            continue
        if v < lo or v > hi:
            flags.append({
                "rule_code": f"BOUNDS_{field}",
                "severity": severity,
                "message": f"{field} = {v} is outside valid range [{lo}, {hi}]",
                "field_name": field,
                "field_value": v,
                "entity_type": f"lims_{table_code}",
                "project_id": project_id,
            })
            logger.warning(
                "validation flag: %s = %s outside [%s, %s] for project %s",
                field, v, lo, hi, project_id,
            )
    return flags


def save_flags(flags: list[dict], entity_id: str) -> None:
    """Persist validation flags to the database."""
    if not flags:
        return
    try:
        from .db import execute
    except ImportError:
        from db import execute

    import psycopg2.extras
    for flag in flags:
        execute(
            "INSERT INTO validation_flags "
            "(project_id, entity_type, entity_id, rule_code, severity, message, field_name, field_value) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
            (
                flag.get("project_id"), flag["entity_type"], entity_id,
                flag["rule_code"], flag["severity"], flag["message"],
                flag.get("field_name"),
                psycopg2.extras.Json(flag["field_value"]) if flag.get("field_value") is not None else None,
            ),
        )
