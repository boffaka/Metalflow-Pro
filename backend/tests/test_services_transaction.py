"""Tests for services.transaction — commit semantics + after-commit hooks."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def fake_conn():
    cur = MagicMock(name="cursor")
    c = MagicMock(name="conn")
    c.cursor.return_value = cur
    return c, cur


def test_transaction_commits_on_success(fake_conn):
    c, cur = fake_conn
    with patch("services.transaction.conn", return_value=c), patch("services.transaction.release") as rel:
        from services.transaction import transaction

        with transaction() as actual_cur:
            assert actual_cur is cur
            actual_cur.execute("SELECT 1")

    c.commit.assert_called_once()
    c.rollback.assert_not_called()
    cur.close.assert_called_once()
    rel.assert_called_once_with(c)


def test_transaction_rolls_back_on_exception(fake_conn):
    c, cur = fake_conn
    with patch("services.transaction.conn", return_value=c), patch("services.transaction.release"):
        from services.transaction import transaction

        with pytest.raises(RuntimeError):
            with transaction():
                raise RuntimeError("boom")

    c.commit.assert_not_called()
    c.rollback.assert_called_once()


def test_after_commit_fires_only_on_success(fake_conn):
    c, _cur = fake_conn
    with patch("services.transaction.conn", return_value=c), patch("services.transaction.release"):
        from services.transaction import register_after_commit, transaction

        cb = MagicMock()
        with transaction():
            register_after_commit(cb)
            cb.assert_not_called()  # not yet — must wait for commit

        cb.assert_called_once()


def test_after_commit_dropped_on_rollback(fake_conn):
    c, _cur = fake_conn
    with patch("services.transaction.conn", return_value=c), patch("services.transaction.release"):
        from services.transaction import register_after_commit, transaction

        cb = MagicMock()
        with pytest.raises(ValueError):
            with transaction():
                register_after_commit(cb)
                raise ValueError("nope")

        cb.assert_not_called()


def test_after_commit_failure_does_not_propagate(fake_conn, caplog):
    c, _cur = fake_conn
    with patch("services.transaction.conn", return_value=c), patch("services.transaction.release"):
        from services.transaction import register_after_commit, transaction

        bad = MagicMock(side_effect=RuntimeError("downstream failed"))
        with transaction():
            register_after_commit(bad)
        # transaction returns cleanly; the failing callback is logged but swallowed

    assert any("after-commit" in rec.message for rec in caplog.records)


def test_after_commit_outside_tx_runs_immediately():
    from services.transaction import register_after_commit

    cb = MagicMock()
    register_after_commit(cb)
    cb.assert_called_once()
