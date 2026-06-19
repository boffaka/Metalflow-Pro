"""
MPDPMS — Funding Model Engine (Debt/Equity Capital Structure).

Pure functions for modeling project financing:
- Debt service schedule (principal + interest)
- DSCR (Debt Service Coverage Ratio)
- Equity IRR vs Project IRR
- Multiple funding scenarios (100% equity, 60/40, 70/30)

All functions are pure (no DB, no I/O) — testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class FundingParams:
    """Parameters for a project funding structure."""
    total_capex_usd: float
    debt_ratio: float  # 0.0 to 1.0 (e.g., 0.6 = 60% debt)
    interest_rate: float  # Annual rate (e.g., 0.08 = 8%)
    tenor_years: int  # Loan duration
    grace_period_years: int = 0  # Interest-only period
    repayment_type: Literal["equal_principal", "annuity"] = "equal_principal"
    # Project cash flows (annual, starting from year 1)
    project_cashflows: list[float] = field(default_factory=list)
    discount_rate: float = 0.08  # For NPV calculations
    tax_rate: float = 0.30  # Corporate tax rate for tax shield


@dataclass
class DebtScheduleRow:
    """Single year of debt service."""
    year: int
    opening_balance: float
    principal_payment: float
    interest_payment: float
    total_payment: float
    closing_balance: float
    tax_shield: float  # Interest × tax_rate


@dataclass
class FundingResult:
    """Complete funding analysis result."""
    # Structure
    total_capex: float
    equity_amount: float
    debt_amount: float
    debt_ratio: float
    equity_ratio: float
    # Debt schedule
    debt_schedule: list[DebtScheduleRow]
    total_interest_paid: float
    total_debt_service: float
    # IRR comparison
    project_irr: float | None  # Unlevered
    equity_irr: float | None  # Levered
    irr_uplift: float | None  # equity_irr - project_irr
    # NPV
    project_npv: float
    equity_npv: float
    # DSCR
    dscr_by_year: list[float]
    min_dscr: float
    avg_dscr: float
    # Payback
    equity_payback_years: float | None


def compute_debt_schedule(params: FundingParams) -> list[DebtScheduleRow]:
    """
    Compute the annual debt service schedule.

    Supports:
    - Equal principal repayment (constant amortization)
    - Annuity (constant total payment)
    - Grace period (interest-only)
    """
    debt_amount = params.total_capex_usd * params.debt_ratio
    if debt_amount <= 0 or params.tenor_years <= 0:
        return []

    schedule: list[DebtScheduleRow] = []
    balance = debt_amount
    repayment_years = params.tenor_years - params.grace_period_years

    if params.repayment_type == "annuity" and repayment_years > 0:
        # Annuity payment (PMT formula)
        r = params.interest_rate
        n = repayment_years
        if r > 0:
            annuity = balance * (r * (1 + r) ** n) / ((1 + r) ** n - 1)
        else:
            annuity = balance / n
    else:
        annuity = 0  # Not used for equal_principal

    for year in range(1, params.tenor_years + 1):
        interest = balance * params.interest_rate

        if year <= params.grace_period_years:
            # Grace period: interest only
            principal = 0.0
        elif params.repayment_type == "annuity":
            principal = annuity - interest
        else:
            # Equal principal
            principal = debt_amount / repayment_years if repayment_years > 0 else 0

        # Ensure we don't overpay
        principal = min(principal, balance)
        total = principal + interest
        closing = balance - principal
        tax_shield = interest * params.tax_rate

        schedule.append(DebtScheduleRow(
            year=year,
            opening_balance=round(balance, 2),
            principal_payment=round(principal, 2),
            interest_payment=round(interest, 2),
            total_payment=round(total, 2),
            closing_balance=round(max(0, closing), 2),
            tax_shield=round(tax_shield, 2),
        ))
        balance = max(0, closing)

    return schedule


def compute_dscr(
    project_cashflows: list[float],
    debt_schedule: list[DebtScheduleRow],
) -> list[float]:
    """
    Compute Debt Service Coverage Ratio for each year.

    DSCR = Operating Cash Flow / Total Debt Service
    DSCR > 1.2 is typically required by lenders.
    """
    dscr_list: list[float] = []
    for i, row in enumerate(debt_schedule):
        if row.total_payment <= 0:
            dscr_list.append(float('inf'))
            continue
        cf = project_cashflows[i] if i < len(project_cashflows) else 0
        dscr = cf / row.total_payment if row.total_payment > 0 else float('inf')
        dscr_list.append(round(dscr, 3))
    return dscr_list


def _irr(cashflows: list[float], max_iter: int = 200, tol: float = 1e-8) -> float | None:
    """Newton-Raphson IRR calculation."""
    if not cashflows or all(cf == 0 for cf in cashflows):
        return None

    # Initial guess
    rate = 0.10
    for _ in range(max_iter):
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))
        dnpv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cashflows))
        if abs(dnpv) < 1e-12:
            break
        new_rate = rate - npv / dnpv
        if abs(new_rate - rate) < tol:
            return round(new_rate * 100, 2)  # Return as percentage
        rate = new_rate
        # Guard against divergence
        if rate < -0.99 or rate > 10.0:
            return None
    return round(rate * 100, 2) if abs(sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))) < 1.0 else None


def _npv(cashflows: list[float], rate: float) -> float:
    """Compute NPV at given discount rate."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))


