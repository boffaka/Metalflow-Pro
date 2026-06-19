"""GISTM tailings — consequence classification, derived design criteria,
violation rules, and dam-break inundation (PFS-level).

Pure functions only — no DB, no I/O beyond loading the YAML matrix at import-time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

ConsequenceClass = Literal["low", "significant", "high", "very_high", "extreme"]

_CLASS_RANK: dict[str, int] = {
    "low": 0,
    "significant": 1,
    "high": 2,
    "very_high": 3,
    "extreme": 4,
}

DEFAULT_MATRIX_PATH = Path(__file__).parent.parent / "config" / "gistm_classification.yaml"


@dataclass(frozen=True)
class ConsequenceInputs:
    par_count: int
    env_damage_class: Literal["none", "minor", "moderate", "major", "catastrophic"]
    economic_damage_usd_m: float
    critical_infra_downstream: bool = False


@dataclass(frozen=True)
class DesignCriteria:
    consequence_class: ConsequenceClass
    idf_return_period_yr: int
    mde_return_period_yr: int
    fs_static_min: float
    fs_seismic_min: float
    fs_post_liquefaction_min: float
    allowed_construction_methods: list[str]
    pga_threshold_g: float | None


@dataclass(frozen=True)
class ClassificationMatrix:
    raw: dict


@dataclass(frozen=True)
class TSFDesignSnapshot:
    """Subset of tsf_design fields required by validation rules."""
    construction_method: str
    fs_static: float | None
    fs_seismic: float | None
    fs_post_liquefaction: float | None = None
    design_flood_return_yr: int | None = None
    site_pga_g: float | None = None


@dataclass(frozen=True)
class DesignBasisSnapshot:
    """Frozen criteria as activated for a given tsf_design."""
    consequence_class: ConsequenceClass
    idf_return_period_yr: int
    mde_return_period_yr: int
    fs_static_min: float
    fs_seismic_min: float
    fs_post_liquefaction_min: float
    allowed_construction_methods: list[str]
    pga_threshold_g: float | None


@dataclass(frozen=True)
class Violation:
    rule_code: str
    severity: Literal["error", "warning"]
    message: str
    observed_value: dict
    required_value: dict


@dataclass(frozen=True)
class InundationZone:
    peak_outflow_m3s: float
    danger_distance_km: float
    arrival_time_min_at_danger: float
    method: str
    disclaimer: str


def load_default_matrix() -> ClassificationMatrix:
    with DEFAULT_MATRIX_PATH.open("r", encoding="utf-8") as f:
        return ClassificationMatrix(raw=yaml.safe_load(f))


def _max_class(classes: list[ConsequenceClass]) -> ConsequenceClass:
    return max(classes, key=lambda c: _CLASS_RANK[c])


def _class_from_numeric(value: float, table: dict) -> ConsequenceClass:
    for cls in ("extreme", "very_high", "high", "significant", "low"):
        bounds = table[cls]
        lo = bounds.get("min", 0)
        hi = bounds.get("max")
        if value >= lo and (hi is None or value <= hi):
            return cls  # type: ignore[return-value]
    return "low"


def _class_from_par(par_count: int, matrix: ClassificationMatrix) -> ConsequenceClass:
    return _class_from_numeric(par_count, matrix.raw["classification"]["par_count"])


def _class_from_economic(value: float, matrix: ClassificationMatrix) -> ConsequenceClass:
    return _class_from_numeric(value, matrix.raw["classification"]["economic_damage_usd_m"])


def _class_from_env(env_class: str, matrix: ClassificationMatrix) -> ConsequenceClass:
    table = matrix.raw["classification"]["env_damage_class"]
    for cls in ("extreme", "very_high", "high", "significant", "low"):
        if env_class in table[cls]:
            return cls  # type: ignore[return-value]
    return "low"


def classify_consequence(
    inputs: ConsequenceInputs, matrix: ClassificationMatrix
) -> ConsequenceClass:
    """Apply 'highest classification wins' across PAR/env/economic + critical infra floor."""
    classes: list[ConsequenceClass] = [
        _class_from_par(inputs.par_count, matrix),
        _class_from_env(inputs.env_damage_class, matrix),
        _class_from_economic(inputs.economic_damage_usd_m, matrix),
    ]
    result = _max_class(classes)
    if inputs.critical_infra_downstream:
        floor = matrix.raw["classification"]["critical_infra_downstream"]["floor_when_true"]
        result = _max_class([result, floor])
    return result


def derive_design_criteria(
    cls: ConsequenceClass, matrix: ClassificationMatrix
) -> DesignCriteria:
    """Look up criteria for the class from the matrix."""
    row = matrix.raw["design_criteria"][cls]
    return DesignCriteria(
        consequence_class=cls,
        idf_return_period_yr=int(row["idf_yr"]),
        mde_return_period_yr=int(row["mde_yr"]),
        fs_static_min=float(row["fs_static"]),
        fs_seismic_min=float(row["fs_seismic"]),
        fs_post_liquefaction_min=float(row["fs_post_liq"]),
        allowed_construction_methods=list(row["methods"]),
        pga_threshold_g=row["pga_threshold_g"],
    )


# --- Validation rules --------------------------------------------------------


def _rule_no_active_basis(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> Violation | None:
    if basis is not None:
        return None
    return Violation(
        rule_code="NO_ACTIVE_BASIS",
        severity="warning",
        message=(
            "Aucun GISTM Design Basis actif pour ce projet. Le TSF est sauvegardé "
            "mais non validé contre une matrice de conséquence."
        ),
        observed_value={},
        required_value={},
    )


def _rule_fs_static_below_min(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> Violation | None:
    if basis is None or tsf.fs_static is None:
        return None
    if tsf.fs_static >= basis.fs_static_min:
        return None
    return Violation(
        rule_code="FS_STATIC_BELOW_MIN",
        severity="error",
        message=(
            f"FoS statique observé {tsf.fs_static:.2f} < minimum requis "
            f"{basis.fs_static_min:.2f} pour classe {basis.consequence_class}."
        ),
        observed_value={"fs_static": tsf.fs_static},
        required_value={"fs_static_min": basis.fs_static_min},
    )


def _rule_fs_seismic_below_min(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> Violation | None:
    if basis is None or tsf.fs_seismic is None:
        return None
    if tsf.fs_seismic >= basis.fs_seismic_min:
        return None
    return Violation(
        rule_code="FS_SEISMIC_BELOW_MIN",
        severity="error",
        message=(
            f"FoS sismique observé {tsf.fs_seismic:.2f} < minimum requis "
            f"{basis.fs_seismic_min:.2f} pour classe {basis.consequence_class}."
        ),
        observed_value={"fs_seismic": tsf.fs_seismic},
        required_value={"fs_seismic_min": basis.fs_seismic_min},
    )


def _rule_fs_post_liq_below_min(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> Violation | None:
    if basis is None or tsf.fs_post_liquefaction is None:
        return None
    if tsf.fs_post_liquefaction >= basis.fs_post_liquefaction_min:
        return None
    return Violation(
        rule_code="FS_POST_LIQ_BELOW_MIN",
        severity="error",
        message=(
            f"FoS post-liquéfaction {tsf.fs_post_liquefaction:.2f} < minimum requis "
            f"{basis.fs_post_liquefaction_min:.2f} pour classe {basis.consequence_class}."
        ),
        observed_value={"fs_post_liquefaction": tsf.fs_post_liquefaction},
        required_value={"fs_post_liquefaction_min": basis.fs_post_liquefaction_min},
    )


def _rule_construction_method_forbidden(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> Violation | None:
    if basis is None:
        return None
    if tsf.construction_method in basis.allowed_construction_methods:
        return None
    return Violation(
        rule_code="CONSTRUCTION_METHOD_FORBIDDEN",
        severity="error",
        message=(
            f"Méthode '{tsf.construction_method}' interdite pour classe "
            f"{basis.consequence_class}. Méthodes autorisées : "
            f"{', '.join(basis.allowed_construction_methods)}."
        ),
        observed_value={"construction_method": tsf.construction_method},
        required_value={"allowed_construction_methods": basis.allowed_construction_methods},
    )


def _rule_upstream_high_pga(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> Violation | None:
    if basis is None or tsf.construction_method != "upstream":
        return None
    if basis.pga_threshold_g is None or tsf.site_pga_g is None:
        return None
    if tsf.site_pga_g < basis.pga_threshold_g:
        return None
    return Violation(
        rule_code="UPSTREAM_HIGH_PGA",
        severity="error",
        message=(
            f"Méthode 'upstream' interdite pour classe {basis.consequence_class} "
            f"en zone PGA ≥ {basis.pga_threshold_g:.2f}g (site : {tsf.site_pga_g:.2f}g)."
        ),
        observed_value={"site_pga_g": tsf.site_pga_g},
        required_value={"pga_threshold_g": basis.pga_threshold_g},
    )


def _rule_idf_below_min(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> Violation | None:
    if basis is None or tsf.design_flood_return_yr is None:
        return None
    if tsf.design_flood_return_yr >= basis.idf_return_period_yr:
        return None
    return Violation(
        rule_code="IDF_BELOW_MIN",
        severity="warning",
        message=(
            f"Période de retour de crue de design {tsf.design_flood_return_yr} ans "
            f"< minimum requis {basis.idf_return_period_yr} ans pour classe "
            f"{basis.consequence_class}."
        ),
        observed_value={"design_flood_return_yr": tsf.design_flood_return_yr},
        required_value={"idf_return_period_yr": basis.idf_return_period_yr},
    )


_RULES = (
    _rule_no_active_basis,
    _rule_fs_static_below_min,
    _rule_fs_seismic_below_min,
    _rule_fs_post_liq_below_min,
    _rule_construction_method_forbidden,
    _rule_upstream_high_pga,
    _rule_idf_below_min,
)


def validate_tsf_design(
    tsf: TSFDesignSnapshot, basis: DesignBasisSnapshot | None
) -> list[Violation]:
    """Run all rules; return list of triggered violations (empty if compliant)."""
    return [v for rule in _RULES if (v := rule(tsf, basis)) is not None]


# --- Dam-break inundation (Froehlich 1995) -----------------------------------

_SLOPE_K = {"flat": 0.020, "gentle": 0.035, "moderate": 0.050, "steep": 0.075}


def estimate_dam_break_inundation(
    stored_volume_m3: float,
    dam_height_m: float,
    downstream_slope: Literal["flat", "gentle", "moderate", "steep"],
) -> InundationZone:
    """Froehlich (1995) peak outflow + simple slope-class downstream propagation.

    Qp = 0.607 × V^0.295 × H^1.24
    danger_distance_km = k × Qp^0.5  (k from slope class)
    arrival_time = distance × 60 / 30  (30 km/h floor wave estimate)
    """
    qp = 0.607 * (stored_volume_m3**0.295) * (dam_height_m**1.24)
    k = _SLOPE_K[downstream_slope]
    danger_km = k * (qp**0.5)
    arrival_min = danger_km * 60 / 30
    return InundationZone(
        peak_outflow_m3s=round(qp, 1),
        danger_distance_km=round(danger_km, 2),
        arrival_time_min_at_danger=round(arrival_min, 1),
        method="Froehlich 1995, simplified PFS-level",
        disclaimer=(
            "Usage indicatif PFS uniquement. FS/DFS requiert modélisation hydraulique "
            "complète (ex. HEC-RAS) avec topographie LiDAR aval."
        ),
    )
