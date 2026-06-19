# backend/tests/test_monte_carlo.py
"""Tests for Monte Carlo engine."""
import pytest

def get_mc():
    try:
        from backend.engines.monte_carlo import run_monte_carlo
    except ImportError:
        from engines.monte_carlo import run_monte_carlo
    return run_monte_carlo


def test_monte_carlo_returns_distribution():
    mc = get_mc()
    result = mc(
        n_iterations=100,
        base_params={
            "mine_life_years": 5, "annual_oz": 100_000,
            "au_price_mean": 1900, "au_price_sigma_pct": 15,
            "royalty_pct": 3.0, "opex_annual": 25_000_000,
            "sustaining_capex": 5_000_000, "tax_rate": 30.0,
            "discount_rate": 5.0, "initial_capex": 150_000_000,
        }
    )
    assert "npv_p10" in result
    assert "npv_p50" in result
    assert "npv_p90" in result
    assert "prob_npv_positive" in result
    assert 0.0 <= result["prob_npv_positive"] <= 1.0

def test_monte_carlo_p10_less_than_p50_less_than_p90():
    mc = get_mc()
    result = mc(
        n_iterations=200,
        base_params={
            "mine_life_years": 5, "annual_oz": 100_000,
            "au_price_mean": 1900, "au_price_sigma_pct": 15,
            "royalty_pct": 3.0, "opex_annual": 25_000_000,
            "sustaining_capex": 5_000_000, "tax_rate": 30.0,
            "discount_rate": 5.0, "initial_capex": 150_000_000,
        }
    )
    assert result["npv_p10"] <= result["npv_p50"] <= result["npv_p90"]

def test_monte_carlo_histogram_has_data():
    mc = get_mc()
    result = mc(
        n_iterations=100,
        base_params={
            "mine_life_years": 5, "annual_oz": 100_000,
            "au_price_mean": 1900, "au_price_sigma_pct": 15,
            "royalty_pct": 3.0, "opex_annual": 25_000_000,
            "sustaining_capex": 5_000_000, "tax_rate": 30.0,
            "discount_rate": 5.0, "initial_capex": 150_000_000,
        }
    )
    assert "histogram" in result
    assert len(result["histogram"]["bins"]) > 0
