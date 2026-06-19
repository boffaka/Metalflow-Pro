"""Pydantic schemas for /capex endpoints. Spec §7."""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class EquipmentItemOut(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    category: str
    name: str
    template_key: Optional[str] = None
    typical_power_kw: Optional[float] = None
    price_cad: float
    is_overridden: bool
    seeded_from_template: bool
    parametric_alpha: Optional[float] = None
    parametric_beta: Optional[float] = None


class FactorsOut(BaseModel):
    indirect_pct: float
    epcm_pct: float
    contingency_pct: float
    overridden: dict  # {indirect: bool, epcm: bool, contingency: bool}


class TotalsOut(BaseModel):
    direct_cad: float
    indirect_cad: float
    epcm_cad: float
    contingency_cad: float
    total_cad: float


class CapexModuleOut(BaseModel):
    circuit_type: str
    equipment: list[EquipmentItemOut]
    factors: FactorsOut
    totals: TotalsOut


class EquipmentIn(BaseModel):
    """Manual equipment add. is_overridden is forced to true server-side."""
    category: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    price_cad: float = Field(ge=0)
    typical_power_kw: Optional[float] = Field(default=None, ge=0)


class EquipmentPatch(BaseModel):
    category: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    price_cad: Optional[float] = Field(default=None, ge=0)
    typical_power_kw: Optional[float] = Field(default=None, ge=0)


class FactorsPatch(BaseModel):
    indirect_pct: Optional[float] = Field(default=None, ge=0, le=2)
    epcm_pct: Optional[float] = Field(default=None, ge=0, le=1)
    contingency_pct: Optional[float] = Field(default=None, ge=0, le=1)


class SeedRequest(BaseModel):
    circuit_type: str
    force: bool = False


class TemplateListItem(BaseModel):
    key: str
    label: str
    equipment_count: int
