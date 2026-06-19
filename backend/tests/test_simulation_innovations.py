"""Bootstrap tests for simulation_innovations write path. Full suite in chunk 12."""
from __future__ import annotations
import pytest
from fastapi.testclient import TestClient


def test_post_node_outputs_creates_rows(client: TestClient, seeded_project, seeded_run, seeded_node):
    """POST /runs/{run_id}/node-outputs accepts a list of metrics and upserts."""
    payload = {
        "metrics": [
            {"operation_id": seeded_node["id"], "metric_key": "recovery_pct", "value_num": 92.5, "value_unit": "%"},
            {"operation_id": seeded_node["id"], "metric_key": "power_kw",     "value_num": 5400.0, "value_unit": "kW"},
        ]
    }
    h = seeded_project["_headers"]
    res = client.post(
        f"/api/v1/projects/{seeded_project['id']}/simulation/runs/{seeded_run['id']}/node-outputs",
        json=payload, headers=h,
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["inserted_or_updated"] == 2

    # Verify roundtrip via GET
    res2 = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/runs/{seeded_run['id']}/node-outputs",
        headers=h,
    )
    assert res2.status_code == 200
    by_op = res2.json()["by_operation_id"]
    op = str(seeded_node["id"])
    assert by_op[op]["recovery_pct"]["value"] == pytest.approx(92.5)
    assert by_op[op]["power_kw"]["value"] == pytest.approx(5400.0)


def test_ai_suggest_falls_back_when_no_api_key(client, monkeypatch, seeded_project):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = client.post(
        f"/api/v1/projects/{seeded_project['id']}/flowsheet/ai-suggest",
        json={},
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "rule_based_fallback"
    assert body["suggested_template"]


def test_ai_suggest_uses_llm_when_key_present(client, monkeypatch, seeded_project):
    """Patch the Anthropic client with a stub that returns valid JSON."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    class _FakeMsg:
        def __init__(self):
            self.content = [type('o', (), {'text': '{"suggested_template":"AU_CIL_OXIDE","rationale":"oxide grade","modifications":[],"alternatives_considered":["AU_CIP_OXIDE"]}'})()]
    class _FakeMessages:
        def create(self, **kw):
            return _FakeMsg()
    class _FakeAnthropic:
        def __init__(self, api_key=None):
            self.messages = _FakeMessages()
    import sys
    sys.modules['anthropic'] = type(sys)('anthropic')
    sys.modules['anthropic'].Anthropic = _FakeAnthropic  # type: ignore[attr-defined]
    res = client.post(
        f"/api/v1/projects/{seeded_project['id']}/flowsheet/ai-suggest",
        json={},
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert body["source"] == "claude"
    assert body["suggested_template"] == "AU_CIL_OXIDE"


def test_gradient_returns_baseline_and_gradients(client, seeded_project, seeded_run):
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/runs/{seeded_run['id']}/gradient",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    body = res.json()
    assert "baseline" in body and isinstance(body["baseline"], dict)
    assert "gradients" in body and isinstance(body["gradients"], dict)
    assert body.get("validity_range_pct") == 25
    assert body.get("is_mock") is True  # v1 — flip to False once real engine wired


def test_gradient_404_on_unknown_run(client, seeded_project):
    bogus = "00000000-0000-0000-0000-000000000000"
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/runs/{bogus}/gradient",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 404


def test_diff_returns_param_topology_kpi_diffs(client, seeded_project):
    """Need 2 runs to diff."""
    from db import execute  # flat import — pytest runs from backend/
    # Note: simulation_runs_v2 has no `status` column in the current schema
    # (see conftest.seeded_run); plan's INSERT was written against an older
    # schema. We use the same shape as the existing seeded_run fixture.
    a = execute(
        "INSERT INTO simulation_runs_v2 (project_id, params) "
        "VALUES (%s, '{}'::jsonb) RETURNING id",
        (seeded_project["id"],),
    )
    b = execute(
        "INSERT INTO simulation_runs_v2 (project_id, params) "
        "VALUES (%s, '{}'::jsonb) RETURNING id",
        (seeded_project["id"],),
    )
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/runs/diff?a={a['id']}&b={b['id']}",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    body = res.json()
    for k in ("param_diffs", "topology_diffs", "kpi_diffs"):
        assert k in body
    assert body.get("is_mock") is True


def test_diff_404_on_missing_run(client, seeded_project, seeded_run):
    bogus = "00000000-0000-0000-0000-000000000000"
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/runs/diff?a={seeded_run['id']}&b={bogus}",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 404


def test_bottlenecks_returns_top_3_with_explanations(client, seeded_project, seeded_node):
    # Patch the seeded node to have a recovery below the threshold
    h = seeded_project["_headers"]
    client.patch(
        f"/api/v1/projects/{seeded_project['id']}/flowsheet/operations/{seeded_node['id']}",
        json={"recovery_pct": 80.0},
        headers=h,
    )
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/runs/latest/bottlenecks",
        headers=h,
    )
    assert res.status_code == 200
    body = res.json()
    assert "bottlenecks" in body
    assert len(body["bottlenecks"]) >= 1
    b = body["bottlenecks"][0]
    for k in ("node_id", "label", "score", "severity", "explanation", "recommendation", "estimated_impact"):
        assert k in b


# ════════════════════════════════════════════════════════════════════════════
# Next-actions rules engine (Chunk 12 Task 12.2)
# ════════════════════════════════════════════════════════════════════════════

def test_next_actions_no_flowsheet_returns_build_card(client, seeded_project):
    # Ensure there's no flowsheet
    from db import execute  # flat import — pytest runs from backend/
    execute("DELETE FROM circuit_templates WHERE project_id=%s", (seeded_project["id"],))
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/next-actions",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    cards = res.json()["cards"]
    assert any(c["id"] == "build_flowsheet" for c in cards)


def test_next_actions_no_bullion_returns_mark_bullion_card(client, seeded_project, seeded_node):
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/next-actions",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    cards = res.json()["cards"]
    assert any(c["id"] == "mark_bullion" for c in cards)


def test_next_actions_caps_at_5(client, seeded_project, seeded_node, seeded_run):
    res = client.get(
        f"/api/v1/projects/{seeded_project['id']}/simulation/next-actions",
        headers=seeded_project["_headers"],
    )
    assert res.status_code == 200
    cards = res.json()["cards"]
    assert len(cards) <= 5
