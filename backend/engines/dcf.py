# backend/engines/dcf.py
"""
DCF (Discounted Cash Flow) engine.

Implements:
  - Annual cashflow model (Revenue → FCF)
  - NPV (Net Present Value)
  - IRR (Internal Rate of Return) via Newton-Raphson
  - AISC (All-In Sustaining Cost) — WGC 2013 standard
"""

from __future__ import annotations
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def build_cashflows(
    mine_life_years: int,
    annual_oz: float,
    au_price: float,
    royalty_pct: float,
    opex_annual: float,
    sustaining_capex_annual: float,
    tax_rate: float,
    discount_rate: float,
    rampup_factors: Optional[List[float]] = None,
    refining_tc_rc: float = 4.0,
    initial_capex: float = 0.0,
) -> List[dict]:
    """
    Build annual cashflow table.

    FCF_y = Revenue_y - OPEX_y - Tax_y - sustaining_capex

    Args:
        mine_life_years: Project duration (years)
        annual_oz: Annual steady-state gold production (oz)
        au_price: Gold price assumption ($/oz)
        royalty_pct: Royalty rate (%)
        opex_annual: Annual operating cost ($)
        sustaining_capex_annual: Annual sustaining capital ($)
        tax_rate: Corporate income tax rate (%)
        discount_rate: Discount rate as PERCENTAGE (e.g. 5.0 for 5%) — note: compute_npv takes a fraction
        rampup_factors: Production factors by year (defaults to full production)
        refining_tc_rc: Refining charges ($/oz)
        initial_capex: Initial capital investment for straight-line depreciation ($)
    Returns:
        List of dicts: [{year, oz_produced, revenue, opex, ebitda, tax, fcf, npv_cumulative}]
        npv_cumulative is the running project NPV (seeded at -initial_capex); the
        final row equals compute_npv(fcf_list, discount_rate/100, initial_capex).
    """
    try:
        if rampup_factors is None:
            rampup_factors = [1.0] * mine_life_years
        while len(rampup_factors) < mine_life_years:
            rampup_factors.append(1.0)

        r = discount_rate / 100.0
        cashflows = []
        # Seed the cumulative-NPV line with the Year-0 CAPEX outflow so the column
        # represents true cumulative project NPV: it starts negative, and its
        # zero-crossing is the discounted payback. The final row then equals the
        # project NPV (compute_npv with the same inputs).
        npv_cumulative = -float(initial_capex)

        # Straight-line depreciation on initial CAPEX over mine life
        annual_depreciation = initial_capex / mine_life_years if mine_life_years > 0 else 0.0

        for y in range(1, mine_life_years + 1):
            factor = rampup_factors[y - 1]
            oz = annual_oz * factor
            revenue = oz * au_price * (1.0 - royalty_pct / 100.0) - oz * refining_tc_rc
            ebitda = revenue - opex_annual * factor
            depreciation = annual_depreciation
            taxable = max(0.0, ebitda - depreciation - sustaining_capex_annual)
            tax = taxable * (tax_rate / 100.0)
            fcf = ebitda - tax - sustaining_capex_annual
            npv_cumulative += fcf / (1.0 + r) ** y

            cashflows.append(
                {
                    "year": y,
                    "oz_produced": round(oz, 0),
                    "revenue": round(revenue, 2),
                    "opex": round(opex_annual * factor, 2),
                    "ebitda": round(ebitda, 2),
                    "tax": round(tax, 2),
                    "fcf": round(fcf, 2),
                    "npv_cumulative": round(npv_cumulative, 2),
                }
            )

        return cashflows
    except Exception as e:
        logger.error(
            "build_cashflows failed (mine_life=%d, annual_oz=%.0f, au_price=%.2f): %s",
            mine_life_years,
            annual_oz,
            au_price,
            e,
        )
        raise RuntimeError(f"build_cashflows failed for mine_life={mine_life_years}") from e


