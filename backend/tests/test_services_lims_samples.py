"""Tests for services.lims_samples — pilot of the services/ pattern."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_sample(**overrides):
    base = {
        "sample_id_display": "S-001",
        "phase": "exploration",
        "sample_type": "core",
        "lithology": "diorite",
        "provenance": "DDH-42",
        "mass_kg": 5.0,
        "representativity": "high",
        "waste_rock_dilution_pct": 0.0,
        "source_horizon": None,
        "depth_interval": "100-105m",
        "total_mass_kg": 5.0,
        "sent_mass_kg": 5.0,
        "collection_date": None,
        "reception_date": None,
        "collection_method": "diamond",
        "qaqc_protocol": "v2",
        "crm_standard": "CDN-GS-3",
        "duplicate_freq": "1/20",
        "blank_freq": "1/40",
        "packaging": "bag",
        "oxidation_state": "fresh",
        "domain": "main",
        "status": "received",
        "observations": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def fake_conn():
    cur = MagicMock(name="cursor")
    cur.description = [("id",), ("project_id",), ("sample_id_display",)]
    cur.fetchone.return_value = ("uuid-1", "proj-1", "S-001")
    c = MagicMock(name="conn")
    c.cursor.return_value = cur
    return c, cur


def test_create_sample_persists_and_signals(fake_conn):
    c, cur = fake_conn
    sample = _make_sample()

    with patch("services.transaction.conn", return_value=c), \
         patch("services.transaction.release"), \
         patch("services.lims_samples._signal_pipeline") as sig:
        from services.lims_samples import create_sample

        result = create_sample("proj-1", sample, user_id="user-7")

    assert result == {"id": "uuid-1", "project_id": "proj-1", "sample_id_display": "S-001"}
    assert cur.execute.call_args[0][1]["sample_id_display"] == "S-001"
    assert cur.execute.call_args[0][1]["project_id"] == "proj-1"
    c.commit.assert_called_once()
    sig.assert_called_once_with("proj-1", "user-7")


def test_create_sample_skips_signal_on_db_failure(fake_conn):
    c, cur = fake_conn
    cur.execute.side_effect = RuntimeError("db down")
    sample = _make_sample()

    with patch("services.transaction.conn", return_value=c), \
         patch("services.transaction.release"), \
         patch("services.lims_samples._signal_pipeline") as sig:
        from services.lims_samples import create_sample

        with pytest.raises(RuntimeError):
            create_sample("proj-1", sample, user_id="user-7")

    c.rollback.assert_called_once()
    sig.assert_not_called()


def test_create_sample_signal_failure_does_not_break_response(fake_conn):
    c, _cur = fake_conn
    sample = _make_sample()

    with patch("services.transaction.conn", return_value=c), \
         patch("services.transaction.release"), \
         patch("services.lims_samples._signal_pipeline", side_effect=RuntimeError("kafka down")):
        from services.lims_samples import create_sample

        result = create_sample("proj-1", sample, user_id="user-7")

    assert result["id"] == "uuid-1"
    c.commit.assert_called_once()
