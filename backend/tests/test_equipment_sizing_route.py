# backend/tests/test_equipment_sizing_route.py
"""Integration tests for equipment sizing endpoints."""
import pytest, os

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Requires TEST_DATABASE_URL"
)

def test_size_ball_mill_endpoint(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/equipment/size",
        json={
            "equipment_type": "ball_mill",
            "params": {
                "wi": 14.0, "tph": 1517.0,
                "p80_um": 75.0, "f80_um": 3000.0,
            }
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "sizing_id" in data
    assert "outputs" in data
    assert data["outputs"]["power_kw"] > 0

def test_capex_estimate_endpoint(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/equipment/capex-estimate",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert "total_capex_usd" in data

def test_vendor_catalog_lookup(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/equipment/catalog?family=Ball+Mill",
        headers=auth_headers,
    )
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    assert len(items) > 0
    assert all("manufacturer" in i for i in items)

def test_size_all_auto_sizes_equipment(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/equipment/size-all",
        json={
            "simulation_params": {
                "wi": 14.0, "spi_kwh_t": 10.0, "tph": 1517.0,
                "p80_um": 75.0, "f80_um": 3000.0,
                "q_cil_m3h": 600.0, "srt_cil_h": 24.0,
                "tpd_solids": 3600.0, "ua_m2_t_d": 0.08,
                "oz_per_day": 500.0,
            }
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "sized_equipment" in data
    assert len(data["sized_equipment"]) > 0
