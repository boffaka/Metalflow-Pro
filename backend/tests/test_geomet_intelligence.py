"""Geomet intelligence API — unit tests with mocks."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.no_db

try:
    from backend.routes import geomet_intelligence as gi
except ImportError:
    import routes.geomet_intelligence as gi


@patch("backend.routes.geomet_intelligence.auto_cluster_domains")
def test_auto_domain_ok(mock_cluster):
    mock_cluster.return_value = {"status": "ok", "domains": [{"id": "d1"}]}
    out = gi.run_auto_domain("proj-1", user=MagicMock())
    assert out["status"] == "ok"
    assert "proj-1" in gi._domain_cache


@patch("backend.routes.geomet_intelligence.predict_recovery")
@patch("backend.routes.geomet_intelligence.auto_cluster_domains")
def test_predict_recovery_endpoint(mock_cluster, mock_pred):
    mock_cluster.return_value = {"status": "ok", "domains": [{"id": "d1"}]}
    mock_pred.return_value = {
        "predicted_recovery_pct": 92.5,
        "domain": "oxide",
        "ore_class": "non_refractory",
        "method": "knn",
        "model_r_squared": 0.88,
    }
    gi._domain_cache.clear()
    body = gi.PredictRecoveryRequest(au_g_t=1.2, bwi_kwh_t=14.0)
    out = gi.run_predict_recovery("proj-1", body, user=MagicMock())
    assert out["predicted_recovery_pct"] == pytest.approx(92.5)


@patch("backend.routes.geomet_intelligence.forecast_lom")
def test_lom_forecast(mock_fc):
    mock_fc.return_value = {"years": [{"year": 1, "recovery_pct": 90}]}
    out = gi.run_lom_forecast("proj-1", user=MagicMock())
    assert "years" in out
