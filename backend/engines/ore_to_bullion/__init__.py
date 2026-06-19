"""
MetalFlow Pro — Ore to Bullion Simulator Engine.
Du Minerai au Lingot : simulateur circuit par circuit.
"""
from __future__ import annotations
from .models import FeedParameters, CircuitConfig, CircuitResult, SimulationResult
from .stream import Stream

__all__ = ["simulate_ore_to_bullion", "FeedParameters", "CircuitConfig", "CircuitResult", "SimulationResult", "Stream"]


def simulate_ore_to_bullion(feed_params: FeedParameters, circuit_config: CircuitConfig, overrides: dict | None = None) -> SimulationResult:
    """Execute full ore-to-bullion simulation. Pure function — no DB, no I/O."""
    from .orchestrator import run_simulation
    return run_simulation(feed_params, circuit_config, overrides)
