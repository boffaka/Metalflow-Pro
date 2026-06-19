"""TDD tests for GISTM tailings module — engines/gistm.py."""

import pytest

try:
    from engines.gistm import (
        classify_consequence,
        ConsequenceInputs,
        DesignCriteria,
        derive_design_criteria,
        load_default_matrix,
        validate_tsf_design,
        TSFDesignSnapshot,
        DesignBasisSnapshot,
        Violation,
        estimate_dam_break_inundation,
    )
except ImportError:
    from backend.engines.gistm import (
        classify_consequence,
        ConsequenceInputs,
        DesignCriteria,
        derive_design_criteria,
        load_default_matrix,
        validate_tsf_design,
        TSFDesignSnapshot,
        DesignBasisSnapshot,
        Violation,
        estimate_dam_break_inundation,
    )


@pytest.fixture(scope="module")
def matrix():
    return load_default_matrix()


def test_classify_consequence_par_only(matrix):
    """PAR=850 alone (other inputs minimal) → very_high per default thresholds (101..1000)."""
    inputs = ConsequenceInputs(
        par_count=850,
        env_damage_class="none",
        economic_damage_usd_m=0,
        critical_infra_downstream=False,
    )
    assert classify_consequence(inputs, matrix) == "very_high"


def test_classify_consequence_env_drives_when_par_low(matrix):
    """PAR=0 (low) but env_damage=catastrophic → extreme (highest wins)."""
    inputs = ConsequenceInputs(
        par_count=0,
        env_damage_class="catastrophic",
        economic_damage_usd_m=0,
        critical_infra_downstream=False,
    )
    assert classify_consequence(inputs, matrix) == "extreme"


def test_classify_consequence_economic_drives(matrix):
    """PAR=0, env=none, economic_damage=$500M → very_high (100..1000)."""
    inputs = ConsequenceInputs(
        par_count=0,
        env_damage_class="none",
        economic_damage_usd_m=500,
        critical_infra_downstream=False,
    )
    assert classify_consequence(inputs, matrix) == "very_high"


def test_classify_consequence_critical_infra_floor(matrix):
    """All inputs minimal but critical_infra_downstream=True → high (floor)."""
    inputs = ConsequenceInputs(
        par_count=0,
        env_damage_class="none",
        economic_damage_usd_m=0,
        critical_infra_downstream=True,
    )
    assert classify_consequence(inputs, matrix) == "high"


def test_classify_consequence_critical_infra_does_not_lower(matrix):
    """If other inputs already extreme, critical_infra floor doesn't bring it down."""
    inputs = ConsequenceInputs(
        par_count=2000,
        env_damage_class="catastrophic",
        economic_damage_usd_m=2000,
        critical_infra_downstream=True,
    )
    assert classify_consequence(inputs, matrix) == "extreme"


def test_derive_design_criteria_high(matrix):
    """High class → IDF=5000y, MDE=5000y, FS_static=1.5, methods=[downstream, centreline]."""
    criteria = derive_design_criteria("high", matrix)
    assert criteria.idf_return_period_yr == 5000
    assert criteria.mde_return_period_yr == 5000
    assert criteria.fs_static_min == 1.5
    assert criteria.fs_seismic_min == 1.2
    assert criteria.fs_post_liquefaction_min == 1.1
    assert set(criteria.allowed_construction_methods) == {"downstream", "centreline"}
    assert criteria.pga_threshold_g == 0.10


def test_derive_design_criteria_low_no_pga_threshold(matrix):
    """Low class has pga_threshold_g = None (no upstream restriction)."""
    criteria = derive_design_criteria("low", matrix)
    assert criteria.pga_threshold_g is None
    assert "upstream" in criteria.allowed_construction_methods


def test_derive_design_criteria_extreme_only_downstream(matrix):
    """Extreme class allows only downstream construction method."""
    criteria = derive_design_criteria("extreme", matrix)
    assert criteria.allowed_construction_methods == ["downstream"]


# --- Helpers for validation tests --------------------------------------------


def _basis(matrix, cls="high"):
    crit = derive_design_criteria(cls, matrix)
    return DesignBasisSnapshot(
        consequence_class=cls,
        idf_return_period_yr=crit.idf_return_period_yr,
        mde_return_period_yr=crit.mde_return_period_yr,
        fs_static_min=crit.fs_static_min,
        fs_seismic_min=crit.fs_seismic_min,
        fs_post_liquefaction_min=crit.fs_post_liquefaction_min,
        allowed_construction_methods=crit.allowed_construction_methods,
        pga_threshold_g=crit.pga_threshold_g,
    )


def _tsf(**overrides):
    defaults = dict(
        construction_method="downstream",
        fs_static=1.6,
        fs_seismic=1.3,
        fs_post_liquefaction=1.2,
        design_flood_return_yr=10000,
        site_pga_g=0.05,
    )
    defaults.update(overrides)
    return TSFDesignSnapshot(**defaults)


