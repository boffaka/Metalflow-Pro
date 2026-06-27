"""
MPDPMS — Pydantic request/response models.
Centralises all schemas used by API endpoints.
"""
from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator
try:
    from .security import validate_password_strength
    from . import config as cfg
except ImportError:  # pragma: no cover - supports direct script imports
    from security import validate_password_strength
    import config as cfg


# ─── Auth ────────────────────────────────────────────────────────────────────

class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str
    password: str = Field(..., min_length=1)


class UserInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str
    email: str
    role: str
    full_name: Optional[str] = None


class LoginOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str
    refresh_token: str | None = None
    token_type: str = "bearer"
    user: UserInfo | None = None


class RefreshTokenIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    refresh_token: str = ""


# ─── Users / Admin ───────────────────────────────────────────────────────────


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=200)
    role: str = Field(
        default="Read-only",
        description="One of: Process Engineer, Metallurgist, Project Manager, Cost Engineer, Reviewer, Read-only",
    )

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_strength(value)


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: Optional[str] = None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    new_password: str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return validate_password_strength(value)


# ─── Projects ───────────────────────────────────────────────────────────────

class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_name: str = Field(..., max_length=200)
    project_code: str = Field(..., max_length=50)
    target_tph: Optional[float] = Field(default=None, ge=0, le=100_000)
    gold_grade_g_t: Optional[float] = Field(
        default=None,
        ge=0,
        le=500,
        description="Teneur en or totale du minerai (g/t Au), tête de traitement.",
    )
    status: Optional[str] = Field(default="SCOPING", max_length=50)
    capex_musd: Optional[float] = Field(default=None, ge=0)
    project_owner: Optional[str] = Field(default=None, max_length=200)
    commodity: Optional[str] = Field(default="Au", max_length=20)
    location: Optional[str] = Field(default=None, max_length=300)
    capacity_mtpa: Optional[float] = Field(default=None, ge=0)
    process_options: Optional[str] = Field(default=None, max_length=2000)
    # Economic parameters
    gold_price_usd_oz: Optional[float] = Field(default=cfg.DEFAULT_GOLD_PRICE_USD_OZ, ge=0)
    discount_rate_pct: Optional[float] = Field(default=5, ge=0, le=100)
    mine_life_years: Optional[int] = Field(default=10, ge=0)
    operating_hours_day: Optional[float] = Field(
        default=24.0,
        ge=0,
        le=24,
        description="Heures d'opération nominale / jour (24 h = continu). Utilisé pour extrapoler t/h → t/j.",
    )
    availability_pct: Optional[float] = Field(
        default=92,
        ge=0,
        le=100,
        description="Disponibilité équipement / calendrier (%) pour dimensionnement et OPEX.",
    )
    electricity_rate: Optional[float] = Field(
        default=0.075,
        ge=0,
        description="Tarif électricité (USD/kWh), base OPEX énergétique.",
    )


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    project_name: Optional[str] = Field(default=None, max_length=200)
    project_code: Optional[str] = Field(default=None, max_length=50)
    target_tph: Optional[float] = Field(default=None, ge=0, le=100_000)
    gold_grade_g_t: Optional[float] = Field(default=None, ge=0, le=500)
    status: Optional[str] = Field(default=None, max_length=50)
    capex_musd: Optional[float] = Field(default=None, ge=0)
    project_owner: Optional[str] = Field(default=None, max_length=200)
    commodity: Optional[str] = Field(default=None, max_length=20)
    location: Optional[str] = Field(default=None, max_length=300)
    capacity_mtpa: Optional[float] = Field(default=None, ge=0)
    process_options: Optional[str] = Field(default=None, max_length=2000)
    # Economic parameters
    gold_price_usd_oz: Optional[float] = Field(default=None, ge=0)
    discount_rate_pct: Optional[float] = Field(default=None, ge=0, le=100)
    mine_life_years: Optional[int] = Field(default=None, ge=0)
    operating_hours_day: Optional[float] = Field(default=None, ge=0, le=24)
    availability_pct: Optional[float] = Field(default=None, ge=0, le=100)
    electricity_rate: Optional[float] = Field(default=None, ge=0)


