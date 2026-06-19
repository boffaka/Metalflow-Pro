"""Schémas OpenAPI — readiness ingénierie & fidélité jumeau numérique."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadinessGateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Identifiant stable de la gate (ex. circuit_criteria, lims).")
    label: str
    fraction: float = Field(ge=0, le=1, description="Crédit partiel 0–1 avant application du poids.")
    weight: float = Field(description="Poids de la gate ; la somme des poids vaut 100.")
    points: float = Field(description="Contribution au numérateur (weight × fraction).")
    ok: bool = Field(description="True si fraction ≥ 0,95 (gate considérée comme satisfaite).")
    hint: str
    detail: dict[str, Any] = Field(
        default_factory=dict,
        description="Compteurs et cibles (critères DC, sections MB, LIMS 90 j, etc.).",
    )


class EngineeringReadinessOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["engineering_readiness"]
    weights_version: str = Field(
        description="Version serveur des pondérations et barèmes ; utile pour comparer les séries dans le temps."
    )
    score: int = Field(ge=0, le=100, description="100 × earned / possible, arrondi.")
    earned: float = Field(description="Points obtenus (somme des contributions pondérées).")
    possible: float = Field(description="Somme des poids des gates présentes (typiquement 100).")
    gates: list[ReadinessGateOut]
    missing_gate_ids: list[str] = Field(
        description="Liste des `id` de gates pour lesquelles `ok` est faux."
    )
    generated_at: str = Field(description="Horodatage ISO 8601 (UTC).")


class FidelityComponentBlockOut(BaseModel):
    """Sous-score d’une composante ; champs supplémentaires selon le bloc (raw, items, …)."""

    model_config = ConfigDict(extra="allow")

    score: int = Field(ge=0, le=100, description="Sous-score 0–100 pour cette composante.")
    note: str = Field(description="Intuition métier / lecture du sous-score.")


class PlantDesignGapOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    code: str
    severity: str
    message: str


class TestworkProgramOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    study_level: str
    study_label: str
    score: int = Field(ge=0, le=100)
    lims_counts: dict[str, int]
    gaps: list[PlantDesignGapOut]
    references: list[str]
    generated_at: str


class SimulationQAStageOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    label: str
    fraction: float = Field(ge=0, le=1)
    ok: bool
    hint: str


class SimulationQAOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    kind: Literal["simulation_qa"]
    weights_version: str
    study_level: str
    score: int = Field(ge=0, le=100)
    can_run_rigorous: bool
    blockers: list[str]
    stages: list[SimulationQAStageOut]
    testwork: TestworkProgramOut
    warnings: list[str]
    references: list[str]
    generated_at: str


class DigitalTwinFidelityOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["digital_twin_fidelity"]
    weights_version: str = Field(description="Même principe que readiness — traçabilité des barèmes.")
    score: float = Field(ge=0, le=100, description="Moyenne pondérée des sous-scores (1 décimale).")
    weights: dict[str, float] = Field(
        description="Poids des composantes (somme = 1). Clés alignées sur `components`."
    )
    components: dict[str, FidelityComponentBlockOut] = Field(
        description="Blocs : mass_balance_streams, simulation_params, equipment_energy_tags, "
        "recent_simulation_runs, lims_test_chain (densité a1+b1)."
    )
    generated_at: str = Field(description="Horodatage ISO 8601 (UTC).")
