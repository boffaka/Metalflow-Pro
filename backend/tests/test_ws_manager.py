# backend/tests/test_ws_manager.py
"""Verify WebSocket manager handles connections and broadcasting."""
import asyncio, pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_connect_adds_to_active_connections():
    from ws_manager import ConnectionManager
    manager = ConnectionManager()
    ws = AsyncMock()
    ws.accept = AsyncMock()
    await manager.connect("proj-1", ws)
    assert ws in manager.active_connections["proj-1"]

@pytest.mark.asyncio
async def test_disconnect_removes_from_active_connections():
    from ws_manager import ConnectionManager
    manager = ConnectionManager()
    ws = AsyncMock()
    ws.accept = AsyncMock()
    await manager.connect("proj-1", ws)
    manager.disconnect("proj-1", ws)
    assert ws not in manager.active_connections.get("proj-1", [])

@pytest.mark.asyncio
async def test_broadcast_sends_to_all_connections_for_project():
    from ws_manager import ConnectionManager
    manager = ConnectionManager()
    ws1, ws2 = AsyncMock(), AsyncMock()
    ws1.accept = ws2.accept = AsyncMock()
    await manager.connect("proj-1", ws1)
    await manager.connect("proj-1", ws2)
    await manager.broadcast("proj-1", {"type": "simulation_progress", "pct": 50})
    ws1.send_json.assert_awaited_once()
    ws2.send_json.assert_awaited_once()

@pytest.mark.asyncio
async def test_broadcast_to_unknown_project_is_noop():
    from ws_manager import ConnectionManager
    manager = ConnectionManager()
    # Should not raise
    await manager.broadcast("unknown-proj", {"type": "test"})
