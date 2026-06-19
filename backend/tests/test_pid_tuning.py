# backend/tests/test_pid_tuning.py
"""Unit tests for PID tuning engine."""
import pytest

def get_tuning():
    try:
        from backend.engines.pid_tuning import (
            tune_ziegler_nichols, tune_lambda, simulate_step_response, identify_fopdt
        )
    except ImportError:
        from engines.pid_tuning import (
            tune_ziegler_nichols, tune_lambda, simulate_step_response, identify_fopdt
        )
    return tune_ziegler_nichols, tune_lambda, simulate_step_response, identify_fopdt


def test_ziegler_nichols_pi_returns_kp_ti():
    zn, *_ = get_tuning()
    result = zn(ku=2.0, pu_s=60.0, controller_type="PI")
    assert "kp" in result
    assert "ti_s" in result
    assert result["kp"] > 0
    assert result["ti_s"] > 0

def test_ziegler_nichols_pid_returns_kp_ti_td():
    zn, *_ = get_tuning()
    result = zn(ku=2.0, pu_s=60.0, controller_type="PID")
    assert "kp" in result
    assert "ti_s" in result
    assert "td_s" in result

def test_lambda_tuning_returns_positive_params():
    _, lam, *_ = get_tuning()
    result = lam(K=1.5, tau_s=120.0, theta_s=15.0, lambda_s=60.0)
    assert result["kp"] > 0
    assert result["ti_s"] > 0

def test_step_response_simulation_returns_time_series():
    _, _, sim, _ = get_tuning()
    result = sim(kp=1.2, ti_s=45.0, td_s=0.0, K=1.5, tau_s=120.0, theta_s=15.0,
                 step_magnitude=1.0, t_end_s=600.0)
    assert "time" in result
    assert "pv" in result
    assert len(result["time"]) > 10
    # PV should eventually reach setpoint (within 20%)
    final_pv = result["pv"][-1]
    assert 0.8 <= final_pv <= 1.2

def test_identify_fopdt_from_step_data():
    _, _, _, identify = get_tuning()
    import numpy as np
    # Synthetic FOPDT: K=2.0, tau=100s, theta=10s
    t = np.linspace(0, 500, 200)
    y = np.where(t < 10, 0.0, 2.0 * (1.0 - np.exp(-(t - 10) / 100.0)))
    result = identify(times_s=t.tolist(), pv_values=y.tolist(), step_magnitude=1.0)
    assert "gain" in result
    assert "tau_s" in result
    assert "theta_s" in result
    assert abs(result["gain"] - 2.0) < 0.5
