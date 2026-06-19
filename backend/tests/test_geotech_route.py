# backend/tests/test_geotech_route.py
"""Integration tests for geotech endpoints."""
import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Integration tests require TEST_DATABASE_URL",
)


def test_submit_g1_test(client, auth_headers, test_project_id):
    payload = {
        "sample_id": "00000000-0000-0000-0000-000000000099",
        "test_code": "G1",
        "laboratory": "TestLab",
        "test_date": "2026-04-07",
        "results": {
            "ucs_mpa": 45.2,
            "youngs_modulus_gpa": 30.1,
            "poissons_ratio": 0.25,
            "failure_mode": "axial_splitting",
        },
    }
    r = client.post(
        f"/api/v1/projects/{test_project_id}/lims/tests/geotech",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["test_code"] == "G1"
    assert data["results"]["ucs_mpa"] == 45.2


def test_reject_invalid_test_code(client, auth_headers, test_project_id):
    payload = {"test_code": "X9", "results": {}, "laboratory": "Lab"}
    r = client.post(
        f"/api/v1/projects/{test_project_id}/lims/tests/geotech",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 422


def test_slope_stability_compliant(client, auth_headers, test_project_id):
    payload = {
        "location": "North wall",
        "slope_angle_deg": 28.0,
        "slope_height_m": 12.0,
        "cohesion_kpa": 25.0,
        "friction_angle_deg": 36.0,
        "gamma_kn_m3": 20.0,
        "pore_pressure_ratio": 0.0,
    }
    r = client.post(
        f"/api/v1/projects/{test_project_id}/geotech/slope-stability",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["fs_static"] >= 1.3
    assert data["is_compliant"] is True


def test_slope_stability_list(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/geotech/slope-stability",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_tsf_design(client, auth_headers, test_project_id):
    payload = {
        "construction_method": "downstream",
        "total_capacity_m3": 5_000_000.0,
        "annual_deposition_t": 2_000_000.0,
        "deposition_density_t_m3": 1.4,
        "embankment_area_ha": 80.0,
        "water_balance": {"precip_mm_y": 450, "evap_mm_y": 800},
        "notes": "Phase 1 raise",
    }
    r = client.post(
        f"/api/v1/projects/{test_project_id}/geotech/tsf-design",
        json=payload, headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["raise_height_m"] > 0
    assert "is_mac_compliant" in data
