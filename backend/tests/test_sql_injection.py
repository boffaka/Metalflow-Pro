"""SQL injection protection tests — Kokoya Gold Mine project."""
import pytest, os

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

def test_patch_project_ignores_extra_fields(client, test_project_id, auth_headers):
    r = client.patch(f"/api/v1/projects/{test_project_id}",
        json={"project_name": "Kokoya Gold Mine — Rev A", "; DROP TABLE projects; --": "evil"},
        headers=auth_headers)
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["project_name"] == "Kokoya Gold Mine — Rev A"
    assert client.get("/api/v1/projects", headers=auth_headers).status_code == 200

def test_patch_sample_with_injected_field(client, test_project_id, auth_headers):
    r = client.post(f"/api/v1/projects/{test_project_id}/lims/samples",
        json={"sample_id_display": "KGM-S-001", "phase": "PFS", "sample_type": "DD"},
        headers=auth_headers)
    if r.status_code != 201:
        pytest.skip("Could not create sample")
    sid = r.json()["id"]
    r2 = client.patch(f"/api/v1/projects/{test_project_id}/lims/samples/{sid}",
        json={"phase": "FS", "; DELETE FROM lims_samples; --": "evil"},
        headers=auth_headers)
    assert r2.status_code in (200, 400, 422)
    assert client.get(f"/api/v1/projects/{test_project_id}/lims/samples", headers=auth_headers).status_code == 200

def test_csv_resource_injection_rejected(client, test_project_id, auth_headers):
    r = client.get(f"/api/v1/projects/{test_project_id}/export/csv/; DROP TABLE risks; --", headers=auth_headers)
    assert r.status_code == 404