# ─── LIMS ────────────────────────────────────────────────────────────────────

class SampleIn(BaseModel):
    # `ignore` instead of `forbid` so unknown columns from the Excel template
    # (e.g. when the spec has new fields that haven't been added to the DB yet)
    # don't kill the whole bulk import; the route's whitelist still gates which
    # columns reach the INSERT.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    sample_id_display: str = Field(..., max_length=100)
    phase: Optional[str] = Field(default="SCOPING", max_length=50)
    sample_type: Optional[str] = Field(default=None, max_length=100)
    lithology: Optional[str] = Field(default=None, max_length=100)
    provenance: Optional[str] = Field(default=None, max_length=200)
    mass_kg: Optional[float] = Field(default=None, ge=0)
    representativity: Optional[str] = Field(default=None, max_length=100)
    waste_rock_dilution_pct: Optional[float] = Field(default=None, ge=0, le=100)
    # Extended SAM-00 fields (Excel template import)
    source_horizon: Optional[str] = Field(default=None, max_length=200)
    depth_interval: Optional[str] = Field(default=None, max_length=100)
    total_mass_kg: Optional[float] = Field(default=None, ge=0)
    sent_mass_kg: Optional[float] = Field(default=None, ge=0)
    # Date strings: 40 chars covers JS Date.toISOString() ("2026-01-15T00:00:00.000Z")
    # and locale strings without coercing to a strict date type (DB column is
    # TIMESTAMPTZ which accepts most ISO-ish formats).
    collection_date: Optional[str] = Field(default=None, max_length=40)
    reception_date: Optional[str] = Field(default=None, max_length=40)
    collection_method: Optional[str] = Field(default=None, max_length=200)
    qaqc_protocol: Optional[str] = Field(default=None, max_length=200)
    crm_standard: Optional[str] = Field(default=None, max_length=100)
    duplicate_freq: Optional[str] = Field(default=None, max_length=50)
    blank_freq: Optional[str] = Field(default=None, max_length=50)
    packaging: Optional[str] = Field(default=None, max_length=200)
    oxidation_state: Optional[str] = Field(default=None, max_length=50)
    domain: Optional[str] = Field(default=None, max_length=100)
    status: Optional[str] = Field(default="Reçu", max_length=50)
    observations: Optional[str] = Field(default=None, max_length=2000)


class CampaignIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(..., max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: str = Field(default="planned", max_length=50)
    test_type: Optional[str] = Field(default="other", max_length=50)
    ore_types: Optional[str] = Field(default=None, max_length=500)
    protocol: Optional[str] = Field(default=None, max_length=500)
    laboratory: Optional[str] = Field(default=None, max_length=200)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    cost_usd: Optional[float] = Field(default=None, ge=0)
    results_summary: Optional[str] = Field(default=None, max_length=4000)


class CampaignPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = Field(default=None, max_length=50)
    test_type: Optional[str] = Field(default=None, max_length=50)
    ore_types: Optional[str] = Field(default=None, max_length=500)
    protocol: Optional[str] = Field(default=None, max_length=500)
    laboratory: Optional[str] = Field(default=None, max_length=200)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    cost_usd: Optional[float] = Field(default=None, ge=0)
    results_summary: Optional[str] = Field(default=None, max_length=4000)


class GeometDomainIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    domain_code: str = Field(..., max_length=50)
    domain_name: str = Field(..., max_length=200)
    lithology: Optional[str] = Field(default=None, max_length=100)
    alteration: Optional[str] = Field(default=None, max_length=100)
    mineralization_style: Optional[str] = Field(default=None, max_length=100)
    oxidation_state: Optional[str] = Field(default=None, max_length=100)
    hardness_class: Optional[str] = Field(default=None, max_length=100)
    variability_index: Optional[float] = Field(default=None, ge=0)
    representative: Optional[bool] = True
    notes: Optional[str] = Field(default=None, max_length=2000)


class GeometDomainPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    domain_name: Optional[str] = Field(default=None, max_length=200)
    lithology: Optional[str] = Field(default=None, max_length=100)
    alteration: Optional[str] = Field(default=None, max_length=100)
    mineralization_style: Optional[str] = Field(default=None, max_length=100)
    oxidation_state: Optional[str] = Field(default=None, max_length=100)
    hardness_class: Optional[str] = Field(default=None, max_length=100)
    variability_index: Optional[float] = Field(default=None, ge=0)
    representative: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class SampleDomainAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    domain_id: str
    confidence_pct: Optional[float] = Field(default=100, ge=0, le=100)
    notes: Optional[str] = Field(default=None, max_length=1000)


class CompositeIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    composite_code: str = Field(..., max_length=50)
    composite_name: str = Field(..., max_length=200)
    purpose: str = Field(..., max_length=100)
    campaign_id: Optional[str] = None
    domain_id: Optional[str] = None
    target_mass_kg: Optional[float] = Field(default=None, ge=0)
    actual_mass_kg: Optional[float] = Field(default=None, ge=0)
    blend_method: Optional[str] = Field(default=None, max_length=200)
    representativity_score: Optional[float] = Field(default=None, ge=0, le=100)
    qa_status: Optional[str] = Field(default="draft", max_length=50)
    notes: Optional[str] = Field(default=None, max_length=2000)


class CompositePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    composite_name: Optional[str] = Field(default=None, max_length=200)
    purpose: Optional[str] = Field(default=None, max_length=100)
    campaign_id: Optional[str] = None
    domain_id: Optional[str] = None
    target_mass_kg: Optional[float] = Field(default=None, ge=0)
    actual_mass_kg: Optional[float] = Field(default=None, ge=0)
    blend_method: Optional[str] = Field(default=None, max_length=200)
    representativity_score: Optional[float] = Field(default=None, ge=0, le=100)
    qa_status: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=2000)


class CompositeSampleLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sample_id: str
    mass_kg: Optional[float] = Field(default=None, ge=0)
    weight_pct: Optional[float] = Field(default=None, ge=0, le=100)
    role_in_composite: Optional[str] = Field(default=None, max_length=100)


# ─── Stage-Gates ─────────────────────────────────────────────────────────────

class ChecklistItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    domain: str = "General"
    item_name: str
    target_pct: Optional[int] = Field(default=100, ge=0, le=100)
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


class ChecklistItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    status: Optional[str] = None
    is_done: Optional[bool] = None
    proof_link: Optional[str] = None
    notes: Optional[str] = None
    assigned_to: Optional[str] = None


class StageGateApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    comment: Optional[str] = None


# ─── Risks (EPCM) ───────────────────────────────────────────────────────────

class RiskIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    risk_number: Optional[str] = None
    description: str
    cause: str | None = None
    consequence: str | None = None
    probability: int = Field(..., ge=1, le=5)
    impact: int = Field(..., ge=1, le=5)
    mitigation: str | None = None
    preventive_actions: str | None = None
    corrective_actions: str | None = None
    alert_indicators: str | None = None
    owner: str | None = None
    category: str | None = None
    phase: Optional[str] = None
    due_date: str | None = None
    review_date: str | None = None
    stage_id: str | None = None
    is_gate_blocker: bool | None = False


class RiskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    risk_number: Optional[str] = None
    description: Optional[str] = None
    cause: Optional[str] = None
    consequence: Optional[str] = None
    probability: Optional[int] = Field(None, ge=1, le=5)
    impact: Optional[int] = Field(None, ge=1, le=5)
    mitigation: Optional[str] = None
    preventive_actions: Optional[str] = None
    corrective_actions: Optional[str] = None
    alert_indicators: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[str] = None
    category: Optional[str] = None
    phase: Optional[str] = None
    due_date: Optional[str] = None
    review_date: Optional[str] = None
    stage_id: Optional[str] = None
    is_gate_blocker: Optional[bool] = None