# --- Validation rules --------------------------------------------------------


def test_no_active_basis_emits_warning(matrix):
    """validate_tsf_design with basis=None returns single NO_ACTIVE_BASIS warning."""
    violations = validate_tsf_design(_tsf(), basis=None)
    assert len(violations) == 1
    assert violations[0].rule_code == "NO_ACTIVE_BASIS"
    assert violations[0].severity == "warning"


def test_compliant_design_emits_no_violation(matrix):
    """All checks pass → no violation."""
    assert validate_tsf_design(_tsf(), _basis(matrix, "high")) == []


def test_fs_static_below_min_triggers_error(matrix):
    """fs_static=1.30 against High class (min 1.50) → FS_STATIC_BELOW_MIN error."""
    violations = validate_tsf_design(_tsf(fs_static=1.30), _basis(matrix, "high"))
    codes = [v.rule_code for v in violations]
    assert "FS_STATIC_BELOW_MIN" in codes
    v = next(v for v in violations if v.rule_code == "FS_STATIC_BELOW_MIN")
    assert v.severity == "error"
    assert v.observed_value == {"fs_static": 1.30}
    assert v.required_value == {"fs_static_min": 1.50}


def test_fs_seismic_below_min_triggers_error(matrix):
    violations = validate_tsf_design(_tsf(fs_seismic=1.0), _basis(matrix, "high"))
    assert "FS_SEISMIC_BELOW_MIN" in [v.rule_code for v in violations]


def test_fs_post_liquefaction_below_min(matrix):
    violations = validate_tsf_design(_tsf(fs_post_liquefaction=1.0), _basis(matrix, "high"))
    assert "FS_POST_LIQ_BELOW_MIN" in [v.rule_code for v in violations]


def test_construction_method_forbidden(matrix):
    """upstream method against High class → CONSTRUCTION_METHOD_FORBIDDEN."""
    violations = validate_tsf_design(_tsf(construction_method="upstream"), _basis(matrix, "high"))
    codes = [v.rule_code for v in violations]
    assert "CONSTRUCTION_METHOD_FORBIDDEN" in codes


def test_upstream_above_pga_threshold(matrix):
    """upstream + PGA above threshold → UPSTREAM_HIGH_PGA error.

    Use 'significant' class which permits upstream but with pga_threshold_g=0.10.
    """
    violations = validate_tsf_design(
        _tsf(construction_method="upstream", site_pga_g=0.15),
        _basis(matrix, "significant"),
    )
    assert "UPSTREAM_HIGH_PGA" in [v.rule_code for v in violations]


def test_idf_below_min_emits_warning(matrix):
    """design_flood_return_yr=2000 < 5000 (High class) → IDF_BELOW_MIN warning."""
    violations = validate_tsf_design(_tsf(design_flood_return_yr=2000), _basis(matrix, "high"))
    v = next(v for v in violations if v.rule_code == "IDF_BELOW_MIN")
    assert v.severity == "warning"


# --- Dam-break inundation (Froehlich 1995) -----------------------------------


def test_froehlich_known_value():
    """V=10⁷ m³, H=30 m → Qp ≈ 4,800 m³/s per Froehlich (1995).

    Qp = 0.607 × V^0.295 × H^1.24
       = 0.607 × (10⁷)^0.295 × 30^1.24
       ≈ 4,784 m³/s
    """
    z = estimate_dam_break_inundation(
        stored_volume_m3=1e7, dam_height_m=30, downstream_slope="moderate"
    )
    assert 4_500 <= z.peak_outflow_m3s <= 5_100


def test_froehlich_monotonic_in_volume():
    """Larger stored volume → larger peak outflow (monotonic in V)."""
    small = estimate_dam_break_inundation(1e6, 30, "moderate")
    big = estimate_dam_break_inundation(1e8, 30, "moderate")
    assert big.peak_outflow_m3s > small.peak_outflow_m3s


def test_froehlich_monotonic_in_height():
    """Taller dam → larger peak outflow (monotonic in H)."""
    short = estimate_dam_break_inundation(1e7, 10, "moderate")
    tall = estimate_dam_break_inundation(1e7, 50, "moderate")
    assert tall.peak_outflow_m3s > short.peak_outflow_m3s


def test_dam_break_returns_disclaimer():
    """Output includes PFS-only disclaimer for FS/DFS."""
    z = estimate_dam_break_inundation(
        stored_volume_m3=1e6, dam_height_m=20, downstream_slope="gentle"
    )
    assert "PFS" in z.disclaimer
    assert "HEC-RAS" in z.disclaimer or "modélisation hydraulique" in z.disclaimer


def test_dam_break_steep_slope_propagates_further():
    """Steeper slopes → larger danger distance for same volume/height."""
    flat = estimate_dam_break_inundation(1e7, 30, "flat")
    steep = estimate_dam_break_inundation(1e7, 30, "steep")
    assert steep.danger_distance_km > flat.danger_distance_km