def _payback(cashflows: list[float]) -> float | None:
    """Compute simple payback period (years)."""
    cumulative = 0.0
    for i, cf in enumerate(cashflows):
        cumulative += cf
        if cumulative >= 0 and i > 0:
            # Interpolate
            prev = cumulative - cf
            if cf > 0:
                return i - 1 + abs(prev) / cf
            return float(i)
    return None


def analyze_funding(params: FundingParams) -> FundingResult:
    """
    Complete funding analysis combining debt schedule, DSCR, and IRR comparison.

    Returns a FundingResult with all metrics needed for BFS-level reporting.
    """
    debt_amount = params.total_capex_usd * params.debt_ratio
    equity_amount = params.total_capex_usd * (1 - params.debt_ratio)

    # Debt schedule
    schedule = compute_debt_schedule(params)

    # Total interest and debt service
    total_interest = sum(row.interest_payment for row in schedule)
    total_service = sum(row.total_payment for row in schedule)

    # DSCR
    dscr_list = compute_dscr(params.project_cashflows, schedule)
    finite_dscr = [d for d in dscr_list if d != float('inf')]
    min_dscr = min(finite_dscr) if finite_dscr else float('inf')
    avg_dscr = sum(finite_dscr) / len(finite_dscr) if finite_dscr else float('inf')

    # Project IRR (unlevered) — initial investment + project cashflows
    project_cf = [-params.total_capex_usd] + params.project_cashflows
    project_irr = _irr(project_cf)
    project_npv = _npv(project_cf, params.discount_rate)

    # Equity IRR (levered) — equity investment + (project CF - debt service + tax shield)
    equity_cf = [-equity_amount]
    for i, cf in enumerate(params.project_cashflows):
        debt_payment = schedule[i].total_payment if i < len(schedule) else 0
        tax_shield = schedule[i].tax_shield if i < len(schedule) else 0
        equity_cf.append(cf - debt_payment + tax_shield)

    equity_irr = _irr(equity_cf)
    equity_npv = _npv(equity_cf, params.discount_rate)

    # IRR uplift
    irr_uplift = None
    if project_irr is not None and equity_irr is not None:
        irr_uplift = round(equity_irr - project_irr, 2)

    # Equity payback
    equity_payback = _payback(equity_cf)

    return FundingResult(
        total_capex=params.total_capex_usd,
        equity_amount=equity_amount,
        debt_amount=debt_amount,
        debt_ratio=params.debt_ratio,
        equity_ratio=1 - params.debt_ratio,
        debt_schedule=schedule,
        total_interest_paid=round(total_interest, 2),
        total_debt_service=round(total_service, 2),
        project_irr=project_irr,
        equity_irr=equity_irr,
        irr_uplift=irr_uplift,
        project_npv=round(project_npv, 2),
        equity_npv=round(equity_npv, 2),
        dscr_by_year=dscr_list,
        min_dscr=round(min_dscr, 3) if min_dscr != float('inf') else None,
        avg_dscr=round(avg_dscr, 3) if avg_dscr != float('inf') else None,
        equity_payback_years=round(equity_payback, 1) if equity_payback else None,
    )


def compare_scenarios(
    total_capex: float,
    project_cashflows: list[float],
    scenarios: list[dict] | None = None,
    discount_rate: float = 0.08,
    tax_rate: float = 0.30,
) -> list[dict]:
    """
    Compare multiple funding scenarios side by side.

    Default scenarios: 100% equity, 70/30, 60/40, 50/50.
    """
    if scenarios is None:
        scenarios = [
            {"name": "100% Equity", "debt_ratio": 0.0, "interest_rate": 0.0, "tenor": 1},
            {"name": "70% Equity / 30% Debt", "debt_ratio": 0.30, "interest_rate": 0.07, "tenor": 8},
            {"name": "60% Equity / 40% Debt", "debt_ratio": 0.40, "interest_rate": 0.075, "tenor": 10},
            {"name": "50% Equity / 50% Debt", "debt_ratio": 0.50, "interest_rate": 0.08, "tenor": 10},
        ]

    results = []
    for sc in scenarios:
        params = FundingParams(
            total_capex_usd=total_capex,
            debt_ratio=sc.get("debt_ratio", 0),
            interest_rate=sc.get("interest_rate", 0.08),
            tenor_years=sc.get("tenor", 10),
            grace_period_years=sc.get("grace_period", 0),
            repayment_type=sc.get("repayment_type", "equal_principal"),
            project_cashflows=project_cashflows,
            discount_rate=discount_rate,
            tax_rate=tax_rate,
        )
        result = analyze_funding(params)
        results.append({
            "scenario": sc.get("name", f"{int(sc['debt_ratio']*100)}% Debt"),
            "debt_ratio": result.debt_ratio,
            "equity_amount": result.equity_amount,
            "debt_amount": result.debt_amount,
            "project_irr": result.project_irr,
            "equity_irr": result.equity_irr,
            "irr_uplift": result.irr_uplift,
            "project_npv": result.project_npv,
            "equity_npv": result.equity_npv,
            "min_dscr": result.min_dscr,
            "avg_dscr": result.avg_dscr,
            "total_interest": result.total_interest_paid,
            "equity_payback_years": result.equity_payback_years,
        })

    return results