# ─── Equipment ───────────────────────────────────────────────────────────────

class EquipIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    equipment_tag: str
    equipment_type: str
    power_installed_kw: Optional[float] = Field(default=None, ge=0)
    design_capacity_t_h: Optional[float] = Field(default=None, ge=0)
    is_long_lead: Optional[bool] = False


class EquipPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    equipment_tag: Optional[str] = None
    equipment_type: Optional[str] = None
    power_installed_kw: Optional[float] = Field(default=None, ge=0)
    design_capacity_t_h: Optional[float] = Field(default=None, ge=0)
    is_long_lead: Optional[bool] = None


# ─── Circuit Designer ────────────────────────────────────────────────────────

class CircuitTemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(..., min_length=1, max_length=200,
                      description="Nom unique du template de circuit")


class OperationIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    op_code: str = Field(..., min_length=1, max_length=100,
                         description="Code de l'opération unitaire (doit exister dans le catalogue)")


class OperationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    sort_order: Optional[int] = Field(default=None, ge=0,
                                      description="Ordre d'affichage (>= 0)")
    enabled: Optional[bool] = Field(default=None,
                                    description="Active ou désactive l'opération")


class CriteriaUpdateItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., description="UUID du critère à mettre à jour")
    version: int = Field(..., ge=0, description="Version pour le verrouillage optimiste")
    design_value: Optional[float] = Field(default=None)
    nominal_value: Optional[float] = Field(default=None)
    min_value: Optional[float] = Field(default=None)
    max_value: Optional[float] = Field(default=None)
    source_code: Optional[str] = Field(default=None, max_length=10)
    revision: Optional[str] = Field(default=None, max_length=10)
    author: Optional[str] = Field(default=None, max_length=100)
    comments: Optional[str] = Field(default=None, max_length=2000)


class BulkCriteriaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    updates: list[CriteriaUpdateItem] = Field(..., min_length=1,
                                              description="Liste de critères à mettre à jour (minimum 1)")


# ─── Mass Balance v2 ─────────────────────────────────────────────────────────

class MbStreamPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(..., ge=0,
                         description="Version actuelle du flux (verrouillage optimiste)")
    solids_tph: Optional[float] = Field(default=None, ge=0, le=1_000_000,
                                        description="Débit solides t/h (>= 0)")
    water_tph: Optional[float] = Field(default=None, ge=0, le=1_000_000,
                                       description="Débit eau t/h (>= 0)")
    slurry_pct_w: Optional[float] = Field(default=None, ge=0, le=100,
                                          description="% solides en poids [0-100]")
    au_gt: Optional[float] = Field(default=None, ge=0, le=50_000,
                                   description="Teneur or g/t (>= 0)")
    hours_per_day: Optional[float] = Field(default=None, ge=0, le=24,
                                           description="Heures par jour [0-24]")
    source: Optional[str] = Field(default=None, max_length=50,
                                  description="Source de la valeur (ex. 'Manual', 'LIMS')")


class MbSnapshotIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(..., min_length=1, max_length=200,
                      description="Nom du snapshot (ex. 'PFS Rev A')")


class CarbonFactorPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    factor_value: float = Field(..., ge=0,
                                description="Facteur d'émission CO2 (>= 0, en tCO2/unité)")


# ─── Equipment v2 (MER) ──────────────────────────────────────────────────────

