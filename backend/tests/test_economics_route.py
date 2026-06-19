# backend/tests/test_economics_route.py
"""Integration tests for /economics endpoints."""
import pytest, os

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Requires TEST_DATABASE_URL"
)

def test_dcf_compute_returns_npv(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/economics/dcf",
        json={
            "mine_life_years": 10,
            "annual_oz": 170_000,
            "au_price": 1900.0,
            "royalty_pct": 3.0,
            "opex_annual": 45_000_000,
            "sustaining_capex_annual": 8_000_000,
            "tax_rate": 25.0,
            "discount_rate": 5.0,
            "initial_capex": 180_000_000,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "npv" in data
    assert "irr" in data
    assert "aisc" in data
    assert "cashflows" in data
    assert len(data["cashflows"]) == 10

def test_dcf_stores_in_database(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/economics/dcf",
        json={
            "mine_life_years": 5, "annual_oz": 100_000,
            "au_price": 1900.0, "royalty_pct": 3.0,
            "opex_annual": 25_000_000, "sustaining_capex_annual": 5_000_000,
            "tax_rate": 30.0, "discount_rate": 5.0, "initial_capex": 100_000_000,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "model_id" in r.json()

def test_monte_carlo_queues_task(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/economics/monte-carlo",
        json={
            "n_iterations": 100,
            "base_params": {
                "mine_life_years": 5, "annual_oz": 100_000,
                "au_price_mean": 1900, "au_price_sigma_pct": 15,
                "royalty_pct": 3.0, "opex_annual": 25_000_000,
                "sustaining_capex": 5_000_000, "tax_rate": 30.0,
                "discount_rate": 5.0, "initial_capex": 100_000_000,
            }
        },
        headers=auth_headers,
    )
    assert r.status_code == 202, r.text
    assert "mc_run_id" in r.json()
    assert r.json()["status"] == "queued"

def test_get_indicators(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/economics/indicators",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)
