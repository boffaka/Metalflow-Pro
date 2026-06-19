# backend/engines/monte_carlo.py
"""
Monte Carlo simulation for economic risk analysis.

Stochastic variables:
  - Au price: Log-normal (mean, sigma=15%)
  - CAPEX: Triangular (min=-15%, mode=0, max=+20%)
  - OPEX: Normal (mean, sigma=10%)
  - Grade proxy (availability): Normal (mean=92%, sigma=3%)

10,000 iterations run on Celery worker to avoid API timeout.
"""
from __future__ import annotations
import logging
from typing import Any, Dict
import numpy as np

logger = logging.getLogger(__name__)


def run_monte_carlo(n_iterations: int, base_params: Dict[str, Any]) -> dict:
    """
    Run Monte Carlo simulation.

    Args:
        n_iterations: Number of iterations (default 10,000 for production)
        base_params: Dict with economic parameters
    Returns:
        dict: {npv_p10, npv_p50, npv_p90, irr_p50, prob_npv_positive, histogram, ...}
    """
    try:
        try:
            from engines.dcf import compute_npv, compute_irr, build_cashflows
        except ImportError:
            from .dcf import compute_npv, compute_irr, build_cashflows

        rng = np.random.default_rng()
        n = n_iterations

        mine_life = int(base_params.get("mine_life_years", 10))
        base_oz = float(base_params.get("annual_oz", 100_000))
        base_price = float(base_params.get("au_price_mean", 1900))
        price_sigma = base_params.get("au_price_sigma_pct", 15.0) / 100.0
        royalty = float(base_params.get("royalty_pct", 3.0))
        base_opex = float(base_params.get("opex_annual", 25_000_000))
        opex_sigma = base_params.get("opex_sigma_pct", 10.0) / 100.0
        sustaining = float(base_params.get("sustaining_capex", 5_000_000))
        tax_rate = float(base_params.get("tax_rate", 30.0))
        discount = float(base_params.get("discount_rate", 5.0))  # percentage
        base_capex = float(base_params.get("initial_capex", 150_000_000))
        base_avail = float(base_params.get("avail_pct_mean", 92.0))
        base_grade = float(base_params.get("grade_g_t", 1.5))

        # Sample stochastic variables
        price_samples = rng.lognormal(
            mean=np.log(base_price) - 0.5 * price_sigma**2,
            sigma=price_sigma, size=n
        )
        capex_samples = base_capex * rng.triangular(0.85, 1.0, 1.20, size=n)
        opex_samples = np.maximum(0, rng.normal(base_opex, base_opex * opex_sigma, size=n))
        # Fetch geometallurgical risks or use static values if unavailable
        grade_sigma = float(base_params.get("grade_sigma_pct", 10.0)) / 100.0

        # Sample stochastic variables using advanced distributions
        # 1. Gold Price: Log-normal distribution (prices cannot be negative, log-normal fits commodities well)
        price_samples = rng.lognormal(
            mean=np.log(base_price) - 0.5 * price_sigma**2,
            sigma=price_sigma, size=n
        )

        # 2. CAPEX: Triangular distribution (min=-15%, mode=0, max=+20%)
        capex_min_pct = float(base_params.get("capex_min_pct", -15.0)) / 100.0
        capex_max_pct = float(base_params.get("capex_max_pct", 20.0)) / 100.0
        capex_samples = base_capex * rng.triangular(1.0 + capex_min_pct, 1.0, 1.0 + capex_max_pct, size=n)

        # 3. OPEX: Normal distribution (bounded at 0 to avoid negative costs)
        opex_samples = np.maximum(0, rng.normal(base_opex, base_opex * opex_sigma, size=n))

        # 4. Grade: Log-normal distribution (often skewed right in ore bodies)
        grade_samples = rng.lognormal(
            mean=np.log(base_grade) - 0.5 * grade_sigma**2,
            sigma=grade_sigma, size=n
        )

        # 5. Recovery: Beta distribution (skewed towards the higher end, hard cap at 100%)
        # Modeled as a percentage of base_oz since base_oz implies a certain base recovery
        recovery_base_pct = float(base_params.get("recovery_pct", 90.0)) / 100.0
        recovery_samples = rng.beta(a=8, b=2, size=n) * 0.15 + (recovery_base_pct - 0.10) # Roughly bounds recovery between base-10% and base+5%
        recovery_samples = np.clip(recovery_samples, 0.0, 0.99)

        # 6. Availability: Normal distribution clipped between 60% and 100%
        avail_samples = np.clip(rng.normal(base_avail, 3.0, size=n), 60.0, 100.0)

        npv_results = np.zeros(n)
        irr_results = []

        # Vectorized calculation pre-computation where possible
        for i in range(n):
            # Calculate annual production based on sampled grade, availability, and recovery
            # base_oz assumes base_grade, base_avail, and recovery_base_pct
            oz = base_oz * (grade_samples[i] / base_grade) * (avail_samples[i] / base_avail) * (recovery_samples[i] / recovery_base_pct)

            price = price_samples[i]
            opex = opex_samples[i]
            capex = capex_samples[i]

            cfs = build_cashflows(
                mine_life_years=mine_life, annual_oz=oz,
                au_price=price, royalty_pct=royalty,
                opex_annual=opex, sustaining_capex_annual=sustaining,
                tax_rate=tax_rate, discount_rate=discount,
            )
            fcf_values = [cf["fcf"] for cf in cfs]

            npv = compute_npv(fcf_values, discount_rate=discount / 100.0, initial_capex=capex)
            irr = compute_irr(fcf_values, initial_capex=capex)

            npv_results[i] = npv
            if irr is not None:
                irr_results.append(irr * 100.0)

        irr_arr = np.array(irr_results)

        hist_counts, hist_edges = np.histogram(npv_results, bins=40)

        return {
            "n_iterations": n,
            "npv_p10": float(np.percentile(npv_results, 10)),
            "npv_p50": float(np.percentile(npv_results, 50)),
            "npv_p90": float(np.percentile(npv_results, 90)),
            "npv_mean": float(np.mean(npv_results)),
            "npv_std": float(np.std(npv_results)),
            "irr_p50": float(np.percentile(irr_arr, 50)) if len(irr_arr) > 0 else None,
            "prob_npv_positive": float(np.mean(npv_results > 0)),
            "histogram": {
                "bins": hist_edges.tolist(),
                "counts": hist_counts.tolist(),
            },
        }
    except Exception as e:
        logger.error("run_monte_carlo failed (n_iterations=%d): %s", n_iterations, e)
        raise RuntimeError(f"run_monte_carlo failed for n_iterations={n_iterations}") from e
