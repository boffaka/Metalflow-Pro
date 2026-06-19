"""
MPDPMS — Funding Model API (Debt/Equity Capital Structure).

Endpoints for project financing analysis:
- POST /funding/analyze — Single scenario analysis
- POST /funding/compare — Multi-scenario comparison
"""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

try:
    from ..auth import project_user
    from ..db import qone
    from .. import config as _app_config
    from ..engines.funding_model import FundingParams, analyze_funding, compare_scenarios
except ImportError:  # pragma: no cover
    from auth import project_user

try:
    from ..constants import TROY_OZ_PER_GRAM
except ImportError:  # pragma: no cover - supports direct script imports
    from constants import TROY_OZ_PER_GRAM
    from db import qone
    import config as _app_config
    from engines.funding_model import FundingParams, analyze_funding, compare_scenarios

logger = logging.getLogger("mpdpms.funding")

router = APIRouter(prefix="/api/v1/projects/{pid}/funding", tags=["funding"])


class FundingAnalyzeRequest(BaseModel):
    """Request body for single funding scenario analysis."""
    debt_ratio: float = Field(0.4, ge=0.0, le=0.9, description="Debt as fraction of CAPEX (0-0.9)")
    interest_rate: float = Field(0.075, ge=0.0, le=0.25, description="Annual interest rate")
    tenor_years: int = Field(10, ge=1, le=30, description="Loan duration in years")
    grace_period_years: int = Field(0, ge=0, le=5, description="Interest-only period")
    repayment_type: Literal["equal_principal", "annuity"] = "equal_principal"
    discount_rate: float = Field(0.08, ge=0.0, le=0.30, description="Discount rate for NPV")
    tax_rate: float = Field(0.30, ge=0.0, le=0.50, description="Corporate tax rate")
    # Optional override — if not provided, uses project economics
    capex_override: float | None = Field(None, description="Override CAPEX (USD)")
    cashflows_override: list[float] | None = Field(None, description="Override annual cashflows")


class FundingCompareRequest(BaseModel):
    """Request body for multi-scenario comparison."""
    scenarios: list[dict] | None = Field(None, description="Custom scenarios (null = default 4)")
    discount_rate: float = Field(0.08, ge=0.0, le=0.30)
    tax_rate: float = Field(0.30, ge=0.0, le=0.50)


def _get_project_financials(pid: str) -> tuple[float, list[float]]:
    """Fetch CAPEX and cashflows from project economics."""
    # Try to get from DCF results
    row = qone(
        "SELECT results FROM economics_runs "
        "WHERE project_id = %s AND run_type = 'dcf' AND status = 'done' "
        "ORDER BY created_at DESC LIMIT 1",
        (pid,),
    )
    if row and row.get("results"):
        import json
        results = row["results"] if isinstance(row["results"], dict) else json.loads(row["results"])
        capex = results.get("capex_total", 0) or results.get("total_capex", 0)
        cashflows = results.get("cashflows", [])
        if capex > 0 and cashflows:
            return capex, cashflows

    # Fallback: estimate from project parameters
    project = qone("SELECT target_tph, gold_grade_g_t, mine_life_years FROM projects WHERE id = %s", (pid,))
    if not project:
        raise HTTPException(404, "Project not found")

    tph = float(project.get("target_tph") or _app_config.FUNDING_FALLBACK_TPH)
    grade = float(project.get("gold_grade_g_t") or _app_config.FUNDING_FALLBACK_GRADE_G_T)
    mine_life = int(project.get("mine_life_years") or 10)

    # Industry estimate: CAPEX ~$30k-50k per daily tonne
    daily_tonnes = tph * 24
    capex = daily_tonnes * _app_config.FUNDING_CAPEX_USD_PER_DAILY_TONNE

    # Annual revenue estimate
    avail = _app_config.DEFAULT_AVAILABILITY_PCT / 100.0
    annual_tonnes = tph * 24 * 365 * avail
    recovery = _app_config.FUNDING_FALLBACK_RECOVERY
    gold_oz = annual_tonnes * grade * recovery * TROY_OZ_PER_GRAM
    gold_price = float(_app_config.DEFAULT_GOLD_PRICE_USD_OZ)
    revenue = gold_oz * gold_price
    opex = annual_tonnes * _app_config.FUNDING_FALLBACK_OPEX_USD_PER_T
    annual_cf = revenue - opex

    cashflows = [annual_cf] * mine_life
    return capex, cashflows


@router.post("/analyze")
def analyze_project_funding(pid: str, body: FundingAnalyzeRequest, user=Depends(project_user)):
    """
    Analyze a single funding scenario for the project.

    Returns debt schedule, DSCR, IRR comparison (project vs equity), and payback.
    """
    try:
        capex, cashflows = _get_project_financials(pid)

        if body.capex_override:
            capex = body.capex_override
        if body.cashflows_override:
            cashflows = body.cashflows_override

        if capex <= 0:
            raise HTTPException(400, "CAPEX must be positive. Run economics/DCF first or provide capex_override.")

        params = FundingParams(
            total_capex_usd=capex,
            debt_ratio=body.debt_ratio,
            interest_rate=body.interest_rate,
            tenor_years=body.tenor_years,
            grace_period_years=body.grace_period_years,
            repayment_type=body.repayment_type,
            project_cashflows=cashflows,
            discount_rate=body.discount_rate,
            tax_rate=body.tax_rate,
        )

        result = analyze_funding(params)

        return {
            "total_capex": result.total_capex,
            "equity_amount": result.equity_amount,
            "debt_amount": result.debt_amount,
            "debt_ratio": result.debt_ratio,
            "equity_ratio": result.equity_ratio,
            "project_irr": result.project_irr,
            "equity_irr": result.equity_irr,
            "irr_uplift": result.irr_uplift,
            "project_npv": result.project_npv,
            "equity_npv": result.equity_npv,
            "min_dscr": result.min_dscr,
            "avg_dscr": result.avg_dscr,
            "total_interest_paid": result.total_interest_paid,
            "total_debt_service": result.total_debt_service,
            "equity_payback_years": result.equity_payback_years,
            "debt_schedule": [
                {
                    "year": row.year,
                    "opening_balance": row.opening_balance,
                    "principal": row.principal_payment,
                    "interest": row.interest_payment,
                    "total_payment": row.total_payment,
                    "closing_balance": row.closing_balance,
                    "tax_shield": row.tax_shield,
                }
                for row in result.debt_schedule
            ],
            "dscr_by_year": result.dscr_by_year,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Funding analysis failed: %s", e)
        raise HTTPException(500, f"Funding analysis error: {str(e)}")


@router.post("/compare")
def compare_funding_scenarios(pid: str, body: FundingCompareRequest, user=Depends(project_user)):
    """
    Compare multiple funding scenarios side by side.

    Default: 100% equity, 70/30, 60/40, 50/50.
    """
    try:
        capex, cashflows = _get_project_financials(pid)

        results = compare_scenarios(
            total_capex=capex,
            project_cashflows=cashflows,
            scenarios=body.scenarios,
            discount_rate=body.discount_rate,
            tax_rate=body.tax_rate,
        )

        return {
            "project_capex": capex,
            "mine_life_years": len(cashflows),
            "scenarios": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Funding comparison failed: %s", e)
        raise HTTPException(500, f"Funding comparison error: {str(e)}")
