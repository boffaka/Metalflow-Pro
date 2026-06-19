# backend/tests/test_engines_dcf.py
"""Unit tests for DCF engine — NPV, IRR, AISC."""

import pytest

# Pure-math unit tests: no database needed, so opt out of the global DB skip.
pytestmark = pytest.mark.no_db


def get_engine():
    try:
        from backend.engines.dcf import compute_npv, compute_irr, compute_aisc, build_cashflows
    except ImportError:
        from engines.dcf import compute_npv, compute_irr, compute_aisc, build_cashflows
    return compute_npv, compute_irr, compute_aisc, build_cashflows


def test_npv_positive_for_profitable_project():
    compute_npv, *_ = get_engine()
    cashflows = [1_000_000.0] * 10
    npv = compute_npv(cashflows, discount_rate=0.05, initial_capex=5_000_000)
    assert npv > 0


def test_npv_negative_for_uneconomic_project():
    compute_npv, *_ = get_engine()
    cashflows = [100_000.0] * 5
    npv = compute_npv(cashflows, discount_rate=0.10, initial_capex=5_000_000)
    assert npv < 0


def test_npv_formula():
    """NPV = Σ FCF_y / (1+r)^y − CAPEX."""
    compute_npv, *_ = get_engine()
    cf = [100.0, 200.0, 300.0]
    r = 0.10
    expected = 100 / (1.1) ** 1 + 200 / (1.1) ** 2 + 300 / (1.1) ** 3 - 500
    result = compute_npv(cf, discount_rate=r, initial_capex=500)
    assert abs(result - expected) < 0.01


def test_irr_returns_reasonable_value():
    _, compute_irr, *_ = get_engine()
    cashflows = [500_000.0] * 10
    irr = compute_irr(cashflows, initial_capex=2_000_000)
    assert 0.05 <= irr <= 1.0


def test_irr_unprofitable_raises_or_returns_negative():
    _, compute_irr, *_ = get_engine()
    cashflows = [10_000.0] * 5
    try:
        irr = compute_irr(cashflows, initial_capex=1_000_000)
        assert irr is None or irr < 0
    except ValueError:
        pass


def test_aisc_wgc_2013_formula():
    *_, compute_aisc, _ = get_engine()
    aisc = compute_aisc(
        opex_mining=40_000_000,
        opex_processing=20_000_000,
        ga=5_000_000,
        byproduct_credits=0,
        sustaining_capex=10_000_000,
        royalties=3_000_000,
        exploration=2_000_000,
        corporate_ga=1_000_000,
        oz_produced=100_000,
    )
    # (40M+20M+5M+10M+3M+2M+1M) / 100_000 = 810 $/oz
    assert 700 <= aisc <= 1000


def test_build_cashflows_has_correct_length():
    *_, build = get_engine()
    cfs = build(
        mine_life_years=10,
        annual_oz=100_000,
        au_price=1900,
        royalty_pct=3.0,
        opex_annual=25_000_000,
        sustaining_capex_annual=5_000_000,
        tax_rate=30.0,
        discount_rate=5.0,
    )
    assert len(cfs) == 10
    assert all("fcf" in cf for cf in cfs)


# ─── F4: IRR robustness (Newton + bisection fallback) ────────────────────────
def test_irr_reciprocity_npv_zero_at_irr():
    """NPV evaluated at the returned IRR must be ~0 (definition of IRR)."""
    compute_npv, compute_irr, *_ = get_engine()
    cashflows = [500_000.0] * 10
    capex = 2_000_000
    irr = compute_irr(cashflows, initial_capex=capex)
    assert irr is not None
    assert abs(compute_npv(cashflows, discount_rate=irr, initial_capex=capex)) < 1.0


def test_irr_with_negative_rampup_year():
    """Ramp-up loss in year 1 (negative FCF) is realistic; IRR must still solve.

    Newton-Raphson from a single seed can diverge on such profiles; a bracketing
    fallback must still find the root that zeroes NPV.
    """
    compute_npv, compute_irr, *_ = get_engine()
    cashflows = [-800_000.0, 1_200_000.0, 1_200_000.0, 1_200_000.0, 1_200_000.0]
    capex = 1_000_000
    irr = compute_irr(cashflows, initial_capex=capex)
    assert irr is not None
    assert abs(compute_npv(cashflows, discount_rate=irr, initial_capex=capex)) < 1.0


def test_irr_high_return_short_life():
    """Very high IRR (rapid payback) must converge, not silently return None."""
    compute_npv, compute_irr, *_ = get_engine()
    cashflows = [5_000_000.0, 0.0, 0.0, 0.0, 0.0]  # root at r = 4.0 (400%)
    capex = 1_000_000
    irr = compute_irr(cashflows, initial_capex=capex)
    assert irr is not None
    assert abs(compute_npv(cashflows, discount_rate=irr, initial_capex=capex)) < 1.0


def test_irr_deeply_negative_for_loss_making_project():
    """A uniformly loss-making project still has a real (deeply negative) IRR.

    The bracketing fallback returns that root rather than None — more correct
    than the prior Newton-only behaviour, which gave up. Verify by reciprocity.
    """
    compute_npv, compute_irr, *_ = get_engine()
    cashflows = [100_000.0] * 3
    capex = 10_000_000
    irr = compute_irr(cashflows, initial_capex=capex)
    assert irr is not None and irr < 0
    assert abs(compute_npv(cashflows, discount_rate=irr, initial_capex=capex)) < 1.0


# ─── F5: cumulative NPV column includes the Year-0 CAPEX outflow ─────────────
def test_npv_cumulative_includes_initial_capex():
    """The cumulative-NPV column must start below zero (CAPEX) and the final
    row must equal the project NPV — so the zero-crossing is discounted payback."""
    compute_npv, _, _, build = get_engine()
    capex = 150_000_000
    cfs = build(
        mine_life_years=10,
        annual_oz=100_000,
        au_price=1900,
        royalty_pct=3.0,
        opex_annual=25_000_000,
        sustaining_capex_annual=5_000_000,
        tax_rate=30.0,
        discount_rate=5.0,
        initial_capex=capex,
    )
    # Year 1 cumulative must be below zero (CAPEX not yet recovered).
    assert cfs[0]["npv_cumulative"] < 0
    # Final cumulative == project NPV computed independently.
    fcf = [cf["fcf"] for cf in cfs]
    expected_npv = compute_npv(fcf, discount_rate=0.05, initial_capex=capex)
    assert abs(cfs[-1]["npv_cumulative"] - expected_npv) < 1.0


# ─── F3: tornado direction is unambiguous (downside/upside = min/max NPV) ─────
def test_sensitivity_downside_upside_fields():
    try:
        from backend.engines.dcf import sensitivity_analysis
    except ImportError:
        from engines.dcf import sensitivity_analysis
    base = {
        "mine_life_years": 10,
        "annual_oz": 100_000,
        "au_price": 1900,
        "royalty_pct": 3.0,
        "opex_annual": 25_000_000,
        "sustaining_capex": 5_000_000,
        "tax_rate": 30.0,
        "discount_rate": 5.0,
        "initial_capex": 150_000_000,
    }
    out = sensitivity_analysis(base, ["au_price", "opex_annual"], variation_pct=20.0)
    for var, row in out["variables"].items():
        assert "downside_npv" in row and "upside_npv" in row
        assert row["downside_npv"] <= row["upside_npv"]
        # downside/upside are the extremes of the two evaluated NPVs
        assert row["downside_npv"] == min(row["low_npv"], row["high_npv"])
        assert row["upside_npv"] == max(row["low_npv"], row["high_npv"])
