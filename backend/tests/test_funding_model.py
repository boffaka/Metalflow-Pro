"""Tests for the funding model engine (pure functions, no DB)."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engines.funding_model import (
    FundingParams,
    compute_debt_schedule,
    compute_dscr,
    analyze_funding,
    compare_scenarios,
)

pytestmark = pytest.mark.no_db


class TestDebtSchedule:
    """Debt service schedule computation."""

    def test_zero_debt_returns_empty(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.0,
            interest_rate=0.08,
            tenor_years=10,
            project_cashflows=[20_000_000] * 10,
        )
        schedule = compute_debt_schedule(params)
        assert schedule == []

    def test_equal_principal_schedule(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.5,
            interest_rate=0.08,
            tenor_years=5,
            project_cashflows=[30_000_000] * 5,
        )
        schedule = compute_debt_schedule(params)
        assert len(schedule) == 5
        # First year: 50M debt, principal = 10M, interest = 4M
        assert schedule[0].opening_balance == 50_000_000
        assert schedule[0].principal_payment == 10_000_000
        assert schedule[0].interest_payment == 4_000_000
        # Last year: closing balance should be 0
        assert schedule[-1].closing_balance == 0

    def test_grace_period(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.6,
            interest_rate=0.10,
            tenor_years=8,
            grace_period_years=2,
            project_cashflows=[25_000_000] * 8,
        )
        schedule = compute_debt_schedule(params)
        # During grace period: principal = 0
        assert schedule[0].principal_payment == 0
        assert schedule[1].principal_payment == 0
        # Interest still charged
        assert schedule[0].interest_payment == 6_000_000  # 60M * 10%
        # After grace: principal repayment starts
        assert schedule[2].principal_payment > 0

    def test_annuity_repayment(self):
        params = FundingParams(
            total_capex_usd=50_000_000,
            debt_ratio=0.4,
            interest_rate=0.07,
            tenor_years=7,
            repayment_type="annuity",
            project_cashflows=[15_000_000] * 7,
        )
        schedule = compute_debt_schedule(params)
        # Annuity: total payment should be roughly constant
        payments = [row.total_payment for row in schedule]
        # Allow 1% tolerance
        assert max(payments) - min(payments) < payments[0] * 0.01
        # Final balance should be ~0
        assert schedule[-1].closing_balance < 1.0

    def test_tax_shield_computed(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.5,
            interest_rate=0.08,
            tenor_years=5,
            tax_rate=0.30,
            project_cashflows=[30_000_000] * 5,
        )
        schedule = compute_debt_schedule(params)
        # Tax shield = interest * tax_rate
        assert schedule[0].tax_shield == 4_000_000 * 0.30  # 1,200,000


class TestDSCR:
    """Debt Service Coverage Ratio."""

    def test_healthy_dscr(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.4,
            interest_rate=0.07,
            tenor_years=8,
            project_cashflows=[25_000_000] * 8,
        )
        schedule = compute_debt_schedule(params)
        dscr = compute_dscr(params.project_cashflows, schedule)
        # With 25M CF and ~7.8M debt service, DSCR should be > 2
        assert all(d > 2.0 for d in dscr)

    def test_tight_dscr(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.7,
            interest_rate=0.10,
            tenor_years=5,
            project_cashflows=[20_000_000] * 5,
        )
        schedule = compute_debt_schedule(params)
        dscr = compute_dscr(params.project_cashflows, schedule)
        # High leverage + high rate = tight DSCR
        assert min(dscr) < 2.0


class TestAnalyzeFunding:
    """Full funding analysis."""

    def test_100_percent_equity(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.0,
            interest_rate=0.0,
            tenor_years=1,
            project_cashflows=[30_000_000] * 10,
        )
        result = analyze_funding(params)
        assert result.equity_amount == 100_000_000
        assert result.debt_amount == 0
        assert result.total_interest_paid == 0
        assert result.project_irr is not None
        # With 100% equity, project IRR = equity IRR
        assert result.equity_irr == result.project_irr

    def test_leverage_increases_equity_irr(self):
        """Financial leverage should increase equity IRR when project IRR > cost of debt."""
        cashflows = [30_000_000] * 12

        # Unlevered
        params_equity = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.0,
            interest_rate=0.0,
            tenor_years=1,
            project_cashflows=cashflows,
        )
        result_equity = analyze_funding(params_equity)

        # Levered 60/40
        params_levered = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.4,
            interest_rate=0.07,
            tenor_years=10,
            project_cashflows=cashflows,
        )
        result_levered = analyze_funding(params_levered)

        # Equity IRR should be higher with leverage (positive leverage effect)
        assert result_levered.equity_irr > result_equity.equity_irr
        assert result_levered.irr_uplift > 0

    def test_dscr_metrics(self):
        params = FundingParams(
            total_capex_usd=150_000_000,
            debt_ratio=0.5,
            interest_rate=0.08,
            tenor_years=10,
            project_cashflows=[35_000_000] * 12,
        )
        result = analyze_funding(params)
        assert result.min_dscr is not None
        assert result.min_dscr > 0
        assert result.avg_dscr >= result.min_dscr

    def test_equity_payback(self):
        params = FundingParams(
            total_capex_usd=100_000_000,
            debt_ratio=0.4,
            interest_rate=0.07,
            tenor_years=8,
            project_cashflows=[25_000_000] * 12,
        )
        result = analyze_funding(params)
        assert result.equity_payback_years is not None
        assert result.equity_payback_years > 0
        assert result.equity_payback_years < 12


class TestCompareScenarios:
    """Multi-scenario comparison."""

    def test_default_scenarios(self):
        results = compare_scenarios(
            total_capex=100_000_000,
            project_cashflows=[25_000_000] * 12,
        )
        assert len(results) == 4  # 4 default scenarios
        # 100% equity should have 0 interest
        assert results[0]["total_interest"] == 0
        # Higher debt = higher interest
        assert results[-1]["total_interest"] > results[1]["total_interest"]

    def test_custom_scenarios(self):
        custom = [
            {"name": "Conservative", "debt_ratio": 0.2, "interest_rate": 0.06, "tenor": 5},
            {"name": "Aggressive", "debt_ratio": 0.7, "interest_rate": 0.09, "tenor": 12},
        ]
        results = compare_scenarios(
            total_capex=200_000_000,
            project_cashflows=[40_000_000] * 15,
            scenarios=custom,
        )
        assert len(results) == 2
        assert results[0]["scenario"] == "Conservative"
        assert results[1]["scenario"] == "Aggressive"
        assert results[1]["debt_amount"] > results[0]["debt_amount"]

    def test_all_scenarios_have_required_fields(self):
        results = compare_scenarios(
            total_capex=100_000_000,
            project_cashflows=[20_000_000] * 10,
        )
        required_fields = [
            "scenario", "debt_ratio", "equity_amount", "debt_amount",
            "project_irr", "equity_irr", "project_npv", "equity_npv",
            "min_dscr", "total_interest", "equity_payback_years",
        ]
        for r in results:
            for field in required_fields:
                assert field in r, f"Missing field: {field} in scenario {r['scenario']}"
