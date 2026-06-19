"""Pydantic schemas for the Geometallurgical Intelligence module (GADE · PRD · IMBO).

All recovery values in API responses are represented as fractions [0, 1].
Input values may be expressed in fraction (0–1) or percent (0–100); they are
normalised to fraction before serialisation via field validators.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_recovery(v: float | None) -> float | None:
    """Normalise a recovery value to fraction [0, 1].

    Accepts both fraction (0–1) and percent (0–100) inputs.
    Values > 1 are assumed to be percentages and divided by 100.
    """
    if v is None:
        return None
    if v > 1.0:
        v = v / 100.0
    # Clamp to [0, 1] after conversion
    return max(0.0, min(1.0, v))


# ===========================================================================
# GADE Schemas
# ===========================================================================


class GadeSessionCreate(BaseModel):
    """Parameters to create a new GADE domaining session."""

    name: str
    algorithm: Literal["kmeans", "gmm", "hdbscan", "hierarchical"]
    n_domains: Optional[int] = None
    features: list[str]
    feature_weights: Optional[dict[str, float]] = None
    normalization: Literal["zscore", "minmax", "robust"] = "robust"
    sample_filter: Optional[dict[str, Any]] = None


class GadeSessionResponse(BaseModel):
    """Immediate response after submitting a GADE session (job is asynchronous)."""

    session_id: str
    job_id: str
    status: str


class GeometDomainResponse(BaseModel):
    """Full representation of a metallurgical domain produced by GADE."""

    model_config = ConfigDict(extra="ignore")

    id: str
    session_id: str
    project_id: str
    domain_code: str
    label: Optional[str] = None
    color: str
    n_samples: int
    pct_of_total: float
    statistics: dict[str, Any]
    metallurgical_signature: dict[str, Any]
    discriminating_features: list[Any]


class RecoveryModelResponse(BaseModel):
    """Serialised representation of a trained recovery model."""

    model_config = ConfigDict(extra="ignore")

    id: str
    domain_id: str
    model_type: str
    target_variable: str
    training_samples: Optional[int] = None
    test_r2: Optional[float] = None
    test_rmse: Optional[float] = None
    test_mae: Optional[float] = None
    cross_val_scores: list[float] = Field(default_factory=list)
    feature_importances: dict[str, float] = Field(default_factory=dict)
    model_artifact_path: Optional[str] = None
    trained_at: Optional[str] = None
    is_active: bool = True


class TrainRecoveryModelsRequest(BaseModel):
    """Request body to trigger recovery model training for one or more domains."""

    domain_ids: Optional[list[str]] = None
    model_type: Literal["random_forest", "xgboost"] = "random_forest"
    target_variables: list[str] = Field(default_factory=lambda: ["au_recovery"])


class PredictDomainRequest(BaseModel):
    """Batch of samples to classify into domains and predict recovery."""

    samples: list[dict[str, Any]]


class AssignBlockModelRequest(BaseModel):
    """Request to assign an entire block model to domaining session."""

    block_model_id: str


# ===========================================================================
# PRD Schemas
# ===========================================================================


class MinePlanCreate(BaseModel):
    """Create a new mine plan."""

    name: str
    mine_life_years: int = 15


class AnnualScheduleCreate(BaseModel):
    """Annual mining schedule entry."""

    year: int
    total_ore_mined: float
    total_waste_mined: float
    strip_ratio: float
    block_ids_mined: list[str] = Field(default_factory=list)
    feed_to_plant: float


class PrdAnalysisCreate(BaseModel):
    """Parameters to create a PRD analysis (LOM prediction)."""

    name: str
    mine_plan_id: str
    domaining_session_id: str
    monte_carlo_runs: int = Field(default=500, ge=100, le=5000)


class WhatIfRequest(BaseModel):
    """What-if scenario override parameters."""

    year: int
    override_domain_mix: Optional[list[dict[str, Any]]] = None
    override_feed_rate: Optional[float] = None


class AnnualPredictionResponse(BaseModel):
    """Predicted metallurgical performance for a single LOM year.

    Recovery values are normalised to fraction [0, 1].
    """

    model_config = ConfigDict(extra="ignore")

    year: int
    domain_mix: list[dict[str, Any]]
    blended_feed_grade: Optional[float] = None
    blended_bwi: Optional[float] = None
    predicted_recovery: Optional[float] = None
    recovery_ci: dict[str, float] = Field(
        default_factory=dict,
        description="Confidence interval keys: p10, p50, p90 — all fractions [0, 1].",
    )
    predicted_gold_produced_oz: Optional[float] = None
    predicted_gold_oz_p10: Optional[float] = None
    predicted_gold_oz_p90: Optional[float] = None

    @field_validator("predicted_recovery", mode="before")
    @classmethod
    def _norm_predicted_recovery(cls, v: float | None) -> float | None:
        return _normalise_recovery(v)

    @field_validator("recovery_ci", mode="before")
    @classmethod
    def _norm_recovery_ci(cls, v: dict | None) -> dict:
        if not v:
            return {}
        return {k: _normalise_recovery(float(val)) for k, val in v.items()}


class CriticalPeriodResponse(BaseModel):
    """A period of concern identified by the PRD engine."""

    model_config = ConfigDict(extra="ignore")

    id: str
    year_start: int
    year_end: int
    severity: Literal["low", "medium", "high", "critical"]
    trigger_type: str
    description: Optional[str] = None
    predicted_recovery_drop: Optional[float] = None
    economic_impact_musd: Optional[float] = None
    recommended_actions: list[Any] = Field(default_factory=list)


class LomSummaryResponse(BaseModel):
    """Life-of-mine summary statistics from a PRD analysis."""

    model_config = ConfigDict(extra="ignore")

    total_ore_processed: Optional[float] = None
    total_gold_produced_oz: Optional[float] = None
    average_recovery_lom: Optional[float] = None
    recovery_range: dict[str, float] = Field(
        default_factory=dict,
        description="Keys: min, max — values as fractions [0, 1].",
    )
    n_critical_periods: Optional[int] = None
    worst_year: Optional[int] = None
    best_year: Optional[int] = None

    @field_validator("average_recovery_lom", mode="before")
    @classmethod
    def _norm_avg_recovery(cls, v: float | None) -> float | None:
        return _normalise_recovery(v)

    @field_validator("recovery_range", mode="before")
    @classmethod
    def _norm_recovery_range(cls, v: dict | None) -> dict:
        if not v:
            return {}
        return {k: _normalise_recovery(float(val)) for k, val in v.items()}


# ===========================================================================
# IMBO Schemas
# ===========================================================================


class BlendConstraintCreate(BaseModel):
    """Define a hard or soft blend constraint."""

    name: str
    parameter: str
    operator: Literal["lte", "gte", "eq", "between"]
    value: float
    value_max: Optional[float] = None
    unit: str = ""
    severity: Literal["hard", "soft"] = "hard"
    penalty_per_unit: Optional[float] = None
    description: str = ""


class BlendConstraintResponse(BlendConstraintCreate):
    """Persisted blend constraint with its database id."""

    model_config = ConfigDict(extra="ignore")

    id: str


class BlendSourceCreate(BaseModel):
    """A single ore source (domain) available for blending."""

    label: str
    domain_id: Optional[str] = None
    tonnage_available: float
    tonnage_min: Optional[float] = None
    tonnage_max: Optional[float] = None
    au_grade: float
    bwi: float = 14.0
    s_sulphide: float = 1.0
    cu_ppm: float = 50.0
    carbon_pct: float = 0.0
    preg_robbing_index: float = 0.0
    predicted_recovery: float = Field(
        default=89.0,
        description="Recovery value — can be percent (0–100) or fraction (0–1); "
        "stored internally as fraction after normalisation.",
    )
    predicted_cn_consumption: float = 0.5
    mining_cost_per_tonne: float = 0.0
    haulage_cost_per_tonne: float = 0.0

    @field_validator("predicted_recovery", mode="before")
    @classmethod
    def _norm_predicted_recovery(cls, v: float) -> float:
        result = _normalise_recovery(float(v))
        return result if result is not None else 0.0


class BlendSessionCreate(BaseModel):
    """Parameters to create an IMBO blend optimisation session."""

    name: str
    prd_analysis_id: Optional[str] = None
    target_year: int
    target_variable: Literal[
        "maximize_au_oz",
        "maximize_recovery",
        "minimize_opex",
        "maximize_npv",
    ]
    gold_price: float = Field(default=3200.0, gt=0)
    constraint_ids: list[str]
    sources: list[BlendSourceCreate]


class BlendOptimizationResultResponse(BaseModel):
    """Blend optimisation result returned by the IMBO engine.

    Predicted recovery values are in fraction [0, 1].
    """

    model_config = ConfigDict(extra="ignore")

    session_id: str
    solver: Optional[str] = None
    status: str
    solve_time_ms: Optional[float] = None
    optimal_allocation: Optional[list[dict[str, Any]]] = None
    blended_properties: dict[str, Any] = Field(default_factory=dict)
    predicted_recovery: Optional[float] = None
    predicted_gold_oz: Optional[float] = None
    constraint_analysis: list[dict[str, Any]] = Field(default_factory=list)
    vs_baseline: dict[str, Any] = Field(default_factory=dict)

    @field_validator("predicted_recovery", mode="before")
    @classmethod
    def _norm_predicted_recovery(cls, v: float | None) -> float | None:
        return _normalise_recovery(v)


# ===========================================================================
# LIMS Extended Schemas
# ===========================================================================


class LimsImportResponse(BaseModel):
    """Response after triggering an asynchronous LIMS import job."""

    job_id: str
    status: str


class LimsStatsResponse(BaseModel):
    """Descriptive statistics for LIMS columns.

    Each column entry contains: mean, std, median, p10, p90, n_missing.
    """

    n_samples: int
    stats_by_column: dict[str, dict[str, float | int | None]]


class LimsColumnSchemaResponse(BaseModel):
    """Available columns in LIMS data with basic metadata.

    Each column entry contains: name, type, n_values, n_missing, example_values.
    """

    columns: list[dict[str, Any]]
