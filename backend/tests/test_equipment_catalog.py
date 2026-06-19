"""Sanity test for the static equipment catalog route."""
from __future__ import annotations
from fastapi.testclient import TestClient


def test_equipment_catalog_returns_60_codes(client: TestClient, seeded_project):
    res = client.get("/api/v1/equipment-catalog", headers=seeded_project["_headers"])
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 60
    # 8 main categories + a few specialty ones; must include critical ones
    assert "Concassage" in body["groups"]
    assert "Broyage" in body["groups"]
    assert "Lixiviation" in body["groups"]
    # BULLION must be present
    flat_codes = []
    for cat in body["groups"].values():
        for it in cat:
            flat_codes.append(it["code"])
    assert "BULLION" in flat_codes
    assert "FEED" in flat_codes
    assert len(set(flat_codes)) == 60
