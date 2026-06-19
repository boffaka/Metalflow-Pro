"""Layer 4 — economics.compute_dcf reads from services.capex, not legacy SUM."""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"),
                                reason="needs TEST_DATABASE_URL")


@pytest.fixture
def test_project_id(auth_headers):
    """Function-scoped throwaway project — overrides the session-scoped
    conftest fixture so each economics-vs-capex test starts clean. Mirrors
    the pattern in `test_capex_api.py` / `test_capex_repo.py`."""
    try:
        from backend.db import execute  # type: ignore
    except ImportError:
        from db import execute  # type: ignore
    pid = str(uuid.uuid4())
    short = pid[:8]
    execute(
        "INSERT INTO projects (id, project_name, project_code, target_tph, circuit_type) "
        "VALUES (%s, %s, %s, %s, %s)",
        (pid, f"econ_capex_{short}", f"ECX-{short}", 1517, "cil_conventional"),
    )
    yield pid
    execute("DELETE FROM projects WHERE id=%s", (pid,))


def _seed(client, headers, pid):
    r = client.post(
        f"/api/v1/projects/{pid}/capex/seed",
        json={"circuit_type": "cil_conventional", "force": True},
        headers=headers,
    )
    assert r.status_code == 200, r.text


def _dcf(client, headers, pid):
    r = client.post(
        f"/api/v1/projects/{pid}/economics/dcf",
        json={},
        headers=headers,
    )
    return r


def test_dcf_uses_capex_total_with_factors(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    r = _dcf(client, auth_headers, test_project_id)
    assert r.status_code == 200, r.text
    assert r.json()["initial_capex"] > 0


def test_dcf_reflects_equipment_edit(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    before = _dcf(client, auth_headers, test_project_id).json()["initial_capex"]

    g = client.get(f"/api/v1/projects/{test_project_id}/capex",
                   headers=auth_headers).json()
    eid = g["equipment"][0]["id"]
    huge = float(g["equipment"][0]["price_cad"]) * 10
    client.patch(
        f"/api/v1/projects/{test_project_id}/capex/equipment/{eid}",
        json={"price_cad": huge},
        headers=auth_headers,
    )
    after = _dcf(client, auth_headers, test_project_id).json()["initial_capex"]
    assert after > before


def test_dcf_400_when_capex_empty(client, auth_headers, test_project_id):
    """No fallback to 150M anymore."""
    try:
        from backend.db import execute  # type: ignore
    except ImportError:
        from db import execute  # type: ignore
    execute("UPDATE equipment_v2 SET enabled=false WHERE project_id=%s",
            (test_project_id,))
    r = _dcf(client, auth_headers, test_project_id)
    assert r.status_code == 400
    assert "CAPEX" in r.text


def test_patch_equipment_dcf_inline(client, auth_headers, test_project_id):
    _seed(client, auth_headers, test_project_id)
    g = client.get(f"/api/v1/projects/{test_project_id}/capex",
                   headers=auth_headers).json()
    eid = g["equipment"][0]["id"]
    huge = float(g["equipment"][0]["price_cad"]) * 10
    r = client.patch(
        f"/api/v1/projects/{test_project_id}/capex/equipment/{eid}",
        json={"price_cad": huge},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["dcf"] is not None
    assert body["dcf"]["initial_capex"] > 0
