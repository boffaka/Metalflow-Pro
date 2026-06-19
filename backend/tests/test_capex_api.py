"""Layer 3 — /capex API via TestClient.

This test file defines its OWN function-scoped `test_project_id` fixture
that shadows the session-scoped one in conftest.py — each test must start
from a fresh project so seed/override scenarios stay isolated. Pattern
mirrors `test_capex_repo.py`.
"""
from __future__ import annotations

import os
import uuid
import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")


@pytest.fixture
def test_project_id(auth_headers):
    """Create a throwaway project for each test, drop it after.

    Function-scoped — overrides the session-scoped conftest fixture so each
    API test starts clean. Project is owned by the admin user (resolved from
    the same JWT used by `auth_headers`); admin's `Project Manager` role
    allows access regardless of ownership, so the tests are independent of
    user_id linkage.
    """
    try:
        from backend.db import execute, qone  # type: ignore
    except ImportError:
        from db import execute, qone  # type: ignore
    pid = str(uuid.uuid4())
    short = pid[:8]
    execute(
        "INSERT INTO projects (id, project_name, project_code, target_tph, circuit_type) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, f"capex_api_{short}", f"CAX-{short}", 1517, "cil_conventional"),
    )
    yield pid
    execute("DELETE FROM projects WHERE id=%s", (pid,))


def _seed(client, headers, pid, circuit="cil_conventional"):
    r = client.post(
        f"/api/v1/projects/{pid}/capex/seed",
        json={"circuit_type": circuit, "force": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_get_capex_returns_full_module(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    r = client.get(f"/api/v1/projects/{test_project_id}/capex", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["circuit_type"] == "cil_conventional"
    assert len(body["equipment"]) >= 5
    assert body["totals"]["total_cad"] > body["totals"]["direct_cad"]
    assert "indirect" in body["factors"]["overridden"]


def test_patch_equipment_marks_overridden_and_returns_dcf(
    client, auth_headers, test_project_id
):
    _seed(client, auth_headers, test_project_id)
    g = client.get(f"/api/v1/projects/{test_project_id}/capex", headers=auth_headers).json()
    eid = g["equipment"][0]["id"]
    r = client.patch(
        f"/api/v1/projects/{test_project_id}/capex/equipment/{eid}",
        json={"price_cad": 7777777},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # `dcf` key MUST be present. Its value is null until Task 3.4 lands the
    # _dcf_core extraction; from Task 3.4 onward it carries the recomputed dict.
    assert "capex" in body and "dcf" in body
    overridden = next(e for e in body["capex"]["equipment"] if e["id"] == eid)
    assert overridden["is_overridden"] is True
    assert float(overridden["price_cad"]) == 7777777


def test_post_equipment_creates_manual_row(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    r = client.post(
        f"/api/v1/projects/{test_project_id}/capex/equipment",
        json={"category": "Utilities", "name": "Compresseur d'usine",
              "price_cad": 250000, "typical_power_kw": 75},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    new_row = next(e for e in body["capex"]["equipment"]
                   if e["name"] == "Compresseur d'usine")
    assert new_row["seeded_from_template"] is False
    assert new_row["is_overridden"] is True


def test_delete_equipment_only_allowed_for_manual_rows(
    client, auth_headers, test_project_id
):
    _seed(client, auth_headers, test_project_id)
    g = client.get(f"/api/v1/projects/{test_project_id}/capex",
                   headers=auth_headers).json()
    seeded = next(e for e in g["equipment"] if e["seeded_from_template"])
    r = client.delete(
        f"/api/v1/projects/{test_project_id}/capex/equipment/{seeded['id']}",
        headers=auth_headers,
    )
    assert r.status_code == 409  # cannot hard-delete seeded rows


def test_reset_equipment_clears_override(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    g = client.get(f"/api/v1/projects/{test_project_id}/capex",
                   headers=auth_headers).json()
    eid = g["equipment"][0]["id"]
    client.patch(
        f"/api/v1/projects/{test_project_id}/capex/equipment/{eid}",
        json={"price_cad": 999}, headers=auth_headers,
    )
    r = client.post(
        f"/api/v1/projects/{test_project_id}/capex/equipment/{eid}/reset",
        headers=auth_headers,
    )
    assert r.status_code == 200
    row = next(e for e in r.json()["capex"]["equipment"] if e["id"] == eid)
    assert row["is_overridden"] is False
    assert float(row["price_cad"]) > 0  # parametric value, not 999


def test_patch_factors_validates_bounds(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    bad = client.patch(
        f"/api/v1/projects/{test_project_id}/capex/factors",
        json={"contingency_pct": 1.5},
        headers=auth_headers,
    )
    assert bad.status_code == 422


def test_patch_factors_marks_overridden(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    r = client.patch(
        f"/api/v1/projects/{test_project_id}/capex/factors",
        json={"contingency_pct": 0.20},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["capex"]["factors"]["overridden"]["contingency"] is True
    assert body["capex"]["factors"]["contingency_pct"] == 0.20


def test_seed_switches_circuit_type_on_project(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id, circuit="hpgr_ball")
    r = client.get(f"/api/v1/projects/{test_project_id}/capex", headers=auth_headers)
    assert r.json()["circuit_type"] == "hpgr_ball"


def test_list_templates_returns_eleven_circuits(client, auth_headers, test_project_id):
    r = client.get(
        f"/api/v1/projects/{test_project_id}/capex/templates",
        headers=auth_headers,
    )
    assert r.status_code == 200
    keys = {item["key"] for item in r.json()}
    assert len(keys) == 11


def test_get_capex_404_for_other_project(client, auth_headers, test_project_id):
    other = "00000000-0000-0000-0000-000000000000"
    r = client.get(f"/api/v1/projects/{other}/capex", headers=auth_headers)
    assert r.status_code in (403, 404)


def test_mutating_endpoint_blocks_foreign_project(client, auth_headers, test_project_id):
    """Mutations against a project the caller doesn't own must 403/404."""
    other = "00000000-0000-0000-0000-000000000000"
    r = client.post(
        f"/api/v1/projects/{other}/capex/seed",
        json={"circuit_type": "cil_conventional", "force": True},
        headers=auth_headers,
    )
    assert r.status_code in (403, 404)
