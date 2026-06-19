"""Tests for GET /dc-pipeline/dependencies — Living Circuit DAG endpoint.

These tests are pure unit tests that mock the engine's load_dag() function
and the project_user dependency. No database is required.
"""
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.no_db


def test_get_dependencies_returns_dag_shape(monkeypatch):
    """The new endpoint serializes load_dag() output as JSON."""
    from backend import main
    from backend.routes import dc_pipeline
    from backend.auth import project_user as project_user_orig

    fake_dag = {
        "inputs": ["target_tph", "avg_bwi"],
        "nodes": {
            "sag_power_kw": {
                "depends_on": ["target_tph", "avg_bwi"],
                "formula_ref": "bond_power_sag",
                "section": "comminution",
            },
        },
    }
    monkeypatch.setattr(dc_pipeline, "load_dag", lambda: fake_dag)
    # `_cached_dag` is wrapped in `functools.lru_cache` (audit final
    # review §4) — clear it between tests so monkeypatched load_dag is
    # actually re-invoked rather than serving a previous cache hit.
    dc_pipeline._cached_dag.cache_clear()

    client = TestClient(main.app)
    main.app.dependency_overrides[project_user_orig] = (
        lambda: {"id": "u1", "role": "Process Engineer"}
    )
    try:
        r = client.get("/api/v1/projects/proj-123/dc-pipeline/dependencies")
    finally:
        main.app.dependency_overrides.clear()
        dc_pipeline._cached_dag.cache_clear()

    assert r.status_code == 200
    body = r.json()
    assert body["inputs"] == ["target_tph", "avg_bwi"]
    assert "sag_power_kw" in body["nodes"]
    assert body["nodes"]["sag_power_kw"]["depends_on"] == ["target_tph", "avg_bwi"]


def test_get_dependencies_handles_missing_registry(monkeypatch):
    """If the DAG registry file is missing, return 503."""
    from backend import main
    from backend.routes import dc_pipeline
    from backend.auth import project_user as project_user_orig

    def boom():
        raise FileNotFoundError("dc_dag_registry.yaml")

    monkeypatch.setattr(dc_pipeline, "load_dag", boom)
    dc_pipeline._cached_dag.cache_clear()
    client = TestClient(main.app)
    main.app.dependency_overrides[project_user_orig] = (
        lambda: {"id": "u1", "role": "Process Engineer"}
    )
    try:
        r = client.get("/api/v1/projects/proj-123/dc-pipeline/dependencies")
    finally:
        main.app.dependency_overrides.clear()
        dc_pipeline._cached_dag.cache_clear()
    assert r.status_code == 503


def test_cached_dag_caches_load_dag(monkeypatch):
    """`_cached_dag` only invokes `load_dag` once per process (audit §4)."""
    from backend.routes import dc_pipeline

    call_count = {"n": 0}
    fake_dag = {"inputs": [], "nodes": {}}

    def counting_load_dag():
        call_count["n"] += 1
        return fake_dag

    monkeypatch.setattr(dc_pipeline, "load_dag", counting_load_dag)
    dc_pipeline._cached_dag.cache_clear()
    try:
        a = dc_pipeline._cached_dag()
        b = dc_pipeline._cached_dag()
        c = dc_pipeline._cached_dag()
        assert a is b is c
        assert call_count["n"] == 1
    finally:
        dc_pipeline._cached_dag.cache_clear()
