# backend/engines/pid_tuning.py
"""
PID tuning algorithms and FOPDT step response simulation.

Supported methods:
  - Ziegler-Nichols (ultimate gain + period from oscillation test)
  - Lambda (model-based, robust for gold plant CIL loops)
  - FOPDT identification from step test data

References:
  - Ziegler & Nichols (1942), Trans. ASME 64:759
  - Lambda / IMC: Rivera et al. (1986), ISA Transactions
"""
from __future__ import annotations
import logging
from typing import List

logger = logging.getLogger(__name__)


def tune_ziegler_nichols(
    ku: float,
    pu_s: float,
    controller_type: str = "PI",
) -> dict:
    """
    Ziegler-Nichols tuning from ultimate gain (Ku) and period (Pu).

    Args:
        ku: Ultimate (critical) gain
        pu_s: Ultimate period (seconds)
        controller_type: 'P', 'PI', or 'PID'
    Returns:
        dict: {kp, ti_s, td_s}
    """
    try:
        ct = controller_type.upper()
        if ct == "P":
            return {"kp": 0.5 * ku, "ti_s": None, "td_s": None}
        elif ct == "PI":
            return {"kp": 0.45 * ku, "ti_s": pu_s / 1.2, "td_s": 0.0}
        elif ct == "PID":
            return {"kp": 0.6 * ku, "ti_s": pu_s / 2.0, "td_s": pu_s / 8.0}
        else:
            raise ValueError(f"Unknown controller_type: {ct}")
    except Exception as e:
        logger.error("tune_ziegler_nichols failed (ku=%.4f, pu_s=%.4f, type=%s): %s", ku, pu_s, controller_type, e)
        raise RuntimeError(f"tune_ziegler_nichols failed for controller_type={controller_type}") from e


def tune_lambda(
    K: float,
    tau_s: float,
    theta_s: float,
    lambda_s: float,
) -> dict:
    """
    Lambda (IMC-based) PID tuning for FOPDT processes.

    Args:
        K: Process gain
        tau_s: Process time constant (seconds)
        theta_s: Dead time (seconds)
        lambda_s: Desired closed-loop time constant (seconds)
    Returns:
        dict: {kp, ti_s, td_s}
    """
    try:
        kp = (tau_s + 0.5 * theta_s) / (K * (lambda_s + 0.5 * theta_s))
        ti_s = tau_s + 0.5 * theta_s
        td_s = (tau_s * theta_s) / (2.0 * tau_s + theta_s)
        return {"kp": round(kp, 4), "ti_s": round(ti_s, 2), "td_s": round(td_s, 2)}
    except Exception as e:
        logger.error("tune_lambda failed (K=%.4f, tau_s=%.2f, theta_s=%.2f, lambda_s=%.2f): %s", K, tau_s, theta_s, lambda_s, e)
        raise RuntimeError(f"tune_lambda failed for K={K}, tau_s={tau_s}") from e


def simulate_step_response(
    kp: float,
    ti_s: float,
    td_s: float,
    K: float,
    tau_s: float,
    theta_s: float,
    step_magnitude: float = 1.0,
    t_end_s: float = 600.0,
    dt_s: float = 1.0,
) -> dict:
    """
    Simulate PID step response on a FOPDT process using Euler integration.
    Returns {time[], pv[], mv[]} for frontend charting.

    PV must reach within 20% of step_magnitude by t_end_s.
    """
    try:
        import numpy as np

        t_arr = np.arange(0, t_end_s + dt_s, dt_s)
        pv = np.zeros(len(t_arr))
        mv = np.zeros(len(t_arr))
        error = np.zeros(len(t_arr))
        integral = 0.0

        SP = step_magnitude
        delay_steps = max(1, int(theta_s / dt_s))

        for i in range(1, len(t_arr)):
            e = SP - pv[i - 1]
            error[i] = e
            integral += e * dt_s
            derivative = (e - error[i - 1]) / dt_s if i > 0 else 0.0

            mv_current = kp * (e + integral / max(ti_s, 1e-9) + td_s * derivative)
            mv[i] = np.clip(mv_current, 0, 100)

            mv_delayed = mv[max(0, i - delay_steps)]
            dpv = (K * mv_delayed - pv[i - 1]) / tau_s
            pv[i] = pv[i - 1] + dpv * dt_s

        return {
            "time": t_arr.tolist(),
            "pv": pv.tolist(),
            "mv": mv.tolist(),
        }
    except Exception as e:
        logger.error("simulate_step_response failed (kp=%.4f, K=%.4f, tau_s=%.2f): %s", kp, K, tau_s, e)
        raise RuntimeError(f"simulate_step_response failed for kp={kp}, K={K}") from e


def identify_fopdt(
    times_s: List[float],
    pv_values: List[float],
    step_magnitude: float,
) -> dict:
    """
    Identify FOPDT parameters (K, tau, theta) from open-loop step test data.
    Uses Smith method: 63.2% and 28.3% points on the reaction curve.

    Args:
        times_s: Time array (seconds)
        pv_values: PV response array
        step_magnitude: Magnitude of the MV step applied
    Returns:
        dict: {gain, tau_s, theta_s}
    """
    try:
        import numpy as np
        t = np.array(times_s)
        y = np.array(pv_values)

        y0 = y[0]
        y_inf = y[-1]
        dy = y_inf - y0
        if abs(dy) < 1e-9:
            return {"gain": 0.0, "tau_s": 1.0, "theta_s": 0.0}

        y_norm = (y - y0) / dy

        t283 = t[np.argmin(np.abs(y_norm - 0.283))]
        t632 = t[np.argmin(np.abs(y_norm - 0.632))]

        tau = 1.5 * (t632 - t283)
        theta = t632 - tau
        K = dy / step_magnitude

        return {
            "gain": round(float(K), 4),
            "tau_s": round(float(max(tau, 0.1)), 2),
            "theta_s": round(float(max(theta, 0.0)), 2),
        }
    except Exception as e:
        logger.error("identify_fopdt failed (step_magnitude=%.4f, n_points=%d): %s", step_magnitude, len(times_s), e)
        raise RuntimeError(f"identify_fopdt failed for step_magnitude={step_magnitude}") from e
