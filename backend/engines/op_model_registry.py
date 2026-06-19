"""Map unit_operations_catalog op_codes to process_simulator model names.

Kept in a standalone module (no settings/DB) so compile ↔ simulator alignment
can be tested without loading the full FastAPI stack.
"""
from __future__ import annotations

# op_code → model name in _SIM_DISPATCH (process_simulator)
OP_MODEL_MAP: dict[str, str] = {
    # Comminution
    "GIRATOIRE": "crushing",
    "JAW_CRUSHER": "crushing",
    "CONE_CRUSHER": "crushing",
    "CONE": "crushing",
    "CRIBLE": "screening",
    "CRIBLE_CLASS": "screening",
    "STOCKPILE": "screening",
    "SAG_MILL": "sag_milling",
    "BALL_MILL": "ball_milling",
    "ROD_MILL": "ball_milling",
    "HPGR": "hpgr",
    "HYDROCYCLONE": "classification",
    # Regrind
    "ISAMILL": "regrind",
    "VERTIMILL": "regrind",
    "VERTIMILL_REGRIND": "regrind",
    "SMD": "regrind",
    # Concentration
    "FLOTATION_ROUGHER": "flotation",
    "FLOTATION_CLEANER": "flotation",
    "FLOTATION_SCAVENGER": "flotation",
    "FLOTATION_COLONNE": "flotation",
    "GRAVITE_KNELSON": "gravity",
    "GRAVITE_FALCON": "gravity",
    "GRAVITE_GEMENI": "gravity",
    "GRAVITY_CONC": "gravity",
    "GRAVITY": "gravity",
    # Refractory pretreatment (passthrough mass + metallurgical alert)
    "BIOX": "refractory_pretreatment",
    "POX": "refractory_pretreatment",
    "ROASTING": "refractory_pretreatment",
    "UFG": "refractory_pretreatment",
    # Pretreatment / leach variants
    "PREAERATION": "preaeration",
    "LEACH_CUVES": "leaching",
    "HEAP_LEACH": "leaching",
    "VAT_LEACH": "leaching",
    "CIL": "cil",
    "CIP": "cip",
    # Solid-liquid
    "EPAISSISSEUR": "thickener",
    "EPAISSISSEUR_HD": "thickener",
    "EPAISSISSEUR_CONC": "thickener",
    # ADR
    "ELUTION_AARL": "elution",
    "ELUTION_ZADRA": "elution",
    "ELECTROWINNING": "electrowinning",
    "FONDERIE": "smelting",
    # Detox
    "DETOX_INCO": "detox_inco",
    "DETOX_CARO": "detox_caro",
    "DETOX_PEROXIDE": "detox_peroxide",
    "DETOX_BERLINER": "detox_inco",
    "DETOX_OZONE": "detox_peroxide",
    "DETOX_BIO": "detox_caro",
    "DETOX_NEUTRALISATION": "detox_inco",
}

# Second-level aliases (legacy flowsheet / advisor labels not in catalog)
LEGACY_OP_ALIASES: dict[str, str] = {
    "CRUSH_GYRATORY": "GIRATOIRE",
    "CYCLONE": "HYDROCYCLONE",
    "LEACH_CIL": "CIL",
    "LEACH_CIP": "CIP",
    "FLOT_ROUGHER": "FLOTATION_ROUGHER",
    "GRAVITY_CONCENTRATOR": "GRAVITE_KNELSON",
    "REGRIND_MILL": "ISAMILL",
}

CATALOG_OP_MODEL_MAP: dict[str, str] = {
    **OP_MODEL_MAP,
    **{alias: OP_MODEL_MAP[target] for alias, target in LEGACY_OP_ALIASES.items() if target in OP_MODEL_MAP},
}

# Intentionally passthrough at simulation time (reagents, utilities, tailings)
PASSTHROUGH_OP_PREFIXES: tuple[str, ...] = (
    "REACTIF_",
    "BASSIN_",
    "TRAITEMENT_EFFLUENT",
    "TSF_",
    "PASTE_",
)
PASSTHROUGH_OP_CODES: frozenset[str] = frozenset({
    "FEED",
    "PRODUCT",
})

REFRACTORY_PRETREATMENT_OPS: frozenset[str] = frozenset({
    "BIOX",
    "POX",
    "ROASTING",
    "UFG",
})


def resolve_op_model(op_code: str) -> str | None:
    """Return simulator model name for a catalog op_code, or None if passthrough-only."""
    if not op_code:
        return None
    code = LEGACY_OP_ALIASES.get(op_code, op_code)
    model = OP_MODEL_MAP.get(code)
    if model:
        return model
    return OP_MODEL_MAP.get(code.split("_")[0])


def is_refractory_pretreatment(op_code: str) -> bool:
    return op_code in REFRACTORY_PRETREATMENT_OPS


def is_expected_passthrough(op_code: str) -> bool:
    if op_code in PASSTHROUGH_OP_CODES or op_code in REFRACTORY_PRETREATMENT_OPS:
        return True
    return any(op_code.startswith(p) for p in PASSTHROUGH_OP_PREFIXES)


def unmapped_catalog_ops(catalog_op_codes: set[str]) -> list[str]:
    """Catalog ops with no kinetic model (excluding expected passthrough)."""
    missing = []
    for op in sorted(catalog_op_codes):
        if is_expected_passthrough(op):
            continue
        if resolve_op_model(op) is None:
            missing.append(op)
    return missing


def catalog_coverage_report() -> dict:
    try:
        from .circuit_catalog import get_all_op_codes
    except ImportError:  # pragma: no cover
        from circuit_catalog import get_all_op_codes

    codes = get_all_op_codes()
    missing = unmapped_catalog_ops(codes)
    kinetic = [code for code in codes if resolve_op_model(code)]
    passthrough = [code for code in codes if is_expected_passthrough(code) and not resolve_op_model(code)]
    return {
        "catalog_count": len(codes),
        "kinetic_count": len(kinetic),
        "passthrough_count": len(passthrough),
        "gap_count": len(missing),
        "gaps": missing,
    }


try:
    from .circuit_catalog import get_all_op_codes as _get_all_op_codes
except ImportError:  # pragma: no cover
    from circuit_catalog import get_all_op_codes as _get_all_op_codes

for _op_code in _get_all_op_codes():
    _model = resolve_op_model(_op_code)
    if _model:
        CATALOG_OP_MODEL_MAP[_op_code] = _model
    elif is_expected_passthrough(_op_code):
        CATALOG_OP_MODEL_MAP[_op_code] = "passthrough"

del _get_all_op_codes, _model, _op_code
