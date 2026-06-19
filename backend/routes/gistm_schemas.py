"""Pydantic schemas for /gistm endpoints — GISTM tailings module."""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# --- Inputs ------------------------------------------------------------------


class ConsequenceInputsIn(BaseModel):
    """Inputs to the consequence classifier (matches engines.gistm.ConsequenceInputs)."""
    par_count: int = Field(ge=0, description="Population At Risk")
    env_damage_class: Literal["none", "minor", "moderate", "major", "catastrophic"]
    economic_damage_usd_m: float = Field(ge=0, description="Economic damage in millions USD")
    critical_infra_downstream: bool = False


class DesignBasisCreateIn(ConsequenceInputsIn):
    """Body for POST /design-basis : classification inputs + optional notes."""
    notes: Optional[str] = Field(default=None, max_length=4000)


class OverrideCreateIn(BaseModel):
    """Body for POST /violations/{vid}/override."""
    justification: str = Field(min_length=50, max_length=4000)


# --- Outputs -----------------------------------------------------------------


class DesignCriteriaOut(BaseModel):
    consequence_class: Literal["low", "significant", "high", "very_high", "extreme"]
    idf_return_period_yr: int
    mde_return_period_yr: int
    fs_static_min: float
    fs_seismic_min: float
    fs_post_liquefaction_min: float
    allowed_construction_methods: list[str]
    pga_threshold_g: Optional[float] = None


class DesignBasisOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    project_id: str
    version: int
    status: Literal["draft", "active", "superseded"]
    par_count: int
    env_damage_class: str
    economic_damage_usd_m: float
    critical_infra_downstream: bool
    consequence_class: str
    idf_return_period_yr: int
    mde_return_period_yr: int
    fs_static_min: float
    fs_seismic_min: float
    fs_post_liquefaction_min: float
    allowed_construction_methods: list[str]
    pga_threshold_g: Optional[float]
    created_by: str
    created_at: datetime
    activated_by: Optional[str]
    activated_at: Optional[datetime]
    notes: Optional[str]


class OverrideOut(BaseModel):
    id: str
    violation_id: str
    justification: str
    signed_by: str
    signed_at: datetime


class ViolationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = None
    rule_code: str
    severity: Literal["error", "warning"]
    message: str
    observed_value: dict
    required_value: dict
    override: Optional[OverrideOut] = None


class DesignBasisHistoryOut(BaseModel):
    items: list[DesignBasisOut]
    total: int
