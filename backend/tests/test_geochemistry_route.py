# backend/tests/test_geochemistry_route.py
"""Integration tests for geochemistry endpoints."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Integration tests require TEST_DATABASE_URL",
)


def test_submit_aba_nag(client, auth_headers, test_project_id):
    payload = {
        "sample_id": "00000000-0000-0000-0000-000000000099",
        "total_s_pct": 3.5,
        "sulfide_s_pct": 2.8,
        "sulfate_s_pct": 0.7,
        "np_kg_caco3_t": 20.0,
        "ph_nag": 3.2,
        "test_date": "2026-04-07",
        "laboratory": "SGS",
    }
    r = client.post(
        f"/api/v1/projects/{test_project_id}/geochemistry/aba-nag",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["pag_classification"] == "PAG"  # NNP << -20 and ph_nag < 4.5
    assert "ap_kg_caco3_t" in data


def test_ard_risk_report(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/geochemistry/ard-risk",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "pag_pct" in data
    assert "ard_risk_level" in data
    assert data["ard_risk_level"] in ("Low", "Medium", "High", "Critical")
