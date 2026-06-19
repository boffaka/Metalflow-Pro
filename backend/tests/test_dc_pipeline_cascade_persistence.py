from __future__ import annotations

import pytest

pytestmark = pytest.mark.no_db


def test_run_cascade_persists_downstream_design_criteria_updates(monkeypatch):
    try:
        from routes import dc_pipeline
    except ImportError:  # pragma: no cover
        from backend.routes import dc_pipeline  # type: ignore[no-redef]

    executed: list[tuple[str, tuple]] = []

    def fake_qone(sql, params):
        if "FROM circuit_templates" in sql:
            return {"id": "tpl-1"}
        if "FROM projects" in sql:
            return {
                "target_tph": 1596,
                "gold_grade_g_t": 1.5,
                "operating_hours_day": 24,
                "availability_pct": 92,
            }
        raise AssertionError(sql)

    def fake_qall(sql, params):
        assert "dag_key IS NOT NULL" in sql
        return [
            {"ref_number": "1.1.1", "dag_key": "target_tph", "item": "Débit", "design_value": 1596, "source_code": "P"},
            {"ref_number": "3.1.2", "dag_key": "avg_bwi", "item": "Bond BWi", "design_value": 14.2, "source_code": "L"},
            {"ref_number": "3.1.3", "dag_key": "bm_f80_um", "item": "F80", "design_value": 2000, "source_code": "D"},
            {"ref_number": "3.1.4", "dag_key": "avg_p80_um", "item": "P80", "design_value": 75, "source_code": "D"},
            {"ref_number": "3.1.5", "dag_key": "mech_efficiency", "item": "Rendement", "design_value": 95, "source_code": "D"},
            {"ref_number": "3.1.6", "dag_key": "bm_install_margin_pct", "item": "Marge", "design_value": 10, "source_code": "D"},
            {"ref_number": "3.1.7", "dag_key": "bm_power_kw", "item": "Puissance", "design_value": 23500, "source_code": "D"},
        ]

    def fake_execute(sql, params):
        executed.append((sql, tuple(params)))
        return {"id": "updated"}

    monkeypatch.setattr(dc_pipeline, "qone", fake_qone)
    monkeypatch.setattr(dc_pipeline, "qall", fake_qall)
    monkeypatch.setattr(dc_pipeline, "execute", fake_execute)
    monkeypatch.setattr(dc_pipeline, "record_event", lambda **kwargs: None)

    body = dc_pipeline.CascadeRequest(changes=[dc_pipeline.DCChange(key="target_tph", value=1700)])
    result = dc_pipeline.run_cascade("project-1", body, user={"id": "user-1"})

    assert any(u["key"] == "bm_power_kw" for u in result["updates"])
    assert any("WHERE template_id = %s AND dag_key = %s" in sql for sql, _ in executed)
    assert any("bm_power_kw" in params for _, params in executed)
