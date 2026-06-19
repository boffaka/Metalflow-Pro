# backend/engines/tear_stream_solver.py
"""
TearStreamSolver — Wegstein + damping fallback pour convergence des boucles de recycle.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Callable

from .stream_state import StreamState

logger = logging.getLogger("mpdpms.tear_stream_solver")


@dataclass
class ConvergenceResult:
    converged: bool
    iterations: int
    final_residual: float
    final_streams: dict[str, StreamState]
    history: list[float] = field(default_factory=list)


def _stream_to_vec(s: StreamState) -> list[float]:
    return [s.solids_tph, s.water_tph, s.au_g_t, s.au_recovery_pct, s.p80_um]


def _max_residual(old: dict[str, StreamState], new: dict[str, StreamState]) -> float:
    max_r = 0.0
    for key in old:
        if key not in new:
            continue
        v_old = _stream_to_vec(old[key])
        v_new = _stream_to_vec(new[key])
        for a, b in zip(v_old, v_new):
            denom = max(abs(a), 1e-6)
            max_r = max(max_r, abs(b - a) / denom)
    return max_r


class TearStreamSolver:

    def __init__(self, max_iterations: int = 50, tolerance: float = 1e-4):
        self.max_iterations = max_iterations
        self.tolerance = tolerance

    def solve(
        self,
        initial_tear_streams: dict[str, StreamState],
        update_fn: Callable[[dict[str, StreamState]], dict[str, StreamState]],
    ) -> ConvergenceResult:
        current = {k: v.copy() for k, v in initial_tear_streams.items()}
        prev = None
        history = []

        for iteration in range(1, self.max_iterations + 1):
            updated = update_fn(current)
            residual = _max_residual(current, updated)
            history.append(residual)

            if residual < self.tolerance:
                return ConvergenceResult(
                    converged=True, iterations=iteration,
                    final_residual=residual, final_streams=updated,
                    history=history,
                )

            if prev is not None and iteration >= 2:
                current = self._wegstein_step(prev, current, updated)
            else:
                current = updated

            prev = {k: v.copy() for k, v in current.items()}

            if iteration > 10 and len(history) > 3 and history[-1] > history[-3]:
                logger.warning("Wegstein divergence après %d itérations — résidu=%.4e",
                               iteration, residual)
                break

        return self._damping_fallback(current, update_fn, history)

    def _wegstein_step(
        self,
        prev: dict[str, StreamState],
        current: dict[str, StreamState],
        updated: dict[str, StreamState],
    ) -> dict[str, StreamState]:
        result = {}
        for key in current:
            if key not in updated or key not in prev:
                result[key] = updated.get(key, current[key])
                continue
            x_prev = _stream_to_vec(prev[key])
            x_curr = _stream_to_vec(current[key])
            f_curr = _stream_to_vec(updated[key])
            accelerated = []
            for xp, xc, fc in zip(x_prev, x_curr, f_curr):
                dx = xc - xp
                df = fc - xc
                if abs(dx) > 1e-10:
                    q = df / dx
                    q = max(-5, min(q, 0.9))
                    x_new = xc + (fc - xc) / (1 - q)
                else:
                    x_new = fc
                accelerated.append(x_new)
            s = current[key]
            result[key] = StreamState(
                solids_tph=max(0, accelerated[0]),
                water_tph=max(0, accelerated[1]),
                au_g_t=max(0, accelerated[2]),
                au_recovery_pct=min(100, max(0, accelerated[3])),
                p80_um=max(1, accelerated[4]),
                energy_kwh_t=s.energy_kwh_t,
                cn_kg_t=s.cn_kg_t,
            )
        return result

    def _damping_fallback(
        self,
        current: dict[str, StreamState],
        update_fn: Callable,
        history: list[float],
    ) -> ConvergenceResult:
        remaining = self.max_iterations - len(history)
        for iteration in range(remaining):
            updated = update_fn(current)
            residual = _max_residual(current, updated)
            history.append(residual)
            if residual < self.tolerance:
                return ConvergenceResult(
                    converged=True, iterations=len(history),
                    final_residual=residual, final_streams=updated,
                    history=history,
                )
            damped = {}
            for key in current:
                if key not in updated:
                    continue
                vc = _stream_to_vec(current[key])
                vu = _stream_to_vec(updated[key])
                mixed = [(a + b) / 2 for a, b in zip(vc, vu)]
                s = current[key]
                damped[key] = StreamState(
                    solids_tph=max(0, mixed[0]), water_tph=max(0, mixed[1]),
                    au_g_t=max(0, mixed[2]),
                    au_recovery_pct=min(100, max(0, mixed[3])),
                    p80_um=max(1, mixed[4]),
                    energy_kwh_t=s.energy_kwh_t, cn_kg_t=s.cn_kg_t,
                )
            current = damped

        return ConvergenceResult(
            converged=False, iterations=len(history),
            final_residual=history[-1] if history else float("inf"),
            final_streams=current, history=history,
        )
