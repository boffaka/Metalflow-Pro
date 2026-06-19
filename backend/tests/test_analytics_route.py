"""Integration tests for analytics endpoints."""
import pytest, os, io, csv

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="Requires TEST_DATABASE_URL"
)


def test_create_process_tag(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/analytics/tags",
        json={
            "tag_name": "FIC-400-001.PV",
            "description": "CIL Feed Flow — Process Variable",
            "area": "400",
            "unit": "m3/h",
            "data_type": "float",
            "normal_min": 500.0,
            "normal_target": 650.0,
            "normal_max": 750.0,
            "source": "opc_ua",
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert "tag_id" in r.json()


def test_list_process_tags(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/analytics/tags",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_csv_import_inserts_tag_readings(client, auth_headers, test_project_id):
    """Upload a CSV file and verify readings land in tag_readings."""
    # First create a tag
    r_tag = client.post(
        f"/api/v1/projects/{test_project_id}/analytics/tags",
        json={"tag_name": "TEST.FLOW", "unit": "m3/h", "source": "manual"},
        headers=auth_headers,
    )
    assert r_tag.status_code == 201, r_tag.text
    tag_id = r_tag.json()["tag_id"]

    # Build minimal CSV
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["timestamp", "TEST.FLOW"])
    writer.writerow(["2026-01-01T08:00:00Z", "620.5"])
    writer.writerow(["2026-01-01T08:01:00Z", "625.1"])
    writer.writerow(["2026-01-01T08:02:00Z", "618.9"])
    csv_bytes = buf.getvalue().encode()

    r = client.post(
        f"/api/v1/projects/{test_project_id}/analytics/import",
        files={"file": ("data.csv", csv_bytes, "text/csv")},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["rows_imported"] >= 3


def test_get_kpi_snapshot(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/analytics/kpi",
        headers=auth_headers,
    )
    assert r.status_code == 200


def test_create_connector(client, auth_headers, test_project_id):
    r = client.post(
        f"/api/v1/projects/{test_project_id}/analytics/connectors",
        json={
            "name": "Plant OPC-UA",
            "protocol": "opc_ua",
            "config": {"endpoint_url": "opc.tcp://plc:4840", "security": "None"},
            "poll_interval_s": 5,
        },
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert "connector_id" in r.json()


def test_get_forecast_endpoint(client, auth_headers, test_project_id):
    """GET /analytics/forecast returns list (may be empty if no LSTM model)."""
    r = client.get(
        f"/api/v1/projects/{test_project_id}/analytics/forecast",
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
