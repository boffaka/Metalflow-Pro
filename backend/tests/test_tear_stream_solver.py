# backend/tests/test_tear_stream_solver.py
import pytest
from engines.stream_state import StreamState
from engines.tear_stream_solver import TearStreamSolver, ConvergenceResult

pytestmark = pytest.mark.no_db

STREAM_A = StreamState(solids_tph=100, water_tph=150, au_g_t=1.5,
                       au_recovery_pct=90, p80_um=75, energy_kwh_t=8)


def _identity_update(streams):
    return streams


def test_already_converged():
    solver = TearStreamSolver(max_iterations=20, tolerance=1e-4)
    init = {"e4": STREAM_A}
    result = solver.solve(init, _identity_update)
    assert result.converged
    assert result.iterations <= 2


def test_convergence_within_limit():
    solver = TearStreamSolver(max_iterations=50, tolerance=1e-4)
    init = {"e4": STREAM_A.copy(solids_tph=200)}

    def update(streams):
        updated = {}
        for k, s in streams.items():
            target = 100.0
            new_val = s.solids_tph + (target - s.solids_tph) * 0.6
            updated[k] = s.copy(solids_tph=new_val)
        return updated

    result = solver.solve(init, update)
    assert result.converged
    assert result.iterations < 50


def test_non_convergence_returns_result():
    solver = TearStreamSolver(max_iterations=5, tolerance=1e-10)
    init = {"e4": STREAM_A}

    def diverge(streams):
        return {k: s.copy(solids_tph=s.solids_tph * 2) for k, s in streams.items()}

    result = solver.solve(init, diverge)
    assert not result.converged
    assert result.iterations == 5


def test_residual_decreases_on_convergent_system():
    solver = TearStreamSolver(max_iterations=30, tolerance=1e-6)
    init = {"e4": STREAM_A.copy(solids_tph=200)}

    def update(streams):
        return {k: s.copy(solids_tph=s.solids_tph + (100 - s.solids_tph) * 0.7)
                for k, s in streams.items()}

    result = solver.solve(init, update)
    assert result.converged
    assert result.final_residual < 1e-4
