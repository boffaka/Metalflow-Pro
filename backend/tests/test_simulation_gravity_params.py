"""Simulation param keys for gravity (concentration category)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.engines.gravity_model import (
        gravity_dc_from_simulation,
        plant_gravity_recovery_pct,
        resolve_gravity_params,
    )
    from backend.routes.simulation import SIM_DEFAULTS, _enrich_gravity_sim_rows
except ImportError:
    from engines.gravity_model import (
        gravity_dc_from_simulation,
        plant_gravity_recovery_pct,
        resolve_gravity_params,
    )
    from routes.simulation import SIM_DEFAULTS, _enrich_gravity_sim_rows


def test_sim_defaults_gravity_keys():
    keys = {row[1] for row in SIM_DEFAULTS if row[0] == "concentration" and row[1].startswith("gravity_")}
    assert {
        "gravity_active",
        "gravity_grg",
        "gravity_slip",
        "gravity_rec",
        "gravity_ilr",
        "gravity_mass_pull",
        "gravity_plant_rec",
    } <= keys


def test_gravity_rec_maps_to_knelson_not_plant_recovery():
    gp = resolve_gravity_params({
        "gravity_grg": 35.0,
        "gravity_slip": 30.0,
        "gravity_rec": 50.0,
        "gravity_ilr": 95.0,
    })
    assert gp.knelson_unit_recovery_pct == 50.0
    assert plant_gravity_recovery_pct(gp) == pytest.approx(4.9875, abs=0.01)


def test_gravity_dc_from_simulation_matches_engine():
    sim = {
        "gravity_grg": 35.0,
        "gravity_slip": 30.0,
        "gravity_rec": 50.0,
        "gravity_ilr": 95.0,
        "gravity_mass_pull": 0.2,
    }
    dc = gravity_dc_from_simulation(sim)
    assert dc["grg_pct"] == 35.0
    assert dc["gravity_knelson_recovery_pct"] == 50.0


def test_enrich_computes_gravity_plant_rec():
    rows = [
        {"param_key": "gravity_grg", "param_value": 35.0, "param_label": "x"},
        {"param_key": "gravity_slip", "param_value": 30.0, "param_label": "x"},
        {"param_key": "gravity_rec", "param_value": 50.0, "param_label": "x"},
        {"param_key": "gravity_ilr", "param_value": 95.0, "param_label": "x"},
        {"param_key": "gravity_plant_rec", "param_value": None, "param_label": "x"},
    ]
    out = _enrich_gravity_sim_rows(rows)
    plant = next(r for r in out if r["param_key"] == "gravity_plant_rec")
    assert plant["param_value"] == pytest.approx(4.99, abs=0.01)
    assert plant["param_value_text"] == "Calculé"