def compute_npv(
    cashflows: List[float],
    discount_rate: float,
    initial_capex: float,
) -> float:
    """
    Net Present Value.

    NPV = Σ FCF_y / (1 + r)^y − Initial_CAPEX

    Args:
        cashflows: List of FCF per year (Year 1 first)
        discount_rate: Annual discount rate as FRACTION (e.g. 0.05 for 5%) — note: build_cashflows takes a percentage
        initial_capex: Initial capital investment (Year 0, positive number)
    Returns:
        NPV ($)
    """
    try:
        r = discount_rate
        npv = -initial_capex
        for y, cf in enumerate(cashflows, start=1):
            npv += cf / (1.0 + r) ** y
        return npv
    except Exception as e:
        logger.error("compute_npv failed (discount_rate=%.4f, initial_capex=%.0f): %s", discount_rate, initial_capex, e)
        raise RuntimeError(f"compute_npv failed for discount_rate={discount_rate}") from e


def compute_irr(
    cashflows: List[float],
    initial_capex: float,
    max_iter: int = 1000,
    tolerance: float = 1e-6,
) -> Optional[float]:
    """
    Internal Rate of Return — Newton-Raphson.

    Args:
        cashflows: Annual FCF (Year 1+)
        initial_capex: Year 0 outflow (positive)
        max_iter: Newton-Raphson iterations
        tolerance: Convergence tolerance
    Returns:
        IRR (fraction) or None if no convergence
    """
    try:

        def npv_at_rate(r):
            pv = -initial_capex
            for y, cf in enumerate(cashflows, 1):
                pv += cf / (1.0 + r) ** y
            return pv

        def dnpv_at_rate(r):
            d = 0.0
            for y, cf in enumerate(cashflows, 1):
                d -= y * cf / (1.0 + r) ** (y + 1)
            return d

        r = 0.10
        for _ in range(max_iter):
            f = npv_at_rate(r)
            df = dnpv_at_rate(r)
            if abs(df) < 1e-12:
                break  # flat derivative — hand off to bracketing fallback
            r_new = r - f / df
            # Keep iterates in the economically meaningful domain; if Newton
            # overshoots past -1, abandon it for the bracketing fallback.
            if r_new <= -1.0 or r_new >= 10.0:
                break
            if abs(r_new - r) < tolerance:
                return r_new if -1.0 < r_new < 10.0 else None
            r = r_new

        # Bracketing fallback: Newton diverged or stalled. Scan for a sign change
        # of NPV across the valid domain and bisect. Robust for non-conventional
        # streams (e.g. terminal closure-cost outflows) where Newton is unreliable.
        return _irr_bisection(npv_at_rate, tolerance)
    except Exception as e:
        logger.error("compute_irr failed (initial_capex=%.0f, n_cashflows=%d): %s", initial_capex, len(cashflows), e)
        return None


def _irr_bisection(npv_at_rate, tolerance: float, lo: float = -0.99, hi: float = 10.0, steps: int = 200):
    """Find the smallest root of NPV(r)=0 on (lo, hi] by sign-change scan + bisection."""
    try:
        prev_r = lo
        prev_f = npv_at_rate(prev_r)
        for i in range(1, steps + 1):
            cur_r = lo + (hi - lo) * i / steps
            cur_f = npv_at_rate(cur_r)
            if prev_f == 0.0:
                return prev_r
            if (prev_f < 0.0) != (cur_f < 0.0):
                a, fa, b = prev_r, prev_f, cur_r
                for _ in range(100):
                    mid = (a + b) / 2.0
                    fm = npv_at_rate(mid)
                    if abs(fm) < tolerance or (b - a) / 2.0 < 1e-9:
                        return mid
                    if (fa < 0.0) != (fm < 0.0):
                        b = mid
                    else:
                        a, fa = mid, fm
                return (a + b) / 2.0
            prev_r, prev_f = cur_r, cur_f
        return None
    except Exception as e:
        logger.error("_irr_bisection failed: %s", e)
        return None


