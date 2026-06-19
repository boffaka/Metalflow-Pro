"""Canonical unit-operation registry for flowsheet simulation.

The registry is the shared contract between graph validation, simulation,
optimization, and frontend form generation. It intentionally starts with the
gold-processing units already supported by the dispatcher and can be extended
without changing route or UI code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

REGISTRY_VERSION = "2026-06-02.foundation.v1"


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    default: float | str | bool | None = None
    unit: str = ""
    type: str = "number"
    min: float | None = None
    max: float | None = None
    required: bool = False
    phase: str = "pre"


@dataclass(frozen=True)
class OptimizableParam:
    parameter: str
    min: float
    max: float
    objective_effect: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UnitSpec:
    op_code: str
    display_name: str
    family: str
    ports_in: list[str]
    ports_out: list[str]
    params: list[ParamSpec] = field(default_factory=list)
    optimizable: list[OptimizableParam] = field(default_factory=list)
    stream_type: str = "slurry"
    model_confidence: str = "scoping"
    properties_read: list[str] = field(default_factory=list)
    properties_written: list[str] = field(default_factory=list)

    def default_params(self) -> dict[str, Any]:
        return {p.name: p.default for p in self.params if p.default is not None}

    def to_payload(self) -> dict[str, Any]:
        return {
            "unit_type": self.op_code,
            "op_code": self.op_code,
            "display_name": self.display_name,
            "category": self.family,
            "family": self.family,
            "default_params": self.default_params(),
            "param_schema": [asdict(p) for p in self.params],
            "params": [asdict(p) for p in self.params],
            "inlet_ports": self.ports_in,
            "outlet_ports": self.ports_out,
            "ports_in": self.ports_in,
            "ports_out": self.ports_out,
            "stream_type": self.stream_type,
            "model_confidence": self.model_confidence,
            "optimizable": [asdict(v) for v in self.optimizable],
            "properties_read": self.properties_read,
            "properties_written": self.properties_written,
        }


def _p(
    name: str,
    label: str,
    default: float | str | bool | None = None,
    unit: str = "",
    min: float | None = None,
    max: float | None = None,
    *,
    required: bool = False,
    phase: str = "pre",
    type: str = "number",
) -> ParamSpec:
    return ParamSpec(
        name=name,
        label=label,
        default=default,
        unit=unit,
        min=min,
        max=max,
        required=required,
        phase=phase,
        type=type,
    )


def _opt(parameter: str, min: float, max: float, *effects: str) -> OptimizableParam:
    return OptimizableParam(parameter=parameter, min=min, max=max, objective_effect=list(effects))


POST_STREAM_PARAMS = [
    _p("solids_tph", "Debit solides sortie", unit="t/h", min=0, phase="post"),
    _p("au_g_t", "Teneur Au sortie", unit="g/t", min=0, phase="post"),
    _p("energy_kwh_t", "Energie specifique", unit="kWh/t", min=0, phase="post"),
]


CATALOG: tuple[UnitSpec, ...] = (
    UnitSpec(
        "FEED",
        "Alimentation minerai",
        "utilities",
        [],
        ["out"],
        [
            _p("feed_tph", "Debit alimentation", 1000, "t/h", 0.001, 100000, required=True),
            _p("au_g_t", "Teneur Au", 1.5, "g/t", 0, 1000, required=True),
            _p("p80_um", "P80 alimentation", 150000, "um", 1, 1000000),
        ],
        [_opt("feed_tph", 1, 100000, "throughput", "production")],
        properties_written=["solids_tph", "water_tph", "au_g_t", "p80_um"],
    ),
    UnitSpec(
        "SAG_MILL",
        "Broyeur SAG",
        "comminution",
        ["in"],
        ["out"],
        [_p("wi", "Indice de Bond", 14, "kWh/t", 1, 50), _p("p80_um", "P80 produit", 2000, "um", 100, 10000)],
        [_opt("p80_um", 500, 5000, "recovery", "energy"), _opt("wi", 5, 30, "energy")],
        properties_read=["solids_tph", "p80_um"],
        properties_written=["p80_um", "energy_kwh_t"],
    ),
    UnitSpec(
        "BALL_MILL",
        "Broyeur a boulets",
        "comminution",
        ["in"],
        ["out"],
        [_p("wi", "Indice de Bond", 12, "kWh/t", 1, 50), _p("p80_um", "P80 produit", 75, "um", 20, 500)],
        [_opt("p80_um", 38, 150, "recovery", "energy"), _opt("wi", 5, 30, "energy")],
        properties_read=["solids_tph", "p80_um"],
        properties_written=["p80_um", "energy_kwh_t"],
    ),
    UnitSpec(
        "CYCLONE",
        "Hydrocyclone",
        "classification",
        ["in"],
        ["overflow", "underflow"],
        [_p("efficiency", "Efficacite", 0.75, "", 0, 1), _p("p80_overflow_um", "P80 overflow", 75, "um", 10, 500)],
        [_opt("efficiency", 0.4, 0.95, "classification"), _opt("p80_overflow_um", 38, 150, "recovery", "energy")],
    ),
    UnitSpec(
        "GRAVITE_KNELSON",
        "Concentrateur Knelson",
        "gravity",
        ["in"],
        ["conc", "tails"],
        [_p("recovery_pct", "Recuperation Au", 35, "%", 0, 99), _p("mass_pull_pct", "Mass pull", 2, "%", 0.01, 20)],
        [_opt("recovery_pct", 10, 90, "recovery"), _opt("mass_pull_pct", 0.1, 10, "recovery", "capacity")],
        properties_read=["solids_tph", "au_g_t"],
        properties_written=["au_recovery_pct"],
    ),
    UnitSpec(
        "FLOTATION_ROUGHER",
        "Flottation rougher",
        "flotation",
        ["in"],
        ["conc", "tails"],
        [_p("r_max", "Recuperation maximale", 0.85, "", 0, 1), _p("k", "Constante cinetique", 0.4, "min-1", 0, 10), _p("tau_min", "Temps de sejour", 8, "min", 0.1, 120), _p("mass_pull_pct", "Mass pull", 5, "%", 0.1, 40)],
        [_opt("r_max", 0.5, 0.98, "recovery"), _opt("tau_min", 2, 60, "recovery", "capex"), _opt("mass_pull_pct", 1, 20, "recovery", "opex")],
        properties_read=["solids_tph", "au_g_t"],
        properties_written=["au_recovery_pct"],
    ),
    UnitSpec(
        "CIL_TANK",
        "Cuve CIL",
        "leaching",
        ["in"],
        ["out"],
        [_p("srt_h", "Temps de sejour", 24, "h", 4, 72), _p("r_inf", "Recuperation asymptotique", 0.92, "", 0, 1), _p("k_h", "Constante cinetique", 0.15, "h-1", 0, 5), _p("cn_kg_t", "Cyanure", 0.5, "kg/t", 0.05, 5), _p("pH", "pH", 10.5, "", 8.5, 12)],
        [_opt("srt_h", 12, 48, "recovery", "capex", "opex"), _opt("cn_kg_t", 0.1, 2.5, "recovery", "opex", "environment"), _opt("pH", 9.5, 11.5, "recovery", "environment")],
        properties_read=["solids_tph", "au_g_t", "p80_um"],
        properties_written=["au_recovery_pct", "cn_kg_t"],
    ),
    UnitSpec(
        "CIP",
        "Cuve CIP",
        "leaching",
        ["in"],
        ["out"],
        [_p("srt_h", "Temps de sejour", 24, "h", 4, 72), _p("r_inf", "Recuperation asymptotique", 0.92, "", 0, 1), _p("k_h", "Constante cinetique", 0.15, "h-1", 0, 5), _p("carbon_conc_g_l", "Carbone", 25, "g/L", 5, 80)],
        [_opt("srt_h", 12, 48, "recovery", "capex"), _opt("carbon_conc_g_l", 10, 50, "recovery", "opex")],
    ),
    UnitSpec(
        "DETOX_INCO",
        "Detox INCO/SO2-air",
        "detox",
        ["in"],
        ["out"],
        [_p("target_cn_ppm", "CN residuel cible", 50, "ppm", 0, 500)],
        [_opt("target_cn_ppm", 5, 100, "environment", "opex")],
        properties_written=["cn_kg_t"],
    ),
    UnitSpec("ELUTION_AARL", "Elution AARL", "adr", ["in"], ["eluate", "barren"], [_p("recovery_pct", "Recuperation", 99, "%", 80, 100)], [_opt("recovery_pct", 90, 99.9, "recovery")]),
    UnitSpec("ELUTION_ZADRA", "Elution Zadra", "adr", ["in"], ["eluate", "barren"], [_p("recovery_pct", "Recuperation", 99, "%", 80, 100)], [_opt("recovery_pct", 90, 99.9, "recovery")]),
    UnitSpec("ELECTROLYSE", "Electrolyse", "adr", ["in"], ["sludge", "spent"], [], [], stream_type="solution"),
    UnitSpec("FUSION_DORE", "Fusion dore", "adr", ["in"], ["bullion", "slag"], [], [], stream_type="product"),
    UnitSpec("TSF", "Parc a residus", "tailings", ["in"], [], [], [], stream_type="tailings"),
    UnitSpec("POMPE", "Pompe", "utilities", ["in"], ["out"], POST_STREAM_PARAMS, [], stream_type="slurry"),
)


ALIASES = {
    "LEACH_CIL": "CIL_TANK",
    "LEACH_CIP": "CIP",
    "CIL": "CIL_TANK",
    "GRAVITY": "GRAVITE_KNELSON",
    "FLOTATION": "FLOTATION_ROUGHER",
    "HYDROCYCLONE": "CYCLONE",
    "ELECTROWINNING": "ELECTROLYSE",
    "FONDERIE": "FUSION_DORE",
    "BULLION": "FUSION_DORE",
    "CONCENTRATE": "conc",
    "TAILINGS": "tails",
    "concentrate": "conc",
    "tailings": "tails",
}

_UNITS = {unit.op_code: unit for unit in CATALOG}


def resolve_op_code(code: str | None) -> str:
    if not code:
        return ""
    return ALIASES.get(code, ALIASES.get(str(code).upper(), str(code).upper()))


def list_units() -> list[UnitSpec]:
    return list(CATALOG)


def get_unit(op_code: str) -> UnitSpec:
    resolved = resolve_op_code(op_code)
    try:
        return _UNITS[resolved]
    except KeyError as exc:
        raise KeyError(f"Unknown unit op_code: {op_code}") from exc


def unit_library_payload() -> dict[str, Any]:
    return {"version": REGISTRY_VERSION, "items": [u.to_payload() for u in list_units()]}
