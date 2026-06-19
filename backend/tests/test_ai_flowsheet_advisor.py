# backend/tests/test_ai_flowsheet_advisor.py
import pytest
import time
from unittest.mock import patch, MagicMock, AsyncMock
from engines.ai_flowsheet_advisor import AIFlowsheetAdvisor, AIObservation

pytestmark = pytest.mark.no_db


def test_build_context_not_empty():
    advisor = AIFlowsheetAdvisor()
    graph_summary = {"nodes": 5, "edges": 6}
    kpis = {"total_recovery_pct": 91.4, "energy_kwh_t": 28}
    ctx = advisor.build_context(graph_summary, kpis, lims_data={})
    assert len(ctx) > 50
    assert "91.4" in ctx


@pytest.mark.asyncio
async def test_analyze_returns_observations_on_success():
    advisor = AIFlowsheetAdvisor()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='[{"severity":"warning","message":"Rec faible","action":{}}]')]
    with patch.object(advisor, '_client') as mock_client:
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        obs = await advisor.analyze("contexte test")
    assert isinstance(obs, list)
    assert len(obs) == 1
    assert obs[0].severity == "warning"


@pytest.mark.asyncio
async def test_analyze_returns_empty_on_api_error():
    advisor = AIFlowsheetAdvisor()
    with patch.object(advisor, '_client') as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=Exception("API down"))
        obs = await advisor.analyze("contexte")
    assert obs == []


def test_cooldown_respected():
    advisor = AIFlowsheetAdvisor(cooldown_s=1)
    advisor._last_analysis_ts = time.time()
    assert advisor.is_in_cooldown()

    advisor._last_analysis_ts = time.time() - 2
    assert not advisor.is_in_cooldown()


def test_build_context_includes_lims_data():
    advisor = AIFlowsheetAdvisor()
    ctx = advisor.build_context(
        {"nodes": 3, "edges": 2},
        {"total_recovery_pct": 88},
        lims_data={"mean_au_g_t": 2.5, "mean_sulfur_pct": 1.2},
    )
    assert "2.50" in ctx
    assert "1.20" in ctx