def compute_aisc(
    opex_mining: float,
    opex_processing: float,
    ga: float,
    byproduct_credits: float,
    sustaining_capex: float,
    royalties: float,
    exploration: float,
    corporate_ga: float,
    oz_produced: float,
) -> float:
    """
    AISC — All-In Sustaining Cost (WGC 2013 standard).

    AISC = (Cash_Cost + sustaining_CAPEX + royalties + exploration + corporate_G&A) / oz

    All inputs are annual dollar totals (not per-oz), per WGC 2013 standard.
    Returns $/oz.
    """
    try:
        if oz_produced <= 0:
            return 0.0
        cash_cost = opex_mining + opex_processing + ga - byproduct_credits
        all_in_cost = cash_cost + sustaining_capex + royalties
        aisc_total = all_in_cost + exploration + corporate_ga
        return aisc_total / oz_produced
    except Exception as e:
        logger.error("compute_aisc failed (oz_produced=%.0f): %s", oz_produced, e)
        return 0.0


def compute_payback_period(
    cashflows: list[float],
    initial_capex: float,
) -> float | None:
    """
    Compute simple payback period (years).

    Payback = year when cumulative FCF >= initial CAPEX.

    Args:
        cashflows: Annual FCF (Year 1+)
        initial_capex: Initial capital investment (positive number)
    Returns:
        Payback period (years) or None if never recovered
    """
    try:
        cumulative = -initial_capex
        for y, cf in enumerate(cashflows, start=1):
            prev = cumulative
            cumulative += cf
            if cumulative >= 0 and prev < 0:
                # Interpolate within the year
                fraction = abs(prev) / cf if cf > 0 else 0
                return y - 1 + fraction
        return None  # Never recovered
    except Exception as e:
        logger.error("compute_payback_period failed: %s", e)
        return None


def sensitivity_analysis(
    base_params: dict,
    variables: list[str],
    variation_pct: float = 20.0,
    steps: int = 5,
) -> dict:
    """
    Sensitivity analysis (tornado chart data).

    Varies each parameter ±variation_pct% and computes NPV impact.

    Args:
        base_params: Base case parameters for build_cashflows + compute_npv
        variables: List of parameter names to vary
        variation_pct: Variation range (±%)
        steps: Number of steps per direction
    Returns:
        dict with tornado data: {variable: {low_npv, high_npv, swing}}
    """
    try:
        # Compute base NPV
        base_cfs = build_cashflows(
            mine_life_years=int(base_params.get("mine_life_years", 10)),
            annual_oz=float(base_params.get("annual_oz", 100_000)),
            au_price=float(base_params.get("au_price", 1900)),
            royalty_pct=float(base_params.get("royalty_pct", 3.0)),
            opex_annual=float(base_params.get("opex_annual", 25_000_000)),
            sustaining_capex_annual=float(base_params.get("sustaining_capex", 5_000_000)),
            tax_rate=float(base_params.get("tax_rate", 30.0)),
            discount_rate=float(base_params.get("discount_rate", 5.0)),
            initial_capex=float(base_params.get("initial_capex", 150_000_000)),
        )
        base_fcf = [cf["fcf"] for cf in base_cfs]
        base_npv = compute_npv(
            base_fcf,
            discount_rate=float(base_params.get("discount_rate", 5.0)) / 100.0,
            initial_capex=float(base_params.get("initial_capex", 150_000_000)),
        )

        results = {}
        for var in variables:
            base_val = float(base_params.get(var, 0))
            if base_val == 0:
                continue

            low_val = base_val * (1 - variation_pct / 100.0)
            high_val = base_val * (1 + variation_pct / 100.0)

            npvs = []
            for test_val in [low_val, high_val]:
                test_params = {**base_params, var: test_val}
                try:
                    cfs = build_cashflows(
                        mine_life_years=int(test_params.get("mine_life_years", 10)),
                        annual_oz=float(test_params.get("annual_oz", 100_000)),
                        au_price=float(test_params.get("au_price", 1900)),
                        royalty_pct=float(test_params.get("royalty_pct", 3.0)),
                        opex_annual=float(test_params.get("opex_annual", 25_000_000)),
                        sustaining_capex_annual=float(test_params.get("sustaining_capex", 5_000_000)),
                        tax_rate=float(test_params.get("tax_rate", 30.0)),
                        discount_rate=float(test_params.get("discount_rate", 5.0)),
                        initial_capex=float(test_params.get("initial_capex", 150_000_000)),
                    )
                    fcf = [cf["fcf"] for cf in cfs]
                    npv = compute_npv(
                        fcf,
                        discount_rate=float(test_params.get("discount_rate", 5.0)) / 100.0,
                        initial_capex=float(test_params.get("initial_capex", 150_000_000)),
                    )
                    npvs.append(npv)
                except Exception:
                    npvs.append(base_npv)

            low_npv, high_npv = npvs[0], npvs[1]
            swing = abs(high_npv - low_npv)
            impact_pct = (swing / abs(base_npv) * 100.0) if base_npv != 0 else 0.0

            results[var] = {
                "base_value": round(base_val, 4),
                "low_value": round(low_val, 4),
                "high_value": round(high_val, 4),
                # low_npv/high_npv are indexed by the INPUT value (NPV at low_value /
                # at high_value), not by NPV magnitude — so for cost variables
                # low_npv > high_npv. For unambiguous tornado rendering, use the
                # downside/upside fields below (min/max NPV regardless of direction).
                "low_npv": round(low_npv, 0),
                "high_npv": round(high_npv, 0),
                "downside_npv": round(min(low_npv, high_npv), 0),
                "upside_npv": round(max(low_npv, high_npv), 0),
                "base_npv": round(base_npv, 0),
                "swing": round(swing, 0),
                "impact_pct": round(impact_pct, 2),
                "variation_pct": variation_pct,
            }

        # Sort by swing (largest impact first — tornado chart order)
        sorted_results = dict(sorted(results.items(), key=lambda x: x[1]["swing"], reverse=True))

        return {
            "base_npv": round(base_npv, 0),
            "variables": sorted_results,
        }
    except Exception as e:
        logger.error("sensitivity_analysis failed: %s", e)
        raise RuntimeError(f"sensitivity_analysis failed: {e}") from e


