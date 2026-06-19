# backend/tests/test_closure_route.py
"""Integration tests for mine closure plan endpoints."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Integration tests require TEST_DATABASE_URL",
)


def test_create_closure_activity(client, auth_headers, test_project_id):
    payload = {
        "phase": "final",
        "component": "TSF",
        "activity": "Engineered cap and revegetation",
        "year_target": 2045,
        "unit_cost_usd": 15000.0,
        "quantity": 80.0,
        "unit": "ha",
        "success_criteria": "Vegetation cover > 80%, seepage < 0.1 L/s",
        "responsible": "Environmental team",
    }
    r = client.post(
        f"/api/v1/projects/{test_project_id}/closure/plan",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["total_cost_usd"] == 15000.0 * 80.0
    assert data["phase"] == "final"


def test_reject_invalid_phase(client, auth_headers, test_project_id):
    payload = {"phase": "invalid_phase", "activity": "X", "unit_cost_usd": 100}
    r = client.post(
        f"/api/v1/projects/{test_project_id}/closure/plan",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 422


def test_cost_estimate(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/closure/cost-estimate",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "total_provision_usd" in data
    assert "by_phase" in data
    assert isinstance(data["total_provision_usd"], float)