class EquipmentV2In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    wbs_code: str = Field(..., min_length=1, max_length=20,
                          description="Code WBS (doit exister dans la table wbs_codes)")
    eq_type: str = Field(..., min_length=1, max_length=100,
                         description="Type d'équipement (ex. 'PUMP', 'MILL')")
    equipment_name: str = Field(..., min_length=1, max_length=300,
                                description="Nom descriptif de l'équipement")
    quantity: Optional[int] = Field(default=1, ge=1, le=999,
                                    description="Quantité [1-999]")
    description: Optional[str] = Field(default=None, max_length=1000)
    installed_kw: Optional[float] = Field(default=None, ge=0, le=100_000,
                                          description="Puissance installée kW [0-100 000]")
    is_long_lead: Optional[bool] = Field(default=False)
    lead_time_weeks: Optional[int] = Field(default=None, ge=0, le=260)
    vendor: Optional[str] = Field(default=None, max_length=200)
    price_cad: Optional[float] = Field(default=None, ge=0)


class EquipmentV2Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(..., ge=0,
                         description="Version actuelle pour le verrouillage optimiste")
    equipment_name: Optional[str] = Field(default=None, max_length=300)
    quantity: Optional[int] = Field(default=None, ge=1, le=999)
    description: Optional[str] = Field(default=None, max_length=1000)
    comments: Optional[str] = Field(default=None, max_length=2000)
    specifications: Optional[str] = Field(default=None, max_length=2000)
    has_vfd: Optional[bool] = None
    duty_status: Optional[str] = Field(default=None, max_length=50)
    installed_kw: Optional[float] = Field(default=None, ge=0, le=100_000)
    emergency_power: Optional[bool] = None
    vendor: Optional[str] = Field(default=None, max_length=200)
    price_cad: Optional[float] = Field(default=None, ge=0)
    installation_hours: Optional[float] = Field(default=None, ge=0)
    reference_doc: Optional[str] = Field(default=None, max_length=300)
    is_long_lead: Optional[bool] = None
    lead_time_weeks: Optional[int] = Field(default=None, ge=0, le=260)
    weight_kg: Optional[float] = Field(default=None, ge=0)
    material: Optional[str] = Field(default=None, max_length=100)


# ─── OPEX v2 ─────────────────────────────────────────────────────────────────

class OpexInputPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    param_value: float = Field(..., description="Valeur numérique du paramètre OPEX")


class ManpowerIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    department: str = Field(..., min_length=1, max_length=100,
                            description="Département (ex. 'Operations', 'Maintenance')")
    description: str = Field(..., min_length=1, max_length=300,
                             description="Intitulé du poste")
    category: Optional[str] = Field(default="Staff", max_length=50,
                                    description="'Staff' ou 'Hourly'")
    schedule: Optional[str] = Field(default="Office", max_length=50,
                                    description="'Office' (2080 h/an) ou 'Shift' (3128 h/an)")
    num_employees: Optional[int] = Field(default=1, ge=0, le=9999)
    base_salary_hourly: Optional[float] = Field(default=0, ge=0, le=10_000,
                                                description="Salaire horaire de base CAD/h")
    bonus_pct: Optional[float] = Field(default=5, ge=0, le=100)
    benefits_pct: Optional[float] = Field(default=20, ge=0, le=100)
    overtime_pct: Optional[float] = Field(default=0, ge=0, le=200)
    sort_order: Optional[int] = Field(default=0, ge=0)


class ManpowerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    department: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=300)
    category: Optional[str] = Field(default=None, max_length=50)
    schedule: Optional[str] = Field(default=None, max_length=50)
    num_employees: Optional[int] = Field(default=None, ge=0, le=9999)
    base_salary_hourly: Optional[float] = Field(default=None, ge=0, le=10_000)
    bonus_pct: Optional[float] = Field(default=None, ge=0, le=100)
    benefits_pct: Optional[float] = Field(default=None, ge=0, le=100)
    overtime_pct: Optional[float] = Field(default=None, ge=0, le=200)
    sort_order: Optional[int] = Field(default=None, ge=0)


class ReagentIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: str = Field(..., min_length=1, max_length=100,
                          description="Catégorie (ex. 'Cyanuration', 'Flottation')")
    description: str = Field(..., min_length=1, max_length=300,
                             description="Nom du réactif ou consommable")
    unit_consumption: Optional[str] = Field(default="kg/t", max_length=20,
                                            description="Unité (ex. 'kg/t', 'L/t')")
    consumption_rate: Optional[float] = Field(default=0, ge=0,
                                              description="Consommation unitaire (>= 0)")
    unit_cost_cad: Optional[float] = Field(default=0, ge=0,
                                           description="Coût unitaire CAD (>= 0)")
    source: Optional[str] = Field(default="A", max_length=10)
    sort_order: Optional[int] = Field(default=0, ge=0)


class ReagentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    category: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=300)
    unit_consumption: Optional[str] = Field(default=None, max_length=20)
    consumption_rate: Optional[float] = Field(default=None, ge=0)
    yearly_consumption: Optional[float] = Field(default=None, ge=0)
    unit_cost_cad: Optional[float] = Field(default=None, ge=0)
    source: Optional[str] = Field(default=None, max_length=10)
    sort_order: Optional[int] = Field(default=None, ge=0)


class MobileIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: str = Field(..., min_length=1, max_length=300,
                             description="Description de l'équipement mobile")
    equipment_type: Optional[str] = Field(default="", max_length=100)
    quantity: Optional[int] = Field(default=1, ge=1, le=999)
    operating_hours_year: Optional[float] = Field(default=0, ge=0, le=8760,
                                                  description="Heures d'opération/an [0-8760]")
    cost_per_hour: Optional[float] = Field(default=0, ge=0,
                                           description="Coût horaire CAD/h (>= 0)")
    sort_order: Optional[int] = Field(default=0, ge=0)


class MobilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    description: Optional[str] = Field(default=None, max_length=300)
    equipment_type: Optional[str] = Field(default=None, max_length=100)
    quantity: Optional[int] = Field(default=None, ge=1, le=999)
    operating_hours_year: Optional[float] = Field(default=None, ge=0, le=8760)
    cost_per_hour: Optional[float] = Field(default=None, ge=0)
    sort_order: Optional[int] = Field(default=None, ge=0)


class PowerPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operating_kw: Optional[float] = Field(default=None, ge=0, le=500_000,
                                          description="Puissance opératoire kW [0-500 000]")
    electrical_efficiency: Optional[float] = Field(default=None, ge=0, le=1,
                                                   description="Rendement électrique [0-1]")
    load_factor: Optional[float] = Field(default=None, ge=0, le=1,
                                         description="Facteur de charge [0-1]")
    area_availability: Optional[float] = Field(default=None, ge=0, le=1,
                                               description="Disponibilité zone [0-1]")
    hours_per_day: Optional[float] = Field(default=None, ge=0, le=24,
                                           description="Heures/jour [0-24]")
    wbs_description: Optional[str] = Field(default=None, max_length=200)


# ─── Flowsheet ───────────────────────────────────────────────────────────────

class FlowsheetBlockItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(..., min_length=1, max_length=100)
    type: str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=300)
    x: float = Field(..., ge=0, le=10_000)
    y: float = Field(..., ge=0, le=10_000)


class FlowsheetConnectionItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    from_: str = Field(..., alias="from", min_length=1, max_length=100)
    to: str = Field(..., min_length=1, max_length=100)


class FlowsheetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    blocks: list[FlowsheetBlockItem] = Field(...,
                                             description="Liste des blocs du flowsheet")
    connections: list[FlowsheetConnectionItem] = Field(...,
                                                       description="Liste des connexions entre blocs")


# ── Simulation granulaire models ──────────────────────────────────────────────

class FeedOverride(BaseModel):
    """Feed stream override for section simulation."""
    model_config = ConfigDict(extra="forbid")
    solids_tph: float = Field(gt=0, description="Débit solides t/h")
    au_g_t: float = Field(ge=0, description="Teneur Au g/t")
    p80_um: float = Field(gt=0, description="P80 µm")
    pct_solids: float = Field(gt=0, le=100, description="% solides pulpe")