def compute_break_even_price(
    cashflows_no_revenue: list[float],
    annual_oz: float,
    initial_capex: float,
    discount_rate: float,
    royalty_pct: float = 3.0,
    refining_tc_rc: float = 4.0,
    tolerance: float = 1.0,
    max_iter: int = 100,
) -> float | None:
    """
    Compute break-even gold price (NPV = 0).

    Uses bisection method to find the gold price where NPV = 0.

    Args:
        cashflows_no_revenue: FCF without revenue component (OPEX + tax + sustaining)
        annual_oz: Annual gold production (oz)
        initial_capex: Initial capital (positive)
        discount_rate: Discount rate as fraction (e.g. 0.05)
        royalty_pct: Royalty rate (%)
        refining_tc_rc: Refining charges ($/oz)
        tolerance: Price tolerance for convergence ($/oz)
        max_iter: Maximum bisection iterations
    Returns:
        Break-even gold price ($/oz) or None if not found
    """
    try:

        def npv_at_price(price: float) -> float:
            mine_life = len(cashflows_no_revenue)
            revenue_cfs = []
            for y in range(1, mine_life + 1):
                revenue = annual_oz * price * (1 - royalty_pct / 100.0) - annual_oz * refining_tc_rc
                revenue_cfs.append(cashflows_no_revenue[y - 1] + revenue)
            return compute_npv(revenue_cfs, discount_rate, initial_capex)

        # Bisection between $500 and $5000
        lo, hi = 500.0, 5000.0
        if npv_at_price(lo) > 0:
            return lo  # Already profitable at $500
        if npv_at_price(hi) < 0:
            return None  # Never profitable

        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            if npv_at_price(mid) < 0:
                lo = mid
            else:
                hi = mid
            if hi - lo < tolerance:
                return round((lo + hi) / 2.0, 2)

        return round((lo + hi) / 2.0, 2)
    except Exception as e:
        logger.error("compute_break_even_price failed: %s", e)
        return None
