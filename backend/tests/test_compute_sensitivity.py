"""Layer 2 — pure compute tests for sensitivity (no DB, no worker)."""
from __future__ import annotations

import pytest

from backend.compute.sensitivity import run_spider, run_tornado
from backend.jobs.context import JobContext


class _FakeCtx:
    """Minimal ctx compatible with JobContext.check_cancelled / report_progress."""
    def __init__(self, cancel_after: int | None = None):
        self.calls = 0
        self.cancel_after = cancel_after
        self.progress_calls: list[tuple[int, int, str | None]] = []

    def check_cancelled(self) -> None:
        self.calls += 1
        if self.cancel_after is not None and self.calls > self.cancel_after:
            from backend.jobs.context import JobCancelled
            raise JobCancelled()

    def report_progress(self, current: int, total: int, message: str | None = None) -> None:
        self.progress_calls.append((current, total, message))


BASE_PARAMS = {"p80": 75.0, "cn": 350.0, "do": 7.0, "srt": 24.0}


def test_spider_returns_one_series_per_param():
    ctx = _FakeCtx()
    result = run_spider(
        {"base_params": BASE_PARAMS, "params_to_vary": ["p80", "cn"],
         "delta_pcts": [-10.0, -5.0, 0.0, 5.0, 10.0]},
        ctx,
    )
    assert "series" in result
    assert {s["param_key"] for s in result["series"]} == {"p80", "cn"}
    for s in result["series"]:
        assert len(s["points"]) == 5
        for p in s["points"]:
            assert {"delta_pct", "recovery_pct", "energy_kwh_t"} <= set(p.keys())


def test_spider_calls_check_cancelled_per_param():
    ctx = _FakeCtx()
    run_spider(
        {"base_params": BASE_PARAMS, "params_to_vary": ["p80", "cn", "do"],
         "delta_pcts": [-5.0, 5.0]},
        ctx,
    )
    # one call before each param's inner loop, plus a final call at the end
    assert ctx.calls >= 3


def test_spider_cancellable_mid_iteration():
    from backend.jobs.context import JobCancelled
    ctx = _FakeCtx(cancel_after=1)
    with pytest.raises(JobCancelled):
        run_spider(
            {"base_params": BASE_PARAMS, "params_to_vary": ["p80", "cn", "do"],
             "delta_pcts": [-5.0, 5.0]},
            ctx,
        )


def test_tornado_returns_ranked_rows():
    ctx = _FakeCtx()
    result = run_tornado(
        {"base_params": BASE_PARAMS, "params_to_vary": ["p80", "cn"],
         "delta_pcts": [10.0]},
        ctx,
    )
    assert "rows" in result
    # 2 params x 1 delta x 2 signs = 4 rows
    assert len(result["rows"]) == 4
    ranks = [r["rank"] for r in result["rows"]]
    assert ranks == sorted(ranks) == list(range(1, 5))


def test_tornado_rows_sorted_by_abs_impact_desc():
    ctx = _FakeCtx()
    result = run_tornado(
        {"base_params": BASE_PARAMS, "params_to_vary": ["p80", "cn", "do"],
         "delta_pcts": [10.0]},
        ctx,
    )
    impacts = [abs(r["impact_recovery"]) for r in result["rows"]]
    assert impacts == sorted(impacts, reverse=True)


def test_spider_reports_progress():
    ctx = _FakeCtx()
    run_spider(
        {"base_params": BASE_PARAMS, "params_to_vary": ["p80", "cn"],
         "delta_pcts": [-5.0, 0.0, 5.0]},
        ctx,
    )
    # 2 params * 3 deltas = 6 inner iterations -> at least one progress call
    assert len(ctx.progress_calls) >= 1
    last = ctx.progress_calls[-1]
    assert last[0] == last[1]  # final progress equals total