class RunByOpsRequest(BaseModel):
    """Request body for POST /simulation-v2/run-by-ops."""
    model_config = ConfigDict(extra="forbid")
    op_codes: list[str] = Field(min_length=1, description="Op codes à simuler")
    feed_override: Optional[FeedOverride] = None
    params_override: Optional[dict[str, float]] = None
    label: Optional[str] = Field(default=None, max_length=200)


VALID_SECTIONS = frozenset({
    "comminution", "gravity", "flotation", "pretreatment",
    "leaching", "desorption", "thickening", "detox", "water", "reagents",
})


class RunBySectionsRequest(BaseModel):
    """Request body for POST /simulation-v2/run-by-sections."""
    model_config = ConfigDict(extra="forbid")
    sections: list[str] = Field(min_length=1, description="Catégories à simuler")
    feed_override: Optional[FeedOverride] = None
    params_override: Optional[dict[str, float]] = None
    label: Optional[str] = Field(default=None, max_length=200)


class RunSuggestedRequest(BaseModel):
    """Request body for POST /simulation-v2/run-suggested."""
    model_config = ConfigDict(extra="forbid")
    suggestion_id: str = Field(min_length=1)
    run_mode: str = Field(default="global", pattern=r"^(global|section|multi_section)$")


# ─── Simulation v3 — Compile & source ────────────────────────────────────────

class CompileRequest(BaseModel):
    """POST /simulation-v2/compile body."""
    model_config = ConfigDict(extra="forbid")
    source_type: str = Field(default="flowsheet", pattern=r"^(flowsheet|scenario_flowsheet|custom)$")
    source_id: Optional[str] = Field(default=None, description="UUID of flowsheet or scenario_flowsheet; null = use project's active flowsheet")


class CompileWarning(BaseModel):
    code: str
    message: str
    severity: str = Field(pattern=r"^(info|warning|error)$")


class CompileResponse(BaseModel):
    compilation_id: str
    template_id: str
    blocks_hash: str
    cached: bool = Field(description="True if this compilation was already in DB (dedup hit)")
    sections_resolved: list[str]
    branches_detected: list[dict]
    topo_order: list[str]
    warnings: list[CompileWarning]


class ActiveSourceRequest(BaseModel):
    """POST /simulation-v2/active-source body."""
    model_config = ConfigDict(extra="forbid")
    source_type: str = Field(pattern=r"^(flowsheet|scenario_flowsheet)$")
    source_id: str = Field(description="UUID")


class ActiveSourceResponse(BaseModel):
    source_type: str
    source_id: str
    compilation_id: Optional[str] = None


class RunByBranchesRequest(BaseModel):
    """POST /simulation-v2/run-by-branches body."""
    model_config = ConfigDict(extra="forbid")
    compilation_id: str
    branches: list[str] = Field(min_length=1, description="Branch names from compilation.branches_detected")
    feed_override: Optional[FeedOverride] = None
    params_override: Optional[dict[str, float]] = None
    label: Optional[str] = Field(default=None, max_length=200)


# ─── Simulation v3 — Custom circuits & scenario listing (Plan 2) ───────────

class CustomFromFlowsheetRequest(BaseModel):
    """POST /simulation-v2/custom/from-flowsheet body (all optional)."""
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=200,
                                description="Name for the new scenario; auto-generated if omitted")


class CustomFromTemplateRequest(BaseModel):
    """POST /simulation-v2/custom/from-template body."""
    model_config = ConfigDict(extra="forbid")
    template_name: str = Field(min_length=1, max_length=100,
                               description="One of the in-code custom templates")
    name: Optional[str] = Field(default=None, max_length=200)


class CustomBlankRequest(BaseModel):
    """POST /simulation-v2/custom/blank body (all optional)."""
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=200)


