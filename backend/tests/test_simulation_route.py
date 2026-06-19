# backend/tests/test_simulation_route.py
"""Integration tests for simulation v4 endpoints."""
import pytest, os

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Requires TEST_DATABASE_URL"
)

def test_rigorous_simulation_run_queues_task(client, auth_headers, test_project_id):
    """POST /simulation/run-rigorous must return task_id and status=queued."""
    r = client.post(
        f"/api/v1/projects/{test_project_id}/simulation/run-rigorous",
        json={
            "wi": 14.0, "spi_kwh_t": 10.0, "p80_um": 75.0, "f80_um": 3000.0,
            "r_inf": 0.90, "k_cil": 0.35, "srt_h": 24.0,
            "nacn_mg_l": 350.0, "do_mg_l": 8.0, "ph": 10.5,
            "tph": 1517.0, "op_hours_day": 24.0, "avail_pct": 92.0,
            "grade_g_t": 1.5,
        },
        headers=auth_headers,
    )
    assert r.status_code == 202, r.text
    data = r.json()
    assert "run_id" in data
    assert data["status"] == "queued"

def test_get_simulation_runs_list(client, auth_headers, test_project_id):
    """GET /simulation/runs returns list."""
    r = client.get(
        f"/api/v1/projects/{test_project_id}/simulation/runs",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)

def test_simulation_run_synchronous_returns_results(client, auth_headers, test_project_id):
    """POST /simulation/run-rigorous with sync=true returns results immediately."""
    r = client.post(
        f"/api/v1/projects/{test_project_id}/simulation/run-rigorous?sync=true",
        json={
            "wi": 14.0, "spi_kwh_t": 10.0, "p80_um": 75.0, "f80_um": 3000.0,
            "r_inf": 0.90, "k_cil": 0.35, "srt_h": 24.0,
            "nacn_mg_l": 350.0, "do_mg_l": 8.0, "ph": 10.5,
            "tph": 1517.0, "op_hours_day": 24.0, "avail_pct": 92.0,
            "grade_g_t": 1.5,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "results" in data
    results = data["results"]
    assert "recovery_pct" in results
    assert "annual_oz" in results
    assert "energy_kwh_t" in results
    assert 80.0 <= results["recovery_pct"] <= 99.0
    assert results["annual_oz"] > 100_000

#
# test_sensitivity_endpoint_returns_ranked_params — REMOVED 2026-05-06
# It targeted POST /simulation/sensitivity (deprecated=True), which has been
# removed. The sensitivity compute logic is still tested via
# `test_compute_sensitivity.py` (engine-level). Adding a route-level test for
# the v2 endpoint POST /simulation-v2/sensitivity is tracked as a separate
# follow-up — the v2 contract differs (`params_to_vary` is a list of
# `{key, label}` objects, response shape is `{run_id, tornado}`).
