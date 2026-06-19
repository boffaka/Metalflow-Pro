"""Build optimization problem definitions from a flowsheet graph."""
from __future__ import annotations

from typing import Any

from .flowsheet_validator import validate_flowsheet
from .topology_analyzer import FlowsheetGraph
from .unit_registry import get_unit

DEFAULT_OBJECTIVES = [
    {"metric": "global_results.overall_recovery", "direction": "max"},
    {"metric": "global_results.total_energy_kwh_t", "direction": "min"},
]

DEFAULT_CONSTRAINTS = [
    {"metric": "global_results.cn_in_tailings_ppm", "operator": "<=", "value": 50},
    {"metric": "global_results.overall_recovery", "operator": ">=", "value": 85},
]


def build_optimization_problem(
    graph: FlowsheetGraph,
    objectives: list[dict[str, Any]] | None = None,
    constraints: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validation = validate_flowsheet(graph)
    variables: list[dict[str, Any]] = []

    if validation.valid:
        for node in graph.nodes:
            try:
                unit = get_unit(node.op_code)
            except KeyError:
                continue
            for spec in unit.optimizable:
                variables.append({
                    "node_id": node.id,
                    "op_code": unit.op_code,
                    "parameter": spec.parameter,
                    "min": spec.min,
                    "max": spec.max,
                    "objective_effect": spec.objective_effect,
                    "current_value": (node.params or {}).get(spec.parameter),
                })

    return {
        "valid": validation.valid,
        "variables": variables,
        "objectives": objectives or list(DEFAULT_OBJECTIVES),
        "constraints": constraints or list(DEFAULT_CONSTRAINTS),
        "diagnostics": {
            "errors": [e.to_dict() for e in validation.errors],
            "warnings": [w.to_dict() for w in validation.warnings],
            "suggestions": validation.suggestions,
        },
        "topology": validation.topology,
    }
