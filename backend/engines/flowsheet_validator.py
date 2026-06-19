"""Flowsheet graph validation for simulation and optimization."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .topology_analyzer import FlowsheetGraph, TopologyAnalyzer
from .unit_registry import get_unit, resolve_op_code


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None
    severity: str = "error"
    suggestion: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    topology: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "suggestions": self.suggestions,
            "topology": self.topology,
        }


def _issue(
    code: str,
    message: str,
    *,
    node_id: str | None = None,
    edge_id: str | None = None,
    severity: str = "error",
    suggestion: str | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        message=message,
        node_id=node_id,
        edge_id=edge_id,
        severity=severity,
        suggestion=suggestion,
    )


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_port(port: str | None) -> str:
    value = str(port or "in")
    aliases = {"concentrate": "conc", "tailings": "tails", "product": "out"}
    return aliases.get(value, aliases.get(value.lower(), value))


def validate_flowsheet(graph: FlowsheetGraph) -> ValidationResult:
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    suggestions: list[str] = []
    node_by_id = {n.id: n for n in graph.nodes}
    incoming: dict[str, list[str]] = {n.id: [] for n in graph.nodes}

    for edge in graph.edges:
        incoming.setdefault(edge.target_node, []).append(edge.id)

    unit_by_node: dict[str, Any] = {}
    for node in graph.nodes:
        try:
            unit = get_unit(node.op_code)
            unit_by_node[node.id] = unit
        except KeyError:
            errors.append(
                _issue(
                    "UNKNOWN_OP",
                    f"Unite operatoire inconnue: {node.op_code}",
                    node_id=node.id,
                    suggestion="Choisir une unite dans le registre officiel.",
                )
            )
            continue

        if unit.ports_in and not incoming.get(node.id):
            errors.append(
                _issue(
                    "MISSING_INLET",
                    f"{unit.display_name} doit recevoir au moins un flux d'entree.",
                    node_id=node.id,
                    suggestion="Connecter une sortie amont au port d'entree.",
                )
            )

        param_by_name = {p.name: p for p in unit.params}
        for key, value in (node.params or {}).items():
            spec = param_by_name.get(key)
            if spec is None:
                warnings.append(
                    _issue(
                        "UNKNOWN_PARAM",
                        f"Parametre ignore pour {unit.op_code}: {key}",
                        node_id=node.id,
                        severity="warning",
                    )
                )
                continue
            if spec.type == "number":
                num = _numeric(value)
                if num is None:
                    errors.append(
                        _issue("PARAM_TYPE", f"{key} doit etre numerique.", node_id=node.id)
                    )
                    continue
                if spec.min is not None and num < spec.min:
                    errors.append(
                        _issue(
                            "PARAM_OUT_OF_RANGE",
                            f"{key}={num:g} est inferieur au minimum {spec.min:g}.",
                            node_id=node.id,
                            suggestion=f"Utiliser une valeur >= {spec.min:g}.",
                        )
                    )
                if spec.max is not None and num > spec.max:
                    errors.append(
                        _issue(
                            "PARAM_OUT_OF_RANGE",
                            f"{key}={num:g} est superieur au maximum {spec.max:g}.",
                            node_id=node.id,
                            suggestion=f"Utiliser une valeur <= {spec.max:g}.",
                        )
                    )

    if graph.nodes and not any(resolve_op_code(n.op_code) == "FEED" for n in graph.nodes):
        errors.append(
            _issue(
                "MISSING_FEED",
                "Le flowsheet doit contenir au moins une alimentation FEED.",
                suggestion="Ajouter un bloc FEED au debut du circuit.",
            )
        )

    for edge in graph.edges:
        src = node_by_id.get(edge.source_node)
        tgt = node_by_id.get(edge.target_node)
        if src is None:
            errors.append(_issue("MISSING_SOURCE_NODE", "Source d'edge introuvable.", edge_id=edge.id))
            continue
        if tgt is None:
            errors.append(_issue("MISSING_TARGET_NODE", "Cible d'edge introuvable.", edge_id=edge.id))
            continue

        src_unit = unit_by_node.get(src.id)
        tgt_unit = unit_by_node.get(tgt.id)
        if src_unit and src_unit.ports_out:
            port = _resolve_port(edge.port_source)
            if port not in src_unit.ports_out:
                errors.append(
                    _issue(
                        "INVALID_SOURCE_PORT",
                        f"Port source invalide {edge.port_source} pour {src_unit.op_code}.",
                        edge_id=edge.id,
                        node_id=src.id,
                        suggestion=f"Ports valides: {', '.join(src_unit.ports_out)}.",
                    )
                )
        if src_unit and not src_unit.ports_out:
            errors.append(
                _issue(
                    "SINK_HAS_OUTPUT",
                    f"{src_unit.op_code} est un puits et ne doit pas alimenter un autre bloc.",
                    edge_id=edge.id,
                    node_id=src.id,
                )
            )

        if tgt_unit and tgt_unit.ports_in:
            port = _resolve_port(edge.port_target)
            if port not in tgt_unit.ports_in:
                errors.append(
                    _issue(
                        "INVALID_TARGET_PORT",
                        f"Port cible invalide {edge.port_target} pour {tgt_unit.op_code}.",
                        edge_id=edge.id,
                        node_id=tgt.id,
                        suggestion=f"Ports valides: {', '.join(tgt_unit.ports_in)}.",
                    )
                )

    topology = {"execution_order": [], "loops_detected": [], "tear_streams": []}
    try:
        topo = TopologyAnalyzer(graph).analyze()
        topology = {
            "execution_order": [n.id for n in topo.execution_order],
            "loops_detected": topo.loops_detected,
            "tear_streams": [e.id for e in topo.tear_streams],
            "has_loops": topo.has_loops,
        }
        if topo.has_loops and not topo.tear_streams:
            errors.append(_issue("UNRESOLVED_CYCLE", "Cycle detecte sans tear stream resoluble."))
    except Exception as exc:
        errors.append(_issue("TOPOLOGY_ERROR", f"Analyse topologique impossible: {exc}"))

    if errors:
        suggestions.extend(i.suggestion for i in errors if i.suggestion)

    return ValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        suggestions=suggestions,
        topology=topology,
    )
