"""Tests for risk register auto-generation."""
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.no_db

from backend.routes.risks import _do_generate_risks


PID = "00000000-0000-4000-8000-000000000001"
USER = {"id": "00000000-0000-4000-8000-000000000099"}


def _project_row():
    return {"id": PID, "target_tph": 200, "gold_grade_g_t": 1.2}


def test_autogen_always_includes_baseline_risks():
    captured_risks = []

    def fake_qone(sql, params):
        if "FROM projects" in sql:
            return _project_row()
        if "circuit_templates" in sql:
            return None
        if "FROM samples" in sql:
            return {"n": 0}
        if "block_model_configs" in sql:
            return None
        return None

    with patch("backend.routes.risks.qone", side_effect=fake_qone), \
         patch("backend.routes.risks.qall", return_value=[]), \
         patch("backend.routes.risks.execute"), \
         patch("backend.routes.risks._insert_generated_risks", side_effect=lambda pid, risks: captured_risks.extend(risks) or risks), \
         patch("backend.routes.risks.record_event"):
        result = _do_generate_risks(PID, USER)

    assert result["ok"] is True
    assert len(captured_risks) >= 2
    text = " ".join(r["description"].lower() for r in captured_risks)
    assert "permis" in text
    assert "or" in text or "van" in text


def test_autogen_queries_equipment_v2():
    captured_risks = []
    sql_calls = []

    def fake_qall(sql, params):
        sql_calls.append(sql)
        if "equipment_v2" in sql:
            return [{"lead_time_weeks": 52, "is_long_lead": True}]
        return []

    def fake_qone(sql, params):
        if "FROM projects" in sql:
            return _project_row()
        if "circuit_templates" in sql:
            return {"name": "CIL conventional"}
        return None

    with patch("backend.routes.risks.qone", side_effect=fake_qone), \
         patch("backend.routes.risks.qall", side_effect=fake_qall), \
         patch("backend.routes.risks.execute"), \
         patch("backend.routes.risks._insert_generated_risks", side_effect=lambda pid, risks: captured_risks.extend(risks) or risks), \
         patch("backend.routes.risks.record_event"):
        _do_generate_risks(PID, USER)

    assert any("equipment_v2" in s for s in sql_calls)
    assert any("long délai" in r["description"].lower() for r in captured_risks)
