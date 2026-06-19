"""Integration tests for 6 new features — Kokoya Gold Mine as test project."""
import pytest, os

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set")

# Dashboard
def test_dashboard_returns_all_keys(client, test_project_id, auth_headers):
    r = client.get(f"/api/v1/projects/{test_project_id}/dashboard", headers=auth_headers)
    assert r.status_code == 200
    for key in ("stage_gates", "lims", "costs", "risks", "decisions"):
        assert key in r.json()

def test_dashboard_requires_auth(client, test_project_id):
    assert client.get(f"/api/v1/projects/{test_project_id}/dashboard").status_code == 401

# CSV Export
def test_csv_samples_returns_text_csv(client, test_project_id, auth_headers):
    r = client.get(f"/api/v1/projects/{test_project_id}/export/csv/samples", headers=auth_headers)
    assert r.status_code == 200
    assert "text/csv" in r.headers.get("content-type", "")
    assert "sample_id_display" in r.text.splitlines()[0]

def test_csv_unknown_resource_404(client, test_project_id, auth_headers):
    assert client.get(f"/api/v1/projects/{test_project_id}/export/csv/bogus", headers=auth_headers).status_code == 404

def test_csv_all_resources_accessible(client, test_project_id, auth_headers):
    for res in ("samples", "equipment", "capex", "opex", "risks"):
        assert client.get(f"/api/v1/projects/{test_project_id}/export/csv/{res}", headers=auth_headers).status_code == 200

# Decisions
def test_create_list_patch_decision(client, test_project_id, auth_headers):
    r = client.post(f"/api/v1/projects/{test_project_id}/decisions",
        json={"title": "Sélection procédé HPGR vs SAG", "description": "P80 cible 3.5mm — Kokoya refractory"},
        headers=auth_headers)
    assert r.status_code == 201
    did = r.json()["id"]

    r2 = client.get(f"/api/v1/projects/{test_project_id}/decisions", headers=auth_headers)
    assert any(d["id"] == did for d in r2.json())

    r3 = client.patch(f"/api/v1/projects/{test_project_id}/decisions/{did}",
        json={"status": "accepted"}, headers=auth_headers)
    assert r3.json()["status"] == "accepted"

# Campaigns
def test_create_and_list_campaign(client, test_project_id, auth_headers):
    r = client.post(f"/api/v1/projects/{test_project_id}/campaigns",
        json={"name": "Kokoya Phase 1 — Characterisation", "description": "Flotation bench-scale tests"},
        headers=auth_headers)
    assert r.status_code == 201
    assert client.get(f"/api/v1/projects/{test_project_id}/campaigns", headers=auth_headers).status_code == 200

# Ramp-up
def test_rampup_kokoya_schedule(client, test_project_id, auth_headers):
    """Kokoya ramp-up: 55→92% months 1-6, 100% from month 7."""
    schedule = [
        {"month": 1, "factor_pct": 55.0, "notes": "Commissioning"},
        {"month": 2, "factor_pct": 65.0},
        {"month": 3, "factor_pct": 72.0},
        {"month": 4, "factor_pct": 80.0},
        {"month": 5, "factor_pct": 87.0},
        {"month": 6, "factor_pct": 92.0, "notes": "Near nameplate"},
    ]
    for entry in schedule:
        r = client.post(f"/api/v1/projects/{test_project_id}/rampup", json=entry, headers=auth_headers)
        assert r.status_code == 201, f"month {entry['month']}: {r.text}"

def test_rampup_cumulative_60_months(client, test_project_id, auth_headers):
    r = client.get(f"/api/v1/projects/{test_project_id}/rampup/cumulative", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 60
    assert float(next(e for e in data if e["month"] == 7)["factor_pct"]) == 100.0

def test_rampup_month_61_rejected(client, test_project_id, auth_headers):
    assert client.post(f"/api/v1/projects/{test_project_id}/rampup",
        json={"month": 61, "factor_pct": 100.0}, headers=auth_headers).status_code == 422

# Working Capital
def test_working_capital_kokoya(client, test_project_id, auth_headers):
    """Kokoya WC: 45-day receivables (W. Africa), 60-day inventory (remote site)."""
    r = client.put(f"/api/v1/projects/{test_project_id}/working-capital",
        json={"receivable_days": 45, "inventory_days": 60, "payable_days": 30,
              "other_current_assets": 2500000.0, "other_current_liabilities": 800000.0},
        headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["receivable_days"] == 45

def test_working_capital_computed(client, test_project_id, auth_headers):
    r = client.get(f"/api/v1/projects/{test_project_id}/working-capital/computed", headers=auth_headers)
    assert r.status_code == 200
    assert "net_working_capital" in r.json()

def test_working_capital_idempotent(client, test_project_id, auth_headers):
    client.put(f"/api/v1/projects/{test_project_id}/working-capital", json={"receivable_days": 30}, headers=auth_headers)
    r = client.put(f"/api/v1/projects/{test_project_id}/working-capital", json={"receivable_days": 45}, headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["receivable_days"] == 45
