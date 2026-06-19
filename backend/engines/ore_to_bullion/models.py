"""Pydantic models for the Ore to Bullion Simulator."""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator

# Import from the central constants module — single source of truth.
# Previously defined locally as `1 / 31.1035`.
try:
    from ...constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - direct script imports
    from constants import TROY_OZ_PER_GRAM


class FeedParameters(BaseModel):
    """Core feed parameters for simulation."""
    feed_rate_tph: float = Field(..., ge=1, le=10000, description="Feed rate (t/h)")
    gold_grade_g_t: float = Field(..., ge=0.1, le=100, description="Gold grade (g/t Au)")
    ore_sg: float = Field(2.75, ge=1.5, le=5.0, description="Ore SG")
    bwi_kwh_t: float = Field(14.0, ge=5, le=60, description="Bond Ball Mill Wi (kWh/t)")
    cwi_kwh_t: float = Field(12.0, ge=1, le=30, description="Crushing Wi (kWh/t)")
    axb: float = Field(45.0, ge=10, le=120, description="JK Drop Weight Axb")
    target_recovery_pct: float = Field(92.0, ge=50, le=99, description="Target recovery (%)")
    availability_pct: float = Field(92.0, ge=50, le=99, description="Plant availability (%)")
    operating_hours_day: float = Field(22.1, ge=8, le=24, description="Operating hours/day")

    @field_validator("target_recovery_pct", "availability_pct", mode="before")
    @classmethod
    def normalize_pct(cls, v):
        if isinstance(v, (int, float)) and 0 < v <= 1.0:
            return v * 100.0
        return v

    @property
    def annual_throughput_t(self) -> float:
        return self.feed_rate_tph * self.operating_hours_day * 365 * self.availability_pct / 100

    @property
    def annual_gold_oz(self) -> float:
        g_year = (self.feed_rate_tph * self.gold_grade_g_t * self.target_recovery_pct / 100
                  * self.operating_hours_day * 365 * self.availability_pct / 100)
        return g_year * TROY_OZ_PER_GRAM


class CircuitConfig(BaseModel):
    """Configuration for active circuits and parameters."""
    crushing_enabled: bool = True
    crushing_target_p80_mm: float = Field(
        35.0, ge=5, le=200, description="P80 produit concassage (mm), ex. sortie cône"
    )
    grinding_type: str = Field("hpgr_ball", pattern="^(sag_ball|sag_ball_pebble|sag_ball_verti|hpgr_ball|hpgr_ball_verti|ball_only|ball_verti|vertimill)$")
    grinding_target_p80_um: float = Field(
        75.0,
        ge=20,
        le=500,
        description="P80 produit broyage final (µm / microns), ex. 75 pour 75 µm — pas en mm",
    )

    @field_validator("grinding_target_p80_um", mode="before")
    @classmethod
    def normalize_grinding_p80_um(cls, v):
        """Accept accidental mm entry (0.075) and convert to µm (75)."""
        if isinstance(v, (int, float)):
            fv = float(v)
            if 0 < fv < 1:
                return fv * 1000.0
        return v
    gravity_enabled: bool = True
    grg_pct: float = Field(35.0, ge=0, le=100, description="GRG in ore (% of feed Au)")
    gravity_slip_pct: float = Field(30.0, ge=10, le=50, description="Cyclone U/F slip to gravity (%)")
    knelson_unit_recovery_pct: float = Field(50.0, ge=30, le=75, description="Knelson recovery of GRG on slip (%)")
    ilr_recovery_pct: float = Field(95.0, ge=88, le=99, description="ILR recovery on gravity concentrate (%)")
    gravity_mass_pull_pct: float = Field(0.2, ge=0.05, le=2.0, description="Mass pull on gravity feed (%)")
    flotation_enabled: bool = False
    flotation_k_rate: float = Field(1.5, ge=0.1, le=5.0)
    flotation_residence_min: float = Field(20.0, ge=5, le=60)
    flotation_rmax_pct: float = Field(90.0, ge=50, le=99)
    flotation_mass_pull_pct: float = Field(8.0, ge=1, le=30)
    leaching_type: str = Field("cil", pattern="^(cil|cip|leach_only)$")
    leaching_srt_h: float = Field(24.0, ge=8, le=48)
    leaching_n_tanks: int = Field(8, ge=4, le=12)
    leaching_nacn_kg_t: float = Field(0.5, ge=0.1, le=5.0)
    leaching_cao_kg_t: float = Field(1.5, ge=0.1, le=10.0)
    leaching_recovery_pct: float = Field(92.0, ge=50, le=99)
    elution_type: str = Field("aarl", pattern="^(aarl|zadra)$")
    detox_process: str = Field("inco", pattern="^(inco|caro|peroxide)$")
    detox_wad_cn_inlet_mg_l: float = Field(50.0, ge=1, le=500)
    nacn_price_usd_kg: float = Field(3.50, ge=0)
    cao_price_usd_kg: float = Field(0.12, ge=0)
    energy_rate_usd_kwh: float = Field(0.08, ge=0)
    grid_co2_kg_kwh: float = Field(0.50, ge=0)


class CircuitResult(BaseModel):
    """Result from a single circuit simulation."""
    circuit_name: str
    input_stream: dict
    output_stream: dict
    mass_balance: dict
    equipment: list[dict] = []
    energy_kwh_t: float = 0.0
    power_kw: float = 0.0
    reagents: dict = {}
    alerts: list[dict] = []
    metadata: dict = {}


class SimulationResult(BaseModel):
    """Complete simulation result."""
    feed_params: dict
    circuit_results: list[CircuitResult] = []
    overall_recovery_pct: float = 0.0
    annual_gold_oz: float = 0.0
    total_energy_kwh_t: float = 0.0
    total_power_kw: float = 0.0
    total_reagent_opex_usd_t: float = 0.0
    total_reagent_opex_usd_oz: float = 0.0
    annual_energy_mwh: float = 0.0
    annual_energy_cost_usd: float = 0.0
    co2_kg_per_t: float = 0.0
    co2_kg_per_oz: float = 0.0
    reagent_summary: list[dict] = []
    energy_breakdown: list[dict] = []
    alerts: list[dict] = []
    alerts_summary: dict = {"critical": 0, "warning": 0, "info": 0}
    computation_time_s: float = 0.0
