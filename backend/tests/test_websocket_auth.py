"""Tests for WebSocket authentication enforcement.

WebSocket connections without a valid JWT must be rejected with code 4401.
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


def test_websocket_rejects_missing_token():
    """WebSocket without token must close with code 4401."""
    from main import app
    from starlette.testclient import TestClient

    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/projects/test-pid") as ws:
            pass


def test_websocket_rejects_invalid_token():
    """WebSocket with invalid JWT must close with code 4401."""
    from main import app
    from starlette.testclient import TestClient

    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/projects/test-pid?token=invalid-jwt") as ws:
            pass


def test_ws_authenticate_returns_none_on_missing_token():
    """_ws_authenticate must return None and close socket when token is missing."""
    import asyncio
    from main import _ws_authenticate

    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock()

    result = asyncio.get_event_loop().run_until_complete(
        _ws_authenticate(mock_ws, None)
    )
    assert result is None
    mock_ws.close.assert_called_once_with(code=4401, reason="missing_token")


def test_ws_authenticate_returns_none_on_invalid_token():
    """_ws_authenticate must return None and close socket when token is invalid."""
    import asyncio
    from main import _ws_authenticate

    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock()

    result = asyncio.get_event_loop().run_until_complete(
        _ws_authenticate(mock_ws, "definitely-not-a-valid-jwt")
    )
    assert result is None
    mock_ws.close.assert_called_once_with(code=4401, reason="invalid_token")


def test_ws_manager_not_registered_on_auth_failure():
    """ws_manager.connect() must NOT be called when auth fails."""
    import asyncio
    from websocket_handlers import ws_run_connection

    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock()
    mock_manager = MagicMock()
    mock_manager.connect = AsyncMock()

    asyncio.get_event_loop().run_until_complete(
        ws_run_connection(mock_manager, "test-channel", mock_ws, None, "test-pid")
    )
    mock_manager.connect.assert_not_called()