class CustomScenarioResponse(BaseModel):
    """Shared response for all custom-scenario creation endpoints."""
    scenario_flowsheet_id: str
    scenario_id: str
    name: str


class ForkSuggestionResponse(BaseModel):
    scenario_flowsheet_id: str
    scenario_id: str
    name: str
    ops_added: list[str]
    ops_removed: list[str]


class ScenarioFlowsheetSummary(BaseModel):
    scenario_flowsheet_id: str
    scenario_id: str
    name: str
    source_flowsheet_id: Optional[str] = None
    n_blocks: int
    n_connections: int
    created_at: Optional[str] = None


class ScenarioFlowsheetListResponse(BaseModel):
    items: list[ScenarioFlowsheetSummary]


# ─── Simulation v3 — Optimisation (Plan 3) ─────────────────────────────────

class OptimizationVariable(BaseModel):
    """A single optimisation decision variable."""
    model_config = ConfigDict(extra="forbid")
    param: str = Field(min_length=1, max_length=64, description="Parameter name (e.g. p80_um)")
    min: float
    max: float
    steps: Optional[int] = Field(default=10, ge=2, le=50)


class SweepRequest(BaseModel):
    """POST /optimization/sweep body."""
    model_config = ConfigDict(extra="forbid")
    compilation_id: str
    objective: str = Field(pattern=r"^(recovery|energy|aisc)$")
    variables: list[OptimizationVariable] = Field(min_length=1)
    constraints: Optional[list[dict]] = Field(default=None)


class Nsga2Request(BaseModel):
    """POST /optimization/nsga2 body."""
    model_config = ConfigDict(extra="forbid")
    compilation_id: str
    objectives: list[str] = Field(min_length=1, max_length=5)
    variables: list[OptimizationVariable] = Field(min_length=1)
    constraints: Optional[list[dict]] = Field(default=None)
    generations: int = Field(default=10, ge=1, le=200)
    population_size: Optional[int] = Field(default=20, ge=4, le=200)


class OptimizationJobResponse(BaseModel):
    """GET /optimization/{job_id} response."""
    id: str
    project_id: str
    compilation_id: Optional[str] = None
    mode: str
    status: str
    objective: Optional[str] = None
    objectives: Optional[list[str]] = None
    variables: Optional[list[dict]] = None
    constraints: Optional[list[dict]] = None
    result: Optional[dict] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class ParetoFrontResponse(BaseModel):
    """GET /optimization/{job_id}/pareto response."""
    job_id: str
    pareto: list[list[float]] = Field(description="List of objective vectors")
    pareto_full: Optional[list[dict]] = Field(default=None, description="Full solution details")
    best_balanced: Optional[dict] = Field(default=None)


# ─── Simulation v3 — Comparaison (Plan 3) ──────────────────────────────────

class CompareSetCreateRequest(BaseModel):
    """POST /simulation-v2/compare body."""
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    run_ids: list[str] = Field(min_length=2, max_length=5)


class CompareSetResponse(BaseModel):
    set_id: str
    name: str
    run_ids: list[str]
    created_at: Optional[str] = None


class CompareKpiRow(BaseModel):
    """KPIs normalized per run."""
    recovery: Optional[float] = None
    energy: Optional[float] = None
    capex: Optional[float] = None
    opex: Optional[float] = None
    score: Optional[float] = None


class CompareRunEntry(BaseModel):
    run_id: str
    label: Optional[str] = None
    kpis: CompareKpiRow


class CompareMatrixResponse(BaseModel):
    set_id: str
    name: str
    runs: list[CompareRunEntry]


class CircuitDiffPair(BaseModel):
    from_run: str
    to_run: str
    ops_added: list[str] = []
    ops_removed: list[str] = []


class CompareDiffResponse(BaseModel):
    set_id: str
    ops_added_per_pair: list[CircuitDiffPair]
    ops_removed_per_pair: list[CircuitDiffPair]


# Fix forward reference for LoginOut
LoginOut.model_rebuild()
