"""Layer 2 — pure compute tests for simulate-optimize."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

from backend.compute.simulate_optimize import run_optimize, _pareto_front
from backend.jobs.context import JobCancelled


class _FakeCtx:
    def __init__(self, cancel_after: int | None = None):
        self.calls = 0
        self.cancel_after = cancel_after
        self.progress_calls: list[tuple[int, int, str | None]] = []

    def check_cancelled(self) -> None:
        self.calls += 1
        if self.cancel_after is not None and self.calls > self.cancel_after:
            raise JobCancelled()

    def report_progress(self, current: int, total: int, message: str | None = None) -> None:
        self.progress_calls.append((current, total, message))


def test_pareto_front_drops_dominated_points():
    pts = [
        {"recovery": 90.0, "energy": 12.0},  # dominated by p3
        {"recovery": 92.0, "energy": 14.0},  # on front
        {"recovery": 91.0, "energy": 11.5},  # on front
        {"recovery": 88.0, "energy": 11.0},  # on front
    ]
    front = _pareto_front(pts, max_key="recovery", min_key="energy")
    assert {(p["recovery"], p["energy"]) for p in front} == {
        (92.0, 14.0), (91.0, 11.5), (88.0, 11.0)
    }


def test_run_optimize_returns_expected_schema():
    ctx = _FakeCtx()
    payload = {
        "base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24},
        "study_context": {
            "study_level": "pre_feasibility",
            "capex_opex_tolerance_pct": 30,
        },
        "grid": {
            "p80": [70, 75, 80],
            "cn": [300, 350, 400],
            "do": [6, 8],
            "srt": [24, 28],
        },
    }
    result = run_optimize(payload, ctx)
    assert result["ok"] is True
    assert result["solver"].startswith("Grid")
    assert result["evaluation_mode"] == "surrogate"
    assert result["study_context"]["study_level"] == "pre_feasibility"
    assert "methodology_notes" in result
    assert "MetPlant 2008" in result["methodology_notes"]["reference"]
    assert result["iterations"] == 3 * 3 * 2 * 2
    assert isinstance(result["pareto_front"], list) and len(result["pareto_front"]) >= 1
    for p in result["pareto_front"]:
        assert {"p80", "cn", "do", "srt", "expected_recovery", "expected_energy"} <= set(p.keys())
    assert "recommended_optimum" in result
    assert "all_results" in result
    assert len(result["all_results"]) <= 100


def test_run_optimize_uses_default_grid_when_none_provided():
    ctx = _FakeCtx()
    payload = {"base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24}}
    result = run_optimize(payload, ctx)
    # default grid is small but non-empty
    assert result["iterations"] >= 16
    assert len(result["pareto_front"]) >= 1


def test_run_optimize_cancellable_mid_sweep():
    ctx = _FakeCtx(cancel_after=2)
    with pytest.raises(JobCancelled):
        run_optimize(
            {"base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24}},
            ctx,
        )


def test_run_optimize_reports_progress_at_least_once():
    ctx = _FakeCtx()
    run_optimize({"base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24}}, ctx)
    assert ctx.progress_calls
    last = ctx.progress_calls[-1]
    assert last[0] == last[1]


def test_higher_nacn_increases_recovery_at_fixed_grind():
    """Cn/do must influence k (was ignored before alias + effective_k_cil wiring)."""
    ctx = _FakeCtx()
    low_cn = run_optimize(
        {
            "base_params": {"p80": 75, "cn": 250, "do": 8.0, "srt": 24},
            "grid": {"p80": [75], "cn": [250], "do": [8.0], "srt": [24]},
        },
        ctx,
    )
    high_cn = run_optimize(
        {
            "base_params": {"p80": 75, "cn": 450, "do": 8.0, "srt": 24},
            "grid": {"p80": [75], "cn": [450], "do": [8.0], "srt": [24]},
        },
        _FakeCtx(),
    )
    assert high_cn["all_results"][0]["expected_recovery"] >= low_cn["all_results"][0]["expected_recovery"]
    assert "k_cil_effective" in high_cn["all_results"][0]


def test_uncertainty_adds_percentile_bands():
    ctx = _FakeCtx()
    payload = {
        "base_params": {"p80": 75, "cn": 350, "do": 8.0, "srt": 24, "k_cil": 0.35},
        "grid": {"p80": [75], "cn": [350], "do": [8.0], "srt": [24]},
        "uncertainty": {"n_samples": 12, "seed": 7, "relative_sigma": {"k_cil": 0.2}},
    }
    result = run_optimize(payload, ctx)
    row = result["all_results"][0]
    assert "expected_recovery_p10" in row
    assert "expected_recovery_p90" in row
    assert result["uncertainty"]["n_samples"] == 12


def test_run_optimize_rejects_oversized_grid():
    ctx = _FakeCtx()
    many = list(range(60))
    payload = {
        "base_params": {"p80": 75, "cn": 350, "do": 7, "srt": 24},
        "grid": {"p80": many, "cn": many, "do": [7.0], "srt": [24]},
    }
    with pytest.raises(ValueError, match="max"):
        run_optimize(payload, ctx)
