"""Layer 1 — pure functions in services.capex (no DB)."""
from __future__ import annotations

import math
import pytest


def test_parametric_price_formula():
    from services.capex import parametric_price
    # alpha=10000, beta=0.7, tph=2000 -> 10000 * 2000^0.7
    expected = 10000 * (2000 ** 0.7)
    assert math.isclose(parametric_price(10000, 0.7, 2000), expected, rel_tol=1e-9)


def test_parametric_price_zero_tph_returns_zero():
    from services.capex import parametric_price
    assert parametric_price(10000, 0.7, 0) == 0


def test_aggregate_totals_cumulative_factors():
    """Direct=100, indirect 30%, epcm 15%, contingency 15% (cumulative)."""
    from services.capex import aggregate_totals
    out = aggregate_totals(direct=100.0,
                           indirect_pct=0.30,
                           epcm_pct=0.15,
                           contingency_pct=0.15)
    # direct=100; indirect=30; epcm=(100+30)*0.15=19.5; contingency=(100+30+19.5)*0.15=22.425
    assert math.isclose(out["direct_cad"], 100.0)
    assert math.isclose(out["indirect_cad"], 30.0)
    assert math.isclose(out["epcm_cad"], 19.5)
    assert math.isclose(out["contingency_cad"], 22.425)
    assert math.isclose(out["total_cad"], 171.925)


def test_aggregate_totals_zero_direct_returns_zeros():
    from services.capex import aggregate_totals
    out = aggregate_totals(direct=0.0, indirect_pct=0.3, epcm_pct=0.15, contingency_pct=0.15)
    assert out["total_cad"] == 0.0
