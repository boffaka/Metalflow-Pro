"""
Circuit trade-off compatibility wrapper.

The implementation now delegates scenario generation/scoring to the unified
`circuit_strategy` engine to avoid hardcoded scenario ids.
"""
from __future__ import annotations

try:
    from .circuit_strategy import analyze_circuit_strategy
except ImportError:  # pragma: no cover
    from engines.circuit_strategy import analyze_circuit_strategy


def compare_tradeoff_circuits(pid: str, db_qall, db_qone) -> dict:
    strategy = analyze_circuit_strategy(pid, db_qall, db_qone)
    tradeoff = dict(strategy.get("tradeoff") or {})
    tradeoff["strategy_source"] = strategy.get("scenario_source")
    return tradeoff
